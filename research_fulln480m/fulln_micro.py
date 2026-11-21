"""B-R63X Stage A micro builder for the BTC full-N search: per-second count, OPEN, LAST, LOW, HIGH.

Binding: card B-R63X r2.1 a312e9be2d95 (Boss GO 39ecf833b6c2; RK PASS be4ed68cd3de) s4.1, s5.1 A-1 /
A-2 / A-3, s5.2 R-C1 / R-C2 / R-C3, s6 S-1..S-5 / S-10 / S-11 / S-12, SB-4 (BC-2); R63-G r6 CP-1.
Rules c342ecf8ec9a. Live OFF. Paper only. Gross only (R61). N stays 1,922,088,960; nothing here
narrows a horizon, a direction, a column or a cell (the builder is direction- and column-free).

What it builds (s4.1): from the raw Bitfinex trades [id, ts_ms, amount, price] of each PASS day
(bfx_day form, float64), sorted per EXT2-SORT (lexsort by (ts, id), as ext2 L610) so that same-ms
prints have a TOTAL order (timestamp, then trade id; SB-4 / BC-2), per second s:
  count[s] = number of trades with floor(ts_ms / 1000) == s (integer floor, never a float round),
  OPEN[s]  = price of the FIRST trade of second s in that order,
  LAST[s]  = price of the LAST trade of second s in that order (ext2 per_second "last", L702-L710),
  LOW[s]   = min price over second s,  HIGH[s] = max price over second s;
OPEN / LAST / LOW / HIGH are float64 and NaN on an empty row (count 0). The second axis is ONE
CONTIGUOUS axis over a valid span of consecutive PASS UTC days (as ext2 L741-L753), row index
= epoch second - base (never "mod 86,400"), never clipped at a UTC day end (R-C1). A per-day chunk
with a 14,400 s look-ahead into the next PASS day of the span is available (build_micro_chunk);
it is bit-identical to the same rows of the span build.

STOP 78 (each raises fulln_closure.ClosureViolation; *_guarded / run_guarded -> sys.exit(78)):
  S-1  a traded row with LOW or HIGH missing (NaN);
  S-2  an empty row with OPEN, LAST, LOW or HIGH present;
  S-3  LAST not bit-equal (NaN-equal, int64 bit view) to the given ext2 px, or has-trades differs;
  S-4  a traded row violating LOW <= OPEN <= HIGH or LOW <= LAST <= HIGH (F-G-OHLC);
  S-5  a window row outside the built span (micro-axis accessor);
  S-10 malformed input (shape / dtype / non-integer or non-finite id or ts / day start not a UTC
       day / days not increasing / horizon not one of the 12);
  S-11 builder input integrity (R-C3): (a) PASS day with an empty trade array; (b) ts outside
       [day start, + 86,400,000 ms); (c) non-finite or <= 0 price; (d) duplicate ids in a day;
       (e) missing day in the span;
  S-12 cross-input (R-C3): over every second of the span, count > 0 equals the `has` array fed to
       decide_core, LAST equals the px behind last_tr (bit view, NaN-equal), and valid is True.
Out-of-day trades are NEVER silently dropped (ext2 per_second's mask L704-L705 would hide them).

This module never imports tests/, the ext2 reference copy, any oracle, ext2_search or a kernel.
"""
from typing import NamedTuple

import numpy as np

from research_fulln480m import fulln_decide_pins as _dpins
from research_fulln480m.fulln_closure import ClosureViolation, run_guarded  # imports:closure

EXIT_STOP = 78
DAY_S = 86_400
DAY_MS = 86_400_000
LOOKAHEAD_S = 14_400  # s4.1 / OPEN-X-10: the longest horizon
HORIZONS_12 = tuple(_dpins.HORIZONS_12)
N_COLS = 4  # [id, ts_ms, amount, price]
assert DAY_S == _dpins.DAY_S and LOOKAHEAD_S == max(HORIZONS_12) and len(HORIZONS_12) == 12


class Micro(NamedTuple):
    """Per-second micro arrays on one contiguous axis. Row r is epoch second base_s + r."""
    base_s: int
    count: np.ndarray   # uint32
    open: np.ndarray    # float64, NaN on an empty row
    last: np.ndarray    # float64, NaN on an empty row
    low: np.ndarray     # float64, NaN on an empty row
    high: np.ndarray    # float64, NaN on an empty row


def _stop(tag, reason):
    raise ClosureViolation("B-R63X micro STOP 78 %s: %s" % (tag, reason))


def _is_int(x):
    return type(x) is int or isinstance(x, np.integer)


def bits_equal(a, b):
    """Float compare by int64 bit view, NaN-equal (OPEN-X-5 point 7); never ==."""
    a = np.asarray(a)
    b = np.asarray(b)
    if a.dtype != np.float64 or b.dtype != np.float64 or a.shape != b.shape:
        return False
    na, nb = np.isnan(a), np.isnan(b)
    if not np.array_equal(na, nb):
        return False
    return bool(np.array_equal(a[~na].view(np.int64), b[~nb].view(np.int64)))


