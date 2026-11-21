"""TEST-ONLY from-spec oracle for per-second count, OPEN, LAST, LOW and HIGH.

Pure Python loops; numpy is used only to build the output arrays. Written
from the card text only; this file must stay independent of the Stage A
micro builder.

Governing card: CARD-B-R63X-EXITPATH-r2.1-20261121.md
sha256 a312e9be2d95b067172c805d7f1e191ee1254bc25c5e1c6fbb7bd28eacdba03d.

Card lines used:
  L1139 (s4.1): per second, count, OPEN, LAST, LOW, HIGH (float64; NaN on an
    empty row) from raw trades [id, ts_ms, amount, price]; total order by
    lexsort (ts, id), EXT2-SORT; contiguous second axis over a valid span.
  L1124 (CP-1): per-second trade count, OPEN, LAST, LOW and HIGH from the
    same raw trades; LOW <= OPEN, LAST <= HIGH on every traded row.
  L1189-L1193 (R-C2, MX-MICRO-SECROUND): from-spec oracle for OPEN / LOW /
    HIGH / count; a trade's second is the integer floor of ts_ms/1000
    (integer arithmetic, never a float round); fixtures at ms 0 and 999.
  L1229 (SB-4): total order is timestamp, then trade id / file order
    (lexsort by (ts, id), EXT2-SORT).
  L1285 (S-1..S-4): invariants a correct result satisfies (a traded row has
    LOW/HIGH and OPEN/LAST; an empty row has none of those; LOW <= OPEN,
    LAST <= HIGH). This oracle does not raise STOP 78; those STOPs belong
    to the builder.
  L280-L295 and s2.6 (OPEN-X-5, especially point 6): independent from-spec
    oracle in pure Python loops, different author from the builder (P-8).

Out-of-span trades: S-11 / R-C3 (s2.6 L254, L1193) forbid silently dropping
timestamps whose second is outside the axis (MX-MICRO-OUTOFDAY-SILENT).
The card assigns STOP 78 to the builder, not to this oracle, and does not
name an oracle exception type, so oracle_micro raises ValueError. Output
length is S = 86400 * len(days); a 14,400 s look-ahead second is in-span
only when that next UTC day is included in `days`.
"""

from __future__ import annotations

import math

import numpy as np

_SECONDS_PER_DAY = 86400
_MS_PER_SECOND = 1000


def _floor_ts_ms_to_sec(ts_ms):
    """Integer floor of ts_ms/1000; integer arithmetic, never float round.

    MX-MICRO-SECROUND (L1191): ts/1000 float round vs floor; fixtures at
    ms 0 and 999. math.floor(ts_ms) yields an int, then // is integer
    division. This must not be written as int(ts_ms / 1000) or round().
    """
    ts_ms_int = math.floor(ts_ms)
    return ts_ms_int // _MS_PER_SECOND


def _iter_trade_rows(trades):
    """Yield (id, ts_ms, price) from an (n, 4) array or a list of rows."""
    if trades is None:
        return
    n = len(trades)
    for i in range(n):
        row = trades[i]
        if len(row) != 4:
            raise ValueError(
                "each trade row must be [id, ts_ms, amount, price]; "
                "got length %d at index %d" % (len(row), i)
            )
        yield row[0], row[1], row[3]


def _require_consecutive_days(days):
    if days is None or len(days) == 0:
        raise ValueError("days must be a non-empty list of consecutive UTC days")
    starts = []
    for i, item in enumerate(days):
        try:
            day_start, _trades = item
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "days[%d] must be (day_start_epoch_s, trades)" % i
            ) from exc
        try:
            start = int(day_start)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "days[%d] day_start_epoch_s must be an int, got %r" % (i, day_start)
            ) from exc
        if start != day_start:
            raise ValueError(
                "days[%d] day_start_epoch_s must be an exact integer, got %r"
                % (i, day_start)
            )
        starts.append(start)
    base_s = starts[0]
    for i, start in enumerate(starts):
        expected = base_s + i * _SECONDS_PER_DAY
        if start != expected:
            raise ValueError(
                "days must be consecutive UTC days (each start == days[0][0] "
                "+ i*86400); days[%d] starts at %d, expected %d"
                % (i, start, expected)
            )
    return base_s, len(days) * _SECONDS_PER_DAY


