"""
Trading Genome Architecture v1 — frozen execution tiles.

CONTINUOUS: permanent benchmark / scientific control group.
TYPE_B_HUNTER_V1: research candidate — pre-entry TYPE_B prediction (independent AI, shadow).
SR_MICRO_TILE_V1: research candidate — micro S/R mean-reversion (independent AI, shadow).

Retired 2026-07-11:
  SL_AVOIDANCE_V1 — 47% WR LAB, -$2.03, EV -$0.14/close (UNDERPERFORMING)
  SIZED_CONTINUOUS_V1 — 31% WR LAB, -$81.08, EV -$0.84/close (UNDERPERFORMING)

Retired 2026-07-08:
  AI60_SP3_VIRTUAL_CHASE — TIES vs CONTINUOUS (no edge). CSV preserved.
  A160_CONTEXT_CHASE_EXIT_V2 — 0 approves in shadow. CSV preserved.

Earlier retired: COMBO_604_SP4_CHASE_3PLUS, COMBO_65_SP5 — historical data preserved.
"""
from __future__ import annotations

# [CLEAN 2026-07-11] Housekeeping — only CONTINUOUS + 2 new research candidates remain.
RESEARCH_LANE_AI_SCAN = "AI_SCAN"

# New research candidates 2026-07-11 — independent AI, shadow collecting, toggle to promote
RESEARCH_LANE_TYPE_B_HUNTER_V1 = "TYPE_B_HUNTER_V1"
RESEARCH_LANE_SR_MICRO_TILE_V1 = "SR_MICRO_TILE_V1"

# Legacy constants — preserved for CSV/historical data references. No live execution.
RESEARCH_LANE_COMBO_65_SP5_CHASE = "COMBO_65_SP5_CHASE_3PLUS"
RESEARCH_LANE_COMBO_65_SP5_DIRECT = "COMBO_65_SP5_DIRECT"
RESEARCH_LANE_COMBO_604_SP4_CHASE = "COMBO_604_SP4_CHASE_3PLUS"
RESEARCH_LANE_COMBO_604_SP4_DIRECT = "COMBO_604_SP4_DIRECT"
RESEARCH_LANE_AI60_SP3_VIRTUAL_CHASE = "AI60_SP3_VIRTUAL_CHASE"
RESEARCH_LANE_A160_CONTEXT_CHASE_EXIT_V2 = "A160_CONTEXT_CHASE_EXIT_V2"
RESEARCH_LANE_SL_AVOIDANCE_V1 = "SL_AVOIDANCE_V1"
RESEARCH_LANE_SIZED_CONTINUOUS_V1 = "SIZED_CONTINUOUS_V1"

# Active execution lanes (CONTINUOUS benchmark + 2 shadow research candidates)
COMBO_EXECUTION_LANES = (
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
    RESEARCH_LANE_SR_MICRO_TILE_V1,
)

