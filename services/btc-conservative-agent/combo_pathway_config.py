"""
Combo Pathway Lab v2 — two chase execution tiles + CONTINUOUS benchmark yardstick.

PRIMARY_PRODUCTION: COMBO_65_SP5_CHASE_3PLUS (live deployable edge).
COMPARISON_BENCHMARK: CONTINUOUS (Continuous AI Research) — AI_SCAN mirror every ~180s;
  toggle ON = limit orders + 25% chase + Scenario C; OFF = shadow/data only.
Direct combo lanes retired 2026-06-21 — see PATHWAY_NEXT_PHASE.md / pathway_lane_roster.py.
Architecture frozen — see PATHWAY_ARCHITECTURE_FREEZE.md (change tiles/filters only).
"""
from __future__ import annotations

RESEARCH_LANE_AI_SCAN = "AI_SCAN"

RESEARCH_LANE_COMBO_65_SP5_CHASE = "COMBO_65_SP5_CHASE_3PLUS"
RESEARCH_LANE_COMBO_65_SP5_DIRECT = "COMBO_65_SP5_DIRECT"
RESEARCH_LANE_COMBO_604_SP4_CHASE = "COMBO_604_SP4_CHASE_3PLUS"
RESEARCH_LANE_COMBO_604_SP4_DIRECT = "COMBO_604_SP4_DIRECT"

COMBO_EXECUTION_LANES = (
    RESEARCH_LANE_COMBO_65_SP5_CHASE,
    RESEARCH_LANE_COMBO_604_SP4_CHASE,
)

# Dashboard tile order — chase-only live roster
COMBO_TILE_DISPLAY_ORDER = (
    RESEARCH_LANE_COMBO_65_SP5_CHASE,
    RESEARCH_LANE_COMBO_604_SP4_CHASE,
)

COMBO_LANE_SPECS = {
    RESEARCH_LANE_COMBO_65_SP5_CHASE: {
        "label": "AI65+ · Spread5+ · Chase 3+",
        "subtitle": "PRIMARY_PRODUCTION · best deployable edge · TYPE_B is post-trade fingerprint",
        "combo_key": "AI65++SPREAD5++TYPE_B+CHASE_3PLUS_ALPHA",
        "ai_min": 65,
        "ai_max": 101,
        "spread_min": 5,
        "spread_max": 99,
        "entry_mode": "CHASE_3PLUS",
        "is_benchmark": False,
        "is_primary_production": True,
        "id_prefix": "c65c",
    },
    RESEARCH_LANE_COMBO_65_SP5_DIRECT: {
        "label": "AI65+ · Spread5+ · Continuous",
        "subtitle": "Immediate limit · same entry filters as PRIMARY_PRODUCTION",
        "combo_key": "AI65++SPREAD5++TYPE_B+CONTINUOUS",
        "ai_min": 65,
        "ai_max": 101,
        "spread_min": 5,
        "spread_max": 99,
        "entry_mode": "IMMEDIATE",
        "is_benchmark": False,
        "id_prefix": "c65d",
    },
    RESEARCH_LANE_COMBO_604_SP4_CHASE: {
        "label": "AI60-65 · Spread4 · Chase 3+",
        "subtitle": "Moderate AI + spread 4 · delayed virtual chase",
        "combo_key": "AI60-65+SPREAD4+TYPE_B+CHASE_3PLUS_ALPHA",
        "ai_min": 60,
        "ai_max": 65,
        "spread_min": 4,
        "spread_max": 4,
        "entry_mode": "CHASE_3PLUS",
        "is_benchmark": False,
        "id_prefix": "c604c",
    },
    RESEARCH_LANE_COMBO_604_SP4_DIRECT: {
        "label": "AI60-65 · Spread4 · Continuous",
        "subtitle": "Moderate AI + spread 4 · immediate limit",
        "combo_key": "AI60-65+SPREAD4+TYPE_B+CONTINUOUS",
        "ai_min": 60,
        "ai_max": 65,
        "spread_min": 4,
        "spread_max": 4,
        "entry_mode": "IMMEDIATE",
        "is_benchmark": False,
        "id_prefix": "c604d",
    },
}