def oracle_micro(days):
    """Build per-second count / OPEN / LAST / LOW / HIGH from raw trades.

    Parameters
    ----------
    days : list of (day_start_epoch_s: int, trades)
        Consecutive UTC days. `trades` is an (n, 4) float64 array or a list
        of rows [id, ts_ms, amount, price]. An empty trades list or array
        is allowed and contributes no prints (count 0, NaN OHLC on those
        seconds).

    Returns
    -------
    dict
        Keys: base_s, count, open, last, low, high.
        Arrays have length S = 86400 * len(days). Row r is epoch second
        base_s + r, where base_s = days[0][0]. count is the number of
        trades in that second. OPEN / LAST are the first / last price in
        total order (ts_ms, then id) per SB-4 L1229. LOW / HIGH are min /
        max price. A second with no trades has count 0 and NaN OHLC.

    Raises
    ------
    ValueError
        If `days` is empty or not consecutive, a trade row is malformed, or
        a trade's second (integer floor of ts_ms/1000) lies outside
        [base_s, base_s + S). The card is silent on the oracle's exception
        type for out-of-span trades (builder S-11 is STOP 78); silent drop
        is forbidden (L254, L1193, MX-MICRO-OUTOFDAY-SILENT).
    """
    base_s, S = _require_consecutive_days(days)

    # Collect every trade, then a stable sort by (ts_ms, id) — SB-4 L1229.
    # File order is the remaining tie-break because sorted() is stable.
    collected = []
    for day_start, trades in days:
        del day_start  # binning is by floor(ts_ms/1000), not the day bucket
        for trade_id, ts_ms, price in _iter_trade_rows(trades):
            sec = _floor_ts_ms_to_sec(ts_ms)
            if sec < base_s or sec >= base_s + S:
                raise ValueError(
                    "trade second %d is outside the span [%d, %d); "
                    "silent drop is forbidden (S-11 / R-C3 / MX-MICRO-OUTOFDAY-SILENT)"
                    % (sec, base_s, base_s + S)
                )
            collected.append((int(math.floor(ts_ms)), trade_id, price))

    collected = sorted(collected, key=lambda item: (item[0], item[1]))

    # Python-loop accumulators; numpy is applied only when building outputs.
    counts = [0] * S
    opens = [None] * S
    lasts = [None] * S
    lows = [None] * S
    highs = [None] * S

    for ts_ms_int, trade_id, price in collected:
        del trade_id
        r = (ts_ms_int // _MS_PER_SECOND) - base_s
        px = float(price)
        n = counts[r]
        if n == 0:
            opens[r] = px
            lasts[r] = px
            lows[r] = px
            highs[r] = px
        else:
            lasts[r] = px
            if px < lows[r]:
                lows[r] = px
            if px > highs[r]:
                highs[r] = px
        counts[r] = n + 1

    nan = math.nan
    open_out = [nan if v is None else v for v in opens]
    last_out = [nan if v is None else v for v in lasts]
    low_out = [nan if v is None else v for v in lows]
    high_out = [nan if v is None else v for v in highs]

    # Card s2.6 L273: count uint32 + OPEN/LAST/LOW/HIGH f64.
    return {
        "base_s": base_s,
        "count": np.array(counts, dtype=np.uint32),
        "open": np.array(open_out, dtype=np.float64),
        "last": np.array(last_out, dtype=np.float64),
        "low": np.array(low_out, dtype=np.float64),
        "high": np.array(high_out, dtype=np.float64),
    }