COMBO_TILE_DISPLAY_ORDER = (
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
    RESEARCH_LANE_SR_MICRO_TILE_V1,
)

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
        "subtitle": (
            "RETIRED 2026-07-08 · statistical_significance_report shows TIES vs CONTINUOUS "
            "over 71 trades (no edge); replaced by SL_AVOIDANCE_V1"
        ),
        "combo_key": "AI60++SPREAD3++VIRTUAL_CHASE",
        "ai_min": 60,
        "ai_max": 100,
        "spread_min": 3,
        "spread_max": 99,
        "entry_mode": "VIRTUAL_CHASE",
        "is_benchmark": False,
        "is_primary_production": False,
        "is_research_candidate": False,
        "is_legacy": True,
        "id_prefix": "vc603",
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
    RESEARCH_LANE_A160_CONTEXT_CHASE_EXIT_V2: {
        "label": "A160 · Context Chase Exit V2",
        "subtitle": (
            "RETIRED 2026-07-08 · 0 approves / 0 trades after extended shadow period; "
            "no evidence collected. Replaced by SL_AVOIDANCE_V1."
        ),
        "combo_key": "A160++CONTEXT_CHASE_EXIT_V2",
        "ai_min": 60,
        "ai_max": 100,
        "spread_min": 3,
        "spread_max": 99,
        "entry_mode": "VIRTUAL_CHASE",
        "is_benchmark": False,
        "is_primary_production": False,
        "is_research_candidate": False,
        "is_independent_ai": True,
        "is_legacy": True,
        "id_prefix": "a160v2",
        "ladder": [(30, 20), (40, 30), (50, 40), (60, 50)],
        "ladder_label": "30→20, 40→30, 50→40, 60→50",
        "ladder_profile_id": "SCENARIO_C_PROFILE_30_v1",
        "promotion_criteria": (
            "ALL required: ≥100 completed trades · positive EV/appr · beats CONTINUOUS "
            "and AI60_SP3_VIRTUAL_CHASE over same window · context-veto loss reduction · "
            "stable across regimes"
        ),
        "kill_criteria": (
            "ANY after ≥50 trades: negative EV · fails to beat CONTINUOUS "
            "or AI60_SP3 on EV/appr · parse failure rate elevated · context veto ineffective"
        ),
        "research_question": (
            "Does A160 V2 beat CONTINUOUS and AI60_SP3 on EV/appr (same window)?"
        ),
        "hypothesis": (
            "Independent V2 prompt + context veto + chase 3–5 / age≥180s + Scenario C "
            "improves EV vs AI60_SP3 and CONTINUOUS on independent paper fills."
        ),
    },
    # =====================================================================
    # NEW RESEARCH CANDIDATES 2026-07-11
    # TYPE_B_HUNTER_V1 — independent AI + pre-entry composite scoring
    # SR_MICRO_TILE_V1 — independent AI + micro S/R mean-reversion
    # Both start as SHADOW_COLLECTING — toggle ON to promote to live orders.
    # =====================================================================
    RESEARCH_LANE_TYPE_B_HUNTER_V1: {
        "label": "Type B Hunter V1 — pre-entry TYPE_B prediction",
        "subtitle": (
            "RESEARCH_CANDIDATE · SHADOW ONLY · toggle ON = live orders · "
            "composite scoring (delta+volume+adx+conf) · independent AI at T+60s"
        ),
        "combo_key": "TYPE_B_HUNTER++PRE_ENTRY_SCORING_V1",
        "ai_min": 55,
        "ai_max": 101,
        "spread_min": 2,
        "spread_max": 99,
        "entry_mode": "IMMEDIATE",
        "is_benchmark": False,
        "is_research_candidate": True,
        "is_independent_ai": True,
        "id_prefix": "tbhv1",
        "module": "type_b_hunter_v1.py",
        "ai_cadence_offset_sec": 60,
        "promotion_criteria": (
            "ALL required: ≥150 shadow closes · positive EV · beats CONTINUOUS "
            "(95% CI) · P(TYPE_B) ≥ 40% · WR ≥ 75%"
        ),
        "kill_criteria": (
            "ANY after ≥75 closes: negative EV · P(TYPE_B) < 35% · WR < 65% · "
            "filter selectivity > 40%"
        ),
        "hypothesis": (
            "TYPE_B trades (MFE≥15%) are identifiable pre-entry via order-flow "
            "delta (+67% vs TYPE_A) + composite scoring (conf, volume_ratio, adx, "
            "ema_slope, structure)."
        ),
    },
    RESEARCH_LANE_SR_MICRO_TILE_V1: {
        "label": "S/R Micro Tile V1 — micro S/R mean-reversion",
        "subtitle": (
            "RESEARCH_CANDIDATE · SHADOW ONLY · toggle ON = live orders · "
            "LONG@support + SHORT@resistance · midpoint avoidance · volatility suspend"
        ),
        "combo_key": "SR_MICRO_TILE++MEAN_REVERSION_V1",
        "ai_min": 55,
        "ai_max": 101,
        "spread_min": 2,
        "spread_max": 99,
        "entry_mode": "IMMEDIATE",
        "is_benchmark": False,
        "is_research_candidate": True,
        "is_independent_ai": True,
        "id_prefix": "srmv1",
        "module": "sr_micro_tile_v1.py",
        "ai_cadence_offset_sec": 120,
        "extra_filters": {"adx_max": 40},
        "promotion_criteria": (
            "ALL required: ≥150 shadow closes · positive EV · beats CONTINUOUS "
            "(95% CI) · WR ≥ 65% · stable across at least 2 regime types"
        ),
        "kill_criteria": (
            "ANY after ≥75 closes: negative EV · WR < 55% · >30% trades hit "
            "midpoint avoidance · >20% trades suspended by volatility guard"
        ),
        "hypothesis": (
            "85-90% of market time is range-bound. Mean-reversion at micro S/R "
            "with midpoint avoidance captures crab movement income while volatility "
            "guard prevents trending losses."
        ),
    },
    # =====================================================================
    # [RETIRED 2026-07-11] SL_AVOIDANCE_V1 — UNDERPERFORMING (LAB: 47% WR, -$2.03)
    # [RETIRED 2026-07-11] SIZED_CONTINUOUS_V1 — UNDERPERFORMING (LAB: 31% WR, -$81.08)
    # Specs preserved for CSV/historical data analysis.
    # =====================================================================
    RESEARCH_LANE_SL_AVOIDANCE_V1: {
        "label": "SL Avoidance V1 (RETIRED)",
        "subtitle": "RETIRED 2026-07-11 — LAB: 47% WR, -$2.03, EV -$0.14/close",
        "combo_key": "SL_AVOIDANCE++DATA_GROUNDED_V1",
        "is_legacy": True,
    },
    RESEARCH_LANE_SIZED_CONTINUOUS_V1: {
        "label": "SIZED_CONTINUOUS V1 (RETIRED)",
        "subtitle": "RETIRED 2026-07-11 — LAB: 31% WR, -$81.08, EV -$0.84/close",
        "combo_key": "SIZED_CONTINUOUS++SESSION_SIZE_V1",
        "is_legacy": True,
    },
}

