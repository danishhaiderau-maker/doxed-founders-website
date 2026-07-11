"""
Quality pathway roster -- v11.6-dual-research-candidates (2026-07-11).

3-lane research stack:
  - CONTINUOUS (benchmark, T+0s AI, live orders)
  - TYPE_B_HUNTER_V1 (research candidate, T+60s AI, shadow)
  - SR_MICRO_TILE_V1 (research candidate, T+120s AI, shadow)
  + AI_SCAN (internal scanner, no orders)

All legacy lanes (SL_AVOIDANCE, SIZED_CONTINUOUS, COMBO variants, shadow collecting,
experimental) retired 2026-07-11 -- none outperformed CONTINUOUS.
Historical CSV data preserved; lane definitions purged from codebase.
"""
from __future__ import annotations

from combo_pathway_config import (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
    RESEARCH_LANE_SR_MICRO_TILE_V1,
)

RESEARCH_LANE_AI_SCAN = "AI_SCAN"

DASHBOARD_PRIMARY_LANES = (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
    RESEARCH_LANE_SR_MICRO_TILE_V1,
)

LIVE_PATHWAY_TILE_ORDER = (
    COMPARISON_BENCHMARK_LANE,
)

DASHBOARD_PATHWAY_LANES = LIVE_PATHWAY_TILE_ORDER

RETIRED_PATHWAY_LANES = frozenset()

DATA_RETIRED_PATHWAY_LANES = frozenset()

PATHWAY_SHADOW_COLLECTING_ENABLED = False

ROSTER_PHASE = "v11.6-dual-research-candidates"
ROSTER_NOTES = (
    "3-lane research stack (CONTINUOUS + TYPE_B_HUNTER_V1 + SR_MICRO_TILE_V1). "
    "All legacy lanes permanently retired 2026-07-11. "
    "Each tile has independent AI prompt and staggered cadence (T+0/60/120s)."
)

ANALYZER_COMPARE_LANES = (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
    RESEARCH_LANE_SR_MICRO_TILE_V1,
    RESEARCH_LANE_AI_SCAN,
)


def is_ai_focused_lane(lane: str) -> bool:
    """Primary dashboard filter: 3-lane research stack only.

    Legacy COMBO/EDGE/CHASE/shadow lanes permanently retired.
    Historical CSV data preserved for reference only.
    """
    u = str(lane or "").upper().strip()
    return u in DASHBOARD_PRIMARY_LANES


# Back-compat alias used by older dashboard imports.
DASHBOARD_PRIMARY_LANES_FILTERED = DASHBOARD_PRIMARY_LANES
