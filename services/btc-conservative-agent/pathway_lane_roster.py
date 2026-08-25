"""Derived active roster; combo_pathway_config is the single source of truth."""
from __future__ import annotations

from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    RETIRED_TILE_LANES,
    validate_tile_registry,
)

RESEARCH_LANE_AI_SCAN = "AI_SCAN"

_REGISTRY_DEFECTS = validate_tile_registry()
if _REGISTRY_DEFECTS:
    raise RuntimeError("Invalid tile registry: " + "; ".join(_REGISTRY_DEFECTS))

DASHBOARD_PRIMARY_LANES = tuple(ACTIVE_TILE_ORDER)
LIVE_PATHWAY_TILE_ORDER = DASHBOARD_PRIMARY_LANES
DASHBOARD_PATHWAY_LANES = DASHBOARD_PRIMARY_LANES
RETIRED_PATHWAY_LANES = frozenset(RETIRED_TILE_LANES)
DATA_RETIRED_PATHWAY_LANES = frozenset(RETIRED_TILE_LANES)
PATHWAY_SHADOW_COLLECTING_ENABLED = False

ROSTER_PHASE = "v31-four-tile-protected-patient-chase"
ROSTER_NOTES = (
    "Active stack is derived from ACTIVE_TILE_REGISTRY: Continuous, Patient Chase, "
    "Protected Static, and Protected Regime-Adaptive; one shared direction call and "
    "four independent paper lifecycles."
)

ANALYZER_COMPARE_LANES = DASHBOARD_PRIMARY_LANES


def is_ai_focused_lane(lane: str) -> bool:
    return str(lane or "").upper().strip() in DASHBOARD_PRIMARY_LANES


DASHBOARD_PRIMARY_LANES_FILTERED = DASHBOARD_PRIMARY_LANES
