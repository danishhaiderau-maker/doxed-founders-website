"""3-minute decision-cycle indicators (not 5m / 15m / 1h).

The Showcase AI cadence is ~180s. Bitfinex REST candles have 1m and 5m but not 3m,
so this module resamples native 1m OHLC into 3m bars and computes Wilder RSI(14),
StochRSI(14,14,3), and Wilder ADX(14) on that 3m series.

Do not treat 5m as close enough. Higher-TF RSI is not computed here.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence


EXHAUSTION_POLICY_TAG = "mtf_exhaustion_log_v1"
UNIVERSE_POLICY_TAG = "cycle_3m_universe_v1"
UNIVERSE_SCHEMA = "cycle_3m_universe_v1"
UNIVERSE_FILE = "cycle_3m_universe.jsonl"
DECISION_BAR_SEC = 180
SOURCE_BAR = "1m"
DECISION_BAR = "3m"
SOURCE_NOTE = "bitfinex_1m_resampled_to_3m"
RSI_PERIOD = 14
STOCH_PERIOD = 14
STOCH_D_PERIOD = 3
ADX_PERIOD = 14
DONCHIAN_PERIOD = 20
BB_PERIOD = 20
BB_STD = 2.0
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


def _true_range_and_dm(candles: Sequence[Sequence[Any]]) -> Optional[tuple]:
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
    return trs, plus_dm, minus_dm


def wilder_atr(candles: Sequence[Sequence[Any]], period: int = ADX_PERIOD) -> Optional[float]:
    """Wilder ATR from the same true-range series ADX already uses."""
    if len(candles) < period + 2:
        return None
    packed = _true_range_and_dm(candles)
    if packed is None:
        return None
    trs, _, _ = packed
    atr = _wilder_smooth(trs, period)
    if not atr:
        return None
    return round(atr[-1], 8)


def atr14_pct_of_price(candles: Sequence[Sequence[Any]], period: int = ADX_PERIOD) -> Optional[float]:
    """ATR(14) as percent of last close. 0.12 means 0.12% of price."""
    atr = wilder_atr(candles, period)
    close = _close(candles[-1]) if candles else None
    if atr is None or close is None or close <= 0:
        return None
    return round((atr / close) * 100.0, 8)


def donchian_channel(candles: Sequence[Sequence[Any]], period: int = DONCHIAN_PERIOD) -> dict:
    """Close location in the last ``period`` 3m highs/lows. 0=low, 1=high."""
    empty = {"high": None, "low": None, "loc": None, "close": None, "period": period}
    if len(candles) < 2:
        return empty
    window = candles[-min(period, len(candles)):]
    highs, lows = [], []
    for row in window:
        h, l = _finite(row[2]) if len(row) > 2 else None, _finite(row[3]) if len(row) > 3 else None
        if h is not None:
            highs.append(h)
        if l is not None:
            lows.append(l)
    close = _close(window[-1]) if window else None
    if not highs or not lows or close is None:
        return empty
    hi, lo = max(highs), min(lows)
    span = hi - lo
    loc = 0.5 if span <= 1e-12 else (close - lo) / span
    return {
        "high": round(hi, 4),
        "low": round(lo, 4),
        "loc": round(loc, 6),
        "close": round(close, 4),
        "period": period,
    }


def bollinger_width(closes: Sequence[float], period: int = BB_PERIOD, num_std: float = BB_STD) -> Optional[float]:
    """(upper - lower) / middle for a ``period`` SMA ± ``num_std`` σ band."""
    if len(closes) < period:
        return None
    window = [float(v) for v in closes[-period:]]
    mean = sum(window) / float(period)
    if mean <= 1e-12:
        return None
    var = sum((value - mean) ** 2 for value in window) / float(period)
    width = (2.0 * float(num_std) * (var ** 0.5)) / mean
    return round(width, 8)


def session_utc_label(ts: Optional[float] = None) -> str:
    from datetime import datetime, timezone
    if ts is None:
        hour = datetime.now(timezone.utc).hour
    else:
        hour = datetime.fromtimestamp(float(ts), timezone.utc).hour
    if hour < 8:
        return "ASIA"
    if hour < 16:
        return "EU"
    return "US"


def hour_utc_bucket(ts: Optional[float] = None) -> int:
    from datetime import datetime, timezone
    if ts is None:
        return datetime.now(timezone.utc).hour
    return datetime.fromtimestamp(float(ts), timezone.utc).hour


def cycle_bucket_3m(ts: Optional[float] = None) -> int:
    from time import time as _now
    stamp = float(ts if ts is not None else _now())
    return int(stamp // DECISION_BAR_SEC) * DECISION_BAR_SEC


def wilder_adx(candles: Sequence[Sequence[Any]], period: int = ADX_PERIOD) -> Optional[float]:
    if len(candles) < period + 2:
        return None
    packed = _true_range_and_dm(candles)
    if packed is None:
        return None
    trs, plus_dm, minus_dm = packed
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


def format_universe_line(snapshot: Mapping[str, Any]) -> str:
    outcome = snapshot.get("cycle_outcome") or "cycle"
    return (
        f"3m universe {outcome} atr%={snapshot.get('atr14_pct_3m')} "
        f"donchian={snapshot.get('donchian_loc_3m')} bb={snapshot.get('bb_width_3m')} "
        f"delta={snapshot.get('delta_3m')} imb={snapshot.get('imbalance_3m')} "
        f"{snapshot.get('session_utc')} (log-only)"
    )


def _base_3m_snapshot(
    candles_1m: Sequence[Sequence[Any]],
    *,
    dist_to_support: Optional[float] = None,
    dist_to_resistance: Optional[float] = None,
    structure_score: Optional[float] = None,
) -> tuple:
    bars = resample_1m_to_3m(candles_1m)
    closes = [c for c in (_close(row) for row in bars) if c is not None]
    rsi = wilder_rsi(closes)
    stoch = stoch_rsi(closes)
    adx = wilder_adx(bars)
    atr = wilder_atr(bars)
    atr_pct = atr14_pct_of_price(bars)
    donch = donchian_channel(bars)
    bb_w = bollinger_width(closes)
    ret_3m = ret_from_closes(closes)
    block = would_block_short_3m(rsi=rsi, stoch_k=stoch.get("k"), adx=adx)
    snap = {
        "bar": DECISION_BAR,
        "source_bar": SOURCE_BAR,
        "source": SOURCE_NOTE,
        "rsi14": rsi,
        "stoch_rsi_k": stoch.get("k"),
        "stoch_rsi_d": stoch.get("d"),
        "adx14": adx,
        "atr14": atr,
        "atr14_pct_3m": atr_pct,
        "donchian_loc_3m": donch.get("loc"),
        "donchian_high_3m": donch.get("high"),
        "donchian_low_3m": donch.get("low"),
        "bb_width_3m": bb_w,
        "ret_3m": ret_3m,
        "dist_to_support": dist_to_support,
        "dist_to_resistance": dist_to_resistance,
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
    return snap, bars


def compute_3m_exhaustion_snapshot(
    candles_1m: Sequence[Sequence[Any]],
    *,
    dist_to_support: Optional[float] = None,
    structure_score: Optional[float] = None,
    dist_to_resistance: Optional[float] = None,
) -> dict:
    snap, _ = _base_3m_snapshot(
        candles_1m,
        dist_to_support=dist_to_support,
        dist_to_resistance=dist_to_resistance,
        structure_score=structure_score,
    )
    snap["schema"] = "exhaustion_3m_v1"
    snap["policy_tag"] = EXHAUSTION_POLICY_TAG
    snap["line"] = format_exhaustion_line(snap)
    return snap


def compute_3m_universe_snapshot(
    candles_1m: Sequence[Sequence[Any]],
    *,
    dist_to_support: Optional[float] = None,
    dist_to_resistance: Optional[float] = None,
    structure_score: Optional[float] = None,
    delta_3m: Optional[float] = None,
    imbalance_3m: Optional[float] = None,
    support_price: Optional[float] = None,
    resistance_price: Optional[float] = None,
    ts: Optional[float] = None,
    cycle_outcome: str = "SKIPPED",
    skip_reason: Optional[str] = None,
    decision: Optional[str] = None,
    trade_id: Optional[str] = None,
    direction: Optional[str] = None,
) -> dict:
    """Taken + skipped 3m decision-universe row. Never a live veto."""
    snap, _ = _base_3m_snapshot(
        candles_1m,
        dist_to_support=dist_to_support,
        dist_to_resistance=dist_to_resistance,
        structure_score=structure_score,
    )
    stamp = float(ts) if ts is not None else None
    snap.update({
        "schema": UNIVERSE_SCHEMA,
        "policy_tag": UNIVERSE_POLICY_TAG,
        "path_replay_tag": "path_replay_v1",
        "cycle_bucket": cycle_bucket_3m(stamp),
        "captured_ts": stamp,
        "session_utc": session_utc_label(stamp),
        "hour_utc": hour_utc_bucket(stamp),
        "delta_3m": None if delta_3m is None else _finite(delta_3m),
        "imbalance_3m": None if imbalance_3m is None else _finite(imbalance_3m),
        "support_price": None if support_price is None else _finite(support_price),
        "resistance_price": None if resistance_price is None else _finite(resistance_price),
        "cycle_outcome": str(cycle_outcome or "SKIPPED").upper(),
        "skip_reason": skip_reason,
        "decision": decision,
        "trade_id": trade_id,
        "direction": None if direction is None else str(direction).upper(),
        "live_veto": False,
    })
    snap["line"] = format_universe_line(snap)
    snap["exhaustion_line"] = format_exhaustion_line(snap)
    return snap