COMPARISON_BENCHMARK_LANE = "CONTINUOUS"
CONTINUOUS_PROXY_LANES = ()
PRIMARY_PRODUCTION_LANE = COMPARISON_BENCHMARK_LANE
BENCHMARK_LANE = COMPARISON_BENCHMARK_LANE
BENCHMARK_PROFILE_ID = "CONTINUOUS_BENCHMARK_v1"
BENCHMARK_ROLE = "BENCHMARK"
PRIMARY_PRODUCTION_ROLE = "BENCHMARK"
RESEARCH_CANDIDATE_LANE = RESEARCH_LANE_TYPE_B_HUNTER_V1
RESEARCH_CANDIDATE_ROLE = "RESEARCH_CANDIDATE"

RESEARCH_STACK_VERSION = "v11.6-dual-research-candidates"
RESEARCH_STACK_FEATURES = (
    "CONTINUOUS benchmark + 2 research candidates (TYPE_B_HUNTER_V1, SR_MICRO_TILE_V1) "
    "— all legacy lanes retired 2026-07-11 — "
    "Trading Genome v1 — Event bus + research.db — "
    "3-lane AI cadence (T+0, T+60, T+120)"
)
EXECUTION_FIX_VERSION = RESEARCH_STACK_VERSION
ANALYZER_SYNC_ID = RESEARCH_STACK_VERSION
RESEARCH_DASHBOARD_VERSION = RESEARCH_STACK_VERSION
EXPECTED_EXCHANGE = "bitfinex"
EXPECTED_BOT_VERSION = EXECUTION_FIX_VERSION

COMBO_CHASE_DELAY_LANES = COMBO_TILE_DISPLAY_ORDER
COMBO_CHASE_ISOLATION_PAIRS = ()
ACTIVE_CHASE_ISOLATION_PAIRS = ()
ACTIVE_CHASE_ISOLATION_LANES = (COMPARISON_BENCHMARK_LANE,)
COMBO_CHASE_DIRECT_REFERENCE = None

