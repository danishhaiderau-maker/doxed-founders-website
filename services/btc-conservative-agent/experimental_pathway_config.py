"""
Experimental Pathway Lab tiles — independent research lanes (2026-06-20).

TYPE_B_PREDICTOR_V1: pre-entry fingerprint aligned to post-trade Type-B averages.
RECOVERY_MONSTER_V1: benchmark entry, wider thesis stop + runner ladder.
AI_DISAGREEMENT_ALPHA / AI_DISAGREEMENT_REPLAY: AI vs replay-model disagreement cohorts.
"""
from __future__ import annotations

RESEARCH_LANE_TYPE_B_PREDICTOR_V1 = "TYPE_B_PREDICTOR_V1"
RESEARCH_LANE_RECOVERY_MONSTER_V1 = "RECOVERY_MONSTER_V1"
RESEARCH_LANE_AI_DISAGREEMENT_ALPHA = "AI_DISAGREEMENT_ALPHA"
RESEARCH_LANE_AI_DISAGREEMENT_REPLAY = "AI_DISAGREEMENT_REPLAY"

EXPERIMENTAL_EXECUTION_LANES = (RESEARCH_LANE_AI_DISAGREEMENT_REPLAY,)

EXPERIMENTAL_TILE_DISPLAY_ORDER = (RESEARCH_LANE_AI_DISAGREEMENT_REPLAY,)

EXPERIMENTAL_LANE_LABELS = {
    RESEARCH_LANE_TYPE_B_PREDICTOR_V1: "Type B Predictor v1",
    RESEARCH_LANE_RECOVERY_MONSTER_V1: "Recovery Monster v1",
    RESEARCH_LANE_AI_DISAGREEMENT_ALPHA: "AI Disagreement · AI Wins",
    RESEARCH_LANE_AI_DISAGREEMENT_REPLAY: "AI Disagreement · Replay Wins",
}

EXPERIMENTAL_LANE_SPECS = {
    RESEARCH_LANE_TYPE_B_PREDICTOR_V1: {
        "label": "Type B Predictor v1",
        "subtitle": "AI≥60 · spread≥4 · ADX≥20 · vol≥1.8 · struct≤-3 · Scenario C exit",
        "hypothesis": "Pre-entry features matching Type-B averages predict outsized MFE before the trade runs.",
        "research_question": "Can we identify Type-B runners at entry without post-trade MFE labels?",
        "id_prefix": "tbv1",
        "spawn_on": "APPROVE",
        "entry_filters": {
            "ai_min": 60,
            "spread_min": 4,
            "adx_min": 20.0,
            "volume_ratio_min": 1.8,
            "structure_max": -3.0,
        },
        "exit_profile": "SCENARIO_C",
    },
    RESEARCH_LANE_RECOVERY_MONSTER_V1: {
        "label": "Recovery Monster v1",
        "subtitle": "Benchmark entry · thesis −40% · ladder 18→14 · MFE protect 2%",
        "hypothesis": "Wide thesis stop + runner ladder captures recoveries that −12% fast-cut kills.",
        "research_question": "Are exits (not entries) the main drag vs replay-optimal PnL?",
        "id_prefix": "rcv1",
        "spawn_on": "APPROVE",
        "entry_filters": "benchmark_all_approve",
        "exit_profile": "RECOVERY_MONSTER",
    },
    RESEARCH_LANE_AI_DISAGREEMENT_ALPHA: {
        "label": "AI Disagreement · AI Wins",
        "subtitle": "AI APPROVE + replay REJECT · benchmark entry · Scenario C exit",
        "hypothesis": "When AI approves but replay rejects, AI directional read may still carry edge.",
        "research_question": "Does AI outperform the deterministic replay scorecard on disagreement?",
        "id_prefix": "aida",
        "spawn_on": "APPROVE",
        "entry_filters": "ai_approve_replay_reject",
        "exit_profile": "SCENARIO_C",
    },
    RESEARCH_LANE_AI_DISAGREEMENT_REPLAY: {
        "label": "AI Disagreement · Replay Wins",
        "subtitle": "AI REJECT + replay APPROVE · benchmark entry · Scenario C exit",
        "hypothesis": "Replay-approved signals that AI rejected are hidden alpha.",
        "research_question": "Does the replay model find winners the LLM skips?",
        "id_prefix": "rida",
        "spawn_on": "REJECT",
        "entry_filters": "ai_reject_replay_approve",
        "exit_profile": "SCENARIO_C",
    },
}

