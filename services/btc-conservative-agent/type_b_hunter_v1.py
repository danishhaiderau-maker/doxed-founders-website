"""
TYPE_B_HUNTER_V1 — Pre-entry TYPE_B prediction scoring module.

Classification: TYPE_A (MFE<10%), TYPE_B (MFE>=15%), MIXED (between).
TYPE_B is the only profitable cohort (96.5% WR, +$357.20, EV +$2.52).

This module scores signals BEFORE entry using features that separate TYPE_B
from TYPE_A in post-trade analysis. It runs as a SHADOW lane — toggle ON to
place real limit orders, OFF for shadow simulation with full data collection.

Data source: type_b_predictor_report.json from the Type B Discovery dashboard.
"""
from __future__ import annotations


# ── Cohort definitions (from Type B Discovery) ────────────────────────────
TYPE_B_MIN_MFE_PCT = 15      # MFE >= 15% → TYPE_B
TYPE_A_MAX_MFE_PCT = 10      # MFE < 10%  → TYPE_A
# MIXED: 10% <= MFE < 15%


# ── Scoring weights (data-backed from type_b_predictor_report.json) ──────
# Tier 1 — strongest separators between TYPE_A and TYPE_B
SCORE_CONF65_PLUS          = 2.0   # P(TYPE_B): 48.3%, WR: 86.2% (29 trades)
SCORE_DELTA_18_PLUS        = 1.5   # TYPE_B mean=21.22 vs TYPE_A mean=12.74 (+67%)
SCORE_VOLUME_RATIO_090     = 1.0   # TYPE_B mean=1.12 vs TYPE_A mean=0.76 (+47%)

# Tier 2 — moderate predictors
SCORE_ADX_20_40            = 0.5   # adx20-30: 35.2% P(TYPE_B), adx30+: 36.0%
SCORE_SPREAD_3_4           = 0.5   # spread3-4: 36.3% P(TYPE_B), 69.2% WR (237 trades)
SCORE_EMA_UP               = 0.3   # ema_up: 34.3% P(TYPE_B), 67.6% WR (210 trades)

# Tier 3 — weak but directional
SCORE_STRUCTURE_MINUS3     = 0.3   # TYPE_B mean=-2.35 vs TYPE_A mean=-1.93
SCORE_EDGE_3_5_PLUS        = 0.2   # TYPE_B mean=3.44 vs TYPE_A mean=3.33

# ── Entry thresholds ─────────────────────────────────────────────────────
MIN_SCORE_TO_ENTER         = 2.5   # Spawn shadow entry only if score >= this
MIN_CONFIDENCE_FLOOR        = 55    # Absolute floor — never enter below this
MIN_SPREAD_FLOOR            = 2     # Absolute floor — never enter below this

# ── Lane configuration ────────────────────────────────────────────────────
LANE_ID                     = "TYPE_B_HUNTER_V1"
LANE_LABEL                  = "Type B Hunter V1 — pre-entry TYPE_B prediction"
LANE_ID_PREFIX              = "tbhv1"
LANE_STATUS                 = "SHADOW_COLLECTING"  # starts as shadow — toggle ON for live
IS_INDEPENDENT_AI           = True  # Uses its own DeepSeek prompt (not CONTINUOUS mirror)

# ── AI configuration ──────────────────────────────────────────────────────
# Independent AI — fires 60s after CONTINUOUS to avoid collision in 3-lane setup.
# Cadence: T+60s (CONTINUOUS at T+0, TYPE_B at T+60, S/R at T+120)
AI_OFFSET_SEC               = 60
AI_MAX_AGE_SEC              = 180
AI_PROMPT_FOCUS             = (
    "TYPE_B_HUNTER: Evaluate if current setup has strong directional follow-through. "
    "Key factors: order-flow delta, volume ratio, trend strength, structure. "
    "Confidence >= 55 required. Respond with direction, confidence, bull_score, bear_score."
)


