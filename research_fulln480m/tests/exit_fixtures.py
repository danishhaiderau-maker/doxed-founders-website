"""Synthetic builders for B-R63X Stage A exit fixtures (card r2.1 a312 s4.1 / s7).

TEST-ONLY. No data files. Never imported by production. Live OFF. Gross only.
"""
import numpy as np

from research_fulln480m.fulln_micro import Micro
from research_fulln480m.fulln_exit_pins import HORIZONS_12, N_EXITS

DAY_S = 86_400
HORIZONS = HORIZONS_12
DIRS = (1, -1)
HD = tuple((h, d) for h in HORIZONS for d in DIRS)


def empty_micro(n, base_s=0):
    """n contiguous seconds, all empty (count 0, OHLC NaN float64)."""
    n = int(n)
    nan = np.full(n, np.nan, dtype=np.float64)
    return Micro(int(base_s), np.zeros(n, dtype=np.uint32), nan.copy(), nan.copy(),
                 nan.copy(), nan.copy())


def copy_micro(m):
    return Micro(int(m.base_s), m.count.copy(), m.open.copy(), m.last.copy(),
                 m.low.copy(), m.high.copy())


def set_bar(m, s, *, p0, last=None, low=None, high=None, count=1):
    """Write a traded row at index s. Default last/low/high = p0 (flat, no trigger)."""
    s = int(s)
    p0 = np.float64(p0)
    if last is None:
        last = p0
    if low is None:
        low = p0 if last is None else min(np.float64(last), p0)
    if high is None:
        high = p0 if last is None else max(np.float64(last), p0)
    # Keep LOW <= min(OPEN, LAST) <= max(OPEN, LAST) <= HIGH (S-4).
    last = np.float64(last)
    low = np.float64(low)
    high = np.float64(high)
    low = min(low, p0, last)
    high = max(high, p0, last)
    m.count[s] = np.uint32(count)
    m.open[s] = p0
    m.last[s] = last
    m.low[s] = low
    m.high[s] = high
    return m


def last_for_r(p0, direction, r_bp):
    """LAST that realises r_bp at p0, direction d: r = d*(LAST/p0-1)*1e4."""
    p0 = np.float64(p0)
    return p0 * (1.0 + np.float64(direction) * np.float64(r_bp) / np.float64(1e4))


def ext_for_g(p0, direction, g_bp):
    """EXT (LOW long / HIGH short) that realises g_adv = g_bp at p0."""
    p0 = np.float64(p0)
    return p0 * (1.0 + np.float64(direction) * np.float64(g_bp) / np.float64(1e4))


def flat_window(h, t_e=0, extra=0, base_s=0, p0=100.0, fill_before=True):
    """Micro long enough for window t_e..t_e+h-1 plus `extra` trailing seconds.

    Every window (and, if fill_before, 0..t_e-1) second is a flat traded bar at p0
    so r = 0 and g_adv = 0: no TP / SL / TR / LIQ trigger. TIME wins at k = h-1.
    """
    h = int(h)
    t_e = int(t_e)
    n = t_e + h + int(extra)
    m = empty_micro(n, base_s=base_s)
    start = 0 if fill_before else t_e
    for s in range(start, t_e + h):
        set_bar(m, s, p0=p0)
    return m, np.float64(p0)


def poke_ext(m, s, direction, g_bp, p0=None):
    """Set the adverse extreme of row s to realise g_adv = g_bp, leaving LAST at p0 or as-is."""
    s = int(s)
    if p0 is None:
        p0 = m.open[s]
    ext = ext_for_g(p0, direction, g_bp)
    last = m.last[s]
    if not np.isfinite(last):
        last = np.float64(p0)
    if int(direction) == 1:
        # long: EXT = LOW; keep HIGH legal
        set_bar(m, s, p0=p0, last=last, low=ext, high=max(m.high[s] if np.isfinite(m.high[s]) else p0, p0, last, ext))
    else:
        set_bar(m, s, p0=p0, last=last, low=min(m.low[s] if np.isfinite(m.low[s]) else p0, p0, last, ext), high=ext)
    return m


def poke_last(m, s, direction, r_bp, p0=None):
    """Set LAST of row s to realise r = r_bp, widening LOW/HIGH to stay legal."""
    s = int(s)
    if p0 is None:
        p0 = m.open[s]
    last = last_for_r(p0, direction, r_bp)
    low = m.low[s] if np.isfinite(m.low[s]) else min(np.float64(p0), last)
    high = m.high[s] if np.isfinite(m.high[s]) else max(np.float64(p0), last)
    set_bar(m, s, p0=p0, last=last, low=min(low, last, p0), high=max(high, last, p0))
    return m


def k_max_i12(T, t_e, h):
    """I.12 K_max = floor((t_e + h - 2 - T) / 60)."""
    return (int(t_e) + int(h) - 2 - int(T)) // 60


def legal_Tprimes(T, t_e, h):
    km = k_max_i12(T, t_e, h)
    if km < 1:
        return ()
    return tuple(int(T) + 60 * j for j in range(1, km + 1))


def legal_ks(T, t_e, h):
    return tuple(Tp + 1 - int(t_e) for Tp in legal_Tprimes(T, t_e, h))


def two_day_axis(base_s=0):
    """2 * 86,400 empty seconds (contiguous valid span across midnight)."""
    return empty_micro(2 * DAY_S, base_s=base_s)


# Width pin used by tests (S-14 / F-X-768).
assert N_EXITS == 768
