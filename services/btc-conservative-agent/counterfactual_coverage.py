"""Compact counterfactual coverage from every shadow/snapshot opportunity.

Shadow_outcome is not a substitute for a disposition. Missing evidence is
UNKNOWN, never $0. Fat observation arrays are replaced with content-addressed
refs. Analyzer-only — does not change live knobs or wipe the epoch.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from policy_research_engine import (
    UNKNOWN_CANNOT_COLLECT,
    canonical_opportunity,
    compact_horizon_receipts,
    content_addressed_ref,
    path_gaps,
)

SCHEMA = "compact_counterfactual_v1"


def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def stitch_global_tape(replays):
    """Union side-correct BBO ticks across overlapping replay buffers."""
    tape = []
    for replay in (replays or {}).values():
        if not isinstance(replay, dict):
            continue
        direction = str(replay.get("direction") or "LONG").upper()
        side = "best_bid" if direction == "LONG" else "best_ask"
        for tick in replay.get("ticks") or []:
            if not isinstance(tick, dict):
                continue
            ts = _num(tick.get("observed_ts"))
            t_rel = _num(tick.get("t"))
            price = _num(tick.get(side) or tick.get("price"))
            if price is None:
                continue
            tape.append({
                "observed_ts": ts,
                "t": t_rel,
                "best_bid": tick.get("best_bid"),
                "best_ask": tick.get("best_ask"),
                "price": price,
                "source_trade_id": replay.get("trade_id"),
            })
    tape.sort(key=lambda row: (row.get("observed_ts") or 0, row.get("t") or 0))
    return tape


def ticks_for_window(tape, origin_ts, window_sec=7200):
    if origin_ts is None:
        return []
    out = []
    for tick in tape:
        ts = tick.get("observed_ts")
        if ts is None:
            continue
        if origin_ts <= ts <= origin_ts + window_sec:
            rel = ts - origin_ts
            out.append({**tick, "t": rel})
    return out


def compact_cf_row(row, ticks=None, exclusion=None, observations=None, paper_trade=None):
    """One compact disposition per opportunity. Never embeds thousands of ticks."""
    row = row if isinstance(row, dict) else {}
    ticks = ticks or row.get("ticks") or []
    canon = canonical_opportunity(row, ticks=ticks, observations=observations, paper_trade=paper_trade)
    gaps = canon.get("path_gaps") or path_gaps(ticks)
    receipts = canon.get("horizon_receipts") or compact_horizon_receipts(ticks)
    origin = canon.get("shadow_state") or ((row.get("fill_origin") or {}) if isinstance(row.get("fill_origin"), dict) else {}).get("classification")
    if origin == "NEVER_EXECUTABLE":
        net = None
        exclusion = exclusion or "NEVER_EXECUTABLE"
    if gaps.get("censored") or not receipts.get("complete"):
        net = None
        exclusion = exclusion or gaps.get("censor_reason") or "HORIZON_RECEIPTS_INCOMPLETE"
    if canon.get("not_a_trade") is True:
        net = None
        exclusion = exclusion or "NOT_A_TRADE"
    ref = canon.get("market_path_ref") or content_addressed_ref(
        {"trade_id": row.get("trade_id"), "tick_count": len(ticks)},
        "market_path_v1",
    )
    return {
        "schema": SCHEMA,
        "trade_id": row.get("trade_id"),
        "source_state": canon.get("source_state"),
        "copy_state": canon.get("copy_state"),
        "shadow_state": origin,
        "divergence_cohort": canon.get("divergence_cohort"),
        "chase_count": (canon.get("chase") or {}).get("chase_count"),
        "origin_t_rel": row.get("origin_t_rel") or row.get("virtual_fill_t"),
        "horizon_receipts": receipts,
        "path_gaps": gaps,
        "cost": canon.get("cost"),
        "setup_dna": canon.get("setup_dna"),
        "session": canon.get("session"),
        "clock": canon.get("clock"),
        "slippage": canon.get("slippage"),
        "stop_chain": canon.get("stop_chain"),
        "microstructure": canon.get("microstructure"),
        "portfolio_path": canon.get("portfolio_path"),
        "divergence_telemetry": canon.get("divergence_telemetry"),
        "market_path_ref": ref,
        "replay_complete": canon.get("replay_complete"),
        "replay_complete_reason": canon.get("replay_complete_reason"),
        "exclusion_reason": exclusion,
        "net_pnl_usd": net,
        "not_a_trade": canon.get("not_a_trade") or bool(exclusion),
        "unknown_cannot_collect": list(UNKNOWN_CANNOT_COLLECT),
        "live_policy_change_allowed": False,
        "observations_elided": True,
    }


def cover_universe(shadow_rows, cf_rows, replays=None, paper_by_id=None):
    """Every shadow/CF id gets a compact CF or an explicit exclusion."""
    replays = replays or {}
    paper_by_id = paper_by_id or {}
    tape = stitch_global_tape(replays)
    universe = {}
    for source in (cf_rows, shadow_rows):
        for trade_id, row in (source or {}).items():
            universe.setdefault(str(trade_id), dict(row or {}))
            universe[str(trade_id)].setdefault("trade_id", trade_id)
    compact = {}
    for trade_id, row in universe.items():
        replay = replays.get(trade_id) or {}
        ticks = replay.get("ticks") or row.get("ticks") or []
        origin_ts = _num(row.get("created_ts") or replay.get("start_ts"))
        if (not ticks or len(ticks) < 2) and tape:
            stitched = ticks_for_window(tape, origin_ts)
            if stitched:
                ticks = stitched
        exclusion = None if trade_id in (cf_rows or {}) else (
            None if trade_id in (shadow_rows or {}) else "UNIVERSE_STUB"
        )
        if trade_id not in (cf_rows or {}) and trade_id in (shadow_rows or {}):
            exclusion = exclusion or "COMPACT_CF_FROM_SHADOW"
        compact[trade_id] = compact_cf_row(
            row,
            ticks=ticks,
            exclusion=exclusion,
            paper_trade=paper_by_id.get(trade_id),
        )
    classified = Counter(row.get("shadow_state") or row.get("exclusion_reason") or "UNCLASSIFIED" for row in compact.values())
    return {
        "schema": "counterfactual_coverage_v1",
        "n_shadow": len(shadow_rows or {}),
        "n_cf_in": len(cf_rows or {}),
        "n_compact_out": len(compact),
        "unclassified": classified.get("UNCLASSIFIED", 0),
        "classified": dict(classified),
        "censored": sum(1 for row in compact.values() if (row.get("path_gaps") or {}).get("censored")),
        "horizon_complete": sum(1 for row in compact.values() if (row.get("horizon_receipts") or {}).get("complete")),
        "never_zero_for_missing": True,
        "rows": compact,
        "live_policy_change_allowed": False,
    }
