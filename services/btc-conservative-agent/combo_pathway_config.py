"""
Trading Genome Architecture v1 — frozen execution tiles.

CONTINUOUS: permanent benchmark / scientific control group.
AI60_SP3_VIRTUAL_CHASE: research candidate (NOT production).

Retired: COMBO_604_SP4_CHASE_3PLUS — historical data preserved, no new orders.
"""
from __future__ import annotations

RESEARCH_LANE_AI_SCAN = "AI_SCAN"

RESEARCH_LANE_COMBO_65_SP5_CHASE = "COMBO_65_SP5_CHASE_3PLUS"
RESEARCH_LANE_COMBO_65_SP5_DIRECT = "COMBO_65_SP5_DIRECT"
RESEARCH_LANE_COMBO_604_SP4_CHASE = "COMBO_604_SP4_CHASE_3PLUS"
RESEARCH_LANE_COMBO_604_SP4_DIRECT = "COMBO_604_SP4_DIRECT"
RESEARCH_LANE_AI60_SP3_VIRTUAL_CHASE = "AI60_SP3_VIRTUAL_CHASE"

# Live order generation — research candidate only (+ CONTINUOUS benchmark toggle)
COMBO_EXECUTION_LANES = (RESEARCH_LANE_AI60_SP3_VIRTUAL_CHASE,)

COMBO_TILE_DISPLAY_ORDER = (RESEARCH_LANE_AI60_SP3_VIRTUAL_CHASE,)

COMBO_LANE_SPECS = {
    RESEARCH_LANE_COMBO_65_SP5_CHASE: {
        "label": "AI65+ · Spread5+ · Chase 3+",
        "subtitle": "LEGACY · retired from live execution 2026-06-23",
        "combo_key": "AI65++SPREAD5++TYPE_B+CHASE_3PLUS_ALPHA",
        "ai_min": 65,
        "ai_max": 101,
        "spread_min": 5,
        "spread_max": 99,
        "entry_mode": "CHASE_3PLUS",
        "is_benchmark": False,
        "is_primary_production": False,
        "is_research_candidate": False,
        "is_legacy": True,
        "id_prefix": "c65c",
    },
    RESEARCH_LANE_COMBO_65_SP5_DIRECT: {
        "label": "AI65+ · Spread5+ · Continuous",
        "subtitle": "LEGACY · retired",
        "combo_key": "AI65++SPREAD5++TYPE_B+CONTINUOUS",
        "ai_min": 65,
        "ai_max": 101,
        "spread_min": 5,
        "spread_max": 99,
        "entry_mode": "IMMEDIATE",
        "is_benchmark": False,
        "is_legacy": True,
        "id_prefix": "c65d",
    },
    RESEARCH_LANE_COMBO_604_SP4_CHASE: {
        "label": "AI60-65 · Spread4 · Chase 3+",
        "subtitle": "RETIRED 2026-06-26 · historical research only — replaced by Virtual Chase tile",
        "combo_key": "AI60-65+SPREAD4+TYPE_B+CHASE_3PLUS_ALPHA",
        "ai_min": 60,
        "ai_max": 65,
        "spread_min": 4,
        "spread_max": 4,
        "entry_mode": "CHASE_3PLUS",
        "is_benchmark": False,
        "is_primary_production": False,
        "is_research_candidate": False,
        "is_legacy": True,
        "id_prefix": "c604c",
    },
    RESEARCH_LANE_COMBO_604_SP4_DIRECT: {
        "label": "AI60-65 · Spread4 · Continuous",
        "subtitle": "LEGACY · retired",
        "combo_key": "AI60-65+SPREAD4+TYPE_B+CONTINUOUS",
        "ai_min": 60,
        "ai_max": 65,
        "spread_min": 4,
        "spread_max": 4,
        "entry_mode": "IMMEDIATE",
        "is_benchmark": False,
        "is_legacy": True,
        "id_prefix": "c604d",
    },
    RESEARCH_LANE_AI60_SP3_VIRTUAL_CHASE: {
        "label": "AI60+ · Spread ≥3 · Virtual Chase",
        "subtitle": "RESEARCH CANDIDATE · hide limit chases 1–2, relive at chase 3, market at chase 6+60s",
        "combo_key": "AI60++SPREAD3++VIRTUAL_CHASE",
        "ai_min": 60,
        "ai_max": 100,
        "spread_min": 3,
        "spread_max": 99,
        "entry_mode": "VIRTUAL_CHASE",
        "is_benchmark": False,
        "is_primary_production": False,
        "is_research_candidate": True,
        "id_prefix": "vc603",
        # Per-lane Scenario C ladder override (profile_30 profit-capture test).
        # Omitted on other lanes → they fall back to the global TRAIL_LADDER_SCENARIO_C.
        "ladder": [(30, 20), (40, 30), (50, 40), (60, 50)],
        "ladder_label": "30→20, 40→30, 50→40, 60→50",
        "ladder_profile_id": "SCENARIO_C_PROFILE_30_v1",
        "promotion_criteria": (
            "ALL required: ≥100 completed trades · positive EV · beats CONTINUOUS "
            "over same period · stable DNA Quality · positive across multiple market regimes"
        ),
        "kill_criteria": (
            "ANY after ≥50 trades: negative EV · DNA Quality deterioration · "
            "failure to outperform CONTINUOUS"
        ),
    },
}

