"""GET-only Flask blueprint: /api/monitor/live/* (R1-R16, R19, R21-R24, copy chain).

Wired by bot.py with a context provider (``_live_copy_monitor_context``) and
an authorizer (admin token or MONITOR_READ_TOKEN). Never mutates state, never
calls Bitfinex, never returns secrets. Fails closed: a context error returns
HTTP 503 with verdict RED instead of a fabricated healthy answer.
"""
from __future__ import annotations

import time
from typing import Callable

from flask import Blueprint, jsonify, request

import live_copy_lifecycle as lcl
import live_copy_monitor as lcm
import live_monitor_routes as lmr

blueprint = Blueprint("live_copy_monitor", __name__)
_CTX: dict = {"provider": None, "authorize": None}
# Process boot (module import at bot startup) = start of the running revision for hop windows.
_PROCESS_BOOT_TS = time.time()

ENTRY_TYPES = ("ORDER_SENT", "ORDER_PLACED", "ORDER_AMENDED", "ORDER_CANCELLED",
               "ORDER_FILLED", "ORDER_REJECTED", "INTENT_REJECTED")


def wire(provider: Callable[[], dict], authorize: Callable[[], bool] | None = None) -> None:
    _CTX["provider"] = provider
    _CTX["authorize"] = authorize


def _guard():
    auth = _CTX.get("authorize")
    if auth is None or not auth():
        return jsonify({"error": "unauthorized"}), 401
    provider = _CTX.get("provider")
    if provider is None:
        return jsonify({"verdict": lcm.RED, "causes": ["MONITOR_NOT_WIRED"]}), 503
    try:
        return provider()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"verdict": lcm.RED, "causes": ["MONITOR_CONTEXT_ERROR"],
                        "error": type(exc).__name__}), 503


def _window_sec() -> float:
    try:
        return max(60.0, min(7 * 86400.0, float(request.args.get("window_sec") or 3600)))
    except (TypeError, ValueError):
        return 3600.0


def _analysis(ctx: dict, now: float) -> dict:
    chains = lcm.build_chains(ctx.get("approvals") or [], ctx.get("reports") or [])
    known = list((ctx.get("website") or {}).get("armed_accounts") or [])
    gaps = lcm.detect_gaps(chains, ctx.get("paper_twins") or {}, now=now, known_accounts=known)
    lags = lcm.lag_stats(chains, since_ts=now - _window_sec(), since_process_boot_ts=ctx.get("boot_ts") or _PROCESS_BOOT_TS)
    return {"chains": chains, "gaps": gaps, "lags": lags}


