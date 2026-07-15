"""
SR_MICRO_TILE_V2 — Deterministic micro S/R bracket tile (no AI).

Places simultaneous LONG@micro_support + SHORT@micro_resistance when structural
envelope + midpoint rules pass. Phase 1: shadow LAB replay only (toggle OFF default).

Chase experiment (2026-07-12):
  V2 (this lane) = FULL_CHASE baseline — keep collecting unchanged.
  SR_MICRO_TILE_V2_STATIC = resting limit at exact micro S/R, never chase/reprice.
  LIGHT_CHASE (max_chases=1) reserved as Phase 2 — scaffolding only.
"""
from __future__ import annotations

RESEARCH_LANE_SR_MICRO_TILE_V2 = "SR_MICRO_TILE_V2"
RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC = "SR_MICRO_TILE_V2_STATIC"
LANE_ID = RESEARCH_LANE_SR_MICRO_TILE_V2
LANE_LABEL = "S/R Micro Tile V2 — deterministic bracket (no AI)"
LANE_ID_PREFIX = "srmv2"
LANE_STATUS = "SHADOW_COLLECTING"
IS_DETERMINISTIC_BRACKET = True
IS_INDEPENDENT_AI = False

# Entry chase modes for bracket LAB replay A/B
CHASE_MODE_FULL = "FULL_CHASE"       # current V2 behaviour (do not change V2)
CHASE_MODE_STATIC = "STATIC"         # never move limit; fill at exact S/R or TTL
CHASE_MODE_LIGHT = "LIGHT_CHASE"     # Phase 2: max 1 chase step

STATIC_LANE_ID = RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC
STATIC_LANE_LABEL = "S/R Micro Tile V2 Static — resting limit (no chase)"
STATIC_LANE_ID_PREFIX = "srmv2s"
STATIC_MAX_CHASES = 0
LIGHT_MAX_CHASES = 1
FULL_MAX_CHASES = None  # unlimited / global chase policy

MIDPOINT_BUFFER_PCT = 0.15
MAX_CHASE_DIST_PCT = 0.30

ADX_TRENDING_THRESHOLD = 40
VOLATILITY_PCT_THRESHOLD = 80

BRACKET_TICK_MIN_SEC = 10
BRACKET_TICK_MAX_SEC = 30


def chase_mode_for_lane(lane: str) -> str:
    """Map lane id → chase mode for spawn/fill telemetry."""
    lane_u = str(lane or "").upper()
    if lane_u == RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC:
        return CHASE_MODE_STATIC
    if lane_u == RESEARCH_LANE_SR_MICRO_TILE_V2:
        return CHASE_MODE_FULL
    return CHASE_MODE_FULL


def max_chases_for_mode(mode: str):
    mode_u = str(mode or "").upper()
    if mode_u == CHASE_MODE_STATIC:
        return STATIC_MAX_CHASES
    if mode_u == CHASE_MODE_LIGHT:
        return LIGHT_MAX_CHASES
    return FULL_MAX_CHASES


def trade_id_prefix_for_lane(lane: str) -> str:
    lane_u = str(lane or "").upper()
    if lane_u == RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC:
        return STATIC_LANE_ID_PREFIX
    return LANE_ID_PREFIX


