"""
Trading Genome Architecture v1 — frozen execution tiles.

CONTINUOUS: permanent benchmark / scientific control group.
SL_AVOIDANCE_V1: research candidate (data-grounded filter: ADX<30, no OVERLAP,
  signal_age>=180s, SL fingerprint). Inherits AI_SCAN decision. Same Scenario C exits.
SIZED_CONTINUOUS_V1: research candidate (same entries as CONTINUOUS; session-based
  margin sizing only: LONDON/NY=1.5x, ASIA/OVERLAP=0.5x).

Retired 2026-07-08:
  AI60_SP3_VIRTUAL_CHASE — TIES vs CONTINUOUS (no edge). CSV preserved.
  A160_CONTEXT_CHASE_EXIT_V2 — 0 approves in shadow. CSV preserved.

Earlier retired: COMBO_604_SP4_CHASE_3PLUS — historical data preserved, no new orders.
"""
from __future__ import annotations

RESEARCH_LANE_AI_SCAN = "AI_SCAN"

RESEARCH_LANE_COMBO_65_SP5_CHASE = "COMBO_65_SP5_CHASE_3PLUS"
RESEARCH_LANE_COMBO_65_SP5_DIRECT = "COMBO_65_SP5_DIRECT"
RESEARCH_LANE_COMBO_604_SP4_CHASE = "COMBO_604_SP4_CHASE_3PLUS"
RESEARCH_LANE_COMBO_604_SP4_DIRECT = "COMBO_604_SP4_DIRECT"
RESEARCH_LANE_AI60_SP3_VIRTUAL_CHASE = "AI60_SP3_VIRTUAL_CHASE"
RESEARCH_LANE_A160_CONTEXT_CHASE_EXIT_V2 = "A160_CONTEXT_CHASE_EXIT_V2"
# [ADD_2026-07-08] Data-grounded research candidate (filter-only A/B vs CONTINUOUS).
RESEARCH_LANE_SL_AVOIDANCE_V1 = "SL_AVOIDANCE_V1"
# [ADD_2026-07-08 Phase 2] Position-sizing tile — same entries as CONTINUOUS.
RESEARCH_LANE_SIZED_CONTINUOUS_V1 = "SIZED_CONTINUOUS_V1"

# Live order generation — research candidates (+ CONTINUOUS benchmark toggle)
# AI60 / A160 V2 retired 2026-07-08. Active tiles: SL_AVOIDANCE_V1 + SIZED_CONTINUOUS_V1.
COMBO_EXECUTION_LANES = (
    RESEARCH_LANE_SL_AVOIDANCE_V1,
    RESEARCH_LANE_SIZED_CONTINUOUS_V1,
)

