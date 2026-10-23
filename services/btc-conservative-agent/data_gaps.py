"""DATA-GAPS-20261012 (Strategist): pure, read-only shaping over ``tile_trade_row_v1`` rows.

Field completeness (signal_to_fill_ms, exit_bid, exit_ask, regime_at_signal) per lane and
signal->fill percentiles in milliseconds. Never touches trading, relay or exchange state.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

COMPLETENESS_SCHEMA = "monitor_tile_field_completeness_v1"
FIELDS = ("signal_to_fill_ms", "exit_bid", "exit_ask", "regime_at_signal")


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def signal_to_fill_ms(row: Mapping[str, Any]):
    t = row.get("timing") or {}
    ms = _num(t.get("signal_to_fill_ms"))
    if ms is not None:
        return ms
    sec = _num(t.get("signal_to_fill_sec"))
    if sec is None:
        sec = _num(t.get("signal_to_fill_s"))
    return sec * 1000.0 if sec is not None else None


def field_values(row: Mapping[str, Any]) -> dict:
    ex = row.get("exit") or {}
    return {"signal_to_fill_ms": signal_to_fill_ms(row),
            # R3: book-top comparison columns (exit_book_top_*) on new rows; exit_bid/ask on legacy rows.
            "exit_bid": _num(ex.get("exit_book_top_bid") if ex.get("exit_book_top_bid") is not None else ex.get("exit_bid")),
            "exit_ask": _num(ex.get("exit_book_top_ask") if ex.get("exit_book_top_ask") is not None else ex.get("exit_ask")),
            "regime_at_signal": (row.get("trigger") or {}).get("regime_at_signal") or None}


def _pct(values: list, q: float):
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q * len(s)))], 1)


def field_completeness(by_lane: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict:
    lanes, tot = {}, {"closed": 0, **{f: 0 for f in FIELDS}}
    for lane, rows in by_lane.items():
        closed = [r for r in rows if r.get("status") == "CLOSED"]
        counts = {f: 0 for f in FIELDS}
        missing_ids = {f: [] for f in FIELDS}
        for r in closed:
            vals = field_values(r)
            for f in FIELDS:
                if vals[f] is not None:
                    counts[f] += 1
                elif len(missing_ids[f]) < 5:
                    missing_ids[f].append(r.get("trade_id"))
        n = len(closed)
        lanes[lane] = {"closed": n, "present": counts,
                       "coverage": {f: (round(counts[f] / n, 4) if n else None) for f in FIELDS},
                       "sample_missing_trade_ids": {f: v for f, v in missing_ids.items() if v}}
        tot["closed"] += n
        for f in FIELDS:
            tot[f] += counts[f]
    n = tot["closed"]
    return {"fields": list(FIELDS), "lanes": lanes,
            "total": {"closed": n, "coverage": {f: (round(tot[f] / n, 4) if n else None) for f in FIELDS}},
            "notes": {"signal_to_fill_ms": "timing.signal_to_fill_ms (falls back to *_sec / legacy *_s x1000, e.g. retired DISLOC history)",
                      "exit_bid": "exit.exit_bid (touch at close); main-trade rows from 12 Oct ~13:00 AEDT (#587/#599)",
                      "regime_at_signal": "trigger.regime_at_signal; stamped at order time from 12 Oct data-gaps deploy"}}


def signal_to_fill_ms_by_lane(by_lane: Mapping[str, Iterable[Mapping[str, Any]]], *, since: float) -> dict:
    out = {}
    for lane, rows in by_lane.items():
        vals = []
        for r in rows:
            if r.get("status") != "CLOSED":
                continue
            if (_num((r.get("exit") or {}).get("close_ts")) or 0.0) < since:
                continue
            ms = signal_to_fill_ms(r)
            if ms is not None and ms >= 0:
                vals.append(ms)
        out[lane] = {"n": len(vals), "p50_ms": _pct(vals, 0.5), "p95_ms": _pct(vals, 0.95),
                     "max_ms": round(max(vals), 1) if vals else None, "window_h": 24,
                     "basis": "persisted closed rows (event_timestamps)"}
    return out
