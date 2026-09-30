"""The one Bitfinex BTC-perp execution cost profile.

Runtime paper accounting, counterfactual collection, the analyzer cost model
and dashboards all read their fee and funding assumptions from here. There is
no other venue profile. A Bitfinex fee change is a single edit to
``MAKER_FEE_RATE`` / ``TAKER_FEE_RATE``; the profile id and signature follow.

Zero exchange fees are not zero cost: spread, latency slippage, depth walk,
funding and adverse selection remain modelled from the measured tape.
"""
from __future__ import annotations

import hashlib
import json

VENUE = "bitfinex"
SYMBOL = "tBTCF0:USTF0"

# Derivatives order execution, https://www.bitfinex.com/fees/ (verified
# 2026-10-01: maker Zero, taker Zero; fees removed from 2025-12-17).
MAKER_FEE_RATE = 0.0
TAKER_FEE_RATE = 0.0
FEE_EFFECTIVE_FROM = "2025-12-17"
FEE_VERIFIED_AT = "2026-10-01"
FEE_SOURCES = (
    "https://www.bitfinex.com/fees/",
    "https://www.bitfinex.com/zero-fee-trading/",
    "https://support.bitfinex.com/hc/en-us/articles/360035475374",
)

# Perpetual funding settles every 8h. The simulated rate is the live Bitfinex
# rate clamped to this band.
FUNDING_INTERVAL_HOURS = 8
FUNDING_RATE_BAND_PER_8H = 0.0005

# Non-fee costs are measured, never assumed away.
MODELLED_NON_FEE_COSTS = (
    "spread", "latency_slippage", "depth_walk", "funding", "adverse_selection",
)


def _bps(rate: float) -> str:
    return f"{rate * 1e4:g}".replace(".", "p")


FEE_PROFILE_ID = (
    "BITFINEX_ZERO" if MAKER_FEE_RATE == 0.0 and TAKER_FEE_RATE == 0.0
    else f"BITFINEX_M{_bps(MAKER_FEE_RATE)}_T{_bps(TAKER_FEE_RATE)}"
)


def fee_rates() -> tuple[float, float]:
    """Return ``(maker_rate, taker_rate)`` as fractions of notional."""
    return MAKER_FEE_RATE, TAKER_FEE_RATE


def fee_usd(notional_usd: float, *, maker: bool) -> float:
    rate = MAKER_FEE_RATE if maker else TAKER_FEE_RATE
    return abs(float(notional_usd or 0.0)) * rate


def clamp_funding_rate_8h(rate: float) -> float:
    return max(-FUNDING_RATE_BAND_PER_8H, min(FUNDING_RATE_BAND_PER_8H, float(rate or 0.0)))


def cost_profile() -> dict:
    return {
        "schema": "bitfinex_cost_profile_v1",
        "venue": VENUE,
        "symbol": SYMBOL,
        "fee_profile_id": FEE_PROFILE_ID,
        "maker_fee_rate": MAKER_FEE_RATE,
        "taker_fee_rate": TAKER_FEE_RATE,
        "fee_effective_from": FEE_EFFECTIVE_FROM,
        "fee_verified_at": FEE_VERIFIED_AT,
        "fee_sources": list(FEE_SOURCES),
        "funding_interval_hours": FUNDING_INTERVAL_HOURS,
        "funding_rate_band_per_8h": FUNDING_RATE_BAND_PER_8H,
        "modelled_non_fee_costs": list(MODELLED_NON_FEE_COSTS),
    }


def cost_profile_signature() -> str:
    encoded = json.dumps(cost_profile(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def describe() -> str:
    return (
        f"fee profile {FEE_PROFILE_ID} (maker {MAKER_FEE_RATE * 100:g}% / taker "
        f"{TAKER_FEE_RATE * 100:g}%); spread, latency slippage, funding "
        f"({FUNDING_INTERVAL_HOURS}h, band ±{FUNDING_RATE_BAND_PER_8H * 100:g}%) "
        "and adverse selection remain modelled"
    )