COMBO_TILE_DISPLAY_ORDER = (
    RESEARCH_LANE_SL_AVOIDANCE_V1,
    RESEARCH_LANE_SIZED_CONTINUOUS_V1,
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
    # [ADD_2026-07-08] SL_AVOIDANCE_V1 — filter-only A/B vs CONTINUOUS.
    # Final filter set (revised): ADX<30 + no OVERLAP + signal_age>=180s +
    # SL fingerprint match_max=1. No ladder override (inherits CONTINUOUS).
    # =====================================================================
    RESEARCH_LANE_SL_AVOIDANCE_V1: {
        "label": "SL Avoidance V1 - ADX<30 + no OVERLAP",
        "subtitle": (
            "RESEARCH_CANDIDATE (LAB shadow) - data-grounded via filter_and_sizing_search.py. "
            "OVERLAP session (UTC 13-15) has 55% WR / -$54 PnL (worst). ADX 30+ has -$0.66 EV. "
            "Combined filter: keeps 63% of trades, lifts PnL $100->$175 (+74%) in backtest. "
            "WR 70%->79%. Starts OFF - LAB shadow mode."
        ),
        "combo_key": "SL_AVOIDANCE++DATA_GROUNDED_V1",
        "ai_min": 60,
        "ai_max": 100,
        "spread_min": 3,
        "spread_max": 99,
        "entry_mode": "VIRTUAL_CHASE",
        "is_benchmark": False,
        "is_primary_production": False,
        "is_research_candidate": True,
        "id_prefix": "slav1",
        # No ladder override — inherits TRAIL_LADDER_SCENARIO_C (CONTINUOUS exits).
        "extra_filters": {
            "adx_max": 30,
            "session_blacklist": ["OVERLAP"],
            "signal_age_min_sec": 180,
            "sl_fingerprint_match_max": 1,
            "sl_fingerprint_report_path": "reports/stop_loss_fingerprint_report.json",
        },
        "promotion_criteria": (
            "ALL required: >=150 completed trades - positive EV - beats CONTINUOUS over "
            "same window (95% bootstrap CI excludes benchmark mean) - reduced stop-loss "
            "rate vs CONTINUOUS - stable across sessions and ADX regimes"
        ),
        "kill_criteria": (
            "ANY after >=75 trades: negative EV - TIES or LOSES vs CONTINUOUS on "
            "statistical_significance_report - stop-loss rate not reduced - WR falls "
            "below 60% - filter is too aggressive (rejects >70% of CONTINUOUS signals)"
        ),
        "research_question": (
            "Does filtering out the historical stop-loss fingerprint at entry improve "
            "EV/approval vs CONTINUOUS, without sacrificing winners?"
        ),
        "hypothesis": (
            "STOP_LOSS leakage is concentrated in ADX 30+ and OVERLAP session. Excluding "
            "these conditions plus a fingerprint match threshold will recover leakage "
            "while keeping most PROFIT_LOCK_LADDER winners."
        ),
        "data_grounding": {
            "source_dataset": "trade_outcome.jsonl / trades_3factor.csv 2026-07-01 to 2026-07-07 (283 trades)",
            "analysis_reports": [
                "reports/stop_loss_fingerprint_report.json",
                "reports/session_edge_report.json",
                "reports/filter_and_sizing_search.json",
            ],
            "expected_filter_selectivity": "rejects ~30-40% of CONTINUOUS signals",
            "expected_ev_uplift": "+$0.30 to +$0.60 per trade vs CONTINUOUS",
        },
    },
    # ========================================================================
    # [ADD_2026-07-08 Phase 2] SIZED_CONTINUOUS_V1 — position-sizing tile
    # Same entry criteria as CONTINUOUS (no filter). Only margin changes by session.
    # ========================================================================
    RESEARCH_LANE_SIZED_CONTINUOUS_V1: {
        "label": "SIZED_CONTINUOUS V1 - session-based position sizing",
        "subtitle": (
            "RESEARCH_CANDIDATE (LAB shadow) - Phase 2 position-sizing tile. "
            "Same entries as CONTINUOUS (no filter); only margin changes by session. "
            "LONDON/NEW_YORK=1.5x, ASIA/OVERLAP=0.5x. "
            "Backtest: +$184.82 vs +$100.86 baseline (+83% PnL uplift, no trades rejected). "
            "Biggest unrealized lever in the system. Default OFF - LAB shadow mode."
        ),
        "combo_key": "SIZED_CONTINUOUS++SESSION_SIZE_V1",
        "ai_min": 60,
        "ai_max": 100,
        "spread_min": 3,
        "spread_max": 99,
        "entry_mode": "VIRTUAL_CHASE",
        "is_benchmark": False,
        "is_primary_production": False,
        "is_research_candidate": True,
        "id_prefix": "szdc1",
        "size_multipliers": {
            "session_bucket": {
                "LONDON": 1.5,
                "NEW_YORK": 1.5,
                "ASIA": 0.5,
                "OVERLAP": 0.5,
                "default": 1.0,
            },
        },
        "promotion_criteria": (
            "ALL required: >=100 completed trades - positive EV - beats CONTINUOUS on "
            "total PnL (95% bootstrap CI excludes benchmark mean) - WR unchanged vs "
            "CONTINUOUS (sizing shouldn't affect win/loss, only magnitude)"
        ),
        "kill_criteria": (
            "ANY after >=75 trades: total PnL < CONTINUOUS - WR materially different "
            "(indicates sizing is changing entry decisions, not just magnitude) - "
            "OVERLAP trades losing more than -1.5x CONTINUOUS's OVERLAP losses"
        ),
        "research_question": (
            "Does session-based position sizing (no rejection) beat CONTINUOUS on total "
            "PnL while preserving WR? I.e. is sizing a cleaner lever than filtering?"
        ),
        "hypothesis": (
            "Session WR varies from 55% (OVERLAP) to 88% (LONDON). Rather than rejecting "
            "low-WR signals, reducing their size 3x while boosting high-WR signals 1.5x "
            "captures the session edge without sacrificing trade volume. Backtest "
            "suggests +83% PnL uplift vs baseline."
        ),
        "data_grounding": {
            "source_dataset": "trade_outcome.jsonl 2026-07-01 to 2026-07-07 (283 trades)",
            "analysis_report": "reports/filter_and_sizing_search.json",
            "best_scheme": "LONDON/NY=1.5x, ASIA/OVERLAP=0.5x",
            "backtest_pnl_usd": 184.82,
            "backtest_baseline_pnl_usd": 100.86,
            "backtest_uplift_pct": 83.2,
            "backtest_trade_count": 283,
            "backtest_trade_count_note": "ALL 283 trades taken (none rejected)",
        },
    },
}

