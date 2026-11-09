"""Trade-bar features f1-f6 (A5), trailing percentiles (A6 + RK 95% rule),
causal 29-day hourly sigma and regimes (A8), thesis-cut lookups (A10), and
entry timing (A9). No candle inputs. Paper only.

Boss ruling (9 Nov): features may use the causal as-of last-print close across
empty minutes (carry-forward of the reference only; built by the loader).
Empty minutes never fill. The 95% defined-share rule is NaN-aware and is the
only rule deciding whether a trailing percentile exists.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from research.ext2_calendar import FROZEN, Calendar
from research.ext2_costs import Ext2Stop
from research.ext2_rules import ENTRY_DELAY_MS, ENTRY_LATENCY_CAP_MS, PERCENTILE_DEFINED_SHARE

FEATURES = ("ret_L", "flow_imb_L", "intensity_L", "rv_break_L", "vwap_dev_L", "large_print_L")
PERCENTILES = (80.0, 90.0, 95.0, 97.5, 99.0)
COOLDOWN_BARS = 1
F6_QUANTILE = 0.95
UPPER_Q = tuple(p / 100.0 for p in PERCENTILES)
LOWER_Q = tuple((100.0 - p) / 100.0 for p in PERCENTILES)
ALL_Q = UPPER_Q + LOWER_Q + (0.5,)


def _lerp(a: np.ndarray | float, b: np.ndarray | float, t: np.ndarray | float):
    """numpy's linear-interpolation formula (np.quantile 'linear'), reproduced exactly."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    t = np.asarray(t, dtype=np.float64)
    diff = b - a
    low = a + diff * t
    high = b - diff * (1.0 - t)
    return np.where(t >= 0.5, high, low)


def quantiles_sorted(finite_sorted: np.ndarray, probs: Sequence[float]) -> np.ndarray:
    """Linear quantiles of an already-sorted finite array, identical to np.quantile."""
    n = finite_sorted.shape[0]
    if n == 0:
        return np.full(len(probs), np.nan)
    q = np.asarray(probs, dtype=np.float64)
    virtual = (n - 1) * q
    lo = np.floor(virtual).astype(np.int64)
    lo = np.clip(lo, 0, n - 1)
    hi = np.clip(lo + 1, 0, n - 1)
    frac = virtual - lo
    return _lerp(finite_sorted[lo], finite_sorted[hi], frac)


def defined_share_ok(finite_count: int, window: int) -> bool:
    return finite_count / float(window) + 1e-15 >= PERCENTILE_DEFINED_SHARE


def trailing_percentiles(values: np.ndarray, window: int, probs: Sequence[float] = ALL_Q) -> np.ndarray:
    """Out[t, k] = quantile(probs[k]) of the finite values in values[t-window:t].

    Strictly prior: bar t is never in its own window. Defined only when the
    full calendar window exists (t >= window) and >= 95% of it is finite
    (RK frozen rule, NaN-aware). Exact: a sorted sliding buffer, not an
    approximation. Lookbacks are never shortened.
    """
    v = np.asarray(values, dtype=np.float64)
    n = v.shape[0]
    out = np.full((n, len(probs)), np.nan)
    if n <= window:
        return out
    clean = np.where(np.isfinite(v), v, np.inf)
    buf = np.sort(clean[:window])
    nan_count = int(np.sum(~np.isfinite(v[:window])))
    probs_arr = np.asarray(probs, dtype=np.float64)
    for t in range(window, n):
        finite = window - nan_count
        if finite > 0 and defined_share_ok(finite, window):
            out[t] = quantiles_sorted(buf[:finite], probs_arr)
        if t == n - 1:
            break
        leaving = clean[t - window]
        entering = clean[t]
        if not math.isfinite(v[t - window]):
            nan_count -= 1
        if not math.isfinite(v[t]):
            nan_count += 1
        pos_out = int(np.searchsorted(buf, leaving, side="left"))
        pos_in = int(np.searchsorted(buf, entering, side="left"))
        if pos_in > pos_out:
            buf[pos_out:pos_in - 1] = buf[pos_out + 1:pos_in]
            buf[pos_in - 1] = entering
        elif pos_in < pos_out:
            buf[pos_in + 1:pos_out + 1] = buf[pos_in:pos_out].copy()
            buf[pos_in] = entering
        else:
            buf[pos_in] = entering
    return out


