"""
Quality pathway roster — single list for live tiles, retired lanes, and dashboard scope.

Session evidence (2026-06-21): Replay + 604 Chase carry profit; Direct combos, Recovery Monster,
strict Type-B, and zero-fill shadow lanes add noise without edge.
"""
from __future__ import annotations

from combo_pathway_config import (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_COMBO_604_SP4_CHASE,
    RESEARCH_LANE_COMBO_604_SP4_DIRECT,
    RESEARCH_LANE_COMBO_65_SP5_CHASE,
    RESEARCH_LANE_COMBO_65_SP5_DIRECT,
)
from experimental_pathway_config import (
    RESEARCH_LANE_AI_DISAGREEMENT_ALPHA,
    RESEARCH_LANE_AI_DISAGREEMENT_REPLAY,
    RESEARCH_LANE_RECOVERY_MONSTER_V1,
    RESEARCH_LANE_TYPE_B_PREDICTOR_V1,
)
from legacy_pathway_config import SHADOW_COLLECTING_LANES

RESEARCH_LANE_EXTREME_EDGE = "EXTREME_EDGE"
RESEARCH_LANE_EDGE_PLUS_STACK = "EDGE_PLUS_STACK"
RESEARCH_LANE_AI_SCAN = "AI_SCAN"

# Four live Pathway Lab tiles — benchmark + two chase combos + replay disagreement.
LIVE_PATHWAY_TILE_ORDER = (
    RESEARCH_LANE_COMBO_65_SP5_CHASE,
    RESEARCH_LANE_COMBO_604_SP4_CHASE,
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_AI_DISAGREEMENT_REPLAY,
)

DASHBOARD_PATHWAY_LANES = LIVE_PATHWAY_TILE_ORDER

RETIRED_PATHWAY_LANES = frozenset({
    RESEARCH_LANE_EXTREME_EDGE,
    RESEARCH_LANE_EDGE_PLUS_STACK,
    RESEARCH_LANE_COMBO_65_SP5_DIRECT,
    RESEARCH_LANE_COMBO_604_SP4_DIRECT,
    RESEARCH_LANE_RECOVERY_MONSTER_V1,
    RESEARCH_LANE_TYPE_B_PREDICTOR_V1,
    RESEARCH_LANE_AI_DISAGREEMENT_ALPHA,
})

DATA_RETIRED_PATHWAY_LANES = frozenset(SHADOW_COLLECTING_LANES)

# Shadow sim lanes — virtual fills for legacy attribution (no live orders).
PATHWAY_SHADOW_COLLECTING_ENABLED = True

ROSTER_PHASE = "quality-4-tiles-shadow-on-2026-06-22"
ROSTER_NOTES = (
    "Live: 65+ Chase, 604 Chase, CONTINUOUS benchmark, AI Disagreement Replay. "
    "Retired: Direct combos, Recovery Monster, Type-B strict, AI-disagreement alpha, edge stacks. "
    "Shadow collecting ON for 8 legacy lanes (virtual sim, no orders)."
)

# Every lane still defined in code — analyzer compares all of these (session + full CSV).
# Remove a lane from this tuple only when deleting it from the bot scripts.
ANALYZER_COMPARE_LANES = (
    RESEARCH_LANE_COMBO_65_SP5_CHASE,
    RESEARCH_LANE_COMBO_604_SP4_CHASE,
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_AI_DISAGREEMENT_REPLAY,
    RESEARCH_LANE_COMBO_65_SP5_DIRECT,
    RESEARCH_LANE_COMBO_604_SP4_DIRECT,
    RESEARCH_LANE_RECOVERY_MONSTER_V1,
    RESEARCH_LANE_TYPE_B_PREDICTOR_V1,
    RESEARCH_LANE_AI_DISAGREEMENT_ALPHA,
    RESEARCH_LANE_EXTREME_EDGE,
    RESEARCH_LANE_EDGE_PLUS_STACK,
    RESEARCH_LANE_AI_SCAN,
) + tuple(SHADOW_COLLECTING_LANES)
