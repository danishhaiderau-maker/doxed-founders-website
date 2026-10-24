"""Per-tile trade table with realism stamps, countable on R17 proof only (Strategist goal (c)/(d)).

Every closed paper row is stamped with its fill model, trigger price source, delay source and pooled n, and
whether both book receive timestamps are present. A row is COUNTABLE only when ``r17_exclusion`` finds no
reason to exclude it AND it belongs to the current data epoch; every other row is listed with its reason and
never enters a statistic. Per tile, countable rows only: n, mean net bp and a 95 % CI clustered on 1-hour
entry buckets (trades inside one hour share market state, so they are not independent draws).
Pure; no I/O.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

import r17_exclusion

SCHEMA = "tile_realism_table_v1"
EXPECTED_DATA_EPOCH_ID = "ce-20261004-v31-freeze21b"
REASON_EPOCH = "NOT_CURRENT_DATA_EPOCH"
REASON_EPOCH_MISSING = "DATA_EPOCH_STAMP_MISSING"
REASON_BP_MISSING = "NET_BP_MISSING"
CLUSTER_SEC = 3600
_TILE_KEYS = ("tile_short", "tile_id", "tile", "research_lane", "lane")
_TS_KEYS = ("entry_ts", "open_ts", "ts", "close_ts")
_DELAY_N = re.compile(r"_n(\d+)$")


def _first(row: Mapping, keys) -> Any:
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return None


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def realism_stamps(row: Mapping[str, Any]) -> dict[str, Any]:
    delay = row.get("delay_source")
    m = _DELAY_N.search(str(delay or ""))
    return {
        "fill_model": row.get("fill_model") or row.get("fill_model_version"),
        "trigger_price_source": row.get("trigger_price_source"),
        "delay_source": delay,
        "delay_n": int(m.group(1)) if m else None,
        "entry_book_receive_ts_present": _num(row.get("entry_book_receive_ts")) is not None,
        "exit_book_receive_ts_present": _num(row.get("exit_book_receive_ts")) is not None,
    }


def row_exclusion(row: Mapping[str, Any], *, expected_epoch_id: str = EXPECTED_DATA_EPOCH_ID,
                  ids=None, load_errors=None) -> str | None:
    epoch = row.get("data_epoch_id")
    if epoch in (None, ""):
        return REASON_EPOCH_MISSING  # HM #865 nit 2: no stamp never passes the epoch check
    if str(epoch) != expected_epoch_id:
        return REASON_EPOCH
    reason = r17_exclusion.non_countable_reason(dict(row), ids=ids, load_errors=load_errors)
    if reason:
        return reason
    if _num(row.get("net_pnl_bp")) is None:
        return REASON_BP_MISSING
    return None


def cluster_ci(values: list[float], clusters: list[Any], z: float = 1.96) -> dict[str, Any]:
    """Mean with a cluster-robust (CR1) standard error; None when fewer than 2 clusters."""
    n = len(values)
    if not n:
        return {"n": 0, "mean_bp": None, "ci95_low": None, "ci95_high": None, "clusters": 0}
    mean = sum(values) / n
    sums: dict[Any, float] = defaultdict(float)
    for v, c in zip(values, clusters):
        sums[c] += v - mean
    g = len(sums)
    out = {"n": n, "mean_bp": round(mean, 4), "clusters": g, "ci95_low": None, "ci95_high": None}
    if g >= 2:
        se = math.sqrt(sum(s * s for s in sums.values()) * g / (g - 1)) / n
        out.update(ci95_low=round(mean - z * se, 4), ci95_high=round(mean + z * se, 4), se_bp=round(se, 4))
    return out


def build_tile_table(rows: Iterable[Mapping[str, Any]], *, expected_epoch_id: str = EXPECTED_DATA_EPOCH_ID,
                     ids=None, load_errors=None) -> dict[str, Any]:
    trades, tiles = [], defaultdict(lambda: {"values": [], "clusters": [], "reasons": Counter(), "rows": 0})
    excluded = Counter()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        tile = str(_first(row, _TILE_KEYS) or "UNKNOWN_TILE")
        reason = row_exclusion(row, expected_epoch_id=expected_epoch_id, ids=ids, load_errors=load_errors)
        bucket = tiles[tile]
        bucket["rows"] += 1
        record = {"trade_id": row.get("trade_id"), "tile": tile, "countable": reason is None,
                  "non_countable_reason": reason, "net_pnl_bp": _num(row.get("net_pnl_bp")), **realism_stamps(row)}
        trades.append(record)
        if reason:
            head = reason.split(":", 1)[0]
            excluded[head] += 1
            bucket["reasons"][head] += 1
            continue
        ts = _num(_first(row, _TS_KEYS))
        bucket["values"].append(float(row["net_pnl_bp"]) if _num(row.get("net_pnl_bp")) is not None else 0.0)
        bucket["clusters"].append(int(ts // CLUSTER_SEC) if ts is not None else f"row:{len(trades)}")
    per_tile = {}
    for tile, b in sorted(tiles.items()):
        stats = cluster_ci(b["values"], b["clusters"])
        per_tile[tile] = {**stats, "rows": b["rows"], "countable": len(b["values"]),
                          "non_countable": b["rows"] - len(b["values"]),
                          "non_countable_by_reason": dict(sorted(b["reasons"].items()))}
    countable = sum(1 for t in trades if t["countable"])
    return {"schema": SCHEMA, "expected_epoch_id": expected_epoch_id, "cluster_sec": CLUSTER_SEC,
            "rows": len(trades), "countable_rows": countable, "non_countable_rows": len(trades) - countable,
            "non_countable_by_reason": dict(sorted(excluded.items())),
            "r17_excluded_rows": sum(v for k, v in excluded.items() if k.startswith("R17_")),
            "tiles": per_tile, "trades": trades}