# ---------------------------------------------------------------- input checks
def _check_days(days):
    """Structural checks of the span: [(day_start_epoch_s, trades float64 (n, 4)), ...]."""
    try:
        days = list(days)
    except TypeError:
        _stop("S-10", "days is not a sequence")
    if not days:
        _stop("S-10", "no day in the span")
    out = []
    for k, item in enumerate(days):
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            _stop("S-10", "day %d is not a (day_start_s, trades) pair" % k)
        d, t = item
        if not _is_int(d):
            _stop("S-10", "day %d start %r is not an integer epoch second" % (k, d))
        d = int(d)
        if d % DAY_S != 0:
            _stop("S-10", "day %d start %d is not a UTC day start" % (k, d))
        if not isinstance(t, np.ndarray) or t.dtype != np.float64 or t.ndim != 2 or t.shape[1] != N_COLS:
            _stop("S-10", "day %d trades are not a float64 (n, 4) array" % k)
        out.append((d, t))
    for (a, _ta), (b, _tb) in zip(out, out[1:]):
        if b <= a:
            _stop("S-10", "day starts not strictly increasing at %d" % b)
        if b != a + DAY_S:
            _stop("S-11(e)", "missing day in the span between %d and %d" % (a, b))
    return out


def _check_day_trades(d, t):
    """S-11 (a)..(d) and S-10 on one PASS day; returns (ids int64, ts_ms int64)."""
    if t.shape[0] == 0:
        _stop("S-11(a)", "PASS day %d has an empty trade array" % d)
    ids_f, ts_f, px = t[:, 0], t[:, 1], t[:, 3]
    if not (np.all(np.isfinite(ids_f)) and np.all(np.isfinite(ts_f))):
        _stop("S-10", "day %d has a non-finite id or ts" % d)
    if not (np.all(ids_f == np.floor(ids_f)) and np.all(ts_f == np.floor(ts_f))):
        _stop("S-10", "day %d has a non-integer id or ts" % d)
    if not (np.all(np.isfinite(px)) and np.all(px > 0.0)):
        _stop("S-11(c)", "day %d has a non-finite or <= 0 price" % d)
    ids = ids_f.astype(np.int64)
    ts = ts_f.astype(np.int64)
    lo = d * 1000
    if np.any(ts < lo) or np.any(ts >= lo + DAY_MS):
        _stop("S-11(b)", "day %d has a ts outside [%d, %d)" % (d, lo, lo + DAY_MS))
    if np.unique(ids).size != ids.size:
        _stop("S-11(d)", "day %d has duplicate trade ids" % d)
    return ids, ts


def sort_trades(t):
    """EXT2-SORT (ext2 L610): lexsort by (ts, id): ts first, then id. Returns a new sorted array."""
    ids, ts = t[:, 0].astype(np.int64), t[:, 1].astype(np.int64)
    return t[np.lexsort((ids, ts))]


# ---------------------------------------------------------------- the builder
def _build(days):
    days = _check_days(days)
    base = days[0][0]
    S = DAY_S * len(days)
    count = np.zeros(S, dtype=np.uint32)
    op = np.full(S, np.nan)
    la = np.full(S, np.nan)
    lo = np.full(S, np.nan)
    hi = np.full(S, np.nan)
    for d, t in days:
        ids, ts = _check_day_trades(d, t)
        o = np.lexsort((ids, ts))                      # total order: ts, then id (SB-4 / BC-2)
        ts_o = ts[o]
        px_o = np.ascontiguousarray(t[o, 3], dtype=np.float64)
        rows = ts_o // 1000 - base                     # integer floor; contiguous axis, no mod-day
        first = np.flatnonzero(np.concatenate(([True], rows[1:] != rows[:-1])))
        lastp = np.concatenate((first[1:], [rows.size])) - 1
        r = rows[first]
        count[r] = (lastp - first + 1).astype(np.uint32)
        op[r] = px_o[first]
        la[r] = px_o[lastp]
        lo[r] = np.minimum.reduceat(px_o, first)
        hi[r] = np.maximum.reduceat(px_o, first)
    m = Micro(int(base), count, op, la, lo, hi)
    check_cp1(m)
    return m


def build_micro(days):
    """Micro arrays over ONE contiguous valid span of consecutive PASS UTC days.

    days: sequence of (day_start_epoch_s: int, trades: float64 ndarray (n, 4) [id, ts_ms, amount,
    price]) in increasing day order, any file order inside a day. Returns Micro with
    len == 86,400 * len(days) and base_s == days[0][0].
    """
    return _build(days)


