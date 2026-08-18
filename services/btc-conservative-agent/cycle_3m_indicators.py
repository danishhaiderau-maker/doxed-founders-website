"""3-minute decision-cycle indicators (not 5m / 15m / 1h).

The Showcase AI cadence is ~180s. Bitfinex REST candles have 1m and 5m but not 3m,
so this module resamples native 1m OHLC into 3m bars and computes Wilder RSI(14),
StochRSI(14,14,3), and Wilder ADX(14) on that 3m series.

Do not treat 5m as close enough. Higher-TF RSI is not computed here.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence


EXHAUSTION_POLICY_TAG = "mtf_exhaustion_log_v1"
DECISION_BAR_SEC = 180
SOURCE_BAR = "1m"
DECISION_BAR = "3m"
SOURCE_NOTE = "bitfinex_1m_resampled_to_3m"
RSI_PERIOD = 14
STOCH_PERIOD = 14
STOCH_D_PERIOD = 3
ADX_PERIOD = 14
# Research hypothesis only — LOG-ONLY conjunction. RSI-alone is not a gate
# (winner T1 was also oversold). Hard veto stays off until n + holdout.
SHORT_EXHAUSTION_RSI_MAX = 30.0
SHORT_EXHAUSTION_STOCH_K_MAX = 5.0
SHORT_EXHAUSTION_ADX_MIN = 20.0
WOULD_BLOCK_SHORT_REASON = "SHORT_3M_EXHAUSTION"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _close(candle: Sequence[Any]) -> Optional[float]:
    if not candle or len(candle) < 5:
        return None
    return _finite(candle[4])


def resample_1m_to_3m(candles_1m: Sequence[Sequence[Any]]) -> list:
    """Aggregate Bitfinex 1m rows [ts_ms, o, h, l, c, v] into 3m OHLC."""
    buckets: dict[int, list] = {}
    for row in candles_1m or []:
        if not row or len(row) < 5:
            continue
        ts = _finite(row[0])
        o, h, l, c = _finite(row[1]), _finite(row[2]), _finite(row[3]), _finite(row[4])
        v = _finite(row[5]) if len(row) > 5 else 0.0
        if ts is None or o is None or h is None or l is None or c is None:
            continue
        ts_sec = ts / 1000.0 if ts > 1e12 else ts
        bucket = int(ts_sec // DECISION_BAR_SEC) * DECISION_BAR_SEC
        buckets.setdefault(bucket, []).append((ts_sec, o, h, l, c, v or 0.0))
    out = []
    for bucket in sorted(buckets):
        rows = sorted(buckets[bucket], key=lambda item: item[0])
        out.append([
            bucket * 1000,
            rows[0][1],
            max(item[2] for item in rows),
            min(item[3] for item in rows),
            rows[-1][4],
            sum(item[5] for item in rows),
        ])
    return out


def _wilder_smooth(values: Sequence[float], period: int) -> list:
    if len(values) < period:
        return []
    seed = sum(values[:period]) / float(period)
    out = [seed]
    for value in values[period:]:
        seed = (seed * (period - 1) + value) / float(period)
        out.append(seed)
    return out


def wilder_rsi(closes: Sequence[float], period: int = RSI_PERIOD) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for prev, cur in zip(closes, closes[1:]):
        change = cur - prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = _wilder_smooth(gains, period)
    avg_loss = _wilder_smooth(losses, period)
    if not avg_gain or not avg_loss:
        return None
    gain = avg_gain[-1]
    loss = avg_loss[-1]
    if loss <= 1e-12:
        return 100.0 if gain > 0 else 50.0
    rs = gain / loss
    return round(100.0 - (100.0 / (1.0 + rs)), 4)


def wilder_rsi_series(closes: Sequence[float], period: int = RSI_PERIOD) -> list:
    if len(closes) < period + 1:
        return []
    gains = []
    losses = []
    for prev, cur in zip(closes, closes[1:]):
        change = cur - prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = _wilder_smooth(gains, period)
    avg_loss = _wilder_smooth(losses, period)
    series = []
    for gain, loss in zip(avg_gain, avg_loss):
        if loss <= 1e-12:
            series.append(100.0 if gain > 0 else 50.0)
        else:
            series.append(100.0 - (100.0 / (1.0 + (gain / loss))))
    return series


def stoch_rsi(
    closes: Sequence[float],
    rsi_period: int = RSI_PERIOD,
    stoch_period: int = STOCH_PERIOD,
    d_period: int = STOCH_D_PERIOD,
) -> dict:
    rsi_vals = wilder_rsi_series(closes, rsi_period)
    empty = {"k": None, "d": None, "rsi": None}
    if len(rsi_vals) < stoch_period:
        return empty
    window = rsi_vals[-stoch_period:]
    lo = min(window)
    hi = max(window)
    last = window[-1]
    k = 50.0 if hi - lo <= 1e-12 else 100.0 * (last - lo) / (hi - lo)
    k_series = []
    for idx in range(stoch_period, len(rsi_vals) + 1):
        chunk = rsi_vals[idx - stoch_period:idx]
        c_lo, c_hi, c_last = min(chunk), max(chunk), chunk[-1]
        if c_hi - c_lo <= 1e-12:
            k_series.append(50.0)
        else:
            k_series.append(100.0 * (c_last - c_lo) / (c_hi - c_lo))
    d = None
    if len(k_series) >= d_period:
        d = sum(k_series[-d_period:]) / float(d_period)
    return {
        "k": round(k, 4),
        "d": None if d is None else round(d, 4),
        "rsi": round(rsi_vals[-1], 4),
    }


def wilder_adx(candles: Sequence[Sequence[Any]], period: int = ADX_PERIOD) -> Optional[float]:
    if len(candles) < period + 2:
        return None
    trs, plus_dm, minus_dm = [], [], []
    for prev, cur in zip(candles, candles[1:]):
        h, l, c = _finite(cur[2]), _finite(cur[3]), _finite(cur[4])
        ph, pl, pc = _finite(prev[2]), _finite(prev[3]), _finite(prev[4])
        if None in (h, l, c, ph, pl, pc):
            return None
        up = h - ph
        down = pl - l
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = _wilder_smooth(trs, period)
    pdm = _wilder_smooth(plus_dm, period)
    mdm = _wilder_smooth(minus_dm, period)
    dx_vals = []
    for a, p, m in zip(atr, pdm, mdm):
        if a <= 0:
            dx_vals.append(0.0)
            continue
        pdi = 100.0 * p / a
        mdi = 100.0 * m / a
        denom = pdi + mdi
        dx_vals.append(0.0 if denom <= 0 else abs(pdi - mdi) / denom * 100.0)
    if len(dx_vals) < period:
        return None
    adx = sum(dx_vals[:period]) / float(period)
    for dx in dx_vals[period:]:
        adx = (adx * (period - 1) + dx) / float(period)
    return round(adx, 4)


def ret_from_closes(closes: Sequence[float]) -> Optional[float]:
    if len(closes) < 2:
        return None
    prev, last = closes[-2], closes[-1]
    if prev == 0:
        return None
    return round((last - prev) / prev, 8)


def would_block_short_3m(
    *,
    rsi: Optional[float],
    stoch_k: Optional[float],
    adx: Optional[float],
) -> bool:
    """Conjunction only. RSI-alone is intentionally insufficient."""
    if rsi is None or stoch_k is None or adx is None:
        return False
    return (
        rsi <= SHORT_EXHAUSTION_RSI_MAX
        and stoch_k <= SHORT_EXHAUSTION_STOCH_K_MAX
        and adx >= SHORT_EXHAUSTION_ADX_MIN
    )


def format_exhaustion_line(snapshot: Mapping[str, Any]) -> str:
    rsi = snapshot.get("rsi14")
    k = snapshot.get("stoch_rsi_k")
    d = snapshot.get("stoch_rsi_d")
    adx = snapshot.get("adx14")
    ret = snapshot.get("ret_3m")
    flag = "WOULD_BLOCK_SHORT" if snapshot.get("would_block_short") else "no-block"
    return (
        f"3m exhaustion rsi={rsi} stochK={k} stochD={d} adx={adx} "
        f"ret_3m={ret} {flag} (log-only)"
    )


def compute_3m_exhaustion_snapshot(
    candles_1m: Sequence[Sequence[Any]],
    *,
    dist_to_support: Optional[float] = None,
    structure_score: Optional[float] = None,
) -> dict:
    bars = resample_1m_to_3m(candles_1m)
    closes = [c for c in (_close(row) for row in bars) if c is not None]
    rsi = wilder_rsi(closes)
    stoch = stoch_rsi(closes)
    adx = wilder_adx(bars)
    ret_3m = ret_from_closes(closes)
    block = would_block_short_3m(rsi=rsi, stoch_k=stoch.get("k"), adx=adx)
    snap = {
        "schema": "exhaustion_3m_v1",
        "policy_tag": EXHAUSTION_POLICY_TAG,
        "bar": DECISION_BAR,
        "source_bar": SOURCE_BAR,
        "source": SOURCE_NOTE,
        "rsi14": rsi,
        "stoch_rsi_k": stoch.get("k"),
        "stoch_rsi_d": stoch.get("d"),
        "adx14": adx,
        "ret_3m": ret_3m,
        "dist_to_support": dist_to_support,
        "structure_score": structure_score,
        "would_block_short": block,
        "would_block_reason": WOULD_BLOCK_SHORT_REASON if block else None,
        "hard_veto": False,
        "conjunction": {
            "rsi_max": SHORT_EXHAUSTION_RSI_MAX,
            "stoch_k_max": SHORT_EXHAUSTION_STOCH_K_MAX,
            "adx_min": SHORT_EXHAUSTION_ADX_MIN,
            "note": "LOG-ONLY conjunction; RSI-alone is not a gate",
        },
        "bar_count_3m": len(bars),
        "bar_count_1m": len(candles_1m or []),
    }
    snap["line"] = format_exhaustion_line(snap)
    return snap
