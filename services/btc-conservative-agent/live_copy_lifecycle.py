"""Read-only live-trade lifecycle view (/api/monitor/live/lifecycle).

Pure analysis, no I/O. Joins Fly's signed approvals, the executor's verified
reports (incl. INTENT_RECEIVED / INTENT_SKIPPED / INTENT_SUBMITTED decisions),
the paper twins, the Fly arm switches and the website's fly-view (instance
status, last arm transition with actor/reason/time, exchange mirror) into:

* ``arming``: one ``effective_live`` answer plus every source it came from;
  real disagreements are RED ``ARMING_SOURCES_MISMATCH``; expected pause/deploy
  races (e.g. PAUSED_BUT_LAST_CHANGE_STARTED during USER_REQUEST_START / deploy)
  are INFO under ``expected_mismatches`` / expected_skips and do not paint money_risk.
* ``intents``: per intent and account, every hop exactly once (first value
  wins, so a later lifecycle event can never overwrite an earlier hop), the
  executor decision, the exit record and paper-vs-live drift.
* ``exchange``: positions / open orders / stops / orphans / balance with
  ``read_age_s`` from the executor's read-only mirror.
* ``alerts``: codes with severity; old intents expire after ``GAP_TTL_SEC``
  so alerts do not flap on history.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

SCHEMA = "fly_live_lifecycle_v1"
GREEN, AMBER, RED = "GREEN", "AMBER", "RED"
INFO = "INFO"  # expected / informational: never moves any verdict
_RANK = {INFO: 0, GREEN: 0, AMBER: 1, RED: 2}

GAP_TTL_SEC = 6 * 3600.0
INTENT_UNDECIDED_SEC = 30.0
NO_ORDER_FOR_INTENT_SEC = 60.0
FILL_WITHOUT_STOP_SEC = 10.0
PAPER_CLOSED_LIVE_OPEN_SEC = 30.0
ORPHAN_WHEN_FLAT_SEC = 60.0
EXCHANGE_READ_MAX_AGE_SEC = 180.0
EXIT_DRIFT_BP = 5.0
# LIVE-FIX-1715: closed-trade paper-vs-live parity (exit reason / exit drift)
# is history, not open money risk. Below this |bp| a closed-trade exit drift
# is AMBER and never paints the top-level verdict RED; at/above it the drift
# itself is large enough to count as money risk (RED).
EXIT_DRIFT_MONEY_RISK_BP = 10.0
# Alert codes that describe paper-vs-live parity of a trade. On a CLOSED trade
# they are classed "historical_parity" (parity_verdict), not money risk.
HISTORICAL_PARITY_CODES = frozenset({"EXIT_DRIFT_BP", "EXIT_REASON_MISMATCH", "PRICE_DRIFT_VS_PAPER"})
MONEY_RISK, HISTORICAL_PARITY = "money_risk", "historical_parity"
# LIVE-FIX-1816: a missed copy (no order / no executor decision / undelivered)
# is money risk only while it can still matter: the account was armed when
# the intent was emitted AND (the paper twin is still open/working OR the
# exchange holds exposure no tracked live copy explains). Otherwise:
# * emitted while the account was paused/disarmed -> "paused_window" (INFO,
#   expected skip, listed under expected_skips; moves no verdict);
# * paper twin closed/terminal and exchange flat or fully accounted for ->
#   "historical_missed_copy" (AMBER, missed_copy_verdict / missed_copies).
HISTORICAL_MISSED_COPY, PAUSED_WINDOW = "historical_missed_copy", "paused_window"
ALERT_CLASSES = frozenset({MONEY_RISK, HISTORICAL_PARITY, HISTORICAL_MISSED_COPY, PAUSED_WINDOW})
MISSED_COPY_CODES = frozenset({"NO_ORDER_FOR_INTENT", "INTENT_NO_EXECUTOR_DECISION", "INTENT_UNDELIVERED",
                               "INTENT_NO_ACCOUNT_ORDER", "ACCOUNT_DID_NOT_COPY"})
ARMED_ACTIONS = frozenset({"STARTED", "ARMED", "RESUMED", "OBSERVED_ARMED"})
UNARMED_ACTIONS = frozenset({"STOPPED", "SAFETY_PAUSED", "PAUSED", "DISARMED", "EXPIRED", "HIRE_EXPIRED",
                             "OBSERVED_UNARMED"})
# LIVE-FIX-1928: PAUSED + last_change=STARTED is a normal race during deploy
# force-flat / re-arm (website records STARTED while status is still PAUSED, or
# ops pause right after an arm attempt). Those reasons are expected_mismatches
# (INFO / expected_skips), not money-risk RED. Unexplained cases stay RED.
EXPECTED_PAUSED_STARTED_REASON_TOKENS = (
    "USER_REQUEST", "DEPLOY", "MAINTENANCE", "ADMIN", "FORCE_FLAT", "REARM",
    "OPERATOR", "BOSS_",
)


def _expected_paused_but_started(row: Mapping[str, Any], last: Mapping[str, Any]) -> bool:
    reason = str(last.get("reason") or row.get("paused_reason") or "").upper()
    return any(tok in reason for tok in EXPECTED_PAUSED_STARTED_REASON_TOKENS)

PAPER_TERMINAL_EVENTS = frozenset({"POSITION_CLOSED", "ORDER_EXPIRED", "ORDER_CANCELLED"})
ENTRY_DRIFT_RED_BP = 10.0
CLOCK_OFFSET_AMBER_MS = 1000.0

# Hops in lifecycle order: (name, source key on the report timeline / record)
ENTRY_HOPS = (
    "fly_signal_at_ts", "fly_intent_emitted_at_ts", "fly_delivered_at_ts",
    "railway_received_at_ts", "executor_received_at_ts", "executor_decided_at_ts",
    "order_sent_at_ts", "exchange_ack_at_ts", "fill_at_ts", "stop_placed_at_ts",
    "stop_confirmed_at_ts", "fill_reported_to_fly_at_ts",
)
EXIT_HOPS = (
    "paper_exit_at_ts", "exit_report_at_ts",
)
FINAL_SKIP_PREFIXES = ("instance-status", "relay-sim-active", "hire-expired", "relay-not-armed",
                       "intent-mirror-disabled", "credentials-missing")


def _f(v) -> float | None:
    try:
        x = float(v)
        return x if x == x and x > 0 else None
    except (TypeError, ValueError):
        return None


def worst(*vs: str) -> str:
    return max(vs or (GREEN,), key=lambda v: _RANK.get(v, 2))


def _bp(live: float | None, ref: float | None, side: str | None) -> float | None:
    if not live or not ref:
        return None
    s = str(side or "").upper()
    sign = -1.0 if s in ("SHORT", "SELL") else 1.0
    return round((live - ref) / ref * 1e4 * sign, 2)


def _iso_ts(v) -> float | None:
    if not v or not isinstance(v, str):
        return None
    try:
        import datetime as dt
        return dt.datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Arming
# ---------------------------------------------------------------------------
def effective_arming(*, output: Mapping[str, Any], force_paper_mode: bool, relay_stack_mode: str,
                     switch_rows: Iterable[Mapping[str, Any]], website: Mapping[str, Any],
                     fly_direct_force_paper: bool | None = None) -> dict:
    """One answer to "will a live intent become a Bitfinex order right now?"."""
    output_on = bool((output or {}).get("enabled"))
    tiles_on = [r.get("lane") for r in switch_rows or [] if r.get("bitfinex_live_orders")]
    web = website or {}
    accounts = [a for a in (web.get("accounts") or []) if isinstance(a, Mapping)]
    acct_rows = []
    for a in accounts:
        tr = a.get("last_transition") if isinstance(a.get("last_transition"), Mapping) else {}
        acct_rows.append({
            "account": a.get("account"),
            "instance_status": a.get("status"),
            "relay_mode": a.get("relay_mode"),
            "armed_at": a.get("armed_at"),
            "sim_active": a.get("sim_active"),
            "executor_status": a.get("executor_status"),
            "executor_healthy": a.get("executor_healthy"),
            # Why the Railway executor's last pass stopped (#554 heartbeat).
            "executor_last_pass_exit_stage": a.get("executor_last_pass_exit_stage"),
            "armed": bool(a.get("armed")),
            "paused": str(a.get("status") or "").upper() == "PAUSED",
            "paused_reason": (tr.get("reason") if str(a.get("status") or "").upper() == "PAUSED" else None),
            "last_change": {"action": tr.get("action"), "actor": tr.get("actor"),
                            "reason": tr.get("reason"), "at": tr.get("at")} if tr else None,
            "last_error": a.get("last_error"),
        })
    executor_ok = any(r["armed"] and r.get("executor_healthy") is not False for r in acct_rows)
    sources = {
        "fly_live_copy_output": output_on,
        "fly_force_paper_lock": bool(force_paper_mode),
        "fly_direct_force_paper": fly_direct_force_paper,
        "fly_tiles_live_on": tiles_on,
        "fly_relay_stack_mode": relay_stack_mode or None,
        "website_reachable": bool(web.get("reachable")),
        "website_armed_accounts": list(web.get("armed_accounts") or []),
        "website_accounts": acct_rows,
        "executor_healthy": executor_ok,
    }
    blockers = []
    if not output_on:
        blockers.append("FLY_OUTPUT_OFF")
    if force_paper_mode:
        blockers.append("FLY_FORCE_PAPER")
    if not tiles_on:
        blockers.append("NO_TILE_LIVE_ON")
    if not web.get("reachable"):
        blockers.append("WEBSITE_UNREACHABLE")
    elif not web.get("armed_accounts"):
        blockers.append("NO_ACCOUNT_ARMED")
    if web.get("reachable") and web.get("armed_accounts") and not executor_ok:
        blockers.append("EXECUTOR_UNHEALTHY")
    effective = not blockers
    mismatches = []
    expected_mismatches = []
    # The account's own row must agree with itself (status vs armed flag vs mode).
    for r in acct_rows:
        st = str(r.get("instance_status") or "").upper()
        mode = str(r.get("relay_mode") or "").upper()
        if r["armed"] and (st != "ACTIVE" or (mode and mode != "LIVE")):
            mismatches.append({"code": "ACCOUNT_ROW_INCONSISTENT", "account": r["account"],
                               "status": st, "relay_mode": mode, "armed": r["armed"]})
        if st == "PAUSED" and mode == "LIVE":
            mismatches.append({"code": "PAUSED_BUT_MODE_LIVE", "account": r["account"]})
        last = r.get("last_change") or {}
        if st == "ACTIVE" and str(last.get("action") or "").upper() in ("STOPPED", "SAFETY_PAUSED"):
            mismatches.append({"code": "ACTIVE_BUT_LAST_CHANGE_PAUSED", "account": r["account"],
                               "last_action": last.get("action")})
        if st == "PAUSED" and str(last.get("action") or "").upper() == "STARTED":
            row = {"code": "PAUSED_BUT_LAST_CHANGE_STARTED", "account": r["account"],
                   "last_error": r.get("last_error"), "paused_reason": r.get("paused_reason"),
                   "last_change": last}
            if _expected_paused_but_started(r, last):
                expected_mismatches.append({**row, "expected": True})
            else:
                mismatches.append(row)
    # The relay coordination Fly last heard must agree with the website rows.
    mode = str(relay_stack_mode or "").lower()
    if web.get("reachable"):
        any_active = any(str(r.get("instance_status") or "").upper() == "ACTIVE" and not r.get("sim_active")
                         for r in acct_rows)
        # "research_only" is Fly's normal RELAY_STACK_MODE (paper source; the
        # live-copy outbox delivers independently), not a stale coordination
        # reading, so only the coordination-pause modes count as a mismatch.
        if any_active and mode in ("source_research_only", "fully_paused"):
            mismatches.append({"code": "RELAY_MODE_STALE_VS_ACCOUNT", "fly_relay_stack_mode": relay_stack_mode,
                               "account_active": True})
    return {
        "effective_live": effective,
        "blockers": blockers,
        "sources": sources,
        "mismatches": mismatches,
        "expected_mismatches": expected_mismatches,  # arming_info; not money risk
        "verdict": RED if mismatches else GREEN,
    }


# ---------------------------------------------------------------------------
# Intent lifecycle
# ---------------------------------------------------------------------------
def _first(d: dict, key: str, value) -> None:
    v = _f(value)
    if v is not None and d.get(key) is None:
        d[key] = v


def build_intents(approvals: Iterable[Mapping[str, Any]], reports: Iterable[Mapping[str, Any]],
                  deliveries: Mapping[str, float] | None, paper_twins: Mapping[str, Mapping[str, Any]] | None,
                  *, now: float) -> list[dict]:
    """Per (intent, account) lifecycle with immutable first-seen hop timestamps."""
    intents: dict[str, dict] = {}
    for a in sorted(approvals or [], key=lambda x: _f(x.get("created_at_ts")) or 0):
        cid = str(a.get("correlation_id") or a.get("trade_id") or "")
        if not cid:
            continue
        it = intents.setdefault(cid, {
            "correlation_id": cid, "trade_id": a.get("trade_id") or cid, "lane": a.get("research_lane"),
            "entry_allowed": False, "fly_events": [], "accounts": {},
        })
        _first(it, "fly_signal_at_ts", a.get("signal_at_ts"))
        _first(it, "fly_intent_emitted_at_ts", a.get("created_at_ts"))
        ev = a.get("event")
        it["fly_events"].append(ev)
        if ev in ("ORDER_PLACED", "LIMIT_UPDATED") and a.get("entry_allowed"):
            it["entry_allowed"] = True
        if ev == "POSITION_CLOSED":
            _first(it, "fly_exit_intent_at_ts", a.get("created_at_ts"))
    for cid, it in intents.items():
        ts = (deliveries or {}).get(str(it.get("trade_id") or "")) or (deliveries or {}).get(cid)
        if ts is not None:
            _first(it, "fly_delivered_at_ts", ts)
    seen_reports: set[str] = set()
    for r in sorted(reports or [], key=lambda x: (_f(x.get("event_at_ts")) or _f(x.get("fly_received_at_ts")) or 0)):
        rid = str(r.get("report_id") or "")
        if rid and rid in seen_reports:
            continue
        seen_reports.add(rid)
        cid = str(r.get("correlation_id") or "")
        it = intents.setdefault(cid, {"correlation_id": cid, "trade_id": r.get("trade_id") or cid,
                                      "lane": r.get("lane"), "entry_allowed": False, "fly_events": [],
                                      "accounts": {}, "approval_missing_on_fly": True})
        acct = str(r.get("account") or "UNKNOWN")
        ch = it["accounts"].setdefault(acct, {
            "account": acct, "hops": {}, "decisions": [], "fills": [], "fill_ids": [],
            "stops": [], "errors": [], "exit": None, "report_types": [],
        })
        rtype = str(r.get("type") or "")
        ch["report_types"].append(rtype)
        tl = r.get("timeline") if isinstance(r.get("timeline"), Mapping) else {}
        for k in ("railway_received_at_ts", "order_sent_at_ts", "exchange_ack_at_ts", "fill_at_ts",
                  "stop_placed_at_ts", "stop_confirmed_at_ts"):
            _first(ch["hops"], k, tl.get(k))
        if rtype.startswith("INTENT_") and rtype != "INTENT_REJECTED":
            d = r.get("decision") if isinstance(r.get("decision"), Mapping) else {}
            row = {"decision": d.get("decision") or rtype.replace("INTENT_", ""),
                   "reason": d.get("reason"), "at_ts": _f(d.get("decided_at_ts")) or _f(r.get("event_at_ts"))}
            ch["decisions"].append(row)
            if row["decision"] == "RECEIVED":
                _first(ch["hops"], "executor_received_at_ts", row["at_ts"])
            else:
                _first(ch["hops"], "executor_decided_at_ts", row["at_ts"])
        elif rtype == "ORDER_FILLED":
            fill = r.get("fill") if isinstance(r.get("fill"), Mapping) else {}
            fid = str(fill.get("exchange_trade_id") or fill.get("trade_id") or rid)
            if fid not in ch["fill_ids"]:
                ch["fill_ids"].append(fid)
                ch["fills"].append({"price": _f(fill.get("price")), "qty": _f(fill.get("qty")),
                                    "exchange_trade_id": fid, "at_ts": _f(tl.get("fill_at_ts"))})
            _first(ch["hops"], "fill_reported_to_fly_at_ts", r.get("fly_received_at_ts"))
        elif rtype in ("ORDER_PLACED", "ORDER_SENT"):
            o = r.get("order") if isinstance(r.get("order"), Mapping) else {}
            ch.setdefault("order", {"type": o.get("type"), "side": o.get("side"), "qty": _f(o.get("qty")),
                                    "price": _f(o.get("price")), "exchange_order_id": o.get("exchange_order_id")})
        elif rtype in ("STOP_PLACED", "STOP_CONFIRMED"):
            s = r.get("stop") if isinstance(r.get("stop"), Mapping) else {}
            sid = s.get("exchange_order_id")
            existing = next((x for x in ch["stops"] if x.get("exchange_order_id") == sid), None)
            if existing is None:
                ch["stops"].append({"exchange_order_id": sid, "price": _f(s.get("price")),
                                    "confirmed": rtype == "STOP_CONFIRMED"})
            elif rtype == "STOP_CONFIRMED":
                existing["confirmed"] = True
                existing["price"] = existing.get("price") or _f(s.get("price"))
        elif rtype == "POSITION_CLOSED":
            c = r.get("close") if isinstance(r.get("close"), Mapping) else {}
            if ch["exit"] is None:
                ch["exit"] = {"fill_price": _f(c.get("price")), "reason": c.get("reason"),
                              "paper_exit_price": _f(c.get("paper_exit_price")),
                              "order_id": c.get("exchange_order_id"),
                              "fill_ts": _f(r.get("event_at_ts")),
                              "reported_at_ts": _f(r.get("fly_received_at_ts"))}
        elif rtype in ("ORDER_REJECTED", "INTENT_REJECTED", "STOP_FAILED", "ERROR", "ORDER_CANCELLED"):
            e = r.get("error") if isinstance(r.get("error"), Mapping) else {}
            ch["errors"].append({"type": rtype, "code": e.get("code"),
                                 "message": str(e.get("message") or "")[:200], "at_ts": _f(r.get("event_at_ts"))})
    out = []
    for cid, it in intents.items():
        twin = (paper_twins or {}).get(str(it.get("trade_id"))) or (paper_twins or {}).get(cid) or {}
        emitted = it.get("fly_intent_emitted_at_ts")
        it["age_sec"] = round(now - emitted, 1) if emitted else None
        it["expired"] = bool(emitted and now - emitted > GAP_TTL_SEC)
        it["paper"] = {k: twin.get(k) for k in ("side", "entry_price", "qty", "exit_price", "exit_ts", "exit_reason")} if twin else None
        accts = []
        for ch in it["accounts"].values():
            hops = dict(ch["hops"])
            for k in ("fly_signal_at_ts", "fly_intent_emitted_at_ts", "fly_delivered_at_ts"):
                if it.get(k) is not None:
                    hops[k] = it[k]
            ordered = [(k, hops.get(k)) for k in ENTRY_HOPS]
            lags = {}
            prev = None
            for k, v in ordered:
                if v is None:
                    continue
                if prev is not None:
                    lags[f"{prev[0].replace('_at_ts', '')}->{k.replace('_at_ts', '')}"] = round(v - prev[1], 3)
                prev = (k, v)
            # Fly and Railway clocks differ by up to ~1 s and executor decision
            # times are recorded asynchronously; only a clearly reversed hop
            # (> 5 s) is an ordering fault.
            order_violations = [name for name, lag in lags.items()
                                if lag < -5.0 and "executor_" not in name]
            qty = sum(f["qty"] or 0 for f in ch["fills"]) or None
            vwap = (sum((f["price"] or 0) * (f["qty"] or 0) for f in ch["fills"]) / qty) if qty else (
                ch["fills"][0]["price"] if ch["fills"] else None)
            side = (ch.get("order") or {}).get("side") or (twin.get("side") if twin else None)
            paper_entry = _f(twin.get("entry_price")) if twin else None
            paper_exit = _f(twin.get("exit_price")) if twin else None
            paper_exit_ts = _f(twin.get("exit_ts")) or _iso_ts(twin.get("exit_ts")) if twin else None
            ex = ch["exit"] or {}
            drift = {
                "entry_price_bp": _bp(vwap, paper_entry, side),
                "entry_time_s": round(hops["fill_at_ts"] - emitted, 3) if hops.get("fill_at_ts") and emitted else None,
                "qty_fraction": round((qty - _f(twin.get("qty"))) / _f(twin.get("qty")), 5)
                if qty and twin and _f(twin.get("qty")) else None,
                "exit_price_bp": _bp(ex.get("fill_price"), paper_exit or ex.get("paper_exit_price"), side),
                "exit_time_s": round(ex["fill_ts"] - paper_exit_ts, 3) if ex.get("fill_ts") and paper_exit_ts else None,
                # SHOWCASE_MIRROR = the live copy closed BECAUSE its paper twin
                # closed (whatever the paper reason), so it is a match by design.
                "exit_reason_match": (str(ex.get("reason") or "").upper() in ("SHOWCASE_MIRROR", str(twin.get("exit_reason") or "").upper()))
                if ex.get("reason") and twin and twin.get("exit_reason") else None,
            }
            if vwap and ex.get("fill_price"):
                drift["pnl_bp_live"] = _bp(ex["fill_price"], vwap, side)
            if paper_entry and paper_exit:
                drift["pnl_bp_paper"] = _bp(paper_exit, paper_entry, side)
            last_decision = ch["decisions"][-1] if ch["decisions"] else None
            accts.append({
                "account": ch["account"], "hops": {k: v for k, v in ordered}, "lags_sec": lags,
                "hop_order_violations": order_violations,
                "decision": last_decision, "decisions": ch["decisions"][-10:],
                "order": ch.get("order"), "fills": ch["fills"], "fill_vwap": vwap, "fill_qty": qty,
                "stops": ch["stops"], "stop_count": len(ch["stops"]),
                "exit": ({**ex, "trigger_ts": paper_exit_ts} if ch["exit"] else None),
                "drift": drift, "errors": ch["errors"][-10:],
                "open": bool(ch["fills"]) and ch["exit"] is None,
            })
        it["accounts"] = accts
        out.append(it)
    out.sort(key=lambda x: x.get("fly_intent_emitted_at_ts") or 0, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Exchange mirror, clock and alerts
# ---------------------------------------------------------------------------
def exchange_view(website: Mapping[str, Any]) -> dict:
    out = {}
    for a in (website or {}).get("accounts") or []:
        if not isinstance(a, Mapping):
            continue
        m = a.get("exchange_mirror") if isinstance(a.get("exchange_mirror"), Mapping) else None
        out[str(a.get("account"))] = m if m is not None else {"ok": False, "error": "NO_MIRROR_YET", "read_age_s": None}
    return out


def clock_view(website: Mapping[str, Any], now: float) -> dict:
    web = website or {}
    server = _f(web.get("server_ts"))
    fetched = _f(web.get("fetched_at_ts"))
    offset_ms = round((server - fetched) * 1000.0, 1) if server and fetched else None
    return {"fly_now_ts": now, "website_server_ts": server, "fly_fetch_ts": fetched,
            "fly_to_website_offset_ms": offset_ms,
            "note": "offset includes one-way request latency (<= a few hundred ms)"}


def _ts(v) -> float | None:
    return _f(v) if not isinstance(v, str) else (_iso_ts(v) or _f(v))


def arming_timeline(website: Mapping[str, Any] | None,
                    transitions: Iterable[Mapping[str, Any]] | None) -> dict[str, list[tuple[float, bool, str]]]:
    """Per account, time-sorted (ts, armed, action) arm/pause events.

    Sources: Fly's persisted log of transitions it observed on the website
    (``account_transitions``) plus the current website row (last_transition
    and armed_at). Unknown actions are ignored, so an unknown state is never
    read as "paused"."""
    ev: dict[str, set] = {}

    def put(acct, at, action):
        act = str(action or "").upper()
        t = _ts(at)
        if not acct or t is None or act not in ARMED_ACTIONS | UNARMED_ACTIONS:
            return
        ev.setdefault(str(acct), set()).add((t, act in ARMED_ACTIONS, act))

    for r in transitions or []:
        if isinstance(r, Mapping):
            put(r.get("account"), r.get("at_ts") or r.get("at"), r.get("action"))
    for a in (website or {}).get("accounts") or []:
        if not isinstance(a, Mapping):
            continue
        tr = a.get("last_transition") if isinstance(a.get("last_transition"), Mapping) else {}
        put(a.get("account"), tr.get("at"), tr.get("action"))
        if a.get("armed") and a.get("armed_at"):
            put(a.get("account"), a.get("armed_at"), "ARMED")
    return {k: sorted(v) for k, v in ev.items()}


def armed_at_time(timeline: Mapping[str, list], account: str, ts: float | None) -> bool | None:
    """Was ``account`` armed at ``ts``? None = no evidence either way."""
    if ts is None:
        return None
    state = None
    for t, armed, _act in timeline.get(str(account)) or []:
        if t > ts:
            break
        state = armed
    return state


def paper_terminal(it: Mapping[str, Any]) -> bool:
    """The intent's paper side is finished: twin closed, or Fly emitted a
    terminal event (closed / limit expired / cancelled)."""
    paper = it.get("paper") or {}
    if paper and (_f(paper.get("exit_ts")) or _iso_ts(paper.get("exit_ts"))):
        return True
    return bool(PAPER_TERMINAL_EVENTS & set(it.get("fly_events") or []))


def open_copies_by_account(intents: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in intents or []:
        if it.get("expired"):
            continue
        for a in it.get("accounts") or []:
            if a.get("open"):
                out[a["account"]] = out.get(a["account"], 0) + 1
    return out


def exchange_accounted(m: Mapping[str, Any] | None, open_copies: int) -> bool:
    """Fresh exchange read with no orphans, and flat or every position matched
    by an open tracked live copy (so a missed intent owns no exposure)."""
    if not m or not m.get("ok") or m.get("orphans"):
        return False
    age = m.get("read_age_s")
    if age is None or age > EXCHANGE_READ_MAX_AGE_SEC:
        return False
    if m.get("flat") is True:
        return True
    pos = m.get("positions")
    return isinstance(pos, list) and len(pos) <= int(open_copies or 0)


class MissedCopyClassifier:
    """LIVE-FIX-1816: classify a missed-copy alert for (intent, account)."""

    def __init__(self, *, intents: Iterable[Mapping[str, Any]], exchange: Mapping[str, Mapping[str, Any]],
                 timeline: Mapping[str, list], armed_accounts: Iterable[str]):
        self.by_cid = {str(i.get("correlation_id")): i for i in intents or []}
        for i in intents or []:
            self.by_cid.setdefault(str(i.get("trade_id")), i)
        self.exchange = exchange or {}
        self.timeline = timeline or {}
        self.armed_accounts = sorted(set(armed_accounts or []))
        self.open_copies = open_copies_by_account(intents)

    def classify(self, code: str, correlation_id: str | None, account: str | None) -> dict:
        if code not in MISSED_COPY_CODES:
            return {}
        it = self.by_cid.get(str(correlation_id or ""))
        if it is None:
            return {"alert_class": MONEY_RISK}
        emitted = it.get("fly_intent_emitted_at_ts")
        accts = [account] if account else (self.armed_accounts or sorted(self.timeline))
        states = [armed_at_time(self.timeline, a, emitted) for a in accts]
        info = {"armed_at_emit": (states[0] if account else (None if not states else
                                  (False if all(s is False for s in states) else
                                   (True if any(s for s in states) else None))))}
        if states and all(s is False for s in states):
            return {"alert_class": PAUSED_WINDOW, "severity": INFO, **info}
        closed = paper_terminal(it)
        ex_accts = accts or sorted(self.exchange)
        accounted = bool(ex_accts) and all(
            exchange_accounted(self.exchange.get(a), self.open_copies.get(a, 0)) for a in ex_accts)
        flat = bool(ex_accts) and all((self.exchange.get(a) or {}).get("flat") is True for a in ex_accts)
        info.update(paper_closed=closed, exchange_flat=flat, exchange_accounted=accounted)
        if closed and accounted:
            return {"alert_class": HISTORICAL_MISSED_COPY, "severity": AMBER, **info}
        return {"alert_class": MONEY_RISK, **info}

    def apply(self, alert: dict) -> dict:
        c = self.classify(alert.get("code"), alert.get("correlation_id"), alert.get("account"))
        if c:
            alert.update(c)
        return alert


def alerts(*, arming: Mapping[str, Any], intents: list[dict], exchange: Mapping[str, Mapping[str, Any]],
           clock: Mapping[str, Any], now: float, classifier: MissedCopyClassifier | None = None) -> list[dict]:
    out: list[dict] = []

    def add(code, sev, **kw):
        a = {"code": code, "severity": sev, **kw}
        out.append(classifier.apply(a) if classifier is not None else a)

    src = arming.get("sources") or {}
    output_on = bool(src.get("fly_live_copy_output"))
    tiles_on = bool(src.get("fly_tiles_live_on"))
    for m in arming.get("mismatches") or []:
        add("ARMING_SOURCES_MISMATCH", RED, detail=m)
    # LIVE-FIX-1928: deploy/re-arm races stay visible as INFO expected skips.
    for m in arming.get("expected_mismatches") or []:
        add("ARMING_EXPECTED_MISMATCH", INFO, detail=m, alert_class=PAUSED_WINDOW)
    for r in src.get("website_accounts") or []:
        st = str(r.get("instance_status") or "").upper()
        if output_on and tiles_on and st == "PAUSED":
            add("ACCOUNT_PAUSED_WHILE_OUTPUT_ON", RED, account=r.get("account"),
                paused_reason=r.get("paused_reason"), last_change=r.get("last_change"),
                last_error=r.get("last_error"))
        elif output_on and tiles_on and st and st != "ACTIVE":
            add("ACCOUNT_DISARMED_WHILE_OUTPUT_ON", RED, account=r.get("account"), status=st)
        if r.get("armed") and r.get("executor_healthy") is False:
            add("EXECUTOR_UNHEALTHY", RED, account=r.get("account"), status=r.get("executor_status"))
    armed_accounts = set(src.get("website_armed_accounts") or [])
    for it in intents:
        if it.get("expired") or not it.get("entry_allowed"):
            continue
        age = it.get("age_sec")
        accts = {a["account"]: a for a in it.get("accounts") or []}
        if age is not None and age > INTENT_UNDECIDED_SEC and not it.get("fly_delivered_at_ts") and not accts:
            add("INTENT_UNDELIVERED", AMBER, correlation_id=it["correlation_id"], age_sec=age)
        for acct in armed_accounts:
            a = accts.get(acct)
            decided = a and a.get("decision") and a["decision"].get("decision") in ("SKIPPED", "SUBMITTED")
            if age is not None and age > INTENT_UNDECIDED_SEC and not decided and not (a and a.get("order")):
                add("INTENT_NO_EXECUTOR_DECISION", RED, correlation_id=it["correlation_id"], account=acct, age_sec=age)
            if age is not None and age > NO_ORDER_FOR_INTENT_SEC and not (a and (a.get("order") or a.get("fills"))):
                reason = (a or {}).get("decision", {}) or {}
                final = str(reason.get("reason") or "").startswith(FINAL_SKIP_PREFIXES)
                add("NO_ORDER_FOR_INTENT", AMBER if final else RED, correlation_id=it["correlation_id"],
                    account=acct, age_sec=age, executor_reason=reason.get("reason"))
    for it in intents:
        if it.get("expired"):
            continue
        paper = it.get("paper") or {}
        for a in it.get("accounts") or []:
            hops = a.get("hops") or {}
            fill = hops.get("fill_at_ts")
            confirmed = any(s.get("confirmed") for s in a.get("stops") or [])
            if fill and not confirmed and a.get("exit") is None and now - fill > FILL_WITHOUT_STOP_SEC:
                add("FILL_WITHOUT_STOP", RED, correlation_id=it["correlation_id"], account=a["account"],
                    unprotected_sec=round(now - fill, 1))
            if a.get("open") and len([s for s in a.get("stops") or [] if s.get("exchange_order_id")]) > 1:
                add("DUPLICATE_STOPS", RED, correlation_id=it["correlation_id"], account=a["account"],
                    stops=[s.get("exchange_order_id") for s in a["stops"]])
            if a.get("open") and not paper:
                add("LIVE_POSITION_WITHOUT_PAPER_TWIN", AMBER, correlation_id=it["correlation_id"], account=a["account"])
            pexit = _f(paper.get("exit_ts")) or _iso_ts(paper.get("exit_ts"))
            if a.get("open") and pexit and now - pexit > PAPER_CLOSED_LIVE_OPEN_SEC:
                add("PAPER_CLOSED_LIVE_OPEN", RED, correlation_id=it["correlation_id"], account=a["account"],
                    since_paper_close_sec=round(now - pexit, 1))
            d = a.get("drift") or {}
            closed = not a.get("open")
            if d.get("exit_price_bp") is not None and abs(d["exit_price_bp"]) > EXIT_DRIFT_BP:
                big = abs(d["exit_price_bp"]) >= EXIT_DRIFT_MONEY_RISK_BP
                add("EXIT_DRIFT_BP", RED if (big or not closed) else AMBER, correlation_id=it["correlation_id"],
                    account=a["account"], bp=d["exit_price_bp"], closed=closed,
                    alert_class=HISTORICAL_PARITY if (closed and not big) else MONEY_RISK)
            if d.get("entry_price_bp") is not None and abs(d["entry_price_bp"]) > ENTRY_DRIFT_RED_BP:
                add("ENTRY_DRIFT_BP", RED, correlation_id=it["correlation_id"], account=a["account"], bp=d["entry_price_bp"])
            if d.get("exit_reason_match") is False:
                add("EXIT_REASON_MISMATCH", AMBER if closed else RED, correlation_id=it["correlation_id"],
                    account=a["account"], closed=closed,
                    alert_class=HISTORICAL_PARITY if closed else MONEY_RISK)
            if a.get("hop_order_violations"):
                add("HOP_ORDER_INCONSISTENT", AMBER, correlation_id=it["correlation_id"], account=a["account"],
                    hops=a["hop_order_violations"])
    for acct, m in (exchange or {}).items():
        armed = acct in armed_accounts
        age = m.get("read_age_s")
        if armed and (age is None or age > EXCHANGE_READ_MAX_AGE_SEC):
            add("EXCHANGE_READ_STALE", RED, account=acct, read_age_s=age, error=m.get("error"))
        if m.get("ok") and m.get("orphans"):
            add("ORPHAN_ORDER_WHEN_FLAT", RED, account=acct, orphans=m.get("orphans"))
        for p in m.get("positions") or []:
            if int(p.get("stops") or 0) > 1:
                add("DUPLICATE_STOPS", RED, account=acct, stops=int(p.get("stops")))
            if int(p.get("stops") or 0) == 0:
                add("POSITION_WITHOUT_EXCHANGE_STOP", RED, account=acct)
    off = clock.get("fly_to_website_offset_ms")
    if off is not None and abs(off) > CLOCK_OFFSET_AMBER_MS:
        add("CLOCK_OFFSET_HIGH", AMBER, offset_ms=off)
    return out


def alert_class(a: Mapping[str, Any]) -> str:
    """LIVE-FIX-1715: money_risk unless the alert is closed-trade parity history.
    LIVE-FIX-1816: or a historical missed copy / paused-window expected skip."""
    if a.get("alert_class") in ALERT_CLASSES:
        if a["alert_class"] != MONEY_RISK and a.get("severity") == RED:
            return MONEY_RISK  # a RED alert is never demoted by its tag
        return a["alert_class"]
    if a.get("code") in HISTORICAL_PARITY_CODES and a.get("closed") and a.get("severity") != RED:
        return HISTORICAL_PARITY
    return MONEY_RISK


def split_verdicts(al: Iterable[Mapping[str, Any]]) -> dict:
    """Top-level verdict = money_risk_verdict (open exposure / live capability).
    Closed-trade parity alerts stay listed but only drive parity_verdict."""
    money, parity, missed = GREEN, GREEN, GREEN
    money_codes, parity_codes, missed_codes, skip_codes = set(), set(), set(), set()
    missed_keys, skip_keys = set(), set()
    for a in al or []:
        cls = alert_class(a)
        if cls == HISTORICAL_PARITY:
            parity = worst(parity, a.get("severity") or AMBER)
            parity_codes.add(a["code"])
        elif cls == HISTORICAL_MISSED_COPY:
            missed = worst(missed, a.get("severity") or AMBER)
            missed_codes.add(a["code"])
            missed_keys.add((a.get("correlation_id"), a.get("account")))
        elif cls == PAUSED_WINDOW:
            skip_codes.add(a["code"])
            skip_keys.add((a.get("correlation_id"), a.get("account")))
        else:
            money = worst(money, a.get("severity") or AMBER)
            money_codes.add(a["code"])
    return {"money_risk_verdict": money, "parity_verdict": parity,
            "money_risk_codes": sorted(money_codes), "parity_codes": sorted(parity_codes),
            "missed_copy_verdict": missed, "missed_copy_codes": sorted(missed_codes),
            "missed_copy_count": len({k[0] for k in missed_keys}),
            "expected_skip_codes": sorted(skip_codes), "expected_skip_count": len({k[0] for k in skip_keys})}


def missed_copy_buckets(al: Iterable[Mapping[str, Any]]) -> dict:
    """Group historical missed copies / paused-window skips per intent+account."""
    out: dict[str, dict] = {HISTORICAL_MISSED_COPY: {}, PAUSED_WINDOW: {}}
    for a in al or []:
        cls = alert_class(a)
        if cls not in out:
            continue
        key = f"{a.get('correlation_id')}|{a.get('account') or ''}"
        row = out[cls].setdefault(key, {"correlation_id": a.get("correlation_id"), "account": a.get("account"),
                                        "codes": [], "age_sec": a.get("age_sec"),
                                        "executor_reason": a.get("executor_reason"),
                                        "armed_at_emit": a.get("armed_at_emit"),
                                        "paper_closed": a.get("paper_closed"),
                                        "exchange_flat": a.get("exchange_flat"),
                                        "exchange_accounted": a.get("exchange_accounted")})
        if a["code"] not in row["codes"]:
            row["codes"].append(a["code"])
        if row.get("executor_reason") is None and a.get("executor_reason"):
            row["executor_reason"] = a.get("executor_reason")
        if row.get("account") is None and a.get("account"):
            row["account"] = a.get("account")
    return {"missed_copies": list(out[HISTORICAL_MISSED_COPY].values())[:50],
            "expected_skips": list(out[PAUSED_WINDOW].values())[:50]}


def build(ctx: Mapping[str, Any], *, now: float) -> dict:
    return build_with_classifier(ctx, now=now)[0]


def build_with_classifier(ctx: Mapping[str, Any], *, now: float) -> tuple[dict, MissedCopyClassifier]:
    web = ctx.get("website") or {}
    arming = effective_arming(output=ctx.get("output") or {}, force_paper_mode=bool(ctx.get("force_paper_mode")),
                              relay_stack_mode=str(ctx.get("relay_stack_mode") or ""),
                              switch_rows=ctx.get("switch_rows") or [], website=web,
                              fly_direct_force_paper=ctx.get("fly_direct_force_paper_mode"))
    intents = build_intents(ctx.get("approvals") or [], ctx.get("reports") or [], ctx.get("deliveries") or {},
                            ctx.get("paper_twins") or {}, now=now)
    exchange = exchange_view(web)
    clock = clock_view(web, now)
    classifier = MissedCopyClassifier(
        intents=intents, exchange=exchange,
        timeline=arming_timeline(web, ctx.get("account_transitions") or []),
        armed_accounts=(arming.get("sources") or {}).get("website_armed_accounts") or [])
    al = alerts(arming=arming, intents=intents, exchange=exchange, clock=clock, now=now, classifier=classifier)
    # LIVE-FIX-0702: executor silence (no report and no capability heartbeat
    # for 5 min) while output is ON is RED.
    try:
        import live_copy_monitor_api as _lcma
        fr = _lcma.executor_freshness(ctx, now)
        if fr.get("stale") and (arming.get("sources") or {}).get("fly_live_copy_output"):
            al.append({"code": "EXECUTOR_REPORT_STALE", "severity": RED,
                       "age_s": fr.get("newest_executor_signal_age_s")})
    except Exception:  # noqa: BLE001 - freshness is advisory
        pass
    # LIVE-FIX-0500: operator-resolved correlation ids (root-caused + fixed)
    # drop out of the live alert set; they stay visible under resolved_alerts.
    resolved = ctx.get("resolved_alerts") or {}
    resolved_al = [dict(a, resolution=resolved[a["correlation_id"]]) for a in al
                   if a.get("correlation_id") and a["correlation_id"] in resolved]
    al = [a for a in al if not (a.get("correlation_id") and a["correlation_id"] in resolved)]
    split = split_verdicts(al)
    live = [i for i in intents if not i.get("expired")]
    try:
        retention = retention_facts(ctx.get("approvals") or [], ctx.get("reports") or [],
                                    boot_ts=ctx.get("process_started_ts"), now=now)
    except Exception:  # noqa: BLE001
        retention = {}
    unl = unlinked(intents, now=now)
    return {
        **retention, **unl,
        "schema": SCHEMA, "verdict": split["money_risk_verdict"], **split, **missed_copy_buckets(al),
        "arming": arming, "alerts": al, "resolved_alerts": resolved_al,
        "alert_codes": sorted({a["code"] for a in al}), "exchange": exchange, "clock": clock,
        "intents": live[:100], "intents_expired": len(intents) - len(live), "gap_ttl_sec": GAP_TTL_SEC,
        "hops": list(ENTRY_HOPS), "computed_at_ts": now,
    }, classifier


_RELAY_LIVE_TYPES = frozenset({"ORDER_PLACED", "ORDER_FILLED", "STOP_PLACED", "STOP_CONFIRMED"})


def relay_copy_managed_ids(by_correlation, exchange_positions, size_fn):
    """LIVE-FIX-0500: link Bitfinex exposure placed by the Railway relay to its
    open copied trade so Fly's exposure audit stops calling it an orphan.

    LIVE-FIX-1415: a copy stays managed from the relay's first live report
    (ORDER_PLACED / ORDER_FILLED / STOP_*) until the relay reports
    POSITION_CLOSED, which it only sends after Bitfinex confirmed the close
    (TERMINAL_CLOSE_CONFIRMED -> EXIT). Previously a copy whose ORDER_FILLED
    report lagged (reporter 503s) was dropped and the live position was
    flagged an orphan (dsl-1791772232-3, 12 Oct 13:31). Qty falls back to the
    order / stop qty when the fill report has not arrived yet.
    Returns (order_ids:set[str], position_ids:set[str], links:dict)."""
    order_ids, position_ids, links = set(), set(), {}
    open_qty, sides = 0.0, set()
    per_cid = []
    for cid, rows in (by_correlation or {}).items():
        types = {str(r.get("type")) for r in rows or []}
        if not (types & _RELAY_LIVE_TYPES) or "POSITION_CLOSED" in types:
            continue
        ids, fill_qty, fallback_qty = [], 0.0, 0.0
        cid_sides = set()
        for r in rows:
            for key in ("order", "stop"):
                o = r.get(key) if isinstance(r.get(key), Mapping) else {}
                if o.get("exchange_order_id"):
                    ids.append(str(o.get("exchange_order_id")))
                q = _f(o.get("qty")) or 0.0
                if q and not fallback_qty:
                    fallback_qty = abs(q)
                side = str(o.get("side") or "").upper()
                if key == "order" and side in ("LONG", "SHORT"):
                    sides.add(side)
                    cid_sides.add(side)
            if str(r.get("type")) == "ORDER_FILLED":
                f = r.get("fill") if isinstance(r.get("fill"), Mapping) else {}
                fill_qty += abs(_f(f.get("qty")) or 0.0)
                side = str(f.get("side") or "").upper()
                if side in ("LONG", "SHORT"):
                    sides.add(side)
                    cid_sides.add(side)
        open_qty += fill_qty or fallback_qty
        order_ids.update(ids)
        links[cid] = {"order_ids": sorted(set(ids)), "filled_reported": bool(fill_qty)}
        per_cid.append((cid, fill_qty or fallback_qty, set(cid_sides)))
    if len(exchange_positions or []) == 1 and open_qty > 0:
        p = exchange_positions[0]
        size = size_fn(p)
        pid = str((p or {}).get("id") or "") if isinstance(p, Mapping) else ""
        side = "LONG" if (size or 0) > 0 else "SHORT"
        if pid and size is not None and abs(abs(size) - open_qty) <= max(1e-8, open_qty * 0.05) \
                and (not sides or sides == {side}):
            position_ids.add(pid)
            for v in links.values():
                v["position_id"] = pid
        elif pid and size is not None:
            # LIVE-FIX-1450: a stale unclosed copy (e.g. flattened by a deploy
            # before its POSITION_CLOSED report) inflated the sum and made
            # GS-01's own live position an orphan (194465029, 12 Oct 15:30).
            # Link to the single open copy whose qty and side match exactly.
            hits = [c for c, q, sd in per_cid
                    if q > 0 and abs(abs(size) - q) <= max(1e-8, q * 0.05) and (not sd or sd == {side})]
            if len(hits) == 1:
                position_ids.add(pid)
                links[hits[0]]["position_id"] = pid
    return order_ids, position_ids, links


PAPER_CLOSED_LIVE_OPEN_ALERT_SEC = 5.0
PAPER_CLOSED_LIVE_OPEN_AUTOCLOSE_SEC = 15.0
PAPER_CLOSED_LIVE_OPEN_LOOKBACK_SEC = 900.0


def paper_closed_live_open(paper_closed_ts, by_correlation, exchange_open, now,
                           alert_sec=PAPER_CLOSED_LIVE_OPEN_ALERT_SEC,
                           autoclose_sec=PAPER_CLOSED_LIVE_OPEN_AUTOCLOSE_SEC):
    """LIVE-FIX-1415: live copies whose paper trade closed > alert_sec ago but
    the relay has not reported POSITION_CLOSED while Bitfinex still shows
    exposure. Pure. Returns a list of {trade_id, age_s, autoclose} rows."""
    if not exchange_open:
        return []
    out = []
    for tid, ts in (paper_closed_ts or {}).items():
        try:
            age = float(now) - float(ts)
        except (TypeError, ValueError):
            continue
        if age <= alert_sec or age > PAPER_CLOSED_LIVE_OPEN_LOOKBACK_SEC:
            continue
        types = {str(r.get("type")) for r in (by_correlation or {}).get(tid) or []}
        if not (types & _RELAY_LIVE_TYPES) or "POSITION_CLOSED" in types:
            continue
        out.append({"trade_id": tid, "age_s": round(age, 1), "autoclose": age > autoclose_sec,
                    "alert": "PAPER_CLOSED_LIVE_OPEN"})
    return sorted(out, key=lambda r: -r["age_s"])


def retention_facts(approvals, reports, *, boot_ts: float | None, now: float) -> dict:
    """G6: how far back the lifecycle can see, and what survived the last restart."""
    ats = [_f(a.get("created_at_ts")) for a in approvals or []]
    rts = [_f(r.get("fly_received_at_ts")) for r in reports or []]
    ats = [t for t in ats if t]
    rts = [t for t in rts if t]
    since = min(ats) if ats else None
    pre = sorted({str(a.get("correlation_id") or a.get("trade_id") or "") for a in approvals or []
                  if boot_ts and (_f(a.get("created_at_ts")) or 0) < boot_ts} - {""})
    return {"retained_since_ts": since,
            "retained_days": round((now - since) / 86400.0, 2) if since else None,
            "reports_since_ts": min(rts) if rts else None,
            "process_boot_ts": boot_ts, "pre_restart_intents": len(pre)}


def unlinked(intents, *, now: float, window_sec: float = 86400.0) -> dict:
    """G8: intents built only from executor reports (no Fly approval)."""
    rows = []
    for it in intents or []:
        if not it.get("approval_missing_on_fly"):
            continue
        ts = [a.get("hops", {}).get(k) for a in it.get("accounts") or []
              for k in ("railway_received_at_ts", "executor_received_at_ts", "fill_at_ts")]
        ts = [t for t in ts if t]
        rows.append({"correlation_id": it.get("correlation_id"), "lane": it.get("lane"),
                     "first_seen_ts": min(ts) if ts else None})
    new = [r for r in rows if r["first_seen_ts"] and r["first_seen_ts"] >= now - window_sec]
    return {"unlinked_count": len(rows), "unlinked_new_24h": len(new),
            "unlinked_ids": [r["correlation_id"] for r in rows][:50]}
