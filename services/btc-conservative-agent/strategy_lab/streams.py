"""Mirror stream inventory: what exists, how fresh it is, and who consumes it.

``ANALYZER_USAGE`` is the audited consumer map (2026-10-02). It is static on
purpose: it documents which analyzer path reads each stream and whether that
path sees closed rotations, so the export shows agents which streams are
analysed, partially analysed or ignored. ``continuous`` streams are written
every cycle by a healthy collector, so an old active file is a failure;
event-driven streams only grow when tiles trade or orders move, so an old file
is reported as IDLE. Worker d9f889db owns collector-side data-health panels;
when its payload lands, ``stream_inventory`` should merge it instead of
re-deriving collector health here.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from strategy_lab.tape import generations

FULL = "FULL"                      # all generations read by an analyzer path
ACTIVE_ONLY = "ACTIVE_FILE_ONLY"   # closed rotations silently ignored
STRATEGY_LAB = "STRATEGY_LAB"      # read by the strategy lab (all generations)
HEALTH_ONLY = "HEALTH_ONLY"        # counted for collection health, not analysed
DASHBOARD_ONLY = "DASHBOARD_ONLY"
IGNORED = "IGNORED"

ANALYZER_USAGE = {
    "market_microstructure_1s.jsonl": {
        "usage": STRATEGY_LAB, "continuous": True,
        "consumers": ["strategy_lab (all rotations, cached)", "lead_lag_report", "fill_time_guard_counterfactual"],
        "note": "fill_time_guard read the active file only; now windowed across rotations"},
    "cross_venue_tape_1m.jsonl": {
        "usage": STRATEGY_LAB, "continuous": True, "consumers": ["lead_lag_report", "strategy_lab XVL hypotheses"],
        "note": "lead_lag tests one pre-registered rule with BH only; family-wise null now in strategy_lab"},
    "ai_tranche_log.csv": {
        "usage": FULL, "continuous": True, "consumers": ["load_data ai_log", "strategy_lab AI-call hypotheses"]},
    "ai_input_log.jsonl": {
        "usage": FULL, "continuous": True, "consumers": ["fill_time_guard_counterfactual", "strategy_lab funding"]},
    "ai_shadow_challengers.jsonl": {"usage": FULL, "continuous": True, "consumers": ["ai_challenger_report"]},
    "ai_shadow_compact_prompt.jsonl": {"usage": FULL, "continuous": False, "consumers": ["ai_challenger_report"]},
    "ai_shadow_regime_prompt.jsonl": {"usage": FULL, "continuous": False, "consumers": ["decision_model_report H8"]},
    "decision_feature_snapshots.jsonl": {
        "usage": FULL, "continuous": True, "consumers": ["decision_model_report (health, H8 labels, H9)"]},
    "decisions_3factor.csv": {"usage": FULL, "continuous": True, "consumers": ["load_data decisions (session filter)"]},
    "cycle_3m_universe.jsonl": {"usage": FULL, "continuous": True, "consumers": ["research modules"]},
    "research_events_v22.jsonl": {
        "usage": STRATEGY_LAB, "continuous": True,
        "consumers": ["research_dashboard event views", "stream_studies outcome x observation mix (incremental index)"],
        "note": "fill-time ATR receipts are not copied into the trade ledger, limiting ATR-exit parity"},
    "trades_3factor.csv": {
        "usage": FULL, "continuous": False,
        "consumers": ["load_data trades (epoch cohort)", "strategy_lab live-fill parity + correlation"]},
    "signal_replay.jsonl": {
        "usage": FULL, "continuous": False,
        "consumers": ["_load_jsonl_replays (all rotations)", "first_15m_outcome_report"],
        "note": "first_15m_outcome_report read the active file only; now uses the rotation-aware replay cache"},
    "source_order_market_evidence.jsonl": {
        "usage": FULL, "continuous": False,
        "consumers": ["trade evidence index (all rotations)", "fill_time_guard executable ids"],
        "note": "fill_time_guard read the active file only (~1% of rows); now reads every rotation"},
    "order_multiverse.jsonl": {
        "usage": HEALTH_ONLY, "continuous": False, "consumers": ["multiverse_collection_health"],
        "note": "health read the report dir instead of the data dir (always 0 rows); fixed and epoch-scoped"},
    "order_multiverse_entry_grid.jsonl": {
        "usage": HEALTH_ONLY, "continuous": False, "consumers": ["multiverse_collection_health"]},
    "chase_offset_touch_grid.jsonl": {
        "usage": FULL, "continuous": False,
        "consumers": ["compressed shadow schedule rows", "multiverse health",
                      "shadow_chase_bucket_v1 chase-bucket outcomes (Fly chase panel)"],
        "schemas": ["chase_offset_touch_grid_v1", "compressed_chase_shadow_v1",
                    "compressed_chase_arm_receipt_v1", "shadow_chase_bucket_v1"],
        "note": "compressed shadow rows read the active file only; now include the closed rotation. "
                "shadow_chase_bucket_v1 is one SHADOW_ONLY outcome per compressed shadow order "
                "(chase count, fill, time-to-fill, MFE/MAE, net bp at a 30 min mark)"},
    "post_exit_replay.jsonl": {
        "usage": STRATEGY_LAB, "continuous": False, "consumers": ["stream_studies exit-timing regret (all rotations)"]},
    "opportunity_capture.jsonl": {"usage": FULL, "continuous": False, "consumers": ["opportunity capture reports"]},
    "lane_opportunity_capture.jsonl": {"usage": FULL, "continuous": False,
                                       "consumers": ["lane_opportunity_capture_report"]},
    "signal_snapshot.jsonl": {"usage": FULL, "continuous": False, "consumers": ["entry gate sweeps"]},
    "execution_funnel.jsonl": {"usage": FULL, "continuous": False, "consumers": ["chase attribution"]},
    "taker_signal_counterfactuals.jsonl": {"usage": STRATEGY_LAB, "continuous": False,
                                           "consumers": ["stream_studies taker counterfactual EV"]},
    "fill_markouts.jsonl": {"usage": STRATEGY_LAB, "continuous": False,
                            "consumers": ["stream_studies post-fill markout curves"]},
    "shadow_exit_paths.jsonl": {"usage": FULL, "continuous": False,
                                "consumers": ["shadow_exit_report (all rotations, Shadow exits section)"],
                                "schemas": ["shadow_exit_path_v1"]},
    "indicator_bars_v1.jsonl": {
        "usage": FULL, "continuous": True,
        "consumers": ["indicator_forward_scorer (all rotations)", "Indicator Edge dashboard section"],
        "schemas": ["indicator_bars_v1"],
        "note": "one row per closed 3-minute bar; scored forward only against the frozen pre-registration"},
}


def stream_inventory(data_dir: str, now: Optional[float] = None, stale_after_sec: int = 3600) -> list:
    now = float(now if now is not None else time.time())
    rows = []
    for name, meta in ANALYZER_USAGE.items():
        path = os.path.join(data_dir, name)
        gens = generations(path)
        sizes = []
        for g in gens:
            try:
                sizes.append(os.path.getsize(g))
            except OSError:
                pass
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            age = None
        old = age is None or age > stale_after_sec
        if not gens:
            status = "MISSING"
        elif old:
            status = "STALE" if meta.get("continuous") else "IDLE"
        else:
            status = "OK"
        rows.append({
            "stream": name, "status": status, "continuous": bool(meta.get("continuous")),
            "analyzer_usage": meta["usage"], "consumers": "; ".join(meta.get("consumers") or []),
            "note": meta.get("note", ""), "files": len(gens),
            "rotations": max(len(gens) - (1 if os.path.isfile(path) else 0), 0),
            "bytes": int(sum(sizes)), "active_age_sec": round(age, 1) if age is not None else None,
        })
    return rows
