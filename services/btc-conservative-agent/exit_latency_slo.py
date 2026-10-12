"""Exit-latency SLO over closed trades (stop/exit hit -> close fill).

SLO: p95(exit_trigger_ts -> exit_fill_ts) <= 3 s; any single exit > 5 s alerts.
Only recorded stamps (trade_event_timestamps_v1) count; forced/admin closes are excluded.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable, Mapping

SCHEMA = "exit_latency_slo_v1"
SLO_P95_SEC = 3.0
ALERT_SINGLE_SEC = 5.0
STAGES = ("signal_ts", "order_sent_ts", "fill_ts", "exit_trigger_ts", "exit_sent_ts", "exit_fill_ts")


def iso_ms(ts) -> str | None:
    try:
        v = float(ts)
    except (TypeError, ValueError):
        return None
    if not (v > 0 and math.isfinite(v)):
        return None
    return datetime.fromtimestamp(v, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _pct(values: list, q: float):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


def evaluate(rows: Iterable[Mapping], *, since: float = 0.0, forced: Iterable[str] = ()) -> dict:
    forced_set = {str(r).upper() for r in forced}
    samples, breaches, missing = [], [], 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("exit_reason") or "").upper() in forced_set:
            continue
        ev = row.get("event_timestamps") if isinstance(row.get("event_timestamps"), Mapping) else {}
        trig, fill = ev.get("exit_trigger_ts"), ev.get("exit_fill_ts")
        if not isinstance(fill, (int, float)) or fill < since:
            continue
        if not isinstance(trig, (int, float)) or trig <= 0:
            missing += 1
            continue
        lat = round(float(fill) - float(trig), 3)
        samples.append(lat)
        if lat > ALERT_SINGLE_SEC:
            breaches.append({"trade_id": row.get("trade_id"), "lane": row.get("research_lane"),
                             "exit_reason": row.get("exit_reason"), "trigger_to_fill_sec": lat,
                             "exit_trigger_utc": iso_ms(trig), "exit_fill_utc": iso_ms(fill)})
    p95 = _pct(samples, 0.95)
    status = "NO_DATA" if not samples else ("RED" if breaches or (p95 is not None and p95 > SLO_P95_SEC)
                                           else "GREEN")
    return {"schema": SCHEMA, "slo_p95_sec": SLO_P95_SEC, "alert_single_sec": ALERT_SINGLE_SEC,
            "n": len(samples), "missing_trigger_ts": missing, "p50_sec": _pct(samples, 0.5), "p95_sec": p95,
            "max_sec": max(samples) if samples else None, "slo_met": bool(samples) and p95 <= SLO_P95_SEC,
            "breaches_over_5s": breaches[-50:], "breach_count": len(breaches), "status": status}


def unfilled_rows(orders: Iterable[Mapping], *, since: float, limit: int) -> list:
    """Unfilled (cancelled/expired) orders with their terminal stamp, UTC ms."""
    out = []
    for o in orders:
        if not isinstance(o, Mapping):
            continue
        end = o.get("expired_ts")
        if not isinstance(end, (int, float)) or end < since:
            continue
        out.append({"trade_id": o.get("trade_id") or o.get("id"), "lane": o.get("research_lane"),
                    "order_sent_utc": iso_ms(o.get("created_ts")),
                    "cancel_or_expire_ts": round(float(end), 3), "cancel_or_expire_utc": iso_ms(end),
                    "reason": o.get("reason")})
    return out[-limit:] if limit else []


LIVE_FOLLOW_ALERT_SEC = 3.0
LIVE_FOLLOW_LANE_PREFIXES = ("DISLOC",)


def live_follow(by_corr: Mapping, twins: Mapping, *, since: float,
                prefixes: Iterable[str] = LIVE_FOLLOW_LANE_PREFIXES) -> dict:
    """Live exit lag vs paper per lane: POSITION_CLOSED report sent_at_ts - paper exit_ts.

    Alerts (status RED) when any live exit lands > LIVE_FOLLOW_ALERT_SEC after paper.
    """
    pref = tuple(str(p).upper() for p in prefixes)
    lanes: dict = {}
    for cid, rows in (by_corr or {}).items():
        closes = [r for r in rows or [] if isinstance(r, Mapping) and r.get("type") == "POSITION_CLOSED"]
        if not closes:
            continue
        lane = str(closes[0].get("lane") or "?").upper()
        if pref and not lane.startswith(pref):
            continue
        twin = (twins or {}).get(str(cid)) or {}
        try:
            paper = float(twin.get("exit_ts"))
            live = min(float(r.get("sent_at_ts")) for r in closes if r.get("sent_at_ts") is not None)
        except (TypeError, ValueError):
            lanes.setdefault(lane, {"samples": [], "breaches": [], "missing_paper": 0})["missing_paper"] += 1
            continue
        if live < since:
            continue
        lag = round(live - paper, 3)
        L = lanes.setdefault(lane, {"samples": [], "breaches": [], "missing_paper": 0})
        L["samples"].append(lag)
        if lag > LIVE_FOLLOW_ALERT_SEC:
            L["breaches"].append({"trade_id": cid, "lag_sec": lag, "paper_exit_utc": iso_ms(paper),
                                  "live_exit_utc": iso_ms(live)})
    out, any_red = {}, False
    for lane, L in lanes.items():
        s = L["samples"]
        red = bool(L["breaches"])
        any_red = any_red or red
        out[lane] = {"n": len(s), "p50_sec": _pct(s, 0.5), "p95_sec": _pct(s, 0.95),
                     "max_sec": max(s) if s else None, "missing_paper_exit": L["missing_paper"],
                     "breach_count": len(L["breaches"]), "breaches_over_3s": L["breaches"][-20:],
                     "status": "NO_DATA" if not s else ("RED" if red else "GREEN")}
    return {"schema": "live_exit_follow_v1", "alert_sec": LIVE_FOLLOW_ALERT_SEC, "lanes": out,
            "alert": any_red, "status": "RED" if any_red else ("GREEN" if out else "NO_DATA")}
