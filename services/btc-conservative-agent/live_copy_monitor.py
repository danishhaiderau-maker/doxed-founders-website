"""Read-only live-copy monitoring (R1-R13 + R19, copy chain, gap detector).

Pure analysis over: Fly's emitted signed approvals, the executor's verified
execution reports (``live_copy_control.ExecutionJournal``), the paper twin
rows and the switch state. Exposed by ``live_copy_monitor_api`` as GET routes
that never mutate anything, never call Bitfinex and never return secrets.
Verdicts are Health-Monitor friendly: ``{"verdict": GREEN|AMBER|RED,
"causes": [codes], ...}``. An empty book while output is OFF is GREEN/IDLE,
never a fabricated "OK" while ON.
"""
from __future__ import annotations

import statistics
import time
from typing import Any, Iterable, Mapping

SCHEMA = "fly_live_copy_monitor_v1"

# Thresholds (seconds / bp). Owner may tighten after the rehearsal.
INTENT_TO_ORDER_GAP_SEC = 10.0
FILL_TO_STOP_GAP_SEC = 10.0
REPORT_MISSING_GAP_SEC = 30.0
LAG_AMBER_SEC = 3.0
LAG_RED_SEC = 10.0
PRICE_DRIFT_AMBER_BP = 5.0
PRICE_DRIFT_RED_BP = 15.0
SIZE_DRIFT_RED_FRACTION = 0.01

# Ordered chain hops: (name, from_stage, to_stage)
STAGES = (
    "fly_signal_at_ts", "fly_intent_emitted_at_ts", "railway_received_at_ts",
    "order_sent_at_ts", "exchange_ack_at_ts", "fill_at_ts",
    "stop_placed_at_ts", "stop_confirmed_at_ts", "fill_reported_to_fly_at_ts",
)
HOPS = tuple(zip(STAGES[:-1], STAGES[1:]))

GREEN, AMBER, RED = "GREEN", "AMBER", "RED"
_RANK = {GREEN: 0, AMBER: 1, RED: 2}