def trailing_percentile_prior(values: np.ndarray, end: int, probability: float,
                              window: int = FROZEN.percentile_bars) -> float | None:
    """Reference (slow) form of one trailing percentile at bar `end`. Same rules."""
    start = end - window
    if start < 0:
        return None
    chunk = np.asarray(values[start:end], dtype=np.float64)
    if chunk.shape[0] != window:
        return None
    finite = chunk[np.isfinite(chunk)]
    if finite.size == 0 or not defined_share_ok(int(finite.size), window):
        return None
    return float(np.quantile(finite, probability / 100.0))


def rolling_sum(values: np.ndarray, lookback: int) -> np.ndarray:
    """Sum over the `lookback` bars ending at t. NaN if any input in the window is NaN
    or the window starts before bar 0 (never shortened)."""
    v = np.asarray(values, dtype=np.float64)
    n = v.shape[0]
    out = np.full(n, np.nan)
    if n < lookback:
        return out
    bad = ~np.isfinite(v)
    csum = np.concatenate(([0.0], np.cumsum(np.where(bad, 0.0, v))))
    cbad = np.concatenate(([0], np.cumsum(bad.astype(np.int64))))
    idx = np.arange(lookback - 1, n)
    total = csum[idx + 1] - csum[idx + 1 - lookback]
    nbad = cbad[idx + 1] - cbad[idx + 1 - lookback]
    out[idx] = np.where(nbad == 0, total, np.nan)
    return out


def _lagged(values: np.ndarray, lag: int) -> np.ndarray:
    out = np.full(values.shape[0], np.nan)
    if lag < values.shape[0]:
        out[lag:] = values[:-lag] if lag else values
    return out


def large_print_threshold(trades: Mapping[str, np.ndarray], grid: Mapping[str, Any],
                          window_ms: int) -> np.ndarray:
    """P95 of individual |amount| over trades with mts in [close_t - window, close_t).

    Defined only when the whole calendar window is inside the loaded series
    (close_t - window >= series origin) and has at least one trade. Exact
    sliding order statistic (histogram over |amount| ranks + walking pointer),
    reproducing np.quantile 'linear'.
    """
    mts = np.asarray(trades["mts"], dtype=np.int64)
    size = np.abs(np.asarray(trades["amount"], dtype=np.float64))
    close_mts = np.asarray(grid["close_mts"], dtype=np.int64)
    origin = int(grid["origin_mts"])
    n = close_mts.shape[0]
    out = np.full(n, np.nan)
    if mts.size == 0:
        return out
    uniq, rank = np.unique(size, return_inverse=True)
    hist = np.zeros(uniq.shape[0] + 1, dtype=np.int64)
    lo_all = np.searchsorted(mts, close_mts - window_ms, side="left")
    hi_all = np.searchsorted(mts, close_mts, side="left")
    cur_lo = 0
    cur_hi = 0
    ptr = 0
    below = 0
    for t in range(n):
        if int(close_mts[t]) - window_ms < origin:
            continue
        lo = int(lo_all[t])
        hi = int(hi_all[t])
        if hi > cur_hi:
            add = rank[cur_hi:hi]
            np.add.at(hist, add, 1)
            below += int(np.count_nonzero(add < ptr))
            cur_hi = hi
        if lo > cur_lo:
            if cur_lo < lo:
                rem = rank[cur_lo:lo]
                np.subtract.at(hist, rem, 1)
                below -= int(np.count_nonzero(rem < ptr))
            cur_lo = lo
        count = cur_hi - cur_lo
        if count <= 0:
            continue
        virtual = (count - 1) * F6_QUANTILE
        k0 = int(math.floor(virtual))
        frac = virtual - k0
        while below > k0:
            ptr -= 1
            below -= int(hist[ptr])
        while below + int(hist[ptr]) <= k0:
            below += int(hist[ptr])
            ptr += 1
        v0 = float(uniq[ptr])
        if k0 + 1 < count:
            if k0 + 1 < below + int(hist[ptr]):
                v1 = v0
            else:
                nxt = ptr + 1
                while hist[nxt] == 0:
                    nxt += 1
                v1 = float(uniq[nxt])
        else:
            v1 = v0
        out[t] = float(_lerp(v0, v1, frac))
    return out


