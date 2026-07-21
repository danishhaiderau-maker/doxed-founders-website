"""
Quality pathway roster -- v14-paused-shadow-executor-watchdog-adx-v3 (2026-07-21).

Active paper-research stack:
  - CONTINUOUS (benchmark, T+0s AI, paper research)
  - TYPE_B_HUNTER_V1 (research candidate, shared 3-minute direction AI)
    shared AI direction; ADX-flipped, volume-inverted, regime-aware fixed policy.
  - SR_MICRO_TILE_V2_STATIC (dual $20 resting limits, one per direction)
  + AI_SCAN (internal scanner, no orders)

Retired 2026-07-16 (v12 overhaul):
  SR_MICRO_TILE_V1 -- failed experiment (47% WR, negative PnL).
  Code file (sr_micro_tile_v1.py) preserved for reference.

S/R V2 (full chase) is DATA_RETIRED: historical outcomes remain readable,
but it is not a dashboard tile and cannot spawn work.
"""
from __future__ import annotations

from combo_pathway_config import (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_SR_MICRO_TILE_V1,
    RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
)

RESEARCH_LANE_AI_SCAN = "AI_SCAN"

DASHBOARD_PRIMARY_LANES = (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
    RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC,
)

LIVE_PATHWAY_TILE_ORDER = DASHBOARD_PRIMARY_LANES
DASHBOARD_PATHWAY_LANES = DASHBOARD_PRIMARY_LANES

# Lanes formally retired but whose spec entries remain in COMBO_LANE_SPECS for
# CSV / historical-data decoding. They are marked is_legacy=True so every
# execution / dashboard gate rejects them.
RETIRED_PATHWAY_LANES = frozenset((
    RESEARCH_LANE_SR_MICRO_TILE_V1,  # retired 2026-07-16 v12 overhaul (47% WR, negative PnL)
))

DATA_RETIRED_PATHWAY_LANES = frozenset((
    "SR_MICRO_TILE_V2",  # full-chase variant superseded by V2_STATIC; CSV preserved
))

PATHWAY_SHADOW_COLLECTING_ENABLED = False

ROSTER_PHASE = "v13-tile2-dual-leg"
ROSTER_NOTES = (
    "Active research stack: CONTINUOUS + TYPE_B_HUNTER_V1 (fixed policy) + "
    "SR_MICRO_TILE_V2_STATIC dual-leg. SR_MICRO_TILE_V1 retired 2026-07-16 (47% WR, "
    "negative PnL); code preserved. Full-chase S/R V2 historical-only."
)

ANALYZER_COMPARE_LANES = (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
    RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC,
    RESEARCH_LANE_AI_SCAN,
)


def is_ai_focused_lane(lane: str) -> bool:
    """Primary dashboard filter: active research stack only.

    SR_MICRO_TILE_V1 and all legacy COMBO/EDGE/CHASE/shadow lanes permanently retired.
    Historical CSV data preserved for reference only.
    """
    u = str(lane or "").upper().strip()
    return u in DASHBOARD_PRIMARY_LANES


# Back-compat alias used by older dashboard imports.
DASHBOARD_PRIMARY_LANES_FILTERED = DASHBOARD_PRIMARY_LANES
