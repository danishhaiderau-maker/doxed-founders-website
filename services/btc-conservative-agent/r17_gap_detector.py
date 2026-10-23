"""The ONE R17 observability-gap detector (R4): shared by the protection replay's C2 stop rule (#847) and the
book limit entry's ENTRY_BOOK_GAP (#848).  No caller may keep its own copy of these thresholds.

C2 binding rule (RK + HM agreed, 24 Oct 2026 AEDT; source: TRADEPRINT-GAP-20261023):
    a replay cell is R17_UNPROVEN iff the book's executable exit side touches/passes a stop level during a
    trade-print gap (a, b) [the TOUCH gate lives in research_v3_policy_replay] AND at least one of
      * BOOK_HOLE    - >= 1 missing bfx_book_10l 1-s row overlapping the gap (consecutive rows ``diff >= 2``),
      * BOOT         - a bot boot within [a - 5 s, b + 60 s],
      * GAP_OVER_60S - the gap is longer than 60 s (incl. a touch before the first / after the last print),
      * BOOK_MISSING / BOOT_LOG_MISSING - the book rows or the boot log are ABSENT/UNREADABLE for that window
        (fail closed).  Absence of a WS-resubscribe log is NOT evidence and never flags (none is logged yet).
    A quiet gap with live book rows, a readable boot log and no boot is NOT flagged, whatever its length <= 60 s.
Offline clearing would need an exact print match against Bitfinex public trades/hist stamped with that source;
it is not implemented, so nothing is ever cleared here.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

BOOK_HOLE_MIN_DIFF_S = 2          # consecutive book rows s_i, s_j with s_j - s_i >= 2 -> one missing row = hole
TRADE_GAP_BACKSTOP_S = 60.0       # a print gap longer than this is flagged on its own
BOOT_PRE_S, BOOT_POST_S = 5.0, 60.0
BOOT_LOG_LAG_S = 120.0            # a boot appears in the 1-min telemetry within ~2 min
BOOT_LOG_FILE = "runtime_telemetry_1m.jsonl"

CLAUSE_BOOK_HOLE, CLAUSE_BOOT, CLAUSE_GAP_OVER_60S = 1, 2, 4
CLAUSE_BOOK_MISSING, CLAUSE_BOOT_LOG_MISSING = 8, 16
CLAUSE_NAMES = {CLAUSE_BOOK_HOLE: "BOOK_HOLE", CLAUSE_BOOT: "BOOT", CLAUSE_GAP_OVER_60S: "GAP_OVER_60S",
                CLAUSE_BOOK_MISSING: "BOOK_MISSING", CLAUSE_BOOT_LOG_MISSING: "BOOT_LOG_MISSING"}
CLAUSE_PREFIX = "C2:"

RULE_STATUS = (
    "DATA_DERIVED_IN_SAMPLE: TRADEPRINT-GAP-20261023 (Strategist 23 Oct 2026, HM reproduced); n=17 outage gaps "
    "(6 ordinary WS drops, 11 from the 18-19 Oct restarts); fitted in-sample; pre-registered out-of-sample "
    "re-check pending; Bitfinex REST trades/hist cross-check offline only (no clearing implemented)"
)


def clause_label(mask: int) -> Optional[str]:
    """``C2:BOOK_HOLE+BOOT`` for a non-zero clause bitmask, else None."""
    names = [name for bit, name in CLAUSE_NAMES.items() if int(mask) & bit]
    return CLAUSE_PREFIX + "+".join(names) if names else None


def clause_names(label: Any) -> list[str]:
    text = str(label or "")
    return [n for n in text[len(CLAUSE_PREFIX):].split("+") if n] if text.startswith(CLAUSE_PREFIX) else []


# ---------------------------------------------------------------- book holes (shared with ENTRY_BOOK_GAP)
def missing_seconds_are_a_hole(gap_seconds: Sequence[Any]) -> bool:
    """#848 ENTRY_BOOK_GAP: any missing 1-s book row (``diff >= 2`` between present rows) is a hole."""
    return any(isinstance(g, (int, float)) for g in gap_seconds or ())