def build_micro_chunk(days, k):
    """Day k of the span plus a 14,400 s look-ahead into day k + 1 if day k + 1 is in the span
    (OPEN-X-10 (1)); bit-identical to rows [86,400 k, 86,400 k + len) of build_micro(days)."""
    days = _check_days(days)
    if not _is_int(k) or not 0 <= int(k) < len(days):
        _stop("S-10", "chunk index %r outside the span" % (k,))
    k = int(k)
    pair = days[k:k + 2]
    m = _build(pair)
    n = DAY_S + (LOOKAHEAD_S if len(pair) == 2 else 0)
    return Micro(m.base_s, m.count[:n].copy(), m.open[:n].copy(), m.last[:n].copy(),
                 m.low[:n].copy(), m.high[:n].copy())


def build_micro_guarded(*args, **kwargs):
    """build_micro() under fulln_closure.run_guarded: any ClosureViolation -> sys.exit(78)."""
    return run_guarded(lambda: build_micro(*args, **kwargs))


# ---------------------------------------------------------------- checks on the output
def check_cp1(m, px=None, has=None):
    """CP-1 S-1..S-4 on a Micro; S-3 also against ext2 px / has aligned to the micro axis."""
    S = len(m.count)
    for name in ("open", "last", "low", "high"):
        a = getattr(m, name)
        if not isinstance(a, np.ndarray) or a.dtype != np.float64 or a.shape != (S,):
            _stop("S-10", "micro %s is not float64 of length %d" % (name, S))
    traded = np.asarray(m.count) > 0
    empty = ~traded
    if np.any(np.isnan(m.low[traded])) or np.any(np.isnan(m.high[traded])):
        _stop("S-1", "a traded row is missing LOW or HIGH")
    for name in ("open", "last", "low", "high"):
        if np.any(~np.isnan(getattr(m, name)[empty])):
            _stop("S-2", "an empty row has %s present" % name.upper())
    if (px is None) != (has is None):
        _stop("S-10", "px and has must be given together")
    if px is not None:
        px = np.asarray(px)
        has = np.asarray(has)
        if px.shape != (S,) or has.shape != (S,):
            _stop("S-3", "px / has length differs from the micro axis")
        if not bits_equal(m.last, px):
            _stop("S-3", "LAST is not bit-equal to ext2 px")
        if not np.array_equal(traded, has.astype(bool)):
            _stop("S-3", "has-trades differs from count > 0")
    o, la, lo, hi = m.open[traded], m.last[traded], m.low[traded], m.high[traded]
    if not (np.all(lo <= o) and np.all(o <= hi) and np.all(lo <= la) and np.all(la <= hi)):
        _stop("S-4", "a traded row violates LOW <= OPEN, LAST <= HIGH")
    return None


def cross_check_decide(m, has, px, valid, offset=0):
    """S-12: micro row r is decide-axis second offset + r; checked over every second of the span."""
    if not _is_int(offset):
        _stop("S-10", "offset %r is not an integer" % (offset,))
    offset = int(offset)
    has = np.asarray(has)
    px = np.asarray(px)
    valid = np.asarray(valid)
    n = len(has)
    if px.shape != (n,) or valid.shape != (n,) or has.shape != (n,):
        _stop("S-12", "has / px / valid lengths differ")
    if has.dtype != bool or valid.dtype != bool or px.dtype != np.float64:
        _stop("S-12", "has / valid must be bool and px float64")
    S = len(m.count)
    if offset < 0 or offset + S > n:
        _stop("S-12", "micro span [%d, %d) is outside the decide arrays of length %d" % (offset, offset + S, n))
    seg = slice(offset, offset + S)
    if not np.all(valid[seg]):
        _stop("S-12", "valid is False inside the micro span")
    if not np.array_equal(np.asarray(m.count) > 0, has[seg]):
        _stop("S-12", "count > 0 differs from has")
    if not bits_equal(m.last, px[seg]):
        _stop("S-12", "LAST differs from px")
    return None


# ---------------------------------------------------------------- micro-axis accessor
def window_rows(m, t_e, h):
    """Rows t_e .. t_e + h - 1 (inclusive) of the micro axis as a Micro (base_s = m.base_s + t_e).
    h must be one of the 12 horizons (S-10); any row outside the built span is STOP 78 (S-5).
    No clipping at a UTC day end (R-C1)."""
    if not _is_int(h) or int(h) not in HORIZONS_12:
        _stop("S-10", "horizon %r is not one of the 12" % (h,))
    if not _is_int(t_e):
        _stop("S-10", "t_e %r is not an integer" % (t_e,))
    a = int(t_e)
    b = a + int(h) - 1
    S = len(m.count)
    if a < 0 or b >= S:
        _stop("S-5", "window rows [%d, %d] outside the span [0, %d)" % (a, b, S))
    return Micro(m.base_s + a, m.count[a:b + 1], m.open[a:b + 1], m.last[a:b + 1],
                 m.low[a:b + 1], m.high[a:b + 1])
