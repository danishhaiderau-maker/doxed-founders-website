"""
Legacy pathway lanes — shadow-only data collection (no live orders).
Hidden from Pathway Lab tiles; simulated fills stored in shadow_lane_outcome.jsonl.
"""
from __future__ import annotations

RESEARCH_LANE_HIGH_EDGE_RUNNER = "HIGH_EDGE_RUNNER"
RESEARCH_LANE_SHADOW_RUNNER = "SHADOW_RUNNER"
RESEARCH_LANE_EDGE_ALPHA_4 = "EDGE_ALPHA_4"
RESEARCH_LANE_TYPE_B_HUNTER = "TYPE_B_HUNTER"
RESEARCH_LANE_SHORT_BEAR_ALPHA = "SHORT_BEAR_ALPHA"
RESEARCH_LANE_AI_60_65_ALPHA = "AI_60_65_ALPHA"
RESEARCH_LANE_URGENT_CHASE_ALPHA = "URGENT_CHASE_ALPHA"
RESEARCH_LANE_CHASE_3PLUS_ALPHA = "CHASE_3PLUS_ALPHA"

PATHWAY_STATUS_SHADOW_COLLECTING = "SHADOW_COLLECTING"

SHADOW_COLLECTING_LANES = (
    RESEARCH_LANE_HIGH_EDGE_RUNNER,
    RESEARCH_LANE_SHADOW_RUNNER,
    RESEARCH_LANE_EDGE_ALPHA_4,
    RESEARCH_LANE_TYPE_B_HUNTER,
    RESEARCH_LANE_SHORT_BEAR_ALPHA,
    RESEARCH_LANE_AI_60_65_ALPHA,
    RESEARCH_LANE_URGENT_CHASE_ALPHA,
    RESEARCH_LANE_CHASE_3PLUS_ALPHA,
)

SHADOW_COLLECTING_LANE_LABELS = {
    RESEARCH_LANE_HIGH_EDGE_RUNNER: "High Edge Runner",
    RESEARCH_LANE_SHADOW_RUNNER: "Shadow Runner",
    RESEARCH_LANE_EDGE_ALPHA_4: "Edge Alpha 4",
    RESEARCH_LANE_TYPE_B_HUNTER: "Type B Hunter",
    RESEARCH_LANE_SHORT_BEAR_ALPHA: "Short Bear Alpha",
    RESEARCH_LANE_AI_60_65_ALPHA: "AI 60-65 Alpha",
    RESEARCH_LANE_URGENT_CHASE_ALPHA: "Urgent Chase Alpha",
    RESEARCH_LANE_CHASE_3PLUS_ALPHA: "Chase 3+ Alpha",
}

SHADOW_COLLECTING_ID_PREFIX = {
    RESEARCH_LANE_HIGH_EDGE_RUNNER: "her",
    RESEARCH_LANE_SHADOW_RUNNER: "shrun",
    RESEARCH_LANE_EDGE_ALPHA_4: "ea4",
    RESEARCH_LANE_TYPE_B_HUNTER: "tbh",
    RESEARCH_LANE_SHORT_BEAR_ALPHA: "sba",
    RESEARCH_LANE_AI_60_65_ALPHA: "a6065",
    RESEARCH_LANE_URGENT_CHASE_ALPHA: "ucha",
    RESEARCH_LANE_CHASE_3PLUS_ALPHA: "c3pa",
}


def shadow_collecting_toggle_defaults() -> dict:
    return {lane: False for lane in SHADOW_COLLECTING_LANES}


def is_shadow_collecting_lane(lane: str) -> bool:
    return str(lane or "").upper() in SHADOW_COLLECTING_LANES


def _ai_prob(ai: dict) -> int:
    try:
        return int((ai or {}).get("win_prob") or 0)
    except (TypeError, ValueError):
        return 0


def _edge(features: dict, edge_score: float) -> float:
    try:
        if features and features.get("edge_score") is not None:
            return float(features.get("edge_score"))
    except (TypeError, ValueError):
        pass
    try:
        return float(edge_score or 0)
    except (TypeError, ValueError):
        return 0.0


def _vol_ratio(features: dict) -> float:
    try:
        return float((features or {}).get("volume_ratio") or 0)
    except (TypeError, ValueError):
        return 0.0


def _structure(features: dict) -> float:
    try:
        return float((features or {}).get("structure_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _near_support(features: dict) -> bool:
    sr = str((features or {}).get("sr_state") or "").upper()
    return sr in ("NEAR_SUPPORT", "AT_SUPPORT") or "SUPPORT" in sr


def legacy_lane_matches(
    lane: str,
    ai: dict,
    edge_score: float,
    features: dict,
    direction: str,
    spread: int = None,
) -> bool:
    lane = str(lane or "").upper()
    if not ai or str(ai.get("decision") or "").upper() != "APPROVE":
        return False
    direction = str(direction or ai.get("direction") or "").upper()
    if spread is None:
        try:
            bull = int(ai.get("bull_score") or 0)
            bear = int(ai.get("bear_score") or 0)
            spread = bull - bear if direction == "LONG" else bear - bull
        except (TypeError, ValueError):
            spread = 0
    spread = int(spread or 0)
    edge = _edge(features, edge_score)
    prob = _ai_prob(ai)
    vol = _vol_ratio(features)
    struct = _structure(features)
    bull = int(ai.get("bull_score") or 0)
    bear = int(ai.get("bear_score") or 0)

    if lane == RESEARCH_LANE_HIGH_EDGE_RUNNER:
        return edge >= 3.5 and vol >= 1.5
    if lane == RESEARCH_LANE_SHADOW_RUNNER:
        return edge >= 3.5
    if lane == RESEARCH_LANE_EDGE_ALPHA_4:
        return edge >= 4.0 and _near_support(features)
    if lane == RESEARCH_LANE_TYPE_B_HUNTER:
        return edge >= 3.5 and vol > 1.2 and _near_support(features) and 50 <= prob < 55
    if lane == RESEARCH_LANE_SHORT_BEAR_ALPHA:
        return (
            direction == "SHORT"
            and struct <= -3
            and bear > bull
            and spread >= 3
            and prob >= 55
        )
    if lane == RESEARCH_LANE_AI_60_65_ALPHA:
        return 60 <= prob < 65 and spread >= 3 and edge >= 3
    if lane in (RESEARCH_LANE_URGENT_CHASE_ALPHA, RESEARCH_LANE_CHASE_3PLUS_ALPHA):
        return True
    return False
