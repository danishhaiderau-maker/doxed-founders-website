"""
SR_MICRO_TILE_V2 — Deterministic micro S/R bracket tile (no AI).

Legacy collector lane (RESEARCH_LANE_SR_MICRO_TILE_V2): historical FULL_CHASE
dual-leg bracket. SHORT leg now disabled by default; only LONG@micro_support
remains as a research data point. This module owns both the V2 (full-chase)
and V2_STATIC (resting limit) tunings; the FROZEN policy for promotion-track
collection is the V2_STATIC LONG-only lane under POLICY_ID below.

  V2 (legacy lane)   = FULL_CHASE baseline — historical data only.
  V2_STATIC (frozen) = resting limit at exact micro S/R, never chase/reprice,
                       LONG-only, ADX<=40, LONDON blacklisted, $20 paper margin.

Tile 2 normalized holdout policy:
  - No AI
  - LONG at micro_support only (SHORT fully disabled from execution + dashboard)
  - London bucket blacklisted (08:00-12:59 UTC)
  - ADX must be present and <= 40 (fail-closed on missing ADX)
  - STATIC resting limit at exact micro_support
  - No chase, reprice, or slide
  - Midpoint guard retained unchanged
  - $20 paper margin
  - Entry TTL: 30 minutes (UNFILLED resting limit expiry only)
  - Thesis fast cut remains -12% (semantics fixed, threshold unchanged)
  - Scenario C 12->10 ladder PROVISIONAL (separate exit-profile cohort)

The legacy "places LONG + SHORT" wording is OBSOLETE. Use POLICY_LABEL.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Tile 2 frozen policy identifiers (Section 8 of static integrity repair).
#
# These are STABLE identifiers that travel with every outcome record so that
# the independent holdout cohort can never be silently confused with the
# archived historical 346-row training sample.
# ---------------------------------------------------------------------------
POLICY_ID = "sr_micro_static_normalized_adx_vol_v1_20260718"
POLICY_LABEL = (
    "Tile 2: normalized ADX/ATR-volatility, LONG@micro-support only "
    "(ADX<=40, no LONDON, paper)"
)
# Exit-profile ID for the canonical Scenario C 12->10 ladder used by Tile 2.
# A separate EXIT_PROFILE_*_PROVISIONAL tag tracks the 12->10 ladder cohort
# (19 historical fills) which is held frozen and evaluated separately.
EXIT_PROFILE_ID = "scenario_c_ladder_12_to_10_v1"
EXIT_PROFILE_ID_PROVISIONAL = "scenario_c_ladder_12_to_10_v1_provisional"
# Threshold retained from historical training sample; do NOT regress.
THESIS_FAST_CUT_UNREAL_PCT = -12.0

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

# Global trend/volatility caps (apply to V2 full-chase baseline).
ADX_TRENDING_THRESHOLD = 40
VOLATILITY_PCT_THRESHOLD = 80

# v12 per-tile tuning for SR_MICRO_TILE_V2_STATIC (from 346-trade LAB dataset).
# Reverted cap to 40 after full re-analysis: the ADX 35-40 bucket is the
# second-best in the dataset (+$0.47/close, 50% win), so capping at 35 was
# killing profitable trades. Original cap 40 retained.
#
# Data: SHORT lost -$0.26/close, LONG made +$0.38/close -> SHORT leg disabled.
# Data: LONDON session = -$0.54/close, ASIA = +$0.49/close -> blacklist LONDON.
# Data: Real bleed is THESIS_FAST_CUT (-$165.60 / 69 stops = -$2.40 each), not ADX.
STATIC_ADX_TRENDING_THRESHOLD = 40
STATIC_DISABLE_SHORT_LEG = True
STATIC_SESSION_BLACKLIST = frozenset({"LONDON"})

BRACKET_TICK_MIN_SEC = 10
BRACKET_TICK_MAX_SEC = 30


# ---------------------------------------------------------------------------
# Section 7 of Tile 2 static integrity repair.
#
# Explicit UTC session bucket boundaries. "LONDON" means 08:00-12:59 UTC
# explicitly -- the historical sample showed LONDON = -$0.54/close vs ASIA
# +$0.49/close, so the bucket is blacklisted for the frozen Tile 2 policy.
#
# Boundaries are [start_hour, end_hour_inclusive) so a bucket is exactly 4h.
# Hour values are UTC hours 0-23.
# ---------------------------------------------------------------------------
SESSION_BUCKET_BOUNDARIES_UTC = {
    "ASIA": (0, 8),       # 00:00-07:59 UTC
    "LONDON": (8, 13),    # 08:00-12:59 UTC (blacklisted for STATIC)
    "NEWYORK": (13, 17),  # 13:00-16:59 UTC
    "OVERLAP": (17, 21),  # 17:00-20:59 UTC
    "OFF": (21, 24),      # 21:00-23:59 UTC
}


def utc_session_bucket_for_hour(hour_utc: int) -> str:
    """Return the session bucket name for a given UTC hour (0-23).

    Used by Section 7 to log the exact UTC session bucket alongside every
    bracket evaluation, so the historical ambiguity about what 'LONDON'
    means cannot recur.
    """
    try:
        h = int(hour_utc)
    except (TypeError, ValueError):
        return ""
    if not (0 <= h <= 23):
        return ""
    for name, (lo, hi) in SESSION_BUCKET_BOUNDARIES_UTC.items():
        if lo <= h < hi:
            return name
    return ""


def london_blackout_active(hour_utc: int) -> bool:
    """True iff the given UTC hour is inside the LONDON blackout (08:00-12:59 UTC)."""
    try:
        h = int(hour_utc)
    except (TypeError, ValueError):
        return False
    return 8 <= h <= 12


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
    lane=None,
    session_bucket=None,
) -> dict:
    """Evaluate dual-leg bracket arming — no AI, structural rules only.

    Per-lane tuning (v12):
      - STATIC lane uses ADX cap 40 (cap reverted from 35 after re-analysis:
        the 35-40 bucket was the second-best cohort in the historical sample,
        so capping at 35 was killing profitable trades). STATIC also disables
        the SHORT leg and blacklists LONDON session. Pass
        lane=RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC to opt in; the V2
        full-chase lane keeps the historical baseline.
    """
    lane_u = str(lane or "").upper()
    is_static = lane_u == RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC
    adx_cap = STATIC_ADX_TRENDING_THRESHOLD if is_static else ADX_TRENDING_THRESHOLD
    px = _sf(price)
    ms = _sf(micro_support)
    mr = _sf(micro_resistance)
    sl = _sf(swing_low)
    sh = _sf(swing_high)
    adx_v = _sf(adx)
    vol_v = _sf(vol_pct)
    sess = str(session_bucket or "").upper()

    result = {
        "lane": lane_u or LANE_ID,
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
        "session_bucket": sess,
        "in_midpoint_zone": False,
    }

    if not all([px, ms, mr, sl, sh]):
        result["block_reason"] = "MISSING_LEVELS"
        result["zone"] = "INCOMPLETE"
        return result

    if is_static and sess and sess in STATIC_SESSION_BLACKLIST:
        result["block_reason"] = f"SESSION_BLACKLISTED_{sess}"
        result["zone"] = "SUSPENDED"
        return result

    # Section 7: Tile 2 (STATIC) must FAIL CLOSED when ADX is missing.
    # The historical sample had ADX missing from 42 final-filter outcomes,
    # and the previous logic (only check when adx_v is not None) meant the
    # cap silently passed whenever ADX was absent. The frozen Tile 2 policy
    # requires ADX to be present AND <= cap; otherwise the bracket is
    # refused with MISSING_ADX.
    if is_static and adx_v is None:
        result["block_reason"] = "MISSING_ADX"
        result["zone"] = "SUSPENDED"
        return result

    # Section 7: same fail-closed semantics for volatility percentile. The
    # historical sample had vol_pct missing from all 346 outcomes, so any
    # claim that the gate was evaluated was wrong. For STATIC we now refuse
    # when vol_pct is missing -- the gate is either real or it is not.
    if is_static and vol_v is None:
        result["block_reason"] = "MISSING_VOLATILITY"
        result["zone"] = "SUSPENDED"
        return result

    if adx_v is not None and adx_v > adx_cap:
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
    short_qualifies = bool(px < mr and not short_mid_block and not short_too_far)
    # v12 STATIC-only: SHORT leg historically loses (-$0.26/close vs LONG +$0.38).
    # Disable it for STATIC lane; V2 full-chase baseline keeps dual-leg behavior.
    if is_static and STATIC_DISABLE_SHORT_LEG:
        result["short_armed"] = False
        if short_qualifies:
            result["short_leg_disabled_v12"] = True
    else:
        result["short_armed"] = short_qualifies
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


# ---------------------------------------------------------------------------
# Section 4 of Tile 2 static integrity repair.
#
# Episode ID + one-trade-per-episode guard.
#
# The historical sample inflated fills because a new bracket tick every ~20s
# could spawn a new shadow / paper limit on the same support level while the
# previous trade on that thesis was still active. The frozen Tile 2 policy
# requires at most ONE pending or open Tile 2 LONG per S/R episode.
#
# Episode ID derivation:
#   - micro_support (the level we're trying to buy)
#   - micro_resistance (defines the S/R range)
#   - pivot_revision (a coarse counter that bumps when the S/R levels move)
#   - bucket_ts (the UTC session bucket start, so episodes don't bleed across
#     very different times of day even if the level is identical)
#
# The resulting string is short, stable, and human-auditable. It is stamped
# on every outcome record (Section 8) and used as the dedup key for the
# one-trade-per-episode guard.
# ---------------------------------------------------------------------------

# How much the support/resistance levels must move (in absolute price units)
# before we count it as a new "pivot revision". Small noise within this band
# is treated as the same pivot. Tuned for BTC at the ~$60-120k range.
EPISODE_PIVOT_BAND_USD = 25.0


def derive_sr_episode_id(
    micro_support,
    micro_resistance,
    session_bucket: str = "",
    session_bucket_start_ts: float = 0.0,
    pivot_band_usd: float = EPISODE_PIVOT_BAND_USD,
) -> str:
    """Stable S/R episode ID for one-trade-per-episode dedup (Section 4).

    Buckets the support level to the nearest `pivot_band_usd` so that
    micro-noise on the support level does not inflate the episode count.
    Includes the UTC session bucket so that identical levels seen in very
    different sessions are distinct episodes.
    """
    ms = _sf(micro_support)
    mr = _sf(micro_resistance)
    if ms is None or mr is None or ms <= 0 or mr <= 0:
        return ""
    band = max(1.0, float(pivot_band_usd or EPISODE_PIVOT_BAND_USD))
    ms_b = int(round(ms / band)) * band
    mr_b = int(round(mr / band)) * band
    bucket_label = str(session_bucket or "").upper().strip()
    if session_bucket_start_ts:
        try:
            bucket_label = f"{bucket_label}:{int(float(session_bucket_start_ts))}"
        except (TypeError, ValueError):
            pass
    return f"ms{ms_b:.0f}-mr{mr_b:.0f}-{bucket_label}"


def episode_id_matches_open_trade(
    episode_id: str,
    open_episodes_seen: list,
) -> bool:
    """True iff this episode_id already has an open or pending Tile 2 trade.

    `open_episodes_seen` is the list of episode IDs currently attached to
    open/pending Tile 2 trades (caller-supplied).
    """
    if not episode_id:
        return False
    return any(str(x) == str(episode_id) for x in (open_episodes_seen or []))