def _summary(ctx: dict, analysis: dict, now: float) -> dict:
    reports = ctx.get("reports") or []
    # LIVE-FIX-1816: build the lifecycle first so the gap detector's missed
    # copies (INTENT_NO_ACCOUNT_ORDER / ACCOUNT_DID_NOT_COPY) get the same
    # paused-window / historical_missed_copy classification as lifecycle alerts.
    lc, lc_exc = None, None
    try:
        lc, classifier = lcl.build_with_classifier(ctx, now=now)
        analysis = dict(analysis, gaps=[classifier.apply(dict(g)) for g in analysis.get("gaps") or []])
    except Exception as exc:  # noqa: BLE001 - handled below (fail closed)
        lc_exc = exc
    hour = now - 3600
    recent = [r for r in reports if float(r.get("fly_received_at_ts") or 0) >= hour]
    rejects = sum(1 for r in recent if r.get("type") in ("ORDER_REJECTED", "INTENT_REJECTED"))
    unsigned = sum(1 for r in recent if r.get("type") == "INTENT_REJECTED"
                   and str((r.get("error") or {}).get("code") or "").startswith("APPROVAL"))
    unsigned += sum(1 for r in (ctx.get("ingest_rejects") or []) if float(r.get("ts") or 0) >= hour)
    web_rejects = (ctx.get("website") or {}).get("ingest_rejects_1h") or {}
    try:
        unsigned += int(web_rejects.get("total") or 0)  # website ingest: unsigned/stale approvals
    except (TypeError, ValueError, AttributeError):
        pass
    last_report = max((float(r.get("fly_received_at_ts") or 0) for r in reports), default=0.0) or None
    try:
        cap_ts = float(((ctx.get("protection") or {}).get("executor_capability") or {}).get("received_at_ts") or 0)
    except (TypeError, ValueError):
        cap_ts = 0.0
    last_report = max(last_report or 0.0, cap_ts) or None
    out = lcm.summary(
        output=ctx.get("output") or {}, force_paper_mode=bool(ctx.get("force_paper_mode")),
        relay_stack_mode=str(ctx.get("relay_stack_mode") or ""), switch_rows=ctx.get("switch_rows") or [],
        gaps=analysis["gaps"], lags=analysis["lags"], executor_last_report_ts=last_report,
        website=ctx.get("website") or {}, rejects_1h=rejects, unsigned_rejects_1h=unsigned, now=now,
    )
    delivery = ctx.get("live_copy_delivery") or {}
    out["live_copy_delivery"] = delivery
    if out.get("tiles_live_on") and delivery and not delivery.get("allowed"):
        out["causes"] = sorted(set(out["causes"]) | {"LIVE_COPY_DELIVERY_SKIPPED:" + str(delivery.get("skip_reason"))})
        out["verdict"] = lcm.worst(out["verdict"], lcm.AMBER)
    if ctx.get("journal_write_failures"):
        out["causes"] = sorted(set(out["causes"]) | {"REPORT_JOURNAL_WRITE_FAILED"})
        out["verdict"] = lcm.worst(out["verdict"], lcm.RED)
    # Lifecycle alerts (arming truth, executor decisions, exits/drift, exchange mirror).
    try:
        if lc is None:
            raise lc_exc or RuntimeError("lifecycle unavailable")
        out["arming"] = {k: lc["arming"].get(k) for k in ("effective_live", "blockers", "mismatches", "verdict")}
        out["arming"]["accounts"] = (lc["arming"].get("sources") or {}).get("website_accounts")
        out["lifecycle_alerts"] = lc["alerts"][:50]
        out["causes"] = sorted(set(out["causes"]) | set(lc["alert_codes"]))
        # LIVE-FIX-1715: lc["verdict"] is the money-risk verdict; closed-trade
        # parity only feeds parity_verdict (alerts stay listed above).
        out["verdict"] = lcm.worst(out["verdict"], lc.get("money_risk_verdict") or lc["verdict"])
        out["parity_verdict"] = lcm.worst(out.get("parity_verdict") or lcm.GREEN,
                                          lc.get("parity_verdict") or lcm.GREEN)
        out["parity_causes"] = sorted(set(out.get("parity_causes") or []) | set(lc.get("parity_codes") or []))
        # LIVE-FIX-1816: historical missed copies (no money at risk) -> own bucket.
        out["missed_copy_verdict"] = lcm.worst(out.get("missed_copy_verdict") or lcm.GREEN,
                                               lc.get("missed_copy_verdict") or lcm.GREEN)
        out["missed_copy_causes"] = sorted(set(out.get("missed_copy_causes") or [])
                                           | set(lc.get("missed_copy_codes") or []))
        out["missed_copy_count"] = lc.get("missed_copy_count") or 0
        out["missed_copies"] = lc.get("missed_copies") or []
        out["expected_skip_causes"] = sorted(set(out.get("expected_skip_causes") or [])
                                             | set(lc.get("expected_skip_codes") or []))
        out["expected_skip_count"] = lc.get("expected_skip_count") or 0
        out["expected_skips"] = lc.get("expected_skips") or []
        out["exchange"] = {a: {k: m.get(k) for k in ("ok", "flat", "read_age_s", "error")}
                           | {"open_orders": len(m.get("open_orders") or []), "stops": len(m.get("stops") or []),
                              "orphans": m.get("orphans") or [], "balance": m.get("balance")}
                           for a, m in lc["exchange"].items()}
    except Exception as exc:  # noqa: BLE001 - never fabricate a healthy summary
        out["causes"] = sorted(set(out["causes"]) | {"LIFECYCLE_BUILD_FAILED"})
        out["verdict"] = lcm.worst(out["verdict"], lcm.RED)
        out["lifecycle_error"] = type(exc).__name__
    return out


def _ok(payload: dict):
    payload.setdefault("schema", lcm.SCHEMA)
    payload.setdefault("computed_at_ts", time.time())
    return jsonify(payload)


def _ctx_or_error():
    res = _guard()
    if isinstance(res, dict):
        return res, None
    return None, res


