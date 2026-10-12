"""Strategist CHASE-GAP-EDGE-20261011: chase-window and gap-bucket tables, recomputed on every request.

Pure. Source EXECUTED = closed paper trades (fills) + expired orders (reached, not filled).
bp = price-based net bp (monitor_api.trade_net_bp); CI is hour-clustered (normal approx, 90%).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Mapping

import monitor_api

Z90 = 1.6449
GAP_EDGES = (0, 1, 2, 3, 5, 8)


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _ts(r):
    return _f(r.get("close_ts")) or _f(r.get("exit_ts")) or _f(r.get("ts_epoch")) or 0.0


def _stats(fills: list, reached: int) -> dict:
    bps = [(b, _ts(r)) for r in fills for b in [monitor_api.trade_net_bp(r)] if b is not None]
    n = len(bps)
    usd = sum((_f(r.get("net_pnl_usd_raw")) if _f(r.get("net_pnl_usd_raw")) is not None else (_f(r.get("net_pnl_usd")) or 0.0))
              for r in fills)
    out = {"reached": reached, "fills": len(fills), "fill_pct": round(100.0 * len(fills) / reached, 2) if reached else None,
           "n": n, "sum_usd": round(usd, 6), "wr": None, "mean_bp": None, "ci90_lo": None, "ci90_hi": None,
           "first_half_mean_bp": None, "second_half_mean_bp": None}
    if not n:
        return out
    vals = [b for b, _ in bps]
    mean = sum(vals) / n
    out["wr"] = round(100.0 * sum(1 for v in vals if v > 0) / n, 2)
    out["mean_bp"] = round(mean, 4)
    clusters: dict = {}
    for b, t in bps:
        clusters.setdefault(int(t // 3600), []).append(b - mean)
    g = len(clusters)
    if n >= 2 and g >= 2:
        var = sum(sum(c) ** 2 for c in clusters.values()) / (n * n) * g / (g - 1)
        se = math.sqrt(var)
        out["ci90_lo"], out["ci90_hi"] = round(mean - Z90 * se, 4), round(mean + Z90 * se, 4)
    ordered = [b for b, _ in sorted(bps, key=lambda x: x[1])]
    h = n // 2
    if h:
        out["first_half_mean_bp"] = round(sum(ordered[:h]) / h, 4)
        out["second_half_mean_bp"] = round(sum(ordered[h:]) / (n - h), 4)
    return out


def _table(closed: Iterable[Mapping], expired: Iterable[Mapping], key: Callable[[Mapping], Any],
           forced: set) -> dict:
    fills, unfilled = {}, {}
    for r in closed:
        if str(r.get("exit_reason") or "").upper() in forced:
            continue
        fills.setdefault(key(r), []).append(r)
    for r in expired:
        unfilled.setdefault(key(r), 0)
        unfilled[key(r)] += 1
    out = {}
    for b in sorted(set(fills) | set(unfilled), key=lambda x: (x is None, str(x))):
        f = fills.get(b, [])
        out[str(b)] = {"source": "EXECUTED", **_stats(f, len(f) + unfilled.get(b, 0))}
    return out


def chase_bucket(r: Mapping) -> Any:
    c = r.get("limit_chase_count")
    try:
        c = int(float(c))
    except (TypeError, ValueError):
        return "unknown"
    return str(c) if c < 3 else "3+"


def gap_bucket(r: Mapping) -> Any:
    g = _f(r.get("score_gap"))
    if g is None:
        ls, ss = _f(r.get("long_score")), _f(r.get("short_score"))
        if ls is None or ss is None or (ls == 0 and ss == 0):
            return "missing"
        g = abs(ls - ss)
    for lo, hi in zip(GAP_EDGES, GAP_EDGES[1:]):
        if lo <= g < hi:
            return f"{lo}-{hi}"
    return f"{GAP_EDGES[-1]}+" if g >= GAP_EDGES[-1] else "neg"


def chase_view(closed, expired, forced) -> dict:
    return {"schema": "chase_buckets_v1", "bucket": "limit_chase_count", "buckets": _table(closed, expired, chase_bucket, forced),
            "note": "EXECUTED only; shadow chase log reported separately, never summed"}


def gap_view(closed, expired, forced) -> dict:
    return {"schema": "gap_buckets_v1", "bucket": "score_gap (directional long_score - short_score)",
            "edges": list(GAP_EDGES), "buckets": _table(closed, expired, gap_bucket, forced)}