def large_print_feature(trades: Mapping[str, np.ndarray], grid: Mapping[str, Any], threshold: np.ndarray,
                        lookback: int) -> np.ndarray:
    """f6: signed sum of amounts over trades in bars (t-L, t] with |amount| >= P95_t."""
    amount = np.asarray(trades["amount"], dtype=np.float64)
    size = np.abs(amount)
    start = np.asarray(grid["print_start"], dtype=np.int64)
    end = np.asarray(grid["print_end"], dtype=np.int64)
    defined = np.asarray(grid["defined"], dtype=bool)
    n = start.shape[0]
    out = np.full(n, np.nan)
    for t in range(lookback - 1, n):
        thr = threshold[t]
        if not math.isfinite(thr) or not defined[t - lookback + 1]:
            continue
        lo = int(start[t - lookback + 1])
        hi = int(end[t])
        seg = amount[lo:hi]
        out[t] = float(seg[size[lo:hi] >= thr].sum()) if hi > lo else 0.0
    return out


def feature_matrix(grid: Mapping[str, Any], trades: Mapping[str, np.ndarray],
                   calendar: Calendar = FROZEN) -> dict[str, Any]:
    """f1-f6 at every L on the full minute grid (A5). Undefined stays NaN."""
    if grid.get("source") != "trades":
        raise Ext2Stop("STOP_FEATURE_SOURCE")
    close = np.asarray(grid["close"], dtype=np.float64)
    defined = np.asarray(grid["defined"], dtype=bool)
    n = close.shape[0]
    nan_pre = np.where(defined, 0.0, np.nan)
    buy = np.asarray(grid["buy_amount"], dtype=np.float64) + nan_pre
    sell = np.asarray(grid["sell_amount"], dtype=np.float64) + nan_pre
    count = np.asarray(grid["trade_count"], dtype=np.float64) + nan_pre
    volume = np.asarray(grid["volume"], dtype=np.float64) + nan_pre
    notional = np.asarray(grid["notional"], dtype=np.float64) + nan_pre
    with np.errstate(divide="ignore", invalid="ignore"):
        log_close = np.where(close > 0.0, np.log(close), np.nan)
    step = log_close - _lagged(log_close, 1)
    rv = np.sqrt(rolling_sum(step * step, calendar.rv_bars))
    threshold = large_print_threshold(trades, grid, calendar.f6_window_ms)
    frames: dict[str, dict[int, np.ndarray]] = {name: {} for name in FEATURES}
    with np.errstate(divide="ignore", invalid="ignore"):
        for lookback in calendar.lookbacks:
            log_ret = log_close - _lagged(log_close, lookback)
            ret = log_ret * 10000.0
            num = rolling_sum(buy - sell, lookback)
            den = rolling_sum(buy + sell, lookback)
            imb = np.where(np.isfinite(den) & (den > 0.0), num / den, np.nan)
            counted = rolling_sum(count, lookback)
            intensity = np.where(np.isfinite(ret) & np.isfinite(counted), counted * np.sign(ret), np.nan)
            scale = rv * math.sqrt(lookback)
            rv_break = np.where(np.isfinite(log_ret) & np.isfinite(scale) & (scale > 0.0), log_ret / scale, np.nan)
            vol_l = rolling_sum(volume, lookback)
            not_l = rolling_sum(notional, lookback)
            vwap_l = np.where(np.isfinite(vol_l) & (vol_l > 0.0), not_l / vol_l, np.nan)
            dev = np.where(np.isfinite(vwap_l) & (vwap_l > 0.0) & np.isfinite(close),
                           (close - vwap_l) / vwap_l * 10000.0, np.nan)
            frames["ret_L"][lookback] = ret
            frames["flow_imb_L"][lookback] = imb
            frames["intensity_L"][lookback] = intensity
            frames["rv_break_L"][lookback] = rv_break
            frames["vwap_dev_L"][lookback] = dev
            frames["large_print_L"][lookback] = large_print_feature(trades, grid, threshold, lookback)
    return {
        "close": close,
        "open_mts": np.asarray(grid["open_mts"], dtype=np.int64),
        "close_mts": np.asarray(grid["close_mts"], dtype=np.int64),
        "features": frames,
        "rv_24h": rv,
        "large_print_p95": threshold,
        "n": n,
    }


