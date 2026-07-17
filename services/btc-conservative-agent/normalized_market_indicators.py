"""Pure, fail-closed indicator normalization for research tiles."""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Optional


INDICATOR_NORMALIZATION_VERSION = "normalized_adx_atr_percentile_v1"


def _finite_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def normalize_adx(
    features: Optional[Mapping[str, Any]] = None,
    market_context: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Return ADX on its canonical 0..100 scale plus its selected source."""
    feat = _mapping(features)
    context = _mapping(market_context)
    trend_strength = _mapping(context.get("trend_strength"))
    candidates = (
        ("feature.adx", feat.get("adx")),
        ("feature.adx_at_entry", feat.get("adx_at_entry")),
        ("feature.mom_adx", feat.get("mom_adx")),
        ("market_context.trend_strength.adx", trend_strength.get("adx")),
        ("market_context.adx", context.get("adx")),
    )
    for source, raw in candidates:
        value = _finite_float(raw)
        if value is not None:
            return {"adx": max(0.0, min(100.0, value)), "adx_source": source}
    return {"adx": None, "adx_source": "missing"}


def _candle_ohlc(candle: Any) -> Optional[tuple[float, float, float]]:
    """Return high, low, close from Bitfinex [ts,o,h,l,c,v] or a mapping."""
    if isinstance(candle, Mapping):
        high = _finite_float(candle.get("high"))
        low = _finite_float(candle.get("low"))
        close = _finite_float(candle.get("close"))
    elif isinstance(candle, (list, tuple)) and len(candle) >= 5:
        high = _finite_float(candle[2])
        low = _finite_float(candle[3])
        close = _finite_float(candle[4])
    else:
        return None
    if high is None or low is None or close is None or high < low or close <= 0:
        return None
    return high, low, close


def atr_percentile(
    candles: Optional[Iterable[Any]],
    period: int = 14,
    lookback: int = 120,
    min_atr_observations: int = 5,
) -> dict:
    """Calculate current rolling ATR and its percentile within recent ATRs."""
    period = max(2, int(period))
    lookback = max(period + 1, int(lookback))
    parsed = []
    for candle in list(candles or [])[-(lookback + period + 1) :]:
        row = _candle_ohlc(candle)
        if row is not None:
            parsed.append(row)
    if len(parsed) < period + min_atr_observations:
        return {
            "volatility_percentile": None,
            "atr": None,
            "atr_observations": 0,
            "volatility_source": "insufficient_candles",
        }

    true_ranges = []
    previous_close = None
    for high, low, close in parsed:
        true_range = (
            high - low
            if previous_close is None
            else max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
        true_ranges.append(true_range)
        previous_close = close

    rolling_atrs = []
    for index in range(period - 1, len(true_ranges)):
        window = true_ranges[index - period + 1 : index + 1]
        rolling_atrs.append(sum(window) / period)
    if len(rolling_atrs) < min_atr_observations:
        return {
            "volatility_percentile": None,
            "atr": None,
            "atr_observations": len(rolling_atrs),
            "volatility_source": "insufficient_atr_history",
        }

    current = rolling_atrs[-1]
    ranked = rolling_atrs[-lookback:]
    percentile = 100.0 * sum(1 for value in ranked if value <= current) / len(ranked)
    return {
        "volatility_percentile": round(max(0.0, min(100.0, percentile)), 4),
        "atr": round(current, 8),
        "atr_observations": len(ranked),
        "volatility_source": "rolling_atr_percentile",
    }


def normalize_market_indicators(
    features: Optional[Mapping[str, Any]] = None,
    market_context: Optional[Mapping[str, Any]] = None,
    candles: Optional[Iterable[Any]] = None,
) -> dict:
    """Return the canonical normalized indicator payload used by Tile 2."""
    return {
        "indicator_normalization_version": INDICATOR_NORMALIZATION_VERSION,
        **normalize_adx(features=features, market_context=market_context),
        **atr_percentile(candles),
    }