@blueprint.route("/api/monitor/live/summary", methods=["GET"])
def live_summary():  # R19
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    payload = _summary(ctx, _analysis(ctx, now), now)
    # LIVE-FIX-0702 (HM gap): /api/status reports FORCE_PAPER_MODE from the Fly
    # direct-path flag; this route used the live-copy paper lock under the same
    # name, so the two "disagreed". Report the /api/status source under the
    # shared name and the live-copy lock under its own name.
    if isinstance(payload, dict):
        payload["live_copy_paper_lock"] = bool(ctx.get("force_paper_mode"))
        payload["force_paper_mode"] = bool(ctx.get("fly_direct_force_paper_mode"))
        payload["force_paper_mode_source"] = "_force_paper_mode_active (same as /api/status)"
        payload["executor_freshness"] = executor_freshness(ctx, now)
        extra = extra_alerts(ctx, now)
        if extra:
            payload["monitor_alerts"] = extra
            payload["causes"] = sorted(set(payload.get("causes") or []) | {a["code"] for a in extra})
            for a in extra:
                payload["verdict"] = lcm.worst(payload.get("verdict") or lcm.GREEN, a.get("severity") or lcm.AMBER)
        # LIVE-FIX-1715: top-level verdict == money risk; parity is separate.
        payload["money_risk_verdict"] = payload.get("verdict")
        payload.setdefault("parity_verdict", lcm.GREEN)
        payload.setdefault("missed_copy_verdict", lcm.GREEN)
        payload["verdict_basis"] = ("money_risk (closed-trade parity -> parity_verdict; missed copies with "
                                    "paper closed + exchange flat -> missed_copy_verdict; paused-window "
                                    "skips -> expected_skips)")
    return _ok(payload)


EXECUTOR_REPORT_MAX_AGE_SEC = 300.0


def executor_freshness(ctx, now):
    """Newest executor signal: an execution report or the signed capability
    heartbeat (sent every ~60 s even when idle). Stale > 5 min = RED."""
    last_report = max((float(r.get("fly_received_at_ts") or 0) for r in ctx.get("reports") or []), default=0.0)
    cap = ((ctx.get("protection") or {}).get("executor_capability") or {})
    try:
        cap_ts = float(cap.get("received_at_ts") or 0)
    except (TypeError, ValueError):
        cap_ts = 0.0
    newest = max(last_report, cap_ts)
    age = (now - newest) if newest else None
    stale = age is None or age > EXECUTOR_REPORT_MAX_AGE_SEC
    return {"executor_report_age_s": (now - last_report) if last_report else None,
            "executor_capability_age_s": (now - cap_ts) if cap_ts else None,
            "newest_executor_signal_age_s": age, "max_age_s": EXECUTOR_REPORT_MAX_AGE_SEC,
            "stale": stale, "alert": "EXECUTOR_REPORT_STALE" if stale else None}


@blueprint.route("/api/monitor/live/arming", methods=["GET"])
def live_arming():  # R1
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    out = ctx.get("output") or {}
    rows = ctx.get("switch_rows") or []
    web = ctx.get("website") or {}
    return _ok({
        "route": "R1", "force_paper_mode": ctx.get("force_paper_mode"),
        "live_copy_output_on": bool(out.get("enabled")), "live_copy_output": out,
        "fly_master_retired": True, "relay_stack_mode": ctx.get("relay_stack_mode"),
        "website_armed_accounts": web.get("armed_accounts"), "website_reachable": web.get("reachable"),
        "tiles": [{k: r.get(k) for k in ("lane", "tile_number", "bitfinex_live_orders", "last_allow_ts",
                                          "relay_eligible", "eligible", "denials", "last_denial",
                                          "last_denied_at")} for r in rows],
        "kill_switches": [
            {"name": "FORCE_PAPER_MODE", "state": "ON" if ctx.get("force_paper_mode") else "OFF"},
            {"name": "LIVE_COPY_OUTPUT", "state": "ON" if out.get("enabled") else "OFF",
             "last_change_ts": out.get("last_change_ts"), "by": out.get("last_change_by"),
             "reason": out.get("last_reason")},
            {"name": "RELAY_STACK_MODE", "state": ctx.get("relay_stack_mode")},
        ],
        "audit_tail": out.get("history"),
        "trade_decisions": ctx.get("decisions"),
        "arming": lcl.effective_arming(
            output=out, force_paper_mode=bool(ctx.get("force_paper_mode")),
            relay_stack_mode=str(ctx.get("relay_stack_mode") or ""), switch_rows=rows, website=web,
            fly_direct_force_paper=ctx.get("fly_direct_force_paper_mode")),
    })


