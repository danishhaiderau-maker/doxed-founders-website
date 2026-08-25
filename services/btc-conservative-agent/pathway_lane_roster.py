"""Authoritative two-lane roster for the active paper research stack."""
from __future__ import annotations

from combo_pathway_config import (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_OFFSET_029_ATR_TP_25,
)

RESEARCH_LANE_AI_SCAN = "AI_SCAN"

DASHBOARD_PRIMARY_LANES = (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_OFFSET_029_ATR_TP_25,
)
LIVE_PATHWAY_TILE_ORDER = DASHBOARD_PRIMARY_LANES
DASHBOARD_PATHWAY_LANES = DASHBOARD_PRIMARY_LANES
RETIRED_PATHWAY_LANES = frozenset()
DATA_RETIRED_PATHWAY_LANES = frozenset()
PATHWAY_SHADOW_COLLECTING_ENABLED = False

ROSTER_PHASE = "v31-two-tile-paper"
ROSTER_NOTES = (
    "Active stack: CONTINUOUS benchmark plus OFFSET_029_ATR_TP_25 Patient Chase; "
    "one shared direction call, two independent paper lifecycles, no retired lanes."
)

ANALYZER_COMPARE_LANES = DASHBOARD_PRIMARY_LANES


def is_ai_focused_lane(lane: str) -> bool:
    return str(lane or "").upper().strip() in DASHBOARD_PRIMARY_LANES


DASHBOARD_PRIMARY_LANES_FILTERED = DASHBOARD_PRIMARY_LANES