COMPARISON_BENCHMARK_LANE = "CONTINUOUS"
CONTINUOUS_PROXY_LANES = COMBO_EXECUTION_LANES
PRIMARY_PRODUCTION_LANE = None
BENCHMARK_LANE = COMPARISON_BENCHMARK_LANE
BENCHMARK_PROFILE_ID = "CONTINUOUS_BENCHMARK_v1"
BENCHMARK_ROLE = "BENCHMARK"
PRIMARY_PRODUCTION_ROLE = "RESEARCH_CANDIDATE"
RESEARCH_CANDIDATE_LANE = RESEARCH_LANE_SL_AVOIDANCE_V1
RESEARCH_CANDIDATE_ROLE = "RESEARCH_CANDIDATE"

RESEARCH_STACK_VERSION = "v11.4-sl-avoidance-plus-sizing-v1"
RESEARCH_STACK_FEATURES = (
    "SL_AVOIDANCE_V1 (data-grounded filter: ADX<30, no OVERLAP session, signal_age>=180s) "
    "+ SIZED_CONTINUOUS_V1 (session-based position sizing: LONDON/NY=1.5x, ASIA/OVERLAP=0.5x) "
    "- AI60_SP3 + A160 V2 retired 2026-07-08 - Trading Genome v1 - Event bus + research.db "
    "- Relay snapshot push"
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
ACTIVE_CHASE_ISOLATION_PAIRS = (
    (COMPARISON_BENCHMARK_LANE, RESEARCH_LANE_SL_AVOIDANCE_V1),
    (COMPARISON_BENCHMARK_LANE, RESEARCH_LANE_SIZED_CONTINUOUS_V1),
)
ACTIVE_CHASE_ISOLATION_LANES = (
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_SL_AVOIDANCE_V1,
    RESEARCH_LANE_SIZED_CONTINUOUS_V1,
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
    # Retired research candidates — no new orders.
    RESEARCH_LANE_AI60_SP3_VIRTUAL_CHASE: False,
    RESEARCH_LANE_A160_CONTEXT_CHASE_EXIT_V2: False,
    # LAB shadow defaults OFF — toggle ON only after LAB confirmation.
    RESEARCH_LANE_SL_AVOIDANCE_V1: False,
    RESEARCH_LANE_SIZED_CONTINUOUS_V1: False,
})


def is_independent_ai_lane(lane: str) -> bool:
    """Lanes with their own DeepSeek prompt — never inherit AI_SCAN / CONTINUOUS decisions."""
    lane_u = str(lane or "").upper()
    if lane_u == RESEARCH_LANE_A160_CONTEXT_CHASE_EXIT_V2:
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
            if os.path.exists(fp_path):
                with open(fp_path, "r", encoding="utf-8") as f:
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