@blueprint.route("/api/monitor/live/lifecycle", methods=["GET"])
def live_lifecycle():  # R24: full live-trade lifecycle, per intent and account
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    payload = lcl.build(ctx, now=now)
    payload["route"] = "R24"
    try:  # LIVE-FIX-1511: per-chain declined_reason / band_bp / mark_vs_limit_bp + decline rate
        a = _analysis(ctx, now)
        accts = list(a["chains"]["accounts"].values())
        payload["entry_fidelity"] = lcm.enrich_entry_fidelity(accts, ctx.get("paper_twins") or {})
        keep = ("correlation_id", "account", "order_type", "order_tif", "entry_order_mode", "rested_s",
                "fill_model", "signal_ask_age_ms", "slippage_vs_signal_bp", "slippage_vs_paper_bp",
                "declined_reason", "band_bp", "mark_vs_limit_bp")
        payload["entry_fidelity_chains"] = [{k: c.get(k) for k in keep} for c in accts
                                            if str(c.get("correlation_id") or "").startswith("dsl-")][-100:]
    except Exception as exc:  # noqa: BLE001 - observation only
        payload["entry_fidelity"] = {"error": str(exc)[:200]}
    cid = request.args.get("correlation_id")
    if cid:
        payload["intents"] = [i for i in payload["intents"] if i.get("correlation_id") == cid
                              or i.get("trade_id") == cid]
    acct = request.args.get("account")
    if acct:
        for i in payload["intents"]:
            i["accounts"] = [a for a in i.get("accounts") or [] if a.get("account") == acct]
    return _ok(payload)


@blueprint.route("/api/monitor/runtime/headroom", methods=["GET"])
def runtime_headroom_alias():  # R12 alias readable with the monitor token
    return live_headroom()


@blueprint.route("/api/monitor/restarts", methods=["GET"])
def monitor_restarts():  # R10: boots/restarts as seen by this process (monitor token)
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    started = ctx.get("process_started_ts")
    restarts = ctx.get("restarts") or {}
    return _ok({"route": "R10", "process_started_ts": started,
                "uptime_sec": round(now - float(started), 1) if started else None,
                "bot_instance_id": ctx.get("bot_instance_id"), "source_git_rev": ctx.get("source_git_rev"),
                **(restarts if isinstance(restarts, dict) else {})})


def _orders(ctx: dict) -> list:
    by_cid: dict = {}
    for r in ctx.get("reports") or []:
        key = (str(r.get("correlation_id") or ""), str(r.get("account") or ""))
        row = by_cid.setdefault(key, {"correlation_id": key[0], "account": key[1],
                                      "lane": r.get("lane"), "events": []})
        order = r.get("order") if isinstance(r.get("order"), dict) else {}
        row["events"].append({"type": r.get("type"), "sent_at_ts": r.get("sent_at_ts"),
                              "fly_received_at_ts": r.get("fly_received_at_ts"),
                              "exchange_order_id": order.get("exchange_order_id"),
                              "client_order_id": order.get("client_order_id"),
                              "order_type": order.get("type"), "flags": order.get("flags"),
                              "qty": order.get("qty"), "price": order.get("price"),
                              "status": order.get("status"), "error": r.get("error")})
        if r.get("type") in ENTRY_TYPES:
            row["status"] = r.get("type")
        row["timeline"] = {**(row.get("timeline") or {}), **{k: v for k, v in (r.get("timeline") or {}).items() if v}}
    return list(by_cid.values())


@blueprint.route("/api/monitor/live/orders", methods=["GET"])
def live_orders():  # R2
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    rows = _orders(ctx)
    try:
        limit = max(1, min(500, int(request.args.get("limit") or 100)))
    except (TypeError, ValueError):
        limit = 100
    return _ok({"route": "R2", "count": len(rows), "orders": rows[-limit:]})


@blueprint.route("/api/monitor/live/latency", methods=["GET"])
def live_latency():  # R3
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    a = _analysis(ctx, now)
    worst = sorted((c for c in a["chains"]["accounts"].values() if c.get("intent_to_ack_sec") is not None),
                   key=lambda c: -c["intent_to_ack_sec"])[:5]
    return _ok({"route": "R3", "window_sec": _window_sec(), "hops": list(lcm.STAGES),
                "stats": a["lags"], "budget_sec": {"amber": lcm.LAG_AMBER_SEC, "red": lcm.LAG_RED_SEC},
                "worst": [{"correlation_id": c["correlation_id"], "account": c["account"],
                           "intent_to_ack_sec": c["intent_to_ack_sec"], "lags_sec": c["lags_sec"]} for c in worst]})


