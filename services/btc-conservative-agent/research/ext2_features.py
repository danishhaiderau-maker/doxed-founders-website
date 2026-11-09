"""Trade-bar features, 29-day hourly sigma, and entry events. No candle inputs."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from research.ext2_costs import Ext2Stop
from research.ext2_search import (
    BARS_PER_DAY,
    FEATURE_LOOKBACK_MIN,
    FEATURES,
    LOOKBACKS_MIN,
    PERCENTILE_DAYS,
    PERCENTILES,
    SIGMA_DAYS,
)

ENTRY_DELAY_MS = 1000
COOLDOWN_BARS = 1


def sigma_hourly_r24(close: np.ndarray) -> np.ndarray:
    """Trailing 29-day std of hourly 24h log returns. Shorter history stays NaN."""
    n = close.shape[0]
    out = np.full(n, np.nan)
    if n <= BARS_PER_DAY:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        r24 = np.full(n, np.nan)
        r24[BARS_PER_DAY:] = np.log(close[BARS_PER_DAY:] / close[:-BARS_PER_DAY])
    need = SIGMA_DAYS * 24
    hourly_at = []
    hourly_r = []
    for index in range(BARS_PER_DAY, n, 60):
        if np.isfinite(r24[index]):
            hourly_at.append(index)
            hourly_r.append(r24[index])
    hourly_r_arr = np.asarray(hourly_r, dtype=np.float64)
    hourly_at_arr = np.asarray(hourly_at, dtype=np.int64)
    for cursor, bar in enumerate(hourly_at_arr):
        if cursor < need:
            continue
        window = hourly_r_arr[cursor - need:cursor]
        if window.shape != (need,) or not np.isfinite(window).all():
            continue
        out[bar] = float(window.std(ddof=0))
    # hold the last defined hourly sigma across the minutes until the next sample
    last = np.nan
    for index in range(n):
        if np.isfinite(out[index]):
            last = out[index]
        elif np.isfinite(last) and index % 60 != 0:
            out[index] = last
    return out


def _rolling_sum(values: np.ndarray, lookback: int) -> np.ndarray:
    out = np.full(values.shape[0], np.nan)
    if values.shape[0] < lookback:
        return out
    csum = np.cumsum(np.nan_to_num(values))
    total = csum[lookback - 1:]
    total[1:] -= csum[:-lookback]
    out[lookback - 1:] = total
    return out


def feature_matrix(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """f1–f6 on a full minute grid. Undefined lookbacks stay NaN. No shortening."""
    if any(bar.get("source") != "trades" for bar in bars):
        raise Ext2Stop("STOP_FEATURE_SOURCE")
    close = np.asarray([bar["close"] for bar in bars], dtype=np.float64)
    buy = np.asarray([bar["buy_amount"] for bar in bars], dtype=np.float64)
    sell = np.asarray([bar["sell_amount"] for bar in bars], dtype=np.float64)
    count = np.asarray([bar["trade_count"] for bar in bars], dtype=np.float64)
    vwap = np.asarray([bar["vwap"] for bar in bars], dtype=np.float64)
    signed_large = np.asarray([bar.get("signed_large", 0.0) for bar in bars], dtype=np.float64)
    n = close.shape[0]
    log_ret = np.full(n, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        log_ret[1:] = np.log(close[1:] / close[:-1])
    rv = np.full(n, np.nan)
    if n > BARS_PER_DAY:
        sq = np.nan_to_num(log_ret) ** 2
        window = _rolling_sum(sq, BARS_PER_DAY)
        rv[BARS_PER_DAY:] = np.sqrt(np.maximum(window[BARS_PER_DAY:] / BARS_PER_DAY, 0.0))
    frames = {name: {} for name in FEATURES}
    undefined = np.ones(n, dtype=bool)
    for lookback in LOOKBACKS_MIN:
        ret = np.full(n, np.nan)
        if n > lookback:
            with np.errstate(divide="ignore", invalid="ignore"):
                ret[lookback:] = np.log(close[lookback:] / close[:-lookback]) * 10000.0
        imb = np.full(n, np.nan)
        num = _rolling_sum(buy - sell, lookback)
        den = _rolling_sum(np.abs(buy) + np.abs(sell), lookback)
        ok = np.isfinite(num) & np.isfinite(den) & (den > 0)
        imb[ok] = num[ok] / den[ok]
        intensity = np.full(n, np.nan)
        counted = _rolling_sum(count, lookback)
        both = np.isfinite(ret) & np.isfinite(counted)
        intensity[both] = counted[both] * np.sign(ret[both])
        rv_break = np.full(n, np.nan)
        scale = rv * math.sqrt(lookback)
        ready = np.isfinite(ret) & np.isfinite(scale) & (scale > 0)
        rv_break[ready] = ret[ready] / scale[ready]
        dev = np.full(n, np.nan)
        # trailing VWAP over L minutes of bar vwap weighted by volume
        weighted = _rolling_sum(vwap * np.maximum(buy + sell, 0.0), lookback)
        weights = _rolling_sum(np.maximum(buy + sell, 0.0), lookback)
        vwap_ok = np.isfinite(weighted) & (weights > 0)
        trail = np.full(n, np.nan)
        trail[vwap_ok] = weighted[vwap_ok] / weights[vwap_ok]
        dev_ok = vwap_ok & (trail > 0)
        dev[dev_ok] = (close[dev_ok] - trail[dev_ok]) / trail[dev_ok] * 10000.0
        large = _rolling_sum(signed_large, lookback)
        frames["ret_L"][lookback] = ret
        frames["flow_imb_L"][lookback] = imb
        frames["intensity_L"][lookback] = intensity
        frames["rv_break_L"][lookback] = rv_break
        frames["vwap_dev_L"][lookback] = dev
        frames["large_print_L"][lookback] = large
        defined = np.isfinite(ret) & np.isfinite(imb) & np.isfinite(intensity)
        undefined &= ~defined
    sigma = sigma_hourly_r24(close)
    undefined |= ~np.isfinite(sigma)
    # 30-day percentiles need a full window after the 240-minute lookback
    percentile_ready = np.zeros(n, dtype=bool)
    first = FEATURE_LOOKBACK_MIN + PERCENTILE_DAYS * BARS_PER_DAY
    if n > first:
        percentile_ready[first:] = True
    undefined |= ~percentile_ready
    return {
        "close": close,
        "open_mts": np.asarray([bar["open_mts"] for bar in bars], dtype=np.int64),
        "close_mts": np.asarray([bar["close_mts"] for bar in bars], dtype=np.int64),
        "features": frames,
        "sigma": sigma,
        "r24": _r24(close),
        "undefined": undefined,
        "undefined_stat_bars": int(undefined.sum()),
    }


def _r24(close: np.ndarray) -> np.ndarray:
    out = np.full(close.shape[0], np.nan)
    if close.shape[0] > BARS_PER_DAY:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[BARS_PER_DAY:] = np.log(close[BARS_PER_DAY:] / close[:-BARS_PER_DAY])
    return out


def regime_labels(frame: dict[str, Any]) -> np.ndarray:
    """NONE is not a market state. BULL / BEAR / SIDE need a defined 29-day sigma."""
    r24 = frame["r24"]
    sigma = frame["sigma"]
    labels = np.full(r24.shape[0], "SIDE", dtype=object)
    defined = np.isfinite(r24) & np.isfinite(sigma) & (sigma > 0)
    labels[defined & (r24 > 0.5 * sigma)] = "BULL"
    labels[defined & (r24 < -0.5 * sigma)] = "BEAR"
    labels[~np.isfinite(sigma)] = "UNDEFINED"
    return labels


def trailing_percentile_prior(values: np.ndarray, end: int, probability: float) -> float | None:
    start = end - PERCENTILE_DAYS * BARS_PER_DAY
    if start < FEATURE_LOOKBACK_MIN:
        return None
    window = values[start:end]
    if window.shape[0] != PERCENTILE_DAYS * BARS_PER_DAY or not np.isfinite(window).all():
        return None
    return float(np.quantile(window, probability / 100.0))


def thesis_cross_flags(values: np.ndarray, entry_index: int, print_indexes: np.ndarray) -> np.ndarray:
    """True when the entry feature has crossed back through its prior 50th percentile."""
    baseline = trailing_percentile_prior(values, entry_index, 50.0)
    flags = np.zeros(print_indexes.shape[0], dtype=bool)
    if baseline is None:
        return flags
    side = 1.0 if values[entry_index] >= baseline else -1.0
    for cursor, index in enumerate(print_indexes):
        if 0 <= int(index) < values.shape[0] and np.isfinite(values[int(index)]):
            current = float(values[int(index)])
            flags[cursor] = (side > 0 and current <= baseline) or (side < 0 and current >= baseline)
    return flags


def first_entry_print(trade_mts: np.ndarray, trade_price: np.ndarray, bar_close_mts: int) -> tuple[int, float] | None:
    """First print at or after bar close + 1000 ms. Never the signal bar."""
    ready = int(bar_close_mts) + ENTRY_DELAY_MS
    hits = np.flatnonzero(trade_mts >= ready)
    if hits.size == 0:
        return None
    index = int(hits[0])
    return int(trade_mts[index]), float(trade_price[index])


def apply_cooldown(entry_bars: list[int], exit_bars: list[int]) -> list[int]:
    """One position at a time, then one full bar before the next entry."""
    kept = []
    cursor = -1
    for entry, exit_bar in zip(entry_bars, exit_bars):
        if entry > cursor:
            kept.append(entry)
            cursor = exit_bar + COOLDOWN_BARS
    return kept