COMBO_LANE_LABELS = {lane: spec["label"] for lane, spec in COMBO_LANE_SPECS.items()}
COMBO_LANE_LABELS[RESEARCH_LANE_AI_SCAN] = "AI Scan (no orders)"
COMBO_LANE_LABELS[RESEARCH_LANE_TYPE_B_HUNTER_V1] = "Type B Hunter V1"
COMBO_LANE_LABELS[RESEARCH_LANE_SR_MICRO_TILE_V1] = "S/R Micro Tile V1"

_COMBO_TOGGLE_DEFAULTS = {lane: False for lane in COMBO_EXECUTION_LANES}
# Both new research candidates start OFF (shadow collecting)
_COMBO_TOGGLE_DEFAULTS.update({
    RESEARCH_LANE_TYPE_B_HUNTER_V1: False,
    RESEARCH_LANE_SR_MICRO_TILE_V1: False,
    RESEARCH_LANE_COMBO_65_SP5_CHASE: False,
    RESEARCH_LANE_COMBO_65_SP5_DIRECT: False,
    RESEARCH_LANE_COMBO_604_SP4_DIRECT: False,
    RESEARCH_LANE_COMBO_604_SP4_CHASE: False,
    RESEARCH_LANE_AI60_SP3_VIRTUAL_CHASE: False,
    RESEARCH_LANE_A160_CONTEXT_CHASE_EXIT_V2: False,
    RESEARCH_LANE_SL_AVOIDANCE_V1: False,
    RESEARCH_LANE_SIZED_CONTINUOUS_V1: False,
})


def is_independent_ai_lane(lane: str) -> bool:
    """Lanes with their own DeepSeek prompt — never inherit AI_SCAN / CONTINUOUS decisions."""
    lane_u = str(lane or "").upper()
    if lane_u in (RESEARCH_LANE_A160_CONTEXT_CHASE_EXIT_V2,
                  RESEARCH_LANE_TYPE_B_HUNTER_V1,
                  RESEARCH_LANE_SR_MICRO_TILE_V1):
        return True
    spec = COMBO_LANE_SPECS.get(lane_u) or {}
    return bool(spec.get("is_independent_ai"))


def _session_from_features(features: dict) -> str:
    """Derive session bucket aligned with bot `_research_session_bucket` labels.

    Returns ASIA / LONDON / OVERLAP / NEW_YORK (or unknown).
    """
    if not features:
        return "unknown"
    # Prefer already-computed research bucket when present.
    sess = features.get("session_bucket")
    if not sess:
        rb = features.get("research_buckets") or {}
        sess = rb.get("session_bucket")
    if sess:
        return str(sess).upper()
    ts = (
        features.get("ts_utc")
        or features.get("ts")
        or features.get("signal_ts")
        or features.get("entry_ts")
    )
    if not ts:
        return "unknown"
    try:
        from datetime import datetime, timezone
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return "unknown"
        h = dt.astimezone(timezone.utc).hour
        if h < 8:
            return "ASIA"
        if h < 13:
            return "LONDON"
        if h < 16:
            return "OVERLAP"
        if h < 22:
            return "NEW_YORK"
        return "ASIA"
    except Exception:
        return "unknown"