@blueprint.route("/api/monitor/live/copy-chain", methods=["GET"])
def live_copy_chain():
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    a = _analysis(ctx, now)
    accts = list(a["chains"]["accounts"].values())
    try:
        limit = max(1, min(500, int(request.args.get("limit") or 100)))
    except (TypeError, ValueError):
        limit = 100
    try:  # LIVE-FIX-1510: fill model / ask age / order type / rested_s / slippage + DISLOC fill rate
        fidelity = lcm.enrich_entry_fidelity(accts, ctx.get("paper_twins") or {})
    except Exception as exc:  # noqa: BLE001 - observation only
        fidelity = {"error": str(exc)[:200]}
    return _ok({"stages": list(lcm.STAGES), "intents": list(a["chains"]["intents"].values())[-limit:],
                "accounts": accts[-limit:], "lag_stats": a["lags"], "entry_fidelity": fidelity})


@blueprint.route("/api/monitor/live-vs-paper", methods=["GET"])
def live_vs_paper_route():
    """Read-only live-vs-paper pairs per signal (HM spec 19 Oct). ?lane=GS01 (substring of the lane id), ?limit=N."""
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    import live_vs_paper as lvp
    chains = lcm.build_chains(ctx.get("approvals") or [], ctx.get("reports") or [])
    twins = ctx.get("paper_twins") or {}
    intents = chains["intents"]
    lane = str(request.args.get("lane") or "").strip().upper()
    try:
        limit = max(1, min(500, int(request.args.get("limit") or 200)))
    except (TypeError, ValueError):
        limit = 200

    def _keep(ch, twin):
        if not lane:
            return True
        return lane in str(ch.get("lane") or "").upper() or lane in str((intents.get(ch.get("correlation_id")) or {}).get("lane") or "").upper()

    def _keep_paper(_ch, _twin):  # paper-only rows carry their lane on the emitted intent
        return True

    accts = list(chains["accounts"].values())
    out = lvp.build(accts, twins, intents,
                    paper_signal_ids=[c for c, i in intents.items() if not lane or lane in str(i.get("lane") or "").upper()],
                    skip_reasons=ctx.get("live_skip_reasons") or {}, lane_filter=None if not lane else
                    (lambda ch, tw: _keep(ch, tw) if ch else True), limit=limit)
    return _ok(out)


@blueprint.route("/api/monitor/live/gaps", methods=["GET"])
def live_gaps():
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    a = _analysis(ctx, now)
    verdict = lcm.GREEN
    for g in a["gaps"]:
        verdict = lcm.worst(verdict, g.get("severity") or lcm.AMBER)
    return _ok({"verdict": verdict, "gaps": a["gaps"],
                "thresholds": {"intent_to_order_sec": lcm.INTENT_TO_ORDER_GAP_SEC,
                               "fill_to_stop_sec": lcm.FILL_TO_STOP_GAP_SEC,
                               "report_missing_sec": lcm.REPORT_MISSING_GAP_SEC,
                               "price_drift_bp": [lcm.PRICE_DRIFT_AMBER_BP, lcm.PRICE_DRIFT_RED_BP]}})


@blueprint.route("/api/monitor/live/reconcile", methods=["GET"])
def live_reconcile():  # R4 (Fly view: paper vs executor-reported live)
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    a = _analysis(ctx, time.time())
    open_live = [c for c in a["chains"]["accounts"].values() if c.get("fill_at_ts") and not c.get("closed")]
    twins = ctx.get("paper_twins") or {}
    diffs = []
    for c in open_live:
        twin = twins.get(c["correlation_id"]) or {}
        if twin.get("exit_ts") or twin.get("exit_price"):
            diffs.append({"object": "position", "correlation_id": c["correlation_id"], "account": c["account"],
                          "field": "open", "values": {"paper": "CLOSED", "live": "OPEN"}})
    last_report = max((float(r.get("fly_received_at_ts") or 0) for r in ctx.get("reports") or []), default=0.0)
    fresh = executor_freshness(ctx, time.time())
    return _ok({"route": "R4", "flat": not open_live, "open_live_positions": len(open_live),
                "diffs": diffs, "executor_report_age_s": (time.time() - last_report) if last_report else None,
                "executor_freshness": fresh,
                "alerts": [{"code": fresh["alert"], "severity": "RED"}] if fresh["alert"] else [],
                "note": "Per-account exchange truth is on the website: GET /api/trading-agents/conservative-btc/ops/copy/status"})


