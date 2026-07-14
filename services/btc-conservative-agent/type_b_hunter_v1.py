"""Deterministic, pre-entry gate for the Tile 1 Type B paper study.

``TYPE_B`` remains a post-trade MFE label.  This module does not use that
label to make an entry decision.  The constants below are a fixed,
prospective policy: it must not be retuned until its own paper cohort has
completed the declared evaluation window.
"""
from __future__ import annotations


TYPE_B_MIN_MFE_PCT = 15
TYPE_A_MAX_MFE_PCT = 10

# The lane id remains stable so the three-lane architecture and dashboard do
# not gain a fourth lane.  The policy id splits new outcomes from legacy LAB
# simulations, which were collected under the old confidence/volume scorer.
LANE_ID = "TYPE_B_HUNTER_V1"
LANE_LABEL = "Type B Hunter — prospective fixed policy v2a"
LANE_ID_PREFIX = "tbhv1"
LANE_STATUS = "SHADOW_COLLECTING"
IS_INDEPENDENT_AI = True
POLICY_VERSION = "type_b_fixed_policy_v2a_20260715"

# Independent AI fires sixty seconds after CONTINUOUS in the three-lane setup.
AI_OFFSET_SEC = 60
AI_MAX_AGE_SEC = 180
AI_PROMPT_FOCUS = (
    "TYPE_B_HUNTER: return a direction and market facts. The deterministic "
    "policy, not the model's self-reported confidence, decides whether to enter."
)

# Pre-registered fixed-policy thresholds.  They are deliberately explicit so
# walk-forward reports can identify exactly which policy produced each record.
MIN_SCORE_TO_ENTER = 3.0
MIN_SPREAD_FLOOR = 2
ADX_FLOOR = 20.0
VOLUME_DANGER = 2.0


def _market_context(features: dict) -> dict:
    context = features.get("market_context") or {}
    return context if isinstance(context, dict) else {}


def _market_structure(features: dict) -> dict:
    structure = _market_context(features).get("market_structure") or {}
    return structure if isinstance(structure, dict) else {}


def _first_number(*values) -> float | None:
    for value in values:
        result = _safe_float(value)
        if result is not None:
            return result
    return None


def _normalise_direction(value) -> str:
    direction = str(value or "").upper()
    return direction if direction in {"LONG", "SHORT"} else ""


def _normalise_regime(value) -> str:
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


def resolve_features(features: dict, direction: str = None) -> dict:
    """Normalize only features available before an entry is made."""
    features = features or {}
    market_context = _market_context(features)
    trend_strength = market_context.get("trend_strength") or {}
    if not isinstance(trend_strength, dict):
        trend_strength = {}
    structure_context = _market_structure(features)
    resolved_direction = _normalise_direction(direction or features.get("direction"))
    return {
        "direction": resolved_direction,
        "delta": _first_number(
            features.get("delta"), features.get("features_delta"), features.get("orderflow_delta")
        ),
        "volume_ratio": _first_number(
            features.get("volume_ratio"), features.get("features_volume_ratio"), features.get("vol_ratio")
        ),
        "adx": _first_number(
            features.get("adx"), features.get("adx_at_entry"), features.get("mom_adx"), trend_strength.get("adx")
        ),
        "spread": _safe_int(
            features.get("spread") or features.get("conviction_spread") or features.get("directional_spread")
        ),
        "regime": _normalise_regime(
            features.get("regime") or market_context.get("regime") or structure_context.get("regime")
        ),
        "structure": _first_number(
            features.get("structure_score"), features.get("structure_score_at_entry"),
            features.get("structure_bias_at_entry"), features.get("structure"),
            structure_context.get("structure_score")
        ),
        "edge": _first_number(features.get("edge_score"), features.get("controls_edge_threshold")),
        "ema_slope": str(features.get("ema_slope") or features.get("ema_hybrid_slope") or "").lower(),
    }


