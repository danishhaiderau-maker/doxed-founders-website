"""Pure tests for Tile 2 ADX and rolling-volatility normalization."""
import math

from normalized_market_indicators import (
    INDICATOR_NORMALIZATION_VERSION,
    RESEARCH_FEATURE_SCHEMA_VERSION,
    atr_percentile,
    directional_movement_snapshot,
    normalize_adx,
    normalize_market_indicators,
    volume_percentile,
)


def candle(index: int, spread: float = 10.0):
    close = 1000.0 + index
    return [index * 60_000, close - 1, close + spread, close - spread, close, 1.0]


def run():
    passed = 0

    def check(name, condition):
        nonlocal passed
        if not condition:
            raise AssertionError(name)
        passed += 1

    direct = normalize_adx({"adx": 32.5}, {"trend_strength": {"adx": 20}})
    check("direct ADX wins", direct["adx"] == 32.5)
    check("direct source is exposed", direct["adx_source"] == "feature.adx")

    nested = normalize_adx({}, {"trend_strength": {"adx": 27.25}})
    check("nested ADX fallback", nested["adx"] == 27.25)
    check("ADX clamps high", normalize_adx({"adx": 120})["adx"] == 100.0)
    check("ADX clamps low", normalize_adx({"adx": -1})["adx"] == 0.0)
    check("nonnumeric ADX fails closed", normalize_adx({"adx": "bad"})["adx"] is None)
    check("nonfinite ADX fails closed", normalize_adx({"adx": math.nan})["adx"] is None)

    insufficient = atr_percentile([candle(i) for i in range(10)])
    check("insufficient candles fail closed", insufficient["volatility_percentile"] is None)

    stable = atr_percentile([candle(i, 10.0) for i in range(60)])
    check("stable ATR percentile exists", stable["volatility_percentile"] is not None)
    check("ATR observation count exists", stable["atr_observations"] >= 5)

    expanding = [candle(i, 5.0) for i in range(59)] + [candle(59, 100.0)]
    high = atr_percentile(expanding)
    check("volatility spike ranks high", high["volatility_percentile"] >= 90.0)

    payload = normalize_market_indicators(
        features={},
        market_context={"trend_strength": {"adx": 31}},
        candles=expanding,
    )
    check("normalization version stamped", payload["indicator_normalization_version"] == INDICATOR_NORMALIZATION_VERSION)
    check("research feature schema stamped", payload["research_feature_schema_version"] == RESEARCH_FEATURE_SCHEMA_VERSION)
    check("combined payload has ADX", payload["adx"] == 31.0)
    check("combined payload has volatility", payload["volatility_percentile"] is not None)
    directional = [
        [i * 60_000, 100 + i - 0.2, 100 + i + 0.8, 100 + i - 0.7, 100 + i, 10 + i]
        for i in range(80)
    ]
    dmi = directional_movement_snapshot(directional)
    volume = volume_percentile(directional)
    check("plus DI collected", dmi["plus_di"] is not None)
    check("minus DI collected", dmi["minus_di"] is not None)
    check("DI separation collected", dmi["di_separation"] is not None)
    check("ADX slope collected", dmi["adx_slope_3"] is not None)
    check("DMI is truthfully Wilder-smoothed", dmi["dmi_source"] == "wilder_dmi_14")
    check("DMI period is explicit", dmi["dmi_period"] == 14)
    check("volume percentile collected", volume["volume_percentile"] == 100.0)
    print(f"PASS: {passed} normalized indicator checks")


if __name__ == "__main__":
    run()