COMPARISON_BENCHMARK_LANE = "CONTINUOUS"
CONTINUOUS_PROXY_LANES = COMBO_EXECUTION_LANES
PRIMARY_PRODUCTION_LANE = None
BENCHMARK_LANE = COMPARISON_BENCHMARK_LANE
BENCHMARK_PROFILE_ID = "CONTINUOUS_BENCHMARK_v1"
BENCHMARK_ROLE = "BENCHMARK"
PRIMARY_PRODUCTION_ROLE = "RESEARCH_CANDIDATE"
RESEARCH_CANDIDATE_LANE = RESEARCH_LANE_AI60_SP3_VIRTUAL_CHASE
RESEARCH_CANDIDATE_ROLE = "RESEARCH_CANDIDATE"

RESEARCH_STACK_VERSION = "v11.1-virtual-chase-known-combos-v1"
RESEARCH_STACK_FEATURES = (
    "AI60_SP3 Virtual Chase · known-combo dashboard filters · "
    "Trading Genome v1 · Event bus + research.db · Relay snapshot push"
)
EXECUTION_FIX_VERSION = RESEARCH_STACK_VERSION
ANALYZER_SYNC_ID = RESEARCH_STACK_VERSION
RESEARCH_DASHBOARD_VERSION = RESEARCH_STACK_VERSION
EXPECTED_EXCHANGE = "bitfinex"
EXPECTED_BOT_VERSION = EXECUTION_FIX_VERSION

COMBO_CHASE_DELAY_LANES = COMBO_TILE_DISPLAY_ORDER
COMBO_CHASE_ISOLATION_PAIRS = (
    (RESEARCH_LANE_COMBO_604_SP4_DIRECT, RESEARCH_LANE_COMBO_604_SP4_CHASE),
)
COMBO_CHASE_DIRECT_REFERENCE = RESEARCH_LANE_COMBO_604_SP4_DIRECT

COMBO_LANE_LABELS = {lane: spec["label"] for lane, spec in COMBO_LANE_SPECS.items()}
COMBO_LANE_LABELS[RESEARCH_LANE_AI_SCAN] = "AI Scan (no orders)"

_COMBO_TOGGLE_DEFAULTS = {lane: True for lane in COMBO_EXECUTION_LANES}
_COMBO_TOGGLE_DEFAULTS.update({
    RESEARCH_LANE_COMBO_65_SP5_CHASE: False,
    RESEARCH_LANE_COMBO_65_SP5_DIRECT: False,
    RESEARCH_LANE_COMBO_604_SP4_DIRECT: False,
    RESEARCH_LANE_COMBO_604_SP4_CHASE: False,
})


def combo_lane_matches(lane: str, ai: dict, final_direction: str, spread: int = None) -> bool:
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper())
    if not spec or not ai or spec.get("is_legacy"):
        return False
    try:
        prob = int(ai.get("win_prob") or 0)
    except (TypeError, ValueError):
        prob = 0
    if prob < spec["ai_min"] or prob >= spec["ai_max"]:
        return False
    if spread is None:
        bull = int(ai.get("bull_score") or 0)
        bear = int(ai.get("bear_score") or 0)
        direction = str(final_direction or "").upper()
        spread = bull - bear if direction == "LONG" else bear - bull
    spread = int(spread or 0)
    return spec["spread_min"] <= spread <= spec["spread_max"]


def is_combo_execution_lane(lane: str) -> bool:
    lane_u = str(lane or "").upper()
    if lane_u not in COMBO_LANE_SPECS:
        return False
    return lane_u in COMBO_EXECUTION_LANES


def is_ai_scan_lane(lane: str) -> bool:
    return str(lane or "").upper() == RESEARCH_LANE_AI_SCAN


def combo_entry_mode(lane: str) -> str:
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper(), {})
    return str(spec.get("entry_mode") or "IMMEDIATE")


def is_chase_3plus_entry_lane(lane: str) -> bool:
    return combo_entry_mode(lane) == "CHASE_3PLUS"


def is_virtual_chase_entry_lane(lane: str) -> bool:
    return combo_entry_mode(lane) == "VIRTUAL_CHASE"


def is_immediate_entry_lane(lane: str) -> bool:
    mode = combo_entry_mode(lane)
    return mode in ("IMMEDIATE", "VIRTUAL_CHASE")


def is_benchmark_lane(lane: str) -> bool:
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper(), {})
    return bool(spec.get("is_benchmark")) or str(lane or "").upper() == BENCHMARK_LANE


def is_research_candidate_lane(lane: str) -> bool:
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper(), {})
    return bool(spec.get("is_research_candidate"))


def get_lane_ladder_override(lane: str):
    """Per-lane Scenario C ladder override, or None to fall back to the global ladder.

    Returns a tuple (ladder, ladder_label, ladder_profile_id) when the lane spec declares
    a `ladder` override; otherwise None. Kept optional — lanes without an override use the
    shared global TRAIL_LADDER_SCENARIO_C.
    """
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper(), {})
    ladder = spec.get("ladder")
    if not ladder:
        return None
    return (
        list(ladder),
        str(spec.get("ladder_label") or ""),
        str(spec.get("ladder_profile_id") or ""),
    )


def combo_toggle_defaults() -> dict:
    return dict(_COMBO_TOGGLE_DEFAULTS)


def any_combo_execution_enabled(enabled_map: dict = None, continuous_enabled: bool = False) -> bool:
    merged = combo_toggle_defaults()
    if enabled_map:
        for lane, val in enabled_map.items():
            if lane in merged:
                merged[lane] = bool(val)
    if any(merged.values()):
        return True
    try:
        from experimental_pathway_config import experimental_toggle_defaults

        exp = experimental_toggle_defaults()
        if enabled_map:
            for lane, val in enabled_map.items():
                if lane in exp:
                    exp[lane] = bool(val)
        if any(exp.values()):
            return True
    except ImportError:
        pass
    return bool(continuous_enabled)