COMPARISON_BENCHMARK_LANE = "CONTINUOUS"
CONTINUOUS_PROXY_LANES = COMBO_EXECUTION_LANES
PRIMARY_PRODUCTION_LANE = RESEARCH_LANE_COMBO_65_SP5_CHASE
BENCHMARK_LANE = COMPARISON_BENCHMARK_LANE
BENCHMARK_PROFILE_ID = "CONTINUOUS_BENCHMARK_v1"
BENCHMARK_ROLE = "BENCHMARK"
PRIMARY_PRODUCTION_ROLE = "PRIMARY_PRODUCTION"
# Single release id — bot, analyzer, research dashboard, and local relay desk.
# Bump this string on every deploy; all UIs read it from here (not hardcoded elsewhere).
RESEARCH_STACK_VERSION = "v10.3-dashboard-ultimate-gate-2026-06-23"
RESEARCH_STACK_FEATURES = "Ultimate dashboard gate · Virtual chase defer · AI bands · Relay desk · Bitfinex LIVE"
EXECUTION_FIX_VERSION = RESEARCH_STACK_VERSION
ANALYZER_SYNC_ID = RESEARCH_STACK_VERSION
RESEARCH_DASHBOARD_VERSION = RESEARCH_STACK_VERSION
EXPECTED_EXCHANGE = "bitfinex"
EXPECTED_BOT_VERSION = EXECUTION_FIX_VERSION

# Analyzer chase reports + dashboard Chase Delay tab (combo tiles, not legacy lanes)
COMBO_CHASE_DELAY_LANES = COMBO_TILE_DISPLAY_ORDER
COMBO_CHASE_ISOLATION_PAIRS = (
    (RESEARCH_LANE_COMBO_65_SP5_DIRECT, RESEARCH_LANE_COMBO_65_SP5_CHASE),
    (RESEARCH_LANE_COMBO_604_SP4_DIRECT, RESEARCH_LANE_COMBO_604_SP4_CHASE),
)
COMBO_CHASE_DIRECT_REFERENCE = RESEARCH_LANE_COMBO_65_SP5_DIRECT

COMBO_LANE_LABELS = {lane: spec["label"] for lane, spec in COMBO_LANE_SPECS.items()}
COMBO_LANE_LABELS[RESEARCH_LANE_AI_SCAN] = "AI Scan (no orders)"

_COMBO_TOGGLE_DEFAULTS = {lane: True for lane in COMBO_EXECUTION_LANES}
# Retired direct lanes — keep keys so saved toggle maps do not resurrect orders.
_COMBO_TOGGLE_DEFAULTS.update({
    RESEARCH_LANE_COMBO_65_SP5_DIRECT: False,
    RESEARCH_LANE_COMBO_604_SP4_DIRECT: False,
})


def combo_lane_matches(lane: str, ai: dict, final_direction: str, spread: int = None) -> bool:
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper())
    if not spec or not ai:
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
    return str(lane or "").upper() in COMBO_LANE_SPECS


def is_ai_scan_lane(lane: str) -> bool:
    return str(lane or "").upper() == RESEARCH_LANE_AI_SCAN


def combo_entry_mode(lane: str) -> str:
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper(), {})
    return str(spec.get("entry_mode") or "IMMEDIATE")


def is_chase_3plus_entry_lane(lane: str) -> bool:
    return combo_entry_mode(lane) == "CHASE_3PLUS"


def is_immediate_entry_lane(lane: str) -> bool:
    return combo_entry_mode(lane) == "IMMEDIATE"


def is_benchmark_lane(lane: str) -> bool:
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper(), {})
    return bool(spec.get("is_benchmark")) or str(lane or "").upper() == BENCHMARK_LANE


def combo_toggle_defaults() -> dict:
    return dict(_COMBO_TOGGLE_DEFAULTS)


def any_combo_execution_enabled(enabled_map: dict = None, continuous_enabled: bool = False) -> bool:
    """True when at least one combo or experimental tile can place orders."""
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