def evaluate_bracket(
    price,
    micro_support,
    micro_resistance,
    swing_low,
    swing_high,
    adx=None,
    vol_pct=None,
) -> dict:
    """Evaluate dual-leg bracket arming — no AI, structural rules only."""
    px = _sf(price)
    ms = _sf(micro_support)
    mr = _sf(micro_resistance)
    sl = _sf(swing_low)
    sh = _sf(swing_high)
    adx_v = _sf(adx)
    vol_v = _sf(vol_pct)

    result = {
        "lane": LANE_ID,
        "armed": False,
        "long_armed": False,
        "short_armed": False,
        "long_limit": ms,
        "short_limit": mr,
        "zone": None,
        "block_reason": None,
        "midpoint": None,
        "sr_range": None,
        "price": px,
        "micro_support": ms,
        "micro_resistance": mr,
        "swing_low": sl,
        "swing_high": sh,
        "adx": adx_v,
        "vol_pct": vol_v,
        "in_midpoint_zone": False,
    }

    if not all([px, ms, mr, sl, sh]):
        result["block_reason"] = "MISSING_LEVELS"
        result["zone"] = "INCOMPLETE"
        return result

    if adx_v is not None and adx_v > ADX_TRENDING_THRESHOLD:
        result["block_reason"] = "ADX_TRENDING"
        result["zone"] = "SUSPENDED"
        return result
    if vol_v is not None and vol_v > VOLATILITY_PCT_THRESHOLD:
        result["block_reason"] = "VOLATILITY_SPIKE"
        result["zone"] = "SUSPENDED"
        return result

    if ms < sl or mr > sh:
        result["block_reason"] = "MICRO_OUTSIDE_SWING"
        result["zone"] = "STRUCTURAL"
        return result

    if not (sl <= px <= sh):
        result["block_reason"] = "PRICE_OUTSIDE_ENVELOPE"
        result["zone"] = "ENVELOPE"
        return result

    sr = mr - ms
    if sr <= 0:
        result["block_reason"] = "INVALID_SR_RANGE"
        result["zone"] = "STRUCTURAL"
        return result

    mp = ms + sr / 2
    result["midpoint"] = round(mp, 2)
    result["sr_range"] = round(sr, 2)
    result["long_limit"] = round(ms, 2)
    result["short_limit"] = round(mr, 2)

    in_midpoint = abs(px - mp) < sr * MIDPOINT_BUFFER_PCT
    result["in_midpoint_zone"] = in_midpoint
    if in_midpoint:
        result["block_reason"] = "MIDPOINT"
        result["zone"] = "MIDPOINT"
        return result

    long_mid_block = (mp - px) < sr * MIDPOINT_BUFFER_PCT
    short_mid_block = (px - mp) < sr * MIDPOINT_BUFFER_PCT

    long_dist = px - ms
    short_dist = mr - px
    long_too_far = long_dist > sr * MAX_CHASE_DIST_PCT * 2
    short_too_far = short_dist > sr * MAX_CHASE_DIST_PCT * 2

    result["long_armed"] = bool(px > ms and not long_mid_block and not long_too_far)
    result["short_armed"] = bool(px < mr and not short_mid_block and not short_too_far)
    result["armed"] = bool(result["long_armed"] or result["short_armed"])

    if not result["armed"]:
        if long_too_far and short_too_far:
            result["block_reason"] = "TOO_FAR_BOTH"
        elif long_mid_block and short_mid_block:
            result["block_reason"] = "MIDPOINT"
        elif not result["long_armed"] and not result["short_armed"]:
            result["block_reason"] = "NO_LEG_QUALIFIED"
        else:
            result["block_reason"] = "LEG_FILTER"
        result["zone"] = "FILTERED"
        return result

    result["zone"] = "BRACKET"
    result["block_reason"] = None
    return result


def should_enter_bracket_leg(side: str, eval_result: dict) -> bool:
    """Per-leg gate after bracket evaluation."""
    if not eval_result or eval_result.get("block_reason"):
        return False
    side_u = str(side or "").upper()
    if side_u == "LONG":
        return bool(eval_result.get("long_armed"))
    if side_u == "SHORT":
        return bool(eval_result.get("short_armed"))
    return False


def bracket_limit_pullback(side: str, market_price: float, limit_price: float) -> tuple:
    """Return (start_price, pullback_pct) for shadow limit fill at bracket level."""
    side_u = str(side or "").upper()
    px = _sf(market_price) or 0.0
    lim = _sf(limit_price) or 0.0
    if px <= 0 or lim <= 0:
        return 0.0, 0.0
    if side_u == "LONG":
        if lim >= px:
            return px, 0.0
        return px, max(0.0, (px - lim) / px)
    if side_u == "SHORT":
        if lim <= px:
            return px, 0.0
        return px, max(0.0, (lim - px) / px)
    return px, 0.0


def _sf(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
