"""Pure, fail-closed indicator normalization for research tiles."""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Optional


# Keep the execution-policy identifier frozen: the added DMI/volume fields are
# research observations and do not change Tile 2's ADX/ATR entry policy.
INDICATOR_NORMALIZATION_VERSION = "normalized_adx_atr_percentile_v1"
RESEARCH_FEATURE_SCHEMA_VERSION = "normalized_adx_dmi_atr_volume_v2"


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


def _candle_ohlcv(candle: Any) -> Optional[tuple[float, float, float, float]]:
    row = _candle_ohlc(candle)
    if row is None:
        return None
    if isinstance(candle, Mapping):
        volume = _finite_float(candle.get("volume"))
    elif isinstance(candle, (list, tuple)) and len(candle) >= 6:
        volume = _finite_float(candle[5])
    else:
        volume = None
    if volume is None or volume < 0:
        return None
    return row[0], row[1], row[2], volume


def directional_movement_snapshot(
    candles: Optional[Iterable[Any]],
    period: int = 14,
    lookback: int = 120,
    min_observations: int = 5,
) -> dict:
    """Return Wilder-smoothed +DI/-DI, ADX and recent ADX slope."""
    period = max(2, int(period))
    parsed = []
    for candle in list(candles or [])[-(max(lookback, period * 3) + 2) :]:
        row = _candle_ohlc(candle)
        if row is not None:
            parsed.append(row)
    if len(parsed) < period + min_observations + 1:
        return {
            "plus_di": None,
            "minus_di": None,
            "di_separation": None,
            "adx_derived": None,
            "adx_slope_3": None,
            "dmi_observations": 0,
            "dmi_source": "insufficient_candles",
        }

    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    previous = parsed[0]
    for current in parsed[1:]:
        high, low, _ = current
        prev_high, prev_low, prev_close = previous
        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        previous = current

    smoothed_tr = sum(true_ranges[:period])
    smoothed_plus = sum(plus_dm[:period])
    smoothed_minus = sum(minus_dm[:period])
    dx_values: list[float] = []
    di_rows: list[tuple[float, float]] = []
    for index in range(period - 1, len(true_ranges)):
        if index >= period:
            smoothed_tr = smoothed_tr - (smoothed_tr / period) + true_ranges[index]
            smoothed_plus = smoothed_plus - (smoothed_plus / period) + plus_dm[index]
            smoothed_minus = smoothed_minus - (smoothed_minus / period) + minus_dm[index]
        if smoothed_tr <= 0:
            continue
        plus = 100.0 * smoothed_plus / smoothed_tr
        minus = 100.0 * smoothed_minus / smoothed_tr
        denom = plus + minus
        dx_values.append(100.0 * abs(plus - minus) / denom if denom > 0 else 0.0)
        di_rows.append((plus, minus))
    if len(dx_values) < max(period, min_observations):
        return {
            "plus_di": None,
            "minus_di": None,
            "di_separation": None,
            "adx_derived": None,
            "adx_slope_3": None,
            "dmi_observations": len(dx_values),
            "dmi_source": "insufficient_dmi_history",
        }

    adx_series = [sum(dx_values[:period]) / period]
    for dx in dx_values[period:]:
        adx_series.append(((adx_series[-1] * (period - 1)) + dx) / period)
    plus, minus = di_rows[-1]
    slope_base = adx_series[-4] if len(adx_series) >= 4 else adx_series[0]
    return {
        "plus_di": round(plus, 6),
        "minus_di": round(minus, 6),
        "di_separation": round(abs(plus - minus), 6),
        "adx_derived": round(adx_series[-1], 6),
        "adx_slope_3": round(adx_series[-1] - slope_base, 6),
        "dmi_observations": len(adx_series),
        "dmi_period": period,
        "dmi_source": f"wilder_dmi_{period}",
    }


def volume_percentile(
    candles: Optional[Iterable[Any]],
    lookback: int = 120,
    minimum: int = 20,
) -> dict:
    volumes = []
    for candle in list(candles or [])[-max(lookback, minimum) :]:
        row = _candle_ohlcv(candle)
        if row is not None:
            volumes.append(row[3])
    if len(volumes) < minimum:
        return {
            "volume_percentile": None,
            "volume_observations": len(volumes),
            "volume_percentile_source": "insufficient_candles",
        }
    ranked = volumes[-lookback:]
    percentile = 100.0 * sum(1 for value in ranked if value <= ranked[-1]) / len(ranked)
    return {
        "volume_percentile": round(max(0.0, min(100.0, percentile)), 4),
        "volume_observations": len(ranked),
        "volume_percentile_source": "rolling_candle_volume_percentile",
    }


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
        "research_feature_schema_version": RESEARCH_FEATURE_SCHEMA_VERSION,
        **normalize_adx(features=features, market_context=market_context),
        **atr_percentile(candles),
        **directional_movement_snapshot(candles),
        **volume_percentile(candles),
    }
