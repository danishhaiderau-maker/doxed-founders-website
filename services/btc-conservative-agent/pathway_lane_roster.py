"""
Quality pathway roster -- v15-typeb-opportunity-v2 (2026-07-26).

Active paper-research stack:
  - CONTINUOUS (benchmark, T+0s AI, paper research)
  - OFFSET_029_ATR_TP_25 (shared direction, independent paper lifecycle)
  + AI_SCAN (internal scanner, no orders)

Retired 2026-08-21:
  TYPE_B_HUNTER_V1 -- active tile and execution fan-out removed; immutable
  evidence and historical analyzer decoding are preserved.

Retired 2026-07-30:
  SR_MICRO_TILE_V2_STATIC -- negative shadow expectancy; historical evidence and
  implementation retained for audit only, with all evaluation/order/relay gates off.

Retired 2026-07-16 (v12 overhaul):
  SR_MICRO_TILE_V1 -- failed experiment (47% WR, negative PnL).
  Code file (sr_micro_tile_v1.py) preserved for reference.

S/R V2 (full chase) is DATA_RETIRED: historical outcomes remain readable,
but it is not a dashboard tile and cannot spawn work.
"""
from __future__ import annotations

from combo_pathway_config import (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_OFFSET_029_ATR_TP_25,
    RESEARCH_LANE_SR_MICRO_TILE_V1,
    RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
)

RESEARCH_LANE_AI_SCAN = "AI_SCAN"

DASHBOARD_PRIMARY_LANES = (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_OFFSET_029_ATR_TP_25,
)

LIVE_PATHWAY_TILE_ORDER = DASHBOARD_PRIMARY_LANES
DASHBOARD_PATHWAY_LANES = DASHBOARD_PRIMARY_LANES

# Lanes formally retired but whose spec entries remain in COMBO_LANE_SPECS for
# CSV / historical-data decoding. They are marked is_legacy=True so every
# execution / dashboard gate rejects them.
RETIRED_PATHWAY_LANES = frozenset((
    RESEARCH_LANE_TYPE_B_HUNTER_V1,  # retired 2026-08-21; historical evidence preserved
    RESEARCH_LANE_SR_MICRO_TILE_V1,  # retired 2026-07-16 v12 overhaul (47% WR, negative PnL)
    RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC,  # retired 2026-07-30; negative shadow expectancy
))

DATA_RETIRED_PATHWAY_LANES = frozenset((
    "SR_MICRO_TILE_V2",  # full-chase variant superseded by V2_STATIC; CSV preserved
))

PATHWAY_SHADOW_COLLECTING_ENABLED = False

ROSTER_PHASE = "v16-continuous-offset029-paper"
ROSTER_NOTES = (
    "Active research stack: CONTINUOUS + OFFSET_029_ATR_TP_25, fed by one shared "
    "AI_SCAN direction call with separate paper lifecycles. TYPE_B_HUNTER_V1 retired 2026-08-21; "
    "SR_MICRO_TILE_V2_STATIC retired 2026-07-30 for negative shadow expectancy; "
    "history/code preserved. SR_MICRO_TILE_V1 and full-chase S/R V2 are historical-only."
)

ANALYZER_COMPARE_LANES = (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_OFFSET_029_ATR_TP_25,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
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
