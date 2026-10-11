"""Pure builders for R14/R15/R16/R21/R22/R23 (/api/monitor/live/{events,fill-quality,daily,slo,drills,funding}).

No Flask, no I/O except what the caller passes in. Missing evidence is reported as missing
(``evidence: NONE``), never as a healthy zero.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Callable, Mapping, Optional

GREEN, AMBER, RED = "GREEN", "AMBER", "RED"
_ORDER = {GREEN: 0, AMBER: 1, RED: 2}
MARKOUT_HORIZONS_S = (5, 30, 60)
MARKOUT_WORSE_THAN_TWIN_AMBER_BP = 2.0
SLO_ORDER_BUDGET_S = 2.0
DAILY_MISMATCH_TOL_USD = 0.01


def worst(a: str, b: str) -> str:
    return a if _ORDER.get(a, 0) >= _ORDER.get(b, 0) else b


def _f(v) -> Optional[float]:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _day(ts) -> str:
    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).strftime("%Y-%m-%d")


# ---- R14 events -------------------------------------------------------------
def events(approvals: list, reports: list, alerts: list, *, since_ts: float = 0.0, limit: int = 200,
           cursor: int = 0) -> dict:
    rows = []
    for a in approvals or ():
        rows.append({"ts": _f(a.get("created_at_ts")), "source": "fly_approval", "type": a.get("event"),
                     "correlation_id": a.get("correlation_id") or a.get("trade_id"), "lane": a.get("research_lane"),
                     "event_seq": a.get("event_seq"), "event_id": a.get("event_id")})
    for r in reports or ():
        rows.append({"ts": _f(r.get("fly_received_at_ts") or r.get("sent_at_ts")), "source": "executor_report",
                     "type": r.get("type"), "correlation_id": r.get("correlation_id"), "account": r.get("account"),
                     "lane": r.get("lane"), "error": r.get("error")})
    for al in alerts or ():
        rows.append({"ts": _f(al.get("at_ts") or al.get("ts")), "source": "alert", "type": al.get("code"),
                     "severity": al.get("severity"), "message": al.get("message")})
    rows = sorted((r for r in rows if (r["ts"] or 0) >= since_ts), key=lambda r: (r["ts"] or 0))
    # Sequence gaps: Fly approvals carry a per-trade event_seq (1, 2, 3 ...).
    seen: dict = {}
    for a in approvals or ():
        tid, seq = str(a.get("correlation_id") or a.get("trade_id") or ""), a.get("event_seq")
        if tid and isinstance(seq, int):
            seen.setdefault(tid, set()).add(seq)
    gaps = [{"correlation_id": t, "missing_seq": sorted(set(range(1, max(s) + 1)) - s)}
            for t, s in seen.items() if s and set(range(1, max(s) + 1)) - s]
    page = rows[cursor:cursor + limit]
    return {"route": "R14", "verdict": RED if gaps else GREEN, "total": len(rows), "cursor": cursor,
            "next_cursor": cursor + len(page) if cursor + len(page) < len(rows) else None,
            "events": page, "sequence_gaps": gaps,
            "alerts": [{"code": "LIVE_EVENT_SEQUENCE_GAP", "severity": RED, "count": len(gaps)}] if gaps else []}


# ---- R15 fill quality -------------------------------------------------------
def fill_quality(chains: Mapping, twins: Mapping, mid_at: Callable[[float], Optional[float]], now: float) -> dict:
    rows, by_lane = [], {}
    for c in (chains.get("accounts") or {}).values():
        px, ts = _f(c.get("fill_price")), _f(c.get("fill_at_ts"))
        if not px or not ts:
            continue
        side = 1.0
        twin = twins.get(c.get("correlation_id")) or {}
        if str(twin.get("side") or twin.get("direction") or "").upper() in ("SHORT", "SELL"):
            side = -1.0
        mid0 = mid_at(ts)
        row = {"correlation_id": c.get("correlation_id"), "account": c.get("account"), "lane": c.get("lane"),
               "fill_ts": ts, "fill_price": px, "mid_at_fill": mid0,
               "spread_paid_bp": None if not mid0 else round(side * (px / mid0 - 1.0) * 1e4, 3),
               "paper_entry_price": _f(twin.get("entry_price")),
               "slippage_vs_paper_bp": None, "markout_bp": {}, "twin_markout_bp": {}}
        pe = row["paper_entry_price"]
        if pe:
            row["slippage_vs_paper_bp"] = round(side * (px / pe - 1.0) * 1e4, 3)
        for h in MARKOUT_HORIZONS_S:
            if ts + h > now:
                continue
            m = mid_at(ts + h)
            if m:
                row["markout_bp"][str(h)] = round(side * (m / px - 1.0) * 1e4, 3)
                if pe:
                    row["twin_markout_bp"][str(h)] = round(side * (m / pe - 1.0) * 1e4, 3)
        rows.append(row)
        lane = by_lane.setdefault(str(c.get("lane")), {"n": 0, "live30": [], "twin30": []})
        lane["n"] += 1
        if "30" in row["markout_bp"] and "30" in row["twin_markout_bp"]:
            lane["live30"].append(row["markout_bp"]["30"])
            lane["twin30"].append(row["twin_markout_bp"]["30"])
    verdict, alerts, lanes = GREEN, [], {}
    for lane, d in by_lane.items():
        lm = sum(d["live30"]) / len(d["live30"]) if d["live30"] else None
        tm = sum(d["twin30"]) / len(d["twin30"]) if d["twin30"] else None
        lanes[lane] = {"fills": d["n"], "mean_markout_30s_bp": lm, "twin_mean_markout_30s_bp": tm}
        if lm is not None and tm is not None and tm - lm > MARKOUT_WORSE_THAN_TWIN_AMBER_BP:
            verdict = AMBER
            alerts.append({"code": "LIVE_MARKOUT_WORSE_THAN_PAPER", "severity": AMBER, "lane": lane,
                           "gap_bp": round(tm - lm, 3)})
    return {"route": "R15", "verdict": verdict, "fills": rows[-200:], "lanes": lanes, "alerts": alerts,
            "evidence": "BITFINEX_1S_TAPE_RING" if rows else "NONE",
            "note": "book depth at signal: see /api/monitor/streams/bfx_book (10-level stream)"}


# ---- R16 daily / R23 funding ------------------------------------------------
def _closed_pnl(r: Mapping) -> Optional[float]:
    for key in ("realized_pnl_usd", "pnl_usd", "realized_usd"):
        v = _f(r.get(key)) if key in r else _f((r.get("close") or {}).get(key) if isinstance(r.get("close"), Mapping) else None)
        if v is not None:
            return v
    return None


def funding(reports: list) -> dict:
    rows = []
    for r in reports or ():
        f = r.get("funding") if isinstance(r.get("funding"), Mapping) else None
        amt = _f((f or {}).get("amount_usd")) if f else _f(r.get("funding_usd"))
        if amt is None:
            continue
        rows.append({"correlation_id": r.get("correlation_id"), "account": r.get("account"), "lane": r.get("lane"),
                     "at_ts": _f(r.get("sent_at_ts")), "funding_usd": amt, "kind": "cash_flow_not_fee"})
    per_pos: dict = {}
    for x in rows:
        k = (x["account"], x["correlation_id"])
        per_pos[k] = round(per_pos.get(k, 0.0) + x["funding_usd"], 6)
    return {"route": "R23", "rows": rows[-500:],
            "positions": [{"account": a, "correlation_id": c, "funding_usd": v} for (a, c), v in per_pos.items()],
            "evidence": "EXECUTOR_REPORTS" if rows else "NONE",
            "note": None if rows else "no executor report has carried funding yet (Bitfinex perp funding settles every 8 h)"}


def daily(reports: list, ledger_by_day: Mapping[str, Mapping[str, float]] | None = None) -> dict:
    days: dict = {}
    for r in reports or ():
        ts = _f(r.get("sent_at_ts") or r.get("fly_received_at_ts"))
        if not ts:
            continue
        acct = str(r.get("account") or "UNKNOWN")
        d = days.setdefault(_day(ts), {}).setdefault(acct, {"fills": 0, "closes": 0, "realized_usd": 0.0,
                                                           "funding_usd": 0.0, "end_balance": None,
                                                           "realized_known": True})
        t = r.get("type")
        if t == "ORDER_FILLED":
            d["fills"] += 1
        if t == "POSITION_CLOSED":
            d["closes"] += 1
            p = _closed_pnl(r)
            if p is None:
                d["realized_known"] = False
            else:
                d["realized_usd"] = round(d["realized_usd"] + p, 6)
        f = r.get("funding") if isinstance(r.get("funding"), Mapping) else None
        amt = _f((f or {}).get("amount_usd")) if f else _f(r.get("funding_usd"))
        if amt is not None:
            d["funding_usd"] = round(d["funding_usd"] + amt, 6)
        if isinstance(r.get("balance"), Mapping):
            d["end_balance"] = _f(r["balance"].get("total") or r["balance"].get("usd") or r["balance"].get("equity"))
    verdict, alerts = GREEN, []
    for day, accts in days.items():
        for acct, d in accts.items():
            led = ((ledger_by_day or {}).get(day) or {}).get(acct)
            d["ledger_realized_usd"] = led
            if led is not None and d["realized_known"] and abs(led - d["realized_usd"]) > DAILY_MISMATCH_TOL_USD:
                verdict = RED
                alerts.append({"code": "LIVE_DAILY_LEDGER_MISMATCH", "severity": RED, "day": day, "account": acct,
                               "exchange_usd": d["realized_usd"], "ledger_usd": led})
    return {"route": "R16", "verdict": verdict, "days": dict(sorted(days.items())[-31:]), "alerts": alerts,
            "source": "executor reports (exchange fills/closes/balance as the executor saw them)",
            "evidence": "EXECUTOR_REPORTS" if days else "NONE"}


# ---- R21 SLO / R22 drills ---------------------------------------------------
def slo(chains: Mapping, protection_samples: list, restarts_7d: Optional[int], now: float) -> dict:
    since = now - 7 * 86400
    accts = [c for c in (chains.get("accounts") or {}).values()
             if (_f(c.get("fly_intent_emitted_at_ts")) or 0) >= since]
    timed = [c for c in accts if c.get("intent_to_ack_sec") is not None]
    within = sum(1 for c in timed if c["intent_to_ack_sec"] <= SLO_ORDER_BUDGET_S)
    prot = [s for s in protection_samples or () if (_f(s.get("ts")) or 0) >= since]
    pct = lambda n, d: None if not d else round(100.0 * n / d, 2)  # noqa: E731
    out = {"route": "R21", "window": "7d", "orders": len(timed),
           "pct_orders_within_budget": pct(within, len(timed)), "order_budget_s": SLO_ORDER_BUDGET_S,
           "pct_time_reconciled": pct(sum(1 for s in prot if s.get("reconciled")), len(prot)),
           "pct_time_protected": pct(sum(1 for s in prot if s.get("protected")), len(prot)),
           "restarts_7d": restarts_7d}
    alerts = []
    if out["pct_orders_within_budget"] is not None and out["pct_orders_within_budget"] < 95.0:
        alerts.append({"code": "LIVE_SLO_ORDER_BUDGET", "severity": AMBER, "pct": out["pct_orders_within_budget"]})
    if out["pct_time_protected"] is not None and out["pct_time_protected"] < 100.0:
        alerts.append({"code": "LIVE_SLO_UNPROTECTED_TIME", "severity": RED, "pct": out["pct_time_protected"]})
    v = GREEN
    for a in alerts:
        v = worst(v, a["severity"])
    out.update(verdict=v, alerts=alerts)
    return out


DRILL_KINDS = ("KILL_SWITCH", "RESTART", "RELAY_OUTAGE")


def drills(rows: list, now: float) -> dict:
    last = {k: None for k in DRILL_KINDS}
    for r in rows or ():
        k = str(r.get("kind") or "").upper()
        if k in last and (last[k] is None or (_f(r.get("ts")) or 0) > (_f(last[k].get("ts")) or 0)):
            last[k] = r
    stale = [k for k, r in last.items() if r is None or now - (_f(r.get("ts")) or 0) > 30 * 86400]
    return {"route": "R22", "drills": (rows or [])[-200:], "last_by_kind": last,
            "verdict": AMBER if stale else GREEN,
            "alerts": [{"code": "LIVE_DRILL_OVERDUE", "severity": AMBER, "kinds": stale}] if stale else []}


LIVE_ROUTE_MANIFEST = {
    "/api/monitor/live/summary": "R19", "/api/monitor/live/arming": "R1", "/api/monitor/live/lifecycle": "R24",
    "/api/monitor/live/orders": "R2", "/api/monitor/live/latency": "R3", "/api/monitor/live/copy-chain": "chain",
    "/api/monitor/live/gaps": "gaps", "/api/monitor/live/reconcile": "R4", "/api/monitor/live/protection": "R5",
    "/api/monitor/live/exchange-health": "R6", "/api/monitor/live/balance": "R7", "/api/monitor/live/parity": "R8",
    "/api/monitor/live/relay": "R9", "/api/monitor/live/restarts": "R10", "/api/monitor/live/exit-delay": "R11",
    "/api/monitor/live/headroom": "R12", "/api/monitor/live/deploy-guard": "R13",
    "/api/monitor/live/events": "R14", "/api/monitor/live/fill-quality": "R15", "/api/monitor/live/daily": "R16",
    "/api/monitor/live/slo": "R21", "/api/monitor/live/drills": "R22", "/api/monitor/live/funding": "R23",
    "/api/monitor/live/routes": "manifest",
    "/api/monitor/runtime/headroom": "R12-alias", "/api/monitor/restarts": "R10-alias",
}