def _bucket_adx(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "unknown"
    if v < 15:
        return "lt_15"
    if v < 20:
        return "15_20"
    if v < 25:
        return "20_25"
    if v < 30:
        return "25_30"
    if v < 40:
        return "30_40"
    return "gte_40"


def _bucket_spread(v):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return "unknown"
    if v <= 2:
        return "lte_2"
    if v <= 4:
        return "3_4"
    return "gte_5"


def _apply_extra_filters(lane: str, ai: dict, final_direction: str, spread: int,
                          features: dict = None, signal_age_sec: float = None) -> tuple:
    """Apply data-grounded extra_filters declared in the lane spec.

    Returns (passes: bool, block_reason: str).
    """
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper()) or {}
    xf = spec.get("extra_filters") or {}
    if not xf:
        return True, ""

    features = features or {}

    adx_max = xf.get("adx_max")
    if adx_max is not None:
        adx = (
            features.get("adx_at_entry")
            or features.get("adx")
            or features.get("mom_adx")
        )
        if adx is None:
            mc = features.get("market_context") or {}
            adx = (mc.get("trend_strength") or {}).get("adx")
        if adx is not None:
            try:
                if float(adx) > float(adx_max):
                    return False, f"ADX_OVER_CAP ({float(adx):.1f} > {adx_max})"
            except (TypeError, ValueError):
                pass

    struct_bl = xf.get("structure_blacklist") or []
    if struct_bl:
        struct = (
            features.get("structure_bias_at_entry")
            or features.get("structure_bias")
            or features.get("mtf_structure")
        )
        if struct and str(struct).upper() in [s.upper() for s in struct_bl]:
            return False, f"STRUCTURE_BLACKLISTED ({struct})"

    sess_bl = xf.get("session_blacklist") or []
    if sess_bl:
        sess = _session_from_features(features)
        if sess in [s.upper() for s in sess_bl]:
            return False, f"SESSION_BLACKLISTED ({sess})"

    age_min = xf.get("signal_age_min_sec")
    if age_min is not None and signal_age_sec is not None:
        try:
            if float(signal_age_sec) < float(age_min):
                return False, f"SIGNAL_TOO_YOUNG ({float(signal_age_sec):.0f}s < {age_min}s)"
        except (TypeError, ValueError):
            pass

    fp_path = xf.get("sl_fingerprint_report_path")
    fp_max = xf.get("sl_fingerprint_match_max")
    if fp_path and fp_max is not None:
        try:
            import json
            import os
            resolved = fp_path
            if not os.path.isabs(resolved):
                # Resolve relative to this module / agent cwd so LAB filters work
                # regardless of process working directory.
                candidates = [
                    resolved,
                    os.path.join(os.path.dirname(__file__), resolved),
                    os.path.join(os.getcwd(), resolved),
                ]
                resolved = next((p for p in candidates if os.path.exists(p)), resolved)
            if os.path.exists(resolved):
                with open(resolved, "r", encoding="utf-8") as f:
                    fp_report = json.load(f)
                rules = ((fp_report.get("fingerprint_spec") or {}).get("rules")) or []
                if rules:
                    feature_lookup = {
                        "session": _session_from_features(features),
                        "adx_bucket": _bucket_adx(
                            features.get("adx_at_entry") or features.get("adx")
                        ),
                        "spread_bucket": _bucket_spread(spread),
                        "struct": (
                            features.get("structure_bias_at_entry")
                            or features.get("structure_bias")
                            or "UNKNOWN"
                        ),
                        "direction": final_direction,
                    }
                    matches = 0
                    for rule in rules:
                        feat = rule.get("feature")
                        val = str(rule.get("value") or "").upper()
                        actual = str(feature_lookup.get(feat, "") or "").upper()
                        if actual == val:
                            matches += 1
                    if matches > int(fp_max):
                        return False, f"SL_FINGERPRINT_MATCH ({matches} > {fp_max})"
        except Exception:
            pass

    return True, ""


def combo_lane_matches(lane: str, ai: dict, final_direction: str, spread: int = None,
                       features: dict = None, signal_age_sec: float = None) -> bool:
    """Match AI_SCAN-inherited combo tiles. Independent-AI lanes always return False here.

    Optional `features` / `signal_age_sec` enable data-grounded `extra_filters`
    (SL_AVOIDANCE_V1). Backward compatible when those kwargs are omitted.
    """
    lane_u = str(lane or "").upper()
    if is_independent_ai_lane(lane_u):
        return False
    spec = COMBO_LANE_SPECS.get(lane_u)
    if not spec or not ai or spec.get("is_legacy") or spec.get("is_shadow_only"):
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
    if not (spec["spread_min"] <= spread <= spec["spread_max"]):
        return False
    passes, _ = _apply_extra_filters(
        lane_u, ai, final_direction, spread, features, signal_age_sec
    )
    return passes