@blueprint.route("/api/monitor/live/protection", methods=["GET"])
def live_protection():  # R5
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    a = _analysis(ctx, now)
    rows = []
    for c in a["chains"]["accounts"].values():
        if not c.get("fill_at_ts") or c.get("closed"):
            continue
        rows.append({"correlation_id": c["correlation_id"], "account": c["account"], "lane": c.get("lane"),
                     "protected": bool(c.get("stop_confirmed")),
                     "unprotected_s": None if c.get("stop_confirmed") else round(now - c["fill_at_ts"], 1)})
    return _ok({"route": "R5", "positions": rows, "evidence": ctx.get("protection"),
                "all_protected": all(r["protected"] for r in rows)})


@blueprint.route("/api/monitor/live/exchange-health", methods=["GET"])
def live_exchange_health():  # R6 (executor-side errors as reported)
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    errs = [r for r in ctx.get("reports") or [] if r.get("type") in ("ERROR", "ORDER_REJECTED", "STOP_FAILED")
            and float(r.get("fly_received_at_ts") or 0) >= now - 3600]
    return _ok({"route": "R6", "errors_1h": len(errs),
                "recent_errors": [{"type": r.get("type"), "account": r.get("account"),
                                   "error": r.get("error")} for r in errs[-20:]],
                "website": ctx.get("website"),
                "key_scopes": "GET /api/trading-agents/conservative-btc/ops/account-check (per account)"})


@blueprint.route("/api/monitor/live/balance", methods=["GET"])
def live_balance():  # R7
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    latest = {}
    for r in ctx.get("reports") or []:
        if isinstance(r.get("balance"), dict):
            latest[str(r.get("account") or "")] = {**r["balance"], "at_ts": r.get("sent_at_ts")}
    return _ok({"route": "R7", "accounts": latest, "paper_kept_separate": True})


@blueprint.route("/api/monitor/live/parity", methods=["GET"])
def live_parity():  # R8
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    a = _analysis(ctx, time.time())
    pairs = [{"correlation_id": c["correlation_id"], "account": c["account"], "lane": c.get("lane"),
              "price_drift_bp": c.get("price_drift_bp"), "size_drift_fraction": c.get("size_drift_fraction")}
             for c in a["chains"]["accounts"].values() if c.get("fill_price")]
    drift_gaps = [g for g in a["gaps"] if g["code"] in ("PRICE_DRIFT_VS_PAPER", "SIZE_ABOVE_PAPER", "SIZE_DRIFT_VS_PAPER")]
    return _ok({"route": "R8", "pairs": pairs, "drift_flags": drift_gaps})


@blueprint.route("/api/monitor/live/relay", methods=["GET"])
def live_relay():  # R9
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    return _ok({"route": "R9", "relay_stack_mode": ctx.get("relay_stack_mode"), **(ctx.get("relay") or {})})


@blueprint.route("/api/monitor/live/restarts", methods=["GET"])
def live_restarts():  # R10 (boot record; full history at /api/restart-cause)
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    out = ctx.get("output") or {}
    return _ok({"route": "R10", "process_started_ts": ctx.get("process_started_ts"),
                "bot_instance_id": ctx.get("bot_instance_id"), "source_git_rev": ctx.get("source_git_rev"),
                "live_paused_after_boot": not out.get("enabled"),
                "output_reset_on_boot": any(h.get("reason") == "PROCESS_RESTART_FAIL_CLOSED"
                                            for h in out.get("history") or []),
                "history": "/api/restart-cause"})


@blueprint.route("/api/monitor/live/exit-delay", methods=["GET"])
def live_exit_delay():  # R11 (live closes vs paper close)
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    twins = ctx.get("paper_twins") or {}
    rows = []
    for r in ctx.get("reports") or []:
        if r.get("type") != "POSITION_CLOSED":
            continue
        twin = twins.get(str(r.get("correlation_id") or "")) or {}
        tl = r.get("timeline") or {}
        try:
            paper_exit = float(twin.get("exit_ts")) if twin.get("exit_ts") else None
        except (TypeError, ValueError):
            paper_exit = None
        live_exit = tl.get("close_fill_at_ts") or tl.get("close_ack_at_ts")
        rows.append({"correlation_id": r.get("correlation_id"), "account": r.get("account"),
                     "paper_exit_ts": paper_exit, "live_exit_ts": live_exit,
                     "delay_sec": (round(float(live_exit) - paper_exit, 3) if live_exit and paper_exit else None)})
    return _ok({"route": "R11", "exits": rows[-100:], "paper_exit_delay": "/api/monitor/summary"})