def book_hole_spans(book_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Time spans [start, end) during which a book row is missing: for present rows s_i, s_j (diff >= 2) the
    missing rows s_i+1 .. s_j-1 would have existed over [s_i + 1, s_j)."""
    s = np.asarray(book_s, dtype=np.float64)
    if len(s) < 2:
        return np.empty(0), np.empty(0)
    d = np.diff(s)
    idx = np.flatnonzero(d >= BOOK_HOLE_MIN_DIFF_S)
    return s[idx] + 1.0, s[idx + 1]


# ---------------------------------------------------------------- boot log
class BootLog:
    """Bot boots read from runtime_telemetry (1-min rows carrying ``boot_ts``)."""

    def __init__(self, sample_ts: Iterable[float], boot_ts: Iterable[float]) -> None:
        self.sample_ts = np.unique(np.asarray([float(t) for t in sample_ts], dtype=np.float64))
        self.boots = np.unique(np.asarray([float(t) for t in boot_ts], dtype=np.float64))

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, Any]]) -> "BootLog":
        samples, boots = [], []
        for row in rows:
            try:
                ts = float(row.get("ts"))
            except (TypeError, ValueError, AttributeError):
                continue
            if not math.isfinite(ts):
                continue
            samples.append(ts)
            try:
                boot = float(row.get("boot_ts"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(boot):
                boots.append(boot)
        return cls(samples, boots)

    @classmethod
    def load(cls, paths: Iterable[Path], windows: Optional[Sequence[tuple[float, float]]] = None) -> "BootLog":
        def rows():
            for path in paths:
                try:
                    handle = Path(path).open("r", encoding="utf-8", errors="replace")
                except OSError:
                    continue
                with handle:
                    for line in handle:
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(row, dict):
                            yield row
        return cls.from_rows(rows())

    def readable(self, a: float, b: float) -> bool:
        """A sample before the window and one late enough that a boot in [a-5, b+60] would already be logged."""
        s = self.sample_ts
        return bool(len(s)) and s[0] <= a - BOOT_PRE_S and s[-1] >= b + BOOT_POST_S + BOOT_LOG_LAG_S

    def boot_in(self, a: float, b: float) -> bool:
        lo = int(np.searchsorted(self.boots, a - BOOT_PRE_S, side="left"))
        return lo < len(self.boots) and self.boots[lo] <= b + BOOT_POST_S


# ---------------------------------------------------------------- the detector
def gap_clauses(a: float, b: float, book_s: Optional[np.ndarray], boot_log: Optional[BootLog]) -> int:
    """Clause bitmask for one trade-print gap (a, b) (exchange print times; +/-inf at the tape edges)."""
    mask = 0
    if not (math.isfinite(a) and math.isfinite(b)) or b - a > TRADE_GAP_BACKSTOP_S:
        mask |= CLAUSE_GAP_OVER_60S
    s = np.asarray(book_s if book_s is not None else (), dtype=np.float64)
    lo_t = a if math.isfinite(a) else (b if math.isfinite(b) else 0.0)
    hi_t = b if math.isfinite(b) else lo_t
    # book rows must bracket the gap: a row at/before a and one complete at/after b
    if not len(s) or s[0] + 1.0 > lo_t or s[-1] + 1.0 < hi_t:
        mask |= CLAUSE_BOOK_MISSING
    else:
        starts, ends = book_hole_spans(s)
        if len(starts) and bool(np.any((starts < hi_t) & (ends > lo_t))):
            mask |= CLAUSE_BOOK_HOLE
    if boot_log is None or not boot_log.readable(lo_t, hi_t):
        mask |= CLAUSE_BOOT_LOG_MISSING
    elif boot_log.boot_in(lo_t, hi_t):
        mask |= CLAUSE_BOOT
    return mask


def touch_gap_clauses(ts: np.ndarray, trade_ts: np.ndarray, book_s: Optional[np.ndarray],
                      boot_log: Optional[BootLog]) -> np.ndarray:
    """Per timestamp, the clause bitmask of the print gap it sits in.  ``trade_ts`` must be the FULL tape's prints
    (HM window-edge fix): previous/next prints are never cut off at the replay window."""
    ts = np.asarray(ts, dtype=np.float64)
    out = np.zeros(len(ts), dtype=np.int64)
    if not len(ts):
        return out
    prints = np.asarray(trade_ts, dtype=np.float64)
    after = np.searchsorted(prints, ts, side="right")
    prev = np.where(after > 0, prints[np.maximum(after - 1, 0)] if len(prints) else -np.inf, -np.inf)
    nxt = np.where(after < len(prints), prints[np.minimum(after, max(len(prints) - 1, 0))] if len(prints) else np.inf,
                   np.inf)
    cache: dict[tuple[float, float], int] = {}
    for i, key in enumerate(zip(prev.tolist(), nxt.tolist())):
        if key not in cache:
            cache[key] = gap_clauses(key[0], key[1], book_s, boot_log)
        out[i] = cache[key]
    return out
