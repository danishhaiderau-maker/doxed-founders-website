"""GET-only Flask blueprint: /api/monitor/live/* (R1-R13, R19, copy chain).

Wired by bot.py with a context provider (``_live_copy_monitor_context``) and
an authorizer (admin token or MONITOR_READ_TOKEN). Never mutates state, never
calls Bitfinex, never returns secrets. Fails closed: a context error returns
HTTP 503 with verdict RED instead of a fabricated healthy answer.
"""
from __future__ import annotations

import time
from typing import Callable

from flask import Blueprint, jsonify, request

import live_copy_monitor as lcm

blueprint = Blueprint("live_copy_monitor", __name__)
_CTX: dict = {"provider": None, "authorize": None}

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
    lags = lcm.lag_stats(chains, since_ts=now - _window_sec())
    return {"chains": chains, "gaps": gaps, "lags": lags}


def _summary(ctx: dict, analysis: dict, now: float) -> dict:
    reports = ctx.get("reports") or []
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
    out = lcm.summary(
        output=ctx.get("output") or {}, force_paper_mode=bool(ctx.get("force_paper_mode")),
        relay_stack_mode=str(ctx.get("relay_stack_mode") or ""), switch_rows=ctx.get("switch_rows") or [],
        gaps=analysis["gaps"], lags=analysis["lags"], executor_last_report_ts=last_report,
        website=ctx.get("website") or {}, rejects_1h=rejects, unsigned_rejects_1h=unsigned, now=now,
    )
    if ctx.get("journal_write_failures"):
        out["causes"] = sorted(set(out["causes"]) | {"REPORT_JOURNAL_WRITE_FAILED"})
        out["verdict"] = lcm.worst(out["verdict"], lcm.RED)
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
    return _ok(_summary(ctx, _analysis(ctx, now), now))


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
    })


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
    return _ok({"stages": list(lcm.STAGES), "intents": list(a["chains"]["intents"].values())[-limit:],
                "accounts": accts[-limit:], "lag_stats": a["lags"]})


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
    return _ok({"route": "R4", "flat": not open_live, "open_live_positions": len(open_live),
                "diffs": diffs, "executor_report_age_s": (time.time() - last_report) if last_report else None,
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