@blueprint.route("/api/monitor/live/headroom", methods=["GET"])
def live_headroom():  # R12
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    return _ok({"route": "R12", "runtime_telemetry": ctx.get("runtime_telemetry")})


@blueprint.route("/api/monitor/live/deploy-guard", methods=["GET"])
def live_deploy_guard():  # R13
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    a = _analysis(ctx, now)
    out = ctx.get("output") or {}
    open_live = [c for c in a["chains"]["accounts"].values() if c.get("fill_at_ts") and not c.get("closed")]
    resting = [c for c in a["chains"]["accounts"].values()
               if c.get("exchange_ack_at_ts") and not c.get("fill_at_ts") and not c.get("closed")
               and not any(t in ("ORDER_CANCELLED", "ORDER_REJECTED") for t in c.get("reports") or [])]
    unprotected = [c for c in open_live if not c.get("stop_confirmed")]
    reasons = []
    if out.get("enabled"):
        reasons.append("LIVE_COPY_OUTPUT_ON")
    if open_live:
        reasons.append("OPEN_LIVE_POSITIONS")
    if resting:
        reasons.append("OPEN_LIVE_ORDERS")
    if unprotected:
        reasons.append("UNPROTECTED_POSITIONS")
    return _ok({"route": "R13", "armed": bool(out.get("enabled")), "flat": not open_live,
                "open_live_orders": len(resting), "all_positions_protected": not unprotected,
                "deploy_allowed": not reasons, "blocking_reasons": reasons})



# ---- R14/R15/R16/R21/R22/R23 (LIVE-READINESS-MONITORING-PLAN R14-R23) -----------------
import live_copy_monitor_extra as lcx  # noqa: E402


def _int_arg(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(request.args.get(name) or default)))
    except (TypeError, ValueError):
        return default


def _lifecycle_alerts(ctx, now):
    try:
        return lcl.build(ctx, now=now)["alerts"]
    except Exception:  # noqa: BLE001
        return [{"code": "LIFECYCLE_BUILD_FAILED", "severity": lcm.RED, "at_ts": now}]


@blueprint.route("/api/monitor/live/events", methods=["GET"])
def live_events():  # R14
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    try:
        since = float(request.args.get("since_ts") or 0)
    except (TypeError, ValueError):
        since = 0.0
    return _ok(lcx.events(ctx.get("approvals") or [], ctx.get("reports") or [], _lifecycle_alerts(ctx, now),
                          since_ts=since, limit=_int_arg("limit", 200, 1, 1000), cursor=_int_arg("cursor", 0, 0, 10**7)))


@blueprint.route("/api/monitor/live/fill-quality", methods=["GET"])
def live_fill_quality():  # R15
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    mid_at = ctx.get("mid_at") if callable(ctx.get("mid_at")) else (lambda ts: None)
    return _ok(lcx.fill_quality(_analysis(ctx, now)["chains"], ctx.get("paper_twins") or {}, mid_at, now))


@blueprint.route("/api/monitor/live/daily", methods=["GET"])
def live_daily():  # R16
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    return _ok(lcx.daily(ctx.get("reports") or [], ctx.get("ledger_by_day")))


@blueprint.route("/api/monitor/live/slo", methods=["GET"])
def live_slo():  # R21
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    return _ok(lcx.slo(_analysis(ctx, now)["chains"], ctx.get("protection_samples") or [],
                       ctx.get("restarts_7d"), now))


@blueprint.route("/api/monitor/live/drills", methods=["GET"])
def live_drills():  # R22
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    return _ok(lcx.drills(ctx.get("drills") or [], time.time()))


@blueprint.route("/api/monitor/live/funding", methods=["GET"])
def live_funding():  # R23
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    return _ok(lcx.funding(ctx.get("reports") or []))


@blueprint.route("/api/monitor/live/routes", methods=["GET"])
def live_routes():  # manifest of every live monitor route (monitor token)
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    return _ok({"routes": lcx.LIVE_ROUTE_MANIFEST, "auth": "admin token or MONITOR_READ_TOKEN"})