def get_type_b_score(ai_prob: int, features: dict, direction: str = None) -> float:
    """Return the fixed policy score without using AI probability as a feature.

    ``ai_prob`` remains an argument for backwards-compatible callers and audit
    rows, but it intentionally contributes zero to the score.
    """
    values = resolve_features(features, direction)
    score = 0.0
    adx = values["adx"]
    volume_ratio = values["volume_ratio"]
    structure = values["structure"]
    edge = values["edge"]
    trade_direction = values["direction"]

    if adx is not None:
        if 20.0 <= adx < 25.0:
            score += 1.5
        elif 25.0 <= adx < 30.0:
            score += 1.2
        elif 30.0 <= adx <= 35.0:
            score += 0.75
        elif adx > 35.0:
            score += 0.25

    if volume_ratio is not None:
        if volume_ratio < 0.80:
            score += 1.0
        elif volume_ratio < 1.20:
            score += 0.5

    if values["regime"] == "BEAR":
        score += 0.5
    elif values["regime"] == "RANGE":
        score += 0.25

    if structure is not None:
        if (trade_direction == "SHORT" and structure <= -3.0) or (
            trade_direction == "LONG" and structure >= 3.0
        ):
            score += 1.0
        elif -3.0 < structure < 3.0:
            score += 0.25

    delta = values["delta"]
    if (trade_direction == "LONG" and delta is not None and delta >= 18.0) or (
        trade_direction == "SHORT" and delta is not None and delta <= -18.0
    ):
        score += 0.75

    if 3 <= values["spread"] <= 5:
        score += 0.5
    if edge is not None and 3.0 <= edge <= 5.0:
        score += 0.5

    ema_slope = values["ema_slope"]
    if (trade_direction == "LONG" and ema_slope in {"up", "bullish", "positive"}) or (
        trade_direction == "SHORT" and ema_slope in {"down", "bearish", "negative"}
    ):
        score += 0.25
    return round(score, 2)


def should_enter_type_b(ai_prob: int, features: dict, direction: str = None) -> tuple[bool, dict]:
    """Evaluate the fixed Type B policy and return an auditable decision."""
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
    # BULL regime requires stronger trend confirmation — historically choppier, more false breakouts
    if values["regime"] == "BULL" and values["adx"] is not None and values["adx"] < 25.0:
        detail["block_reason"] = f"BULL_NEEDS_ADX_25_PLUS (adx={values['adx']:.2f})"
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


def classify_type(cohort_pct: float) -> str:
    """Post-trade classification only; never an entry input."""
    if cohort_pct >= TYPE_B_MIN_MFE_PCT:
        return "TYPE_B"
    if cohort_pct <= TYPE_A_MAX_MFE_PCT:
        return "TYPE_A"
    return "MIXED"


def self_test() -> None:
    favorable = {
        "adx": 22.0, "volume_ratio": 0.6, "spread": 4, "regime": "BEAR",
        "structure_score": -4.0, "edge_score": 4.0, "delta": -22.0, "ema_slope": "down",
    }
    entered, detail = should_enter_type_b(1, favorable, "SHORT")
    assert entered and detail["score"] >= MIN_SCORE_TO_ENTER
    assert should_enter_type_b(99, {**favorable, "volume_ratio": 2.1}, "SHORT")[0] is False
    assert should_enter_type_b(99, {**favorable, "adx": 19.9}, "SHORT")[0] is False
    assert should_enter_type_b(99, favorable, "LONG")[0] is False
    assert should_enter_type_b(99, {**favorable, "regime": "UNKNOWN"}, "SHORT")[0] is False
    nested = {"market_context": {"trend_strength": {"adx": 22}, "market_structure": {"regime": "BEAR", "structure_score": -4}}, **{k: v for k, v in favorable.items() if k not in {"adx", "regime", "structure_score"}}}
    assert should_enter_type_b(1, nested, "SHORT")[0] is True
    # BULL regime gate: ADX < 25 must BLOCK even with otherwise favorable features
    bull_favorable_low_adx = {
        "adx": 22.0, "volume_ratio": 0.6, "spread": 4, "regime": "BULL",
        "structure_score": 4.0, "edge_score": 4.0, "delta": 22.0, "ema_slope": "up",
    }
    blocked_low, low_detail = should_enter_type_b(1, bull_favorable_low_adx, "LONG")
    assert blocked_low is False
    assert low_detail["block_reason"].startswith("BULL_NEEDS_ADX_25_PLUS")
    # BULL regime with ADX >= 25 passes the new gate and enters when score is sufficient
    bull_favorable_high_adx = {**bull_favorable_low_adx, "adx": 26.0}
    entered_bull, bull_detail = should_enter_type_b(1, bull_favorable_high_adx, "LONG")
    assert entered_bull is True
    assert bull_detail["score"] >= MIN_SCORE_TO_ENTER


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