def get_type_b_score(
    ai_prob: int,
    features: dict,
) -> float:
    """Compute composite TYPE_B score from pre-entry features.

    Args:
        ai_prob: AI win probability (0-100)
        features: Signal/market features dict. Expected keys:
            delta, volume_ratio, adx, spread, ema_slope, structure_bias, edge_score

    Returns:
        Float score. >= MIN_SCORE_TO_ENTER means the signal qualifies.
    """
    score = 0.0
    features = features or {}

    # Tier 1
    if ai_prob >= 65:
        score += SCORE_CONF65_PLUS

    delta = _safe_float(features.get("delta") or features.get("features_delta") or features.get("orderflow_delta"))
    if delta >= 18:
        score += SCORE_DELTA_18_PLUS

    vol_ratio = _safe_float(features.get("volume_ratio") or features.get("features_volume_ratio") or features.get("vol_ratio"))
    if vol_ratio >= 0.90:
        score += SCORE_VOLUME_RATIO_090

    # Tier 2
    adx = _safe_float(
        features.get("adx") or features.get("adx_at_entry") or features.get("mom_adx")
        or (features.get("market_context") or {}).get("trend_strength", {}).get("adx")
    )
    if 20 <= adx <= 40:
        score += SCORE_ADX_20_40

    spread = _safe_int(
        features.get("spread") or features.get("conviction_spread")
        or features.get("directional_spread")
    )
    if 3 <= spread <= 4:
        score += SCORE_SPREAD_3_4

    ema_slope = str(features.get("ema_slope") or features.get("ema_hybrid_slope") or "").lower()
    if ema_slope in ("up", "bullish", "positive"):
        score += SCORE_EMA_UP

    # Tier 3
    structure = _safe_float(
        features.get("structure_bias_at_entry") or features.get("structure")
        or features.get("structure_score_at_entry")
    )
    if structure is not None and structure <= -3:
        score += SCORE_STRUCTURE_MINUS3

    edge = _safe_float(features.get("edge_score") or features.get("controls_edge_threshold"))
    if edge >= 3.5:
        score += SCORE_EDGE_3_5_PLUS

    return round(score, 2)


def should_enter_type_b(ai_prob: int, features: dict) -> tuple[bool, dict]:
    """Determine if a signal qualifies as a TYPE_B_HUNTER entry.

    Returns:
        (entered, detail_dict) where detail_dict has score, reasons, and block reason.
    """
    features = features or {}
    detail = {"lane": LANE_ID, "score": 0.0, "entered": False, "block_reason": None, "breakdown": {}}

    # Absolute safety floors
    if ai_prob < MIN_CONFIDENCE_FLOOR:
        detail["block_reason"] = f"CONFIDENCE_FLOOR ({ai_prob} < {MIN_CONFIDENCE_FLOOR})"
        return False, detail

    spread = _safe_int(
        features.get("spread") or features.get("conviction_spread")
        or features.get("directional_spread")
    )
    if spread < MIN_SPREAD_FLOOR:
        detail["block_reason"] = f"SPREAD_FLOOR ({spread} < {MIN_SPREAD_FLOOR})"
        return False, detail

    # Composite scoring
    score = get_type_b_score(ai_prob, features)
    detail["score"] = score

    # Build breakdown for audit
    detail["breakdown"] = {
        "ai_prob": ai_prob,
        "delta": _safe_float(features.get("delta") or features.get("features_delta")),
        "volume_ratio": _safe_float(features.get("volume_ratio") or features.get("features_volume_ratio")),
        "adx": _safe_float(features.get("adx") or features.get("adx_at_entry")),
        "spread": spread,
        "ema_slope": str(features.get("ema_slope") or ""),
        "structure": _safe_float(features.get("structure_bias_at_entry") or features.get("structure")),
        "edge_score": _safe_float(features.get("edge_score")),
        "composite_score": score,
        "threshold": MIN_SCORE_TO_ENTER,
    }

    if score >= MIN_SCORE_TO_ENTER:
        detail["entered"] = True
        return True, detail

    detail["block_reason"] = f"SCORE_BELOW_THRESHOLD ({score} < {MIN_SCORE_TO_ENTER})"
    return False, detail


def classify_type(cohort_pct: float) -> str:
    """Post-trade classification based on MFE %.
    Used for shadow outcome labeling — not for entry decisions.
    """
    if cohort_pct >= TYPE_B_MIN_MFE_PCT:
        return "TYPE_B"
    if cohort_pct <= TYPE_A_MAX_MFE_PCT:
        return "TYPE_A"
    return "MIXED"


# ── Helpers ──────────────────────────────────────────────────────────────
def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0