def _f(v) -> float | None:
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def _pct(values: list[float], q: float) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 3)
    k = (len(vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 3)


def worst(*verdicts: str) -> str:
    return max(verdicts or (GREEN,), key=lambda v: _RANK.get(v, 2))


def build_chains(approvals: Iterable[Mapping[str, Any]], reports: Iterable[Mapping[str, Any]]) -> dict:
    """Join emitted approvals and executor reports per (correlation id, account)."""
    intents: dict[str, dict] = {}
    for a in approvals or []:
        cid = str(a.get("correlation_id") or a.get("trade_id") or "")
        if not cid:
            continue
        row = intents.setdefault(cid, {
            "correlation_id": cid, "lane": a.get("research_lane"),
            "fly_signal_at_ts": _f(a.get("signal_at_ts")),
            "fly_intent_emitted_at_ts": _f(a.get("created_at_ts")),
            "entry_allowed": bool(a.get("entry_allowed")), "events": [],
        })
        row["events"].append(a.get("event"))
        if a.get("event") in ("ORDER_PLACED", "LIMIT_UPDATED") and a.get("entry_allowed"):
            row["entry_allowed"] = True
    accounts: dict[tuple[str, str], dict] = {}
    for r in reports or []:
        cid = str(r.get("correlation_id") or "")
        acct = str(r.get("account") or "UNKNOWN")
        key = (cid, acct)
        ch = accounts.setdefault(key, {"correlation_id": cid, "account": acct,
                                       "lane": r.get("lane"), "reports": [], "errors": []})
        ch["reports"].append(r.get("type"))
        tl = r.get("timeline") if isinstance(r.get("timeline"), Mapping) else {}
        for stage in STAGES[2:]:
            v = _f(tl.get(stage))
            if v is not None and ch.get(stage) is None:
                ch[stage] = v
        if r.get("type") in ("ORDER_FILLED",):
            ch["fill_reported_to_fly_at_ts"] = ch.get("fill_reported_to_fly_at_ts") or _f(r.get("fly_received_at_ts"))
            fill = r.get("fill") if isinstance(r.get("fill"), Mapping) else {}
            ch["fill_price"] = _f(fill.get("price")) or ch.get("fill_price")
            ch["fill_qty"] = _f(fill.get("qty")) or ch.get("fill_qty")
        if r.get("type") in ("ORDER_PLACED", "ORDER_SENT"):
            order = r.get("order") if isinstance(r.get("order"), Mapping) else {}
            ch["order_qty"] = _f(order.get("qty")) or ch.get("order_qty")
            ch["order_price"] = _f(order.get("price")) or ch.get("order_price")
            ch["order_type"] = order.get("type") or ch.get("order_type")
        if r.get("type") == "STOP_CONFIRMED":
            ch["stop_confirmed"] = True
        if r.get("type") == "POSITION_CLOSED":
            ch["closed"] = True
        if r.get("type") in ("ORDER_REJECTED", "INTENT_REJECTED", "STOP_FAILED", "ERROR"):
            err = r.get("error") if isinstance(r.get("error"), Mapping) else {}
            ch["errors"].append({"type": r.get("type"), "code": err.get("code"),
                                 "message": str(err.get("message") or "")[:200],
                                 "at_ts": _f(r.get("sent_at_ts"))})
    for ch in accounts.values():
        intent = intents.get(ch["correlation_id"]) or {}
        ch["fly_signal_at_ts"] = intent.get("fly_signal_at_ts")
        ch["fly_intent_emitted_at_ts"] = intent.get("fly_intent_emitted_at_ts")
        lags = {}
        for a, b in HOPS:
            if ch.get(a) is not None and ch.get(b) is not None:
                lags[f"{a.replace('_at_ts', '')}->{b.replace('_at_ts', '')}"] = round(ch[b] - ch[a], 3)
        ch["lags_sec"] = lags
        if ch.get("fly_intent_emitted_at_ts") and ch.get("exchange_ack_at_ts"):
            ch["intent_to_ack_sec"] = round(ch["exchange_ack_at_ts"] - ch["fly_intent_emitted_at_ts"], 3)
    return {"intents": intents, "accounts": accounts}


def detect_gaps(chains: Mapping[str, Any], paper_twins: Mapping[str, Mapping[str, Any]] | None,
                *, now: float, known_accounts: Iterable[str] = ()) -> list[dict]:
    """Gap detector: missing orders, missing stops, unreported fills, drift."""
    gaps: list[dict] = []
    intents = chains.get("intents") or {}
    accounts = chains.get("accounts") or {}
    by_cid: dict[str, list[dict]] = {}
    for ch in accounts.values():
        by_cid.setdefault(ch["correlation_id"], []).append(ch)
    for cid, intent in intents.items():
        if not intent.get("entry_allowed"):
            continue
        emitted = intent.get("fly_intent_emitted_at_ts")
        age = (now - emitted) if emitted else None
        chs = by_cid.get(cid) or []
        with_order = [c for c in chs if c.get("order_sent_at_ts") or c.get("exchange_ack_at_ts")]
        if age is not None and age > INTENT_TO_ORDER_GAP_SEC and not with_order:
            rejected = [e for c in chs for e in c.get("errors") or []]
            gaps.append({"code": "INTENT_NO_ACCOUNT_ORDER", "severity": RED if not rejected else AMBER,
                         "correlation_id": cid, "age_sec": round(age, 1),
                         "rejections": rejected[:5]})
        for acct in known_accounts or []:
            if age is not None and age > INTENT_TO_ORDER_GAP_SEC and not any(
                    c["account"] == acct for c in chs):
                gaps.append({"code": "ACCOUNT_DID_NOT_COPY", "severity": AMBER,
                             "correlation_id": cid, "account": acct, "age_sec": round(age, 1)})
    for ch in accounts.values():
        cid, acct = ch["correlation_id"], ch["account"]
        fill_ts = ch.get("fill_at_ts")
        if fill_ts and not ch.get("stop_confirmed") and not ch.get("closed") and now - fill_ts > FILL_TO_STOP_GAP_SEC:
            gaps.append({"code": "MISSING_STOP", "severity": RED, "correlation_id": cid,
                         "account": acct, "unprotected_sec": round(now - fill_ts, 1)})
        if fill_ts and not ch.get("fill_reported_to_fly_at_ts") and now - fill_ts > REPORT_MISSING_GAP_SEC:
            gaps.append({"code": "FILL_NOT_REPORTED", "severity": AMBER, "correlation_id": cid,
                         "account": acct, "age_sec": round(now - fill_ts, 1)})
        if ch.get("order_type") and str(ch.get("order_type")).upper() != "LIMIT":
            gaps.append({"code": "ENTRY_NOT_LIMIT", "severity": RED, "correlation_id": cid,
                         "account": acct, "order_type": ch.get("order_type")})
        twin = (paper_twins or {}).get(cid) or {}
        paper_px = _f(twin.get("entry_price"))
        live_px = ch.get("fill_price")
        if paper_px and live_px:
            side = str(twin.get("side") or "").upper()
            sign = 1.0 if side in ("LONG", "BUY") else -1.0 if side in ("SHORT", "SELL") else 0.0
            drift_bp = round((live_px - paper_px) / paper_px * 1e4 * (sign or 1.0), 2)
            ch["price_drift_bp"] = drift_bp
            sev = RED if abs(drift_bp) >= PRICE_DRIFT_RED_BP else AMBER if abs(drift_bp) >= PRICE_DRIFT_AMBER_BP else None
            if sev:
                gaps.append({"code": "PRICE_DRIFT_VS_PAPER", "severity": sev, "correlation_id": cid,
                             "account": acct, "drift_bp": drift_bp, "paper": paper_px, "live": live_px})
        paper_qty = _f(twin.get("qty"))
        live_qty = ch.get("fill_qty") or ch.get("order_qty")
        if paper_qty and live_qty:
            frac = (live_qty - paper_qty) / paper_qty
            ch["size_drift_fraction"] = round(frac, 5)
            if frac > SIZE_DRIFT_RED_FRACTION:
                # Live larger than paper = upward rounding: never allowed.
                gaps.append({"code": "SIZE_ABOVE_PAPER", "severity": RED, "correlation_id": cid,
                             "account": acct, "paper_qty": paper_qty, "live_qty": live_qty})
            elif frac < -0.5:
                gaps.append({"code": "SIZE_DRIFT_VS_PAPER", "severity": AMBER, "correlation_id": cid,
                             "account": acct, "paper_qty": paper_qty, "live_qty": live_qty})
    return gaps


def lag_stats(chains: Mapping[str, Any], *, since_ts: float) -> dict:
    """p50/p95/max per hop over chains whose intent/ack falls in the window."""
    buckets: dict[str, list[float]] = {}
    per_account: dict[str, dict[str, list[float]]] = {}
    for ch in (chains.get("accounts") or {}).values():
        anchor = ch.get("fly_intent_emitted_at_ts") or ch.get("railway_received_at_ts") or ch.get("exchange_ack_at_ts")
        if not anchor or anchor < since_ts:
            continue
        for hop, v in (ch.get("lags_sec") or {}).items():
            buckets.setdefault(hop, []).append(v)
            per_account.setdefault(ch["account"], {}).setdefault(hop, []).append(v)
        if ch.get("intent_to_ack_sec") is not None:
            buckets.setdefault("intent->exchange_ack", []).append(ch["intent_to_ack_sec"])
            per_account.setdefault(ch["account"], {}).setdefault("intent->exchange_ack", []).append(ch["intent_to_ack_sec"])
    def summarize(b):
        return {hop: {"n": len(v), "p50": _pct(v, 0.5), "p95": _pct(v, 0.95), "max": round(max(v), 3)}
                for hop, v in b.items() if v}
    return {"all": summarize(buckets), "by_account": {a: summarize(b) for a, b in per_account.items()}}


def summary(*, output: Mapping[str, Any], force_paper_mode: bool, relay_stack_mode: str,
            switch_rows: Iterable[Mapping[str, Any]], gaps: list[dict], lags: Mapping[str, Any],
            executor_last_report_ts: float | None, website: Mapping[str, Any] | None,
            rejects_1h: int, unsigned_rejects_1h: int, now: float) -> dict:
    causes: list[str] = []
    verdict = GREEN
    output_on = bool((output or {}).get("enabled"))
    armed_tiles = [r.get("lane") for r in switch_rows or [] if r.get("bitfinex_live_orders")]
    eligible = [r.get("lane") for r in switch_rows or [] if r.get("relay_eligible")]
    live_capable = output_on and bool(armed_tiles)
    for g in gaps:
        causes.append(g["code"])
        verdict = worst(verdict, g.get("severity") or AMBER)
    p95 = ((lags.get("all") or {}).get("intent->exchange_ack") or {}).get("p95")
    if p95 is not None:
        if p95 > LAG_RED_SEC:
            causes.append("COPY_LAG_HIGH"); verdict = worst(verdict, RED)
        elif p95 > LAG_AMBER_SEC:
            causes.append("COPY_LAG_HIGH"); verdict = worst(verdict, AMBER)
    web = website or {}
    if live_capable:
        if not web.get("reachable"):
            causes.append("WEBSITE_STATE_UNREACHABLE"); verdict = worst(verdict, AMBER)
        elif not web.get("armed_accounts"):
            causes.append("ACCOUNT_NOT_ARMED"); verdict = worst(verdict, AMBER)
        if web.get("executor_heartbeat_age_sec") is None or float(web.get("executor_heartbeat_age_sec") or 1e9) > 60:
            causes.append("EXECUTOR_UNREACHABLE"); verdict = worst(verdict, RED)
        if force_paper_mode:
            causes.append("FORCE_PAPER_BLOCKS_OUTPUT"); verdict = worst(verdict, AMBER)
    if unsigned_rejects_1h:
        causes.append("INTENT_REJECTED_UNSIGNED"); verdict = worst(verdict, RED)
    if rejects_1h:
        causes.append("ORDER_REJECTED"); verdict = worst(verdict, AMBER)
    for acct in (web.get("accounts") or []):
        if acct.get("armed") and acct.get("key_valid") is False:
            causes.append("KEY_INVALID"); verdict = worst(verdict, RED)
    state = "LIVE_CAPABLE" if live_capable else "IDLE_DISARMED"
    return {
        "schema": SCHEMA, "verdict": verdict, "state": state,
        "causes": sorted(set(causes)),
        "output_on": output_on, "force_paper_mode": bool(force_paper_mode),
        "relay_stack_mode": relay_stack_mode, "tiles_live_on": armed_tiles,
        "tiles_relay_eligible": eligible, "gap_count": len(gaps),
        "copy_lag_p95_sec": p95, "executor_last_report_ts": executor_last_report_ts,
        "website": {k: web.get(k) for k in ("reachable", "armed_accounts", "fetched_at_ts",
                                             "executor_heartbeat_age_sec", "error")},
        "computed_at_ts": now,
    }


def now_ts() -> float:
    return time.time()
