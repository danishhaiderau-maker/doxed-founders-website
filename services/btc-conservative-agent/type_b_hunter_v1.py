"""Deterministic pre-entry gate for Tile 1 Type B Hunter.
v12 policy — ADX-flipped, volume-inverted, regime-aware, confidence-blind.
Fixed before evaluation; outcome labels not used for tuning.
"""
from __future__ import annotations

TYPE_B_MIN_MFE_PCT = 15
TYPE_A_MAX_MFE_PCT = 10

LANE_ID = "TYPE_B_HUNTER_V1"
LANE_LABEL = "Type B Hunter -- v12 fixed policy (ADX-flipped)"
LANE_ID_PREFIX = "tbhv1"
LANE_STATUS = "SHADOW_COLLECTING"
IS_INDEPENDENT_AI = True
POLICY_VERSION = "type_b_v12_20260716"

AI_OFFSET_SEC = 60
AI_MAX_AGE_SEC = 180
AI_PROMPT_FOCUS = "TYPE_B_HUNTER: return direction and market facts. The deterministic policy decides entry."

MIN_SCORE_TO_ENTER = 3.0
MIN_SPREAD_FLOOR = 2
ADX_FLOOR = 20.0
BULL_ADX_FLOOR = 28.0
VOLUME_DANGER = 2.0


def _safe_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _market_context(features):
    context = features.get("market_context") or {}
    return context if isinstance(context, dict) else {}


def _market_structure(features):
    structure = _market_context(features).get("market_structure") or {}
    return structure if isinstance(structure, dict) else {}


def _first_number(*values):
    for value in values:
        result = _safe_float(value)
        if result is not None:
            return result
    return None


def _normalise_direction(value):
    direction = str(value or "").upper()
    return direction if direction in {"LONG", "SHORT"} else ""


def _normalise_regime(value):
    regime = str(value or "").upper()
    if regime.startswith("BEAR"):
        return "BEAR"
    if regime.startswith("BULL"):
        return "BULL"
    if regime.startswith("RANGE"):
        return "RANGE"
    if regime.startswith("CHOP"):
        return "CHOPPY"
    if regime.startswith("EXPANS"):
        return "EXPANSION"
    return regime or "UNKNOWN"


def resolve_features(features, direction=None):
    """Normalize features available before entry."""
    features = features or {}
    market_context = _market_context(features)
    trend_strength = market_context.get("trend_strength") or {}
    if not isinstance(trend_strength, dict):
        trend_strength = {}
    structure_context = _market_structure(features)
    resolved_direction = _normalise_direction(direction or features.get("direction"))
    return {
        "direction": resolved_direction,
        "delta": _first_number(features.get("delta"), features.get("features_delta"), features.get("orderflow_delta")),
        "volume_ratio": _first_number(features.get("volume_ratio"), features.get("features_volume_ratio"), features.get("vol_ratio")),
        "adx": _first_number(features.get("adx"), features.get("adx_at_entry"), features.get("mom_adx"), trend_strength.get("adx")),
        "spread": _safe_int(features.get("spread") or features.get("conviction_spread") or features.get("directional_spread")),
        "regime": _normalise_regime(features.get("regime") or market_context.get("regime") or structure_context.get("regime")),
        "structure": _first_number(features.get("structure_score"), features.get("structure_score_at_entry"), features.get("structure_bias_at_entry"), features.get("structure"), structure_context.get("structure_score")),
        "edge": _first_number(features.get("edge_score"), features.get("controls_edge_threshold")),
        "ema_slope": str(features.get("ema_slope") or features.get("ema_hybrid_slope") or "").lower(),
    }


def get_type_b_score(ai_prob, features, direction=None):
    """Return v12 policy score. ai_prob contributes ZERO (it is vestigial)."""
    values = resolve_features(features, direction)
    score = 0.0
    adx = values["adx"]
    volume_ratio = values["volume_ratio"]
    structure = values["structure"]
    edge = values["edge"]
    trade_direction = values["direction"]

    # ADX -- FLIPPED from V1. Fresh data: ADX 30+ = 41.9% Type B, 75.7% WR (BEST)
    if adx is not None:
        if 30.0 <= adx <= 35.0:
            score += 1.5   # OPTIMAL ZONE
        elif 25.0 <= adx < 30.0:
            score += 1.2   # STRONG
        elif adx > 35.0:
            score += 0.75  # VALID but watch exhaustion
        elif 20.0 <= adx < 25.0:
            score += 0.5   # WEAK -- only 28.9% Type B

    # VOLUME RATIO -- INVERTED. Type A mean=1.04, Type B mean=0.60. Low volume = accumulation.
    if volume_ratio is not None:
        if volume_ratio < 0.80:
            score += 1.0   # STRONG (quiet accumulation)
        elif volume_ratio < 1.20:
            score += 0.5   # NEUTRAL

    # REGIME -- BEAR historically best for Type B
    if values["regime"] == "BEAR":
        score += 0.5
    elif values["regime"] == "RANGE":
        score += 0.25

    # STRUCTURE -- direction-aware
    if structure is not None:
        if (trade_direction == "SHORT" and structure <= -3.0) or (trade_direction == "LONG" and structure >= 3.0):
            score += 1.0
        elif -3.0 < structure < 3.0:
            score += 0.25

    # DELTA -- direction-aware confirmation
    delta = values["delta"]
    if (trade_direction == "LONG" and delta is not None and delta >= 18.0) or (trade_direction == "SHORT" and delta is not None and delta <= -18.0):
        score += 0.75

    if 3 <= values["spread"] <= 5:
        score += 0.5
    if edge is not None and 3.0 <= edge <= 5.0:
        score += 0.5

    ema_slope = values["ema_slope"]
    if (trade_direction == "LONG" and ema_slope in {"up", "bullish", "positive"}) or (trade_direction == "SHORT" and ema_slope in {"down", "bearish", "negative"}):
        score += 0.25
    return round(score, 2)