def extra_alerts(ctx: dict, now: float) -> list:
    """R14/R15/R16/R21 alerts folded into /api/monitor/live/summary."""
    out = []
    try:
        chains = _analysis(ctx, now)["chains"]
        mid_at = ctx.get("mid_at") if callable(ctx.get("mid_at")) else (lambda ts: None)
        out += lcx.events(ctx.get("approvals") or [], [], [], limit=1)["alerts"]
        out += lcx.fill_quality(chains, ctx.get("paper_twins") or {}, mid_at, now)["alerts"]
        out += lcx.daily(ctx.get("reports") or [], ctx.get("ledger_by_day"))["alerts"]
        out += lcx.slo(chains, ctx.get("protection_samples") or [], ctx.get("restarts_7d"), now)["alerts"]
    except Exception:  # noqa: BLE001
        out.append({"code": "LIVE_EXTRA_ALERTS_FAILED", "severity": lcm.RED})
    return out


_LIVE_FLAGS_CACHE: dict = {"ts": 0.0, "payload": None}


def live_flags_snapshot(max_age_s: float = 5.0) -> dict:
    """Live flags for /api/state from the same effective_live source as /api/monitor/live/* (never empty)."""
    now = time.time()
    cached = _LIVE_FLAGS_CACHE.get("payload")
    if cached is not None and now - float(_LIVE_FLAGS_CACHE.get("ts") or 0) <= max_age_s:
        return cached
    out = {"schema": "api_state_live_flags_v1", "computed_at_ts": now, "effective_live": None,
           "source": "live_copy_lifecycle.arming"}
    try:
        provider = _CTX.get("provider")
        ctx = provider() if provider is not None else None
        if not isinstance(ctx, dict):
            out["error"] = "MONITOR_NOT_WIRED"
        else:
            arming = (lcl.build(ctx, now=now) or {}).get("arming") or {}
            output = ctx.get("output") or {}
            out.update({
                "effective_live": bool(arming.get("effective_live")),
                "blockers": arming.get("blockers") or [],
                "mismatches": arming.get("mismatches") or [],
                "verdict": arming.get("verdict"),
                "output_enabled": bool(output.get("enabled")) if isinstance(output, dict) else None,
                "force_paper_mode": bool(ctx.get("force_paper_mode")),
                "tiles_live_on": sorted(str(r.get("lane")) for r in ctx.get("switch_rows") or []
                                        if isinstance(r, dict) and (r.get("bitfinex_live_orders") or r.get("live_eligible"))),
                "accounts": (arming.get("sources") or {}).get("website_accounts"),
            })
    except Exception as exc:  # noqa: BLE001 - report, never fabricate
        out["error"] = type(exc).__name__
        out["effective_live"] = False
    _LIVE_FLAGS_CACHE.update({"ts": now, "payload": out})
    return out


# --- LIVE-FIX-1491: HM per-phase routes (read-only, monitor token) -----------
@blueprint.route("/api/monitor/live/timeline", methods=["GET"])
def live_timeline():
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    try:
        limit = max(1, min(500, int(request.args.get("limit") or 50)))
    except (TypeError, ValueError):
        limit = 50
    return _ok(lmr.timeline(ctx.get("reports") or [], ctx.get("approvals") or [], ctx.get("paper_twins") or {},
                            trade_id=(request.args.get("trade_id") or None), limit=limit,
                            since=now - _window_sec() * (24 if not request.args.get("window_sec") else 1)))


@blueprint.route("/api/monitor/live/pause-history", methods=["GET"])
def live_pause_history():
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    return _ok(lmr.pause_history((ctx.get("website") or {}).get("accounts") or [], now=time.time()))


@blueprint.route("/api/monitor/live/protection-state", methods=["GET"])
def live_protection_state():
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    return _ok(lmr.protection_state((ctx.get("website") or {}).get("accounts") or []))


@blueprint.route("/api/monitor/live/clock", methods=["GET"])
def live_clock():
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    now = time.time()
    return _ok(lmr.clock(ctx.get("clock_sync") or {}, ctx.get("reports") or [], since=now - _window_sec()))


@blueprint.route("/api/monitor/live/paths", methods=["GET"])
def live_paths():
    ctx, err = _ctx_or_error()
    if err is not None:
        return err
    return _ok(lmr.paths(ctx.get("relay") or {}, ctx.get("deliveries")))
