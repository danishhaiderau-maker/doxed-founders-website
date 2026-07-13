"""
SR_MICRO_TILE_V1 — Micro Support/Resistance mean-reversion tile.

Places LONG at micro support and SHORT at micro resistance simultaneously.
Midpoint avoidance: never chase beyond the midpoint between S and R.
Volatility guard: suspend when ADX spikes or volatility percentile > threshold.
Independent AI prompt — fires at T+120s in 3-lane cadence.
"""
from __future__ import annotations

LANE_ID                  = "SR_MICRO_TILE_V1"
LANE_LABEL               = "S/R Micro Tile V1 — micro support/resistance mean-reversion"
LANE_ID_PREFIX           = "srmv1"
LANE_STATUS              = "SHADOW_COLLECTING"
IS_INDEPENDENT_AI        = True

AI_OFFSET_SEC            = 120
AI_MAX_AGE_SEC           = 180

MIDPOINT_BUFFER_PCT       = 0.15
MAX_CHASE_DIST_PCT        = 0.30
CHASE_STEP_PCT            = 0.15

ADX_TRENDING_THRESHOLD    = 40
VOLATILITY_PCT_THRESHOLD  = 80
SUSPEND_COOLDOWN_SEC      = 600

MARGIN_USD_SHORT          = 10
MARGIN_USD_LONG           = 10
STOP_LOSS_PCT             = -12
FIXED_TIME_EXIT_SEC       = 3600


def should_enter_sr(side: str, features: dict, ai_prob: int) -> tuple:
    """Determine if signal qualifies for S/R entry.

    Returns (entered: bool, detail: dict).
    """
    features = features or {}
    detail = {"lane": LANE_ID, "side": side, "entered": False, "score": 0.0, "block_reason": None}

    adx = _sf(features.get("adx") or features.get("adx_at_entry"))
    vol_pct = _sf(features.get("volatility_percentile") or features.get("vol_pct"))
    if adx is not None and adx > ADX_TRENDING_THRESHOLD:
        detail["block_reason"] = "ADX_TRENDING"
        return False, detail
    if vol_pct is not None and vol_pct > VOLATILITY_PCT_THRESHOLD:
        detail["block_reason"] = "VOLATILITY_SPIKE"
        return False, detail

    ms = _sf(features.get("micro_support") or features.get("nearest_support_price"))
    mr = _sf(features.get("micro_resistance") or features.get("nearest_resistance_price"))
    px = _sf(features.get("price") or features.get("signal_price") or features.get("entry_price"))

    if not all([ms, mr, px]):
        detail["block_reason"] = "MISSING_SR_LEVELS"
        return False, detail

    sr = mr - ms
    if sr <= 0:
        detail["block_reason"] = "INVALID_SR_RANGE"
        return False, detail

    mp = ms + sr / 2
    detail["sr_range"] = round(sr, 2)
    detail["midpoint"] = round(mp, 2)

    if side == "LONG":
        d = px - ms
        if d > sr * MAX_CHASE_DIST_PCT * 2:
            detail["block_reason"] = "TOO_FAR"
            return False, detail
        if (mp - px) < sr * MIDPOINT_BUFFER_PCT:
            detail["block_reason"] = "MIDPOINT"
            return False, detail
        detail["dist_to_support"] = round(d, 2)
    else:
        d = mr - px
        if d > sr * MAX_CHASE_DIST_PCT * 2:
            detail["block_reason"] = "TOO_FAR"
            return False, detail
        if (px - mp) < sr * MIDPOINT_BUFFER_PCT:
            detail["block_reason"] = "MIDPOINT"
            return False, detail
        detail["dist_to_resistance"] = round(d, 2)

    dp = d / sr if sr > 0 else 1.0
    if dp <= 0.10:
        score = 2.0
    elif dp <= 0.20:
        score = 1.0
    elif dp <= 0.30:
        score = 0.5
    else:
        score = 0.0

    if ai_prob >= 55:
        score += 0.5
    if ai_prob >= 65:
        score += 0.5

    struct = str(features.get("structure_bias") or features.get("structure_bias_at_entry") or "").upper()
    if side == "LONG" and struct in ("BULLISH_STRUCTURE", "BULL_ALIGNED"):
        score += 0.5
    elif side == "SHORT" and struct in ("BEARISH_STRUCTURE", "BEAR_ALIGNED"):
        score += 0.5

    detail["score"] = round(score, 2)
    detail["entered"] = score >= 1.0
    if not detail["entered"]:
        detail["block_reason"] = "LOW_SCORE"
    return detail["entered"], detail


def resolve_chase_limit(side: str, features: dict) -> float:
    ms = _sf((features or {}).get("micro_support"))
    mr = _sf((features or {}).get("micro_resistance"))
    if not ms or not mr:
        return 0.0
    sr = mr - ms
    if sr <= 0:
        return 0.0
    return ms + sr * MAX_CHASE_DIST_PCT if side == "LONG" else mr - sr * MAX_CHASE_DIST_PCT


def resolve_chase_step(side: str, features: dict) -> float:
    f = features or {}
    ms = _sf(f.get("micro_support"))
    mr = _sf(f.get("micro_resistance"))
    px = _sf(f.get("price") or f.get("signal_price"))
    if not all([ms, mr, px]):
        return 0.0
    sr = mr - ms
    if sr <= 0:
        return 0.0
    mp = ms + sr / 2
    return max(0, mp - px) * CHASE_STEP_PCT if side == "LONG" else max(0, px - mp) * CHASE_STEP_PCT


def _sf(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
