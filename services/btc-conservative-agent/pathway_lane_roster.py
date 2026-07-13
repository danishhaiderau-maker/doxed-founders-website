"""
Quality pathway roster -- v11.8-sr-micro-static-ab (2026-07-12).

3-lane paper-research stack:
  - CONTINUOUS (benchmark, T+0s AI, paper research)
  - TYPE_B_HUNTER_V1 (research candidate, T+60s AI, shadow)
  - SR_MICRO_TILE_V2_STATIC (A/B resting limit, no chase, shadow)
  + AI_SCAN (internal scanner, no orders)

S/R V1 and full-chase S/R V2 are DATA_RETIRED: their historical outcomes remain
readable, but they are not dashboard tiles and cannot spawn work.
"""
from __future__ import annotations

from combo_pathway_config import (
    COMPARISON_BENCHMARK_LANE,
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

RETIRED_PATHWAY_LANES = frozenset()

DATA_RETIRED_PATHWAY_LANES = frozenset((
    "SR_MICRO_TILE_V1",
    "SR_MICRO_TILE_V2",
))

PATHWAY_SHADOW_COLLECTING_ENABLED = False

ROSTER_PHASE = "v11.8-sr-micro-static-ab"
ROSTER_NOTES = (
    "3-lane paper-research stack (CONTINUOUS + TYPE_B_HUNTER_V1 + "
    "SR_MICRO_TILE_V2_STATIC). Static S/R remains in probation, never chases, "
    "S/R V1 and full-chase V2 are historical-only."
)

ANALYZER_COMPARE_LANES = (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
    RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC,
    RESEARCH_LANE_AI_SCAN,
)


def is_ai_focused_lane(lane: str) -> bool:
    """Primary dashboard filter: three-lane research stack only.

    Legacy COMBO/EDGE/CHASE/shadow lanes permanently retired.
    Historical CSV data preserved for reference only.
    """
    u = str(lane or "").upper().strip()
    return u in DASHBOARD_PRIMARY_LANES


# Back-compat alias used by older dashboard imports.
DASHBOARD_PRIMARY_LANES_FILTERED = DASHBOARD_PRIMARY_LANES
