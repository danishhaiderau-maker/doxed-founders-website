"""Exit-latency SLO over closed trades (stop/exit hit -> close fill).

SLO: p95(exit_trigger_ts -> exit_fill_ts) <= 3 s; any single exit > 5 s alerts.
Only recorded stamps (trade_event_timestamps_v1) count; forced/admin closes are excluded.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

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


DEPLOY_CLUSTER_GAP_SEC = 600.0


def forced_summary(forced_rows: list, nofill_ids: list | None = None) -> dict:
    """Forced closes (deploy flatten / admin): total, how many lack exit_trigger_ts, and one entry per deploy (closes within
    10 min of each other). AMBER when any forced close in the window has no trigger stamp (visible, never silent).
    These rows stay OUT of n / percentiles / missing_trigger_ts."""
    rows = sorted(forced_rows)
    deploys: list = []
    for fill, has_trig, tid in rows:
        if not deploys or fill - deploys[-1]["_last"] > DEPLOY_CLUSTER_GAP_SEC:
            deploys.append({"first_fill_utc": iso_ms(fill), "n": 0, "n_missing_trigger_ts": 0, "trade_ids_missing": [], "_last": fill})
        d = deploys[-1]
        d["n"] += 1
        d["_last"] = fill
        if not has_trig:
            d["n_missing_trigger_ts"] += 1
            d["trade_ids_missing"].append(tid)
    for d in deploys:
        d.pop("_last", None)
    miss = sum(d["n_missing_trigger_ts"] for d in deploys)
    nofill = list(nofill_ids or [])
    return {"forced_closes": len(rows), "forced_missing_trigger_ts": miss, "per_deploy": deploys[-20:],
            "forced_missing_fill_ts": len(nofill), "forced_missing_fill_ts_trade_ids": nofill[-50:],
            "status": "NO_DATA" if not rows and not nofill else ("AMBER" if miss or nofill else "GREEN"),
            "note": "forced/deploy closes are excluded from the SLO percentiles and from missing_trigger_ts"}


def evaluate(rows: Iterable[Mapping], *, since: float = 0.0, forced: Iterable[str] = ()) -> dict:
    forced_set = {str(r).upper() for r in forced}
    samples, breaches, missing = [], [], 0
    forced_nofill: list = []
    forced_rows: list = []  # (exit_fill_ts, has_trigger_ts, trade_id) of forced/admin/deploy closes: counted, never in percentiles
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("exit_reason") or "").upper() in forced_set:
            fev = row.get("event_timestamps") if isinstance(row.get("event_timestamps"), Mapping) else {}
            ffill, ftrig = fev.get("exit_fill_ts"), fev.get("exit_trigger_ts")
            if not isinstance(ffill, (int, float)) or isinstance(ffill, bool) or not math.isfinite(float(ffill)):
                forced_nofill.append(row.get("trade_id"))  # missing / non-numeric fill stamp: counted + AMBER (HM)
            elif ffill >= since:
                forced_rows.append((float(ffill), isinstance(ftrig, (int, float)) and ftrig > 0, row.get("trade_id")))
            # a valid fill before `since` is simply outside the window: NOT counted, never AMBER
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
            "breaches_over_5s": breaches[-50:], "breach_count": len(breaches), "status": status,
            "forced": forced_summary(forced_rows, forced_nofill)}


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


LIVE_FOLLOW_AMBER_SEC = 3.0
LIVE_FOLLOW_RED_SEC = 8.0
# Back-compat alias used by older callers/tests.
LIVE_FOLLOW_ALERT_SEC = LIVE_FOLLOW_AMBER_SEC
# Live-follow covered only the self-simulated DISLOC / DISLOC-FOI tiles (Tile 6/7), removed
# 23 Oct 2026 (R9). No default lane filter remains; callers pass explicit prefixes.
LIVE_FOLLOW_LANE_PREFIXES: tuple = ()


def _lane_matches_prefixes(lane: str, prefixes: Iterable[str]) -> bool:
    lane_u = str(lane or "").upper()
    prefs = tuple(str(p).upper() for p in prefixes)
    if not prefs:
        return True
    if any(lane_u.startswith(p) or p in lane_u for p in prefs):
        return True
    # Short codes from the executor journal ("GS-01" vs "GS_01").
    short = lane_u.replace("_", "-")
    return any(short == p or short.startswith(p + "-") or short.startswith(p + "_") for p in prefs)


def _top_hop(lags: Mapping | None) -> str | None:
    if not isinstance(lags, Mapping) or not lags:
        return None
    best_name, best_v = None, None
    for name, raw in lags.items():
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if best_v is None or v > best_v:
            best_name, best_v = str(name), v
    return best_name


def live_follow(by_corr: Mapping, twins: Mapping, *, since: float,
                prefixes: Iterable[str] = LIVE_FOLLOW_LANE_PREFIXES,
                hop_lags_by_corr: Mapping | None = None,
                fill_ts_by_corr: Mapping | None = None) -> dict:
    """Live exit lag vs paper per lane: paper_trigger/exit_ts -> live fill seconds.

    SLO style (Danish live-lag audit §7): > LIVE_FOLLOW_AMBER_SEC AMBER,
    > LIVE_FOLLOW_RED_SEC RED. When hop lags are known, breaches name the top hop.
    """
    pref = tuple(str(p).upper() for p in prefixes)
    lanes: dict = {}
    for cid, rows in (by_corr or {}).items():
        closes = [r for r in rows or [] if isinstance(r, Mapping) and r.get("type") == "POSITION_CLOSED"]
        if not closes:
            continue
        raw_lane = str(closes[0].get("lane") or closes[0].get("research_lane") or "?").upper()
        if pref and not _lane_matches_prefixes(raw_lane, pref):
            continue
        lane = raw_lane
        twin = (twins or {}).get(str(cid)) or {}
        try:
            paper = float(twin.get("exit_ts"))
        except (TypeError, ValueError):
            lanes.setdefault(lane, {"samples": [], "breaches": [], "ambers": [], "missing_paper": 0})["missing_paper"] += 1
            continue
        live = None
        if fill_ts_by_corr and cid in (fill_ts_by_corr or {}):
            try:
                live = float(fill_ts_by_corr[cid])
            except (TypeError, ValueError):
                live = None
        if live is None:
            try:
                live = min(float(r.get("event_at_ts") if r.get("event_at_ts") is not None else r.get("sent_at_ts"))
                           for r in closes if (r.get("event_at_ts") or r.get("sent_at_ts")) is not None)
            except (TypeError, ValueError):
                lanes.setdefault(lane, {"samples": [], "breaches": [], "ambers": [], "missing_paper": 0})["missing_paper"] += 1
                continue
        if live < since:
            continue
        lag = round(live - paper, 3)
        L = lanes.setdefault(lane, {"samples": [], "breaches": [], "ambers": [], "missing_paper": 0})
        L["samples"].append(lag)
        top = _top_hop((hop_lags_by_corr or {}).get(str(cid)))
        item = {"trade_id": cid, "lag_sec": lag, "paper_exit_utc": iso_ms(paper),
                "live_exit_utc": iso_ms(live), "top_hop": top}
        if lag > LIVE_FOLLOW_RED_SEC:
            L["breaches"].append(item)
        elif lag > LIVE_FOLLOW_AMBER_SEC:
            L["ambers"].append(item)
    out, any_red, any_amber = {}, False, False
    for lane, L in lanes.items():
        s = L["samples"]
        red = bool(L["breaches"])
        amber = (not red) and bool(L["ambers"])
        any_red = any_red or red
        any_amber = any_amber or amber
        status = "NO_DATA" if not s else ("RED" if red else ("AMBER" if amber else "GREEN"))
        out[lane] = {"n": len(s), "p50_sec": _pct(s, 0.5), "p95_sec": _pct(s, 0.95),
                     "max_sec": max(s) if s else None, "missing_paper_exit": L["missing_paper"],
                     "breach_count": len(L["breaches"]), "amber_count": len(L["ambers"]),
                     "breaches_over_8s": L["breaches"][-20:],
                     "ambers_over_3s": L["ambers"][-20:],
                     # Back-compat key kept for older consumers.
                     "breaches_over_3s": (L["breaches"] + L["ambers"])[-20:],
                     "amber_sec": LIVE_FOLLOW_AMBER_SEC, "red_sec": LIVE_FOLLOW_RED_SEC,
                     "status": status}
    overall = "NO_DATA" if not out else ("RED" if any_red else ("AMBER" if any_amber else "GREEN"))
    return {"schema": "live_exit_follow_v1",
            "alert_sec": LIVE_FOLLOW_AMBER_SEC,
            "amber_sec": LIVE_FOLLOW_AMBER_SEC, "red_sec": LIVE_FOLLOW_RED_SEC,
            "lanes": out, "alert": any_red or any_amber, "status": overall}


def filter_active_per_tile(per_tile: Mapping[str, Any] | None, *,
                           active: Iterable[str], retired: Iterable[str] | None = None) -> dict:
    """Keep only active-dashboard lanes; drop retired / unknown (ROUTER, FADE_POOL, …)."""
    active_set = {str(x).upper() for x in (active or ())}
    retired_set = {str(x).upper() for x in (retired or ())}
    out = {}
    for lane, val in (per_tile or {}).items():
        key = str(lane or "").upper()
        if key in retired_set:
            continue
        if active_set and key not in active_set:
            continue
        out[key] = val
    return out