def r24_series(close: np.ndarray, r24_bars: int) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        log_close = np.where(close > 0.0, np.log(close), np.nan)
    return log_close - _lagged(log_close, r24_bars)


def sigma_hourly_r24(close: np.ndarray, calendar: Calendar = FROZEN) -> np.ndarray:
    """A8 sigma(t): population sd of the `sigma_hours` hourly r24 samples strictly
    before the current hour sample (exactly that many calendar hours; any
    undefined sample in the window -> undefined). Never shortened."""
    close = np.asarray(close, dtype=np.float64)
    n = close.shape[0]
    out = np.full(n, np.nan)
    hb = calendar.hour_bars
    hours = n // hb + (1 if n % hb else 0)
    need = calendar.sigma_hours
    if hours <= need:
        return out
    r24 = r24_series(close, calendar.r24_bars)
    sample = r24[np.arange(hours) * hb]
    windows = np.lib.stride_tricks.sliding_window_view(sample, need)  # windows[k] = sample[k:k+need]
    finite = np.isfinite(windows).all(axis=1)
    sd = np.full(windows.shape[0], np.nan)
    if np.any(finite):
        sd[finite] = windows[finite].std(axis=1, ddof=0)
    per_hour = np.full(hours, np.nan)
    per_hour[need:] = sd[: hours - need]  # hour h uses samples h-need .. h-1
    out[:] = per_hour[np.arange(n) // hb]
    return out


def regime_labels(close: np.ndarray, calendar: Calendar = FROZEN) -> dict[str, np.ndarray]:
    """BULL / BEAR / SIDE need defined r24 and sigma > 0, else UNDEFINED (no entry
    for BULL/BEAR/SIDE cells; NONE cells are not blocked by regime). Codes:
    0 UNDEFINED, 1 BULL, 2 BEAR, 3 SIDE."""
    close = np.asarray(close, dtype=np.float64)
    r24 = r24_series(close, calendar.r24_bars)
    sigma = sigma_hourly_r24(close, calendar)
    ok = np.isfinite(r24) & np.isfinite(sigma) & (sigma > 0.0)
    code = np.zeros(close.shape[0], dtype=np.int8)
    code[ok] = 3
    code[ok & (r24 > 0.5 * sigma)] = 1
    code[ok & (r24 < -0.5 * sigma)] = 2
    return {"code": code, "r24": r24, "sigma": sigma}


REGIME_CODES = {"NONE": -1, "UNDEFINED": 0, "BULL": 1, "BEAR": 2, "SIDE": 3}


def trigger_directions(values: np.ndarray, pct: np.ndarray) -> np.ndarray:
    """Per bar and per p: +1 long trigger, -1 short, 0 none/undefined/both (A6).

    pct columns follow ALL_Q: 5 upper, 5 lower, then p50."""
    v = np.asarray(values, dtype=np.float64)[:, None]
    upper = pct[:, :5]
    lower = pct[:, 5:10]
    ok = np.isfinite(v) & np.isfinite(upper) & np.isfinite(lower)
    long_hit = ok & (v >= upper)
    short_hit = ok & (v <= lower)
    out = np.zeros(upper.shape, dtype=np.int8)
    out[long_hit & ~short_hit] = 1
    out[short_hit & ~long_hit] = -1
    return out


def next_true(mask: np.ndarray) -> np.ndarray:
    """next_true[t] = smallest j >= t with mask[j], else len(mask)."""
    n = mask.shape[0]
    idx = np.where(mask, np.arange(n), n)
    return np.minimum.accumulate(idx[::-1])[::-1].astype(np.int64)


def thesis_next_bar(values: np.ndarray, p50: np.ndarray) -> dict[int, np.ndarray]:
    """For trigger direction d, the first bar j >= t whose CLOSED-bar feature has
    crossed back through its trailing p50 (d=+1: f_j <= p50_j; d=-1: f_j >= p50_j).
    A print may only use bar j once mts >= close_mts(j): the bar that contains
    the print is never read (A10, HM B8)."""
    v = np.asarray(values, dtype=np.float64)
    ok = np.isfinite(v) & np.isfinite(p50)
    return {1: next_true(ok & (v <= p50)), -1: next_true(ok & (v >= p50))}


def thesis_cross_flags(values: np.ndarray, p50: np.ndarray, trigger_dir: int, print_mts: np.ndarray,
                       close_mts: np.ndarray, origin_mts: int, entry_bar: int) -> np.ndarray:
    """Reference per-print thesis flags. A print may read only bars FULLY CLOSED
    before its mts (never the bar containing it). The flag turns on at the first
    print after the close of any bar j >= entry_bar whose feature crossed back
    through its trailing p50 (bars that closed between two sparse prints count)."""
    v = np.asarray(values, dtype=np.float64)
    flags = np.zeros(print_mts.shape[0], dtype=bool)
    scanned = int(entry_bar) - 1
    for k, stamp in enumerate(np.asarray(print_mts, dtype=np.int64)):
        j = min(int((int(stamp) - int(origin_mts)) // 60_000) - 1, v.shape[0] - 1)  # last closed bar
        if j < entry_bar:
            continue
        if int(close_mts[j]) > int(stamp):
            raise Ext2Stop("STOP_THESIS_LOOKAHEAD")
        hit = False
        for bar in range(scanned + 1, j + 1):
            if math.isfinite(v[bar]) and math.isfinite(p50[bar]) and \
                    ((v[bar] <= p50[bar]) if trigger_dir > 0 else (v[bar] >= p50[bar])):
                hit = True
                break
        scanned = max(scanned, j)
        if hit:
            flags[k:] = True
            break
    return flags


def first_entry_print(trade_mts: np.ndarray, trade_price: np.ndarray, bar_close_mts: int,
                      cap_ms: int = ENTRY_LATENCY_CAP_MS) -> tuple[int, float] | None:
    """First print at or after bar close + 1000 ms (never the signal bar), and no
    later than close + 1000 ms + 60 s (N3: stale prints are no-trade)."""
    ready = int(bar_close_mts) + ENTRY_DELAY_MS
    index = int(np.searchsorted(trade_mts, ready, side="left"))
    if index >= trade_mts.shape[0]:
        return None
    if int(trade_mts[index]) - ready > cap_ms:
        return None
    return int(trade_mts[index]), float(trade_price[index])


def entry_print_index(trade_mts: np.ndarray, close_mts: np.ndarray) -> np.ndarray:
    return np.searchsorted(trade_mts, np.asarray(close_mts, dtype=np.int64) + ENTRY_DELAY_MS, side="left")


def apply_cooldown(entry_bars: list[int], exit_bars: list[int]) -> list[int]:
    """A9: one position at a time; the next signal bar must open at or after the
    close of the bar containing the exit print (signal bar >= exit_bar + 1)."""
    kept = []
    earliest = -1
    for entry, exit_bar in zip(entry_bars, exit_bars):
        if entry >= earliest:
            kept.append(entry)
            earliest = exit_bar + COOLDOWN_BARS
    return kept