def combo_lane_match_detail(lane: str, ai: dict, final_direction: str, spread: int = None,
                            features: dict = None, signal_age_sec: float = None) -> dict:
    """Like combo_lane_matches but returns {passes, block_reason} for telemetry."""
    lane_u = str(lane or "").upper()
    if is_independent_ai_lane(lane_u):
        return {"passes": False, "block_reason": "INDEPENDENT_AI_LANE"}
    spec = COMBO_LANE_SPECS.get(lane_u)
    if not spec:
        return {"passes": False, "block_reason": "LANE_NOT_FOUND"}
    if spec.get("is_legacy"):
        return {"passes": False, "block_reason": "LANE_LEGACY"}
    if spec.get("is_shadow_only"):
        return {"passes": False, "block_reason": "LANE_SHADOW_ONLY"}
    if not ai:
        return {"passes": False, "block_reason": "NO_AI"}
    try:
        prob = int(ai.get("win_prob") or 0)
    except (TypeError, ValueError):
        prob = 0
    if prob < spec["ai_min"]:
        return {"passes": False, "block_reason": f"AI_UNDER_MIN ({prob} < {spec['ai_min']})"}
    if prob >= spec["ai_max"]:
        return {"passes": False, "block_reason": f"AI_OVER_MAX ({prob} >= {spec['ai_max']})"}
    if spread is None:
        bull = int(ai.get("bull_score") or 0)
        bear = int(ai.get("bear_score") or 0)
        direction = str(final_direction or "").upper()
        spread = bull - bear if direction == "LONG" else bear - bull
    spread = int(spread or 0)
    if spread < spec["spread_min"]:
        return {"passes": False, "block_reason": f"SPREAD_UNDER_MIN ({spread} < {spec['spread_min']})"}
    if spread > spec["spread_max"]:
        return {"passes": False, "block_reason": f"SPREAD_OVER_MAX ({spread} > {spec['spread_max']})"}
    passes, block_reason = _apply_extra_filters(
        lane_u, ai, final_direction, spread, features, signal_age_sec
    )
    return {"passes": passes, "block_reason": block_reason}


def is_shadow_only_lane(lane: str) -> bool:
    """Shadow/research telemetry lanes — never order-capable by construction."""
    lane_u = str(lane or "").upper()
    if lane_u == RESEARCH_LANE_A160_CONTEXT_CHASE_EXIT_V2:
        return False
    spec = COMBO_LANE_SPECS.get(lane_u) or {}
    return bool(spec.get("is_shadow_only"))


def is_combo_execution_lane(lane: str) -> bool:
    lane_u = str(lane or "").upper()
    if lane_u not in COMBO_LANE_SPECS:
        return False
    if is_shadow_only_lane(lane_u):
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


# ============================================================================
# [ADD_2026-07-08] Per-lane position sizing (Phase 2)
# ============================================================================
SIZE_MULT_MIN = 0.1
SIZE_MULT_MAX = 2.0


def resolve_lane_size_multiplier(lane: str, features: dict = None) -> float:
    """Compute the position-size multiplier for a lane given signal features.

    Returns a float in [SIZE_MULT_MIN, SIZE_MULT_MAX]. Lanes without a
    `size_multipliers` spec return 1.0 (no change).
    """
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper()) or {}
    multipliers_cfg = spec.get("size_multipliers")
    if not multipliers_cfg:
        return 1.0

    features = features or {}
    combined = 1.0
    for feat_name, value_map in multipliers_cfg.items():
        actual = features.get(feat_name)
        if actual is None:
            rb = features.get("research_buckets") or {}
            actual = rb.get(feat_name) or rb.get(feat_name.replace("_bucket", ""))
        if actual is None and feat_name == "session_bucket":
            actual = _session_from_features(features)
        if actual is None:
            continue
        actual_str = str(actual).upper()
        mult = None
        for k, v in value_map.items():
            if str(k).upper() == actual_str:
                mult = v
                break
        if mult is None:
            mult = value_map.get("default", 1.0)
        try:
            combined *= float(mult)
        except (TypeError, ValueError):
            pass

    return max(SIZE_MULT_MIN, min(SIZE_MULT_MAX, combined))


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