# Type-B predictor thresholds (from analyzer TYPE_B cohort averages)
TYPE_B_PREDICTOR_MIN_AI = 60
TYPE_B_PREDICTOR_MIN_SPREAD = 4
TYPE_B_PREDICTOR_MIN_ADX = 20.0
TYPE_B_PREDICTOR_MIN_VOL = 1.8
TYPE_B_PREDICTOR_MAX_STRUCT = -3.0

RECOVERY_MONSTER_THESIS_PCT = -40.0
RECOVERY_MONSTER_MFE_PROTECT_PCT = 2.0
RECOVERY_MONSTER_LADDER = ((18, 14), (25, 18), (40, 28), (55, 38))
RECOVERY_MONSTER_PROFILE_ID = "RECOVERY_MONSTER_V1"


def experimental_toggle_defaults() -> dict:
    defaults = {lane: True for lane in EXPERIMENTAL_EXECUTION_LANES}
    defaults.update({
        RESEARCH_LANE_TYPE_B_PREDICTOR_V1: False,
        RESEARCH_LANE_RECOVERY_MONSTER_V1: False,
        RESEARCH_LANE_AI_DISAGREEMENT_ALPHA: False,
    })
    return defaults


def is_experimental_execution_lane(lane: str) -> bool:
    return str(lane or "").upper() in EXPERIMENTAL_LANE_SPECS


def _ctx_adx(ctx: dict) -> float:
    mc = (ctx or {}).get("market_context") or {}
    ts = mc.get("trend_strength") or {}
    try:
        return float(ts.get("adx") or 0)
    except (TypeError, ValueError):
        return 0.0


def _ctx_structure(ctx: dict) -> float:
    mc = (ctx or {}).get("market_context") or {}
    ms = mc.get("market_structure") or {}
    try:
        return float(ms.get("structure_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def type_b_predictor_matches(
    ai: dict,
    final_direction: str,
    spread: int,
    ctx: dict,
    features: dict = None,
) -> bool:
    if not ai:
        return False
    try:
        prob = int(ai.get("win_prob") or 0)
    except (TypeError, ValueError):
        prob = 0
    if prob < TYPE_B_PREDICTOR_MIN_AI:
        return False
    if int(spread or 0) < TYPE_B_PREDICTOR_MIN_SPREAD:
        return False
    if _ctx_adx(ctx) < TYPE_B_PREDICTOR_MIN_ADX:
        return False
    if _ctx_structure(ctx) > TYPE_B_PREDICTOR_MAX_STRUCT:
        return False
    try:
        vol = float((features or {}).get("volume_ratio") or 0)
    except (TypeError, ValueError):
        vol = 0.0
    return vol >= TYPE_B_PREDICTOR_MIN_VOL


def ai_disagreement_alpha_matches(ai: dict, replay_eval: dict) -> bool:
    """AI APPROVE but replay model would not approve."""
    if str((ai or {}).get("decision") or "").upper() != "APPROVE":
        return False
    return not bool((replay_eval or {}).get("replay_approve"))


def ai_disagreement_replay_matches(ai: dict, replay_eval: dict) -> bool:
    """AI rejected (incl. soft) but replay model approves."""
    decision = str((ai or {}).get("decision") or "").upper()
    if decision in ("APPROVE", "STRONG_APPROVE", "SOFT_APPROVE"):
        return False
    return bool((replay_eval or {}).get("replay_approve"))