def should_enter_type_b(ai_prob, features, direction=None):
    """Evaluate the v12 Type B policy. Returns (entered, detail)."""
    values = resolve_features(features, direction)
    detail = {
        "lane": LANE_ID,
        "policy_version": POLICY_VERSION,
        "score": 0.0,
        "entered": False,
        "block_reason": None,
        "breakdown": {**values, "ai_prob_audit_only": ai_prob, "threshold": MIN_SCORE_TO_ENTER},
    }

    if not values["direction"]:
        detail["block_reason"] = "NO_DIRECTION"
        return False, detail
    missing = [name for name in ("adx", "volume_ratio", "structure", "edge") if values[name] is None]
    if missing:
        detail["block_reason"] = f"MISSING_FEATURES ({','.join(missing)})"
        return False, detail
    if values["regime"] in {"UNKNOWN", "CHOPPY", "EXPANSION"}:
        detail["block_reason"] = f"REGIME_BLOCK ({values['regime']})"
        return False, detail
    if values["adx"] < ADX_FLOOR:
        detail["block_reason"] = f"ADX_FLOOR ({values['adx']:.2f} < {ADX_FLOOR:.0f})"
        return False, detail
    # BULL regime needs stronger trend confirmation (historically choppier)
    if values["regime"] == "BULL" and values["adx"] < BULL_ADX_FLOOR:
        detail["block_reason"] = f"BULL_NEEDS_ADX_{BULL_ADX_FLOOR:.0f}_PLUS (adx={values['adx']:.2f})"
        return False, detail
    if values["spread"] < MIN_SPREAD_FLOOR:
        detail["block_reason"] = f"SPREAD_FLOOR ({values['spread']} < {MIN_SPREAD_FLOOR})"
        return False, detail
    if values["volume_ratio"] > VOLUME_DANGER:
        detail["block_reason"] = f"VOLUME_DANGER ({values['volume_ratio']:.3f} > {VOLUME_DANGER:.1f})"
        return False, detail
    if values["direction"] == "LONG" and values["structure"] <= -3.0:
        detail["block_reason"] = "COUNTER_STRUCTURE_LONG"
        return False, detail
    if values["direction"] == "SHORT" and values["structure"] >= 3.0:
        detail["block_reason"] = "COUNTER_STRUCTURE_SHORT"
        return False, detail
    if values["edge"] > 5.0 and values["volume_ratio"] > 1.20:
        detail["block_reason"] = "EDGE_VOLUME_DANGER"
        return False, detail
    if values["adx"] > 35.0 and values["edge"] > 5.0 and values["volume_ratio"] > 1.50:
        detail["block_reason"] = "TRIPLE_DANGER"
        return False, detail

    score = get_type_b_score(ai_prob, features, values["direction"])
    detail["score"] = score
    detail["breakdown"]["composite_score"] = score
    if score >= MIN_SCORE_TO_ENTER:
        detail["entered"] = True
        return True, detail
    detail["block_reason"] = f"SCORE_BELOW_THRESHOLD ({score} < {MIN_SCORE_TO_ENTER})"
    return False, detail


def classify_type(cohort_pct):
    if cohort_pct >= TYPE_B_MIN_MFE_PCT:
        return "TYPE_B"
    if cohort_pct <= TYPE_A_MAX_MFE_PCT:
        return "TYPE_A"
    return "MIXED"


def self_test():
    favorable = {
        "adx": 32.0, "volume_ratio": 0.6, "spread": 4, "regime": "BEAR",
        "structure_score": -4.0, "edge_score": 4.0, "delta": -22.0, "ema_slope": "down",
    }
    entered, detail = should_enter_type_b(1, favorable, "SHORT")
    assert entered and detail["score"] >= MIN_SCORE_TO_ENTER, f"Should enter, got {detail}"
    assert should_enter_type_b(99, {**favorable, "volume_ratio": 2.1}, "SHORT")[0] is False
    assert should_enter_type_b(99, {**favorable, "adx": 19.9}, "SHORT")[0] is False
    assert should_enter_type_b(99, favorable, "LONG")[0] is False  # counter-structure
    assert should_enter_type_b(99, {**favorable, "regime": "UNKNOWN"}, "SHORT")[0] is False
    # BULL needs higher ADX
    bull_weak = {**favorable, "regime": "BULL", "adx": 24.0, "structure_score": 4.0, "delta": 22.0, "ema_slope": "up"}
    assert should_enter_type_b(99, bull_weak, "LONG")[0] is False  # BULL + ADX 24 blocked
    bull_strong = {**favorable, "regime": "BULL", "adx": 30.0, "structure_score": 4.0, "delta": 22.0, "ema_slope": "up"}
    ok, d = should_enter_type_b(99, bull_strong, "LONG")
    assert ok, f"BULL+ADX30 should enter, got {d}"
    # Nested context extraction
    nested = {"market_context": {"trend_strength": {"adx": 32}, "market_structure": {"regime": "BEAR", "structure_score": -4}}, **{k: v for k, v in favorable.items() if k not in {"adx", "regime", "structure_score"}}}
    assert should_enter_type_b(1, nested, "SHORT")[0] is True
