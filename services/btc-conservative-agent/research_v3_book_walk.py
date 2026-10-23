"""Order-book-only exit pricing for the Safe Policy Genome protection replay.

HM re-review 23 Oct 2026 (~21:10 AEDT) defect 2 and RK binding conditions (~21:12 AEDT):

* ``market_microstructure_1s`` / ``book_tape_1s`` NEVER prices a fill, stop, TP or exit.  It is not
  read here at all.  The 1 m candle and segment last-price fallbacks are gone.
* Exits are repriced on real ``bfx_book_10l_1s`` rows (collector receive-time second ``s``; a row is
  complete at ``s + 1``) through the shared pricer ``paper_fill.paper_fill``.
* Stops (hard stop, ATR stop, thesis cut) TRIGGER on the first trade print at or through the level
  (``bfx_trades.jsonl``), with no delay, and FILL on the first complete book row at or after the
  trigger (GS-FADE3-TP30 AMEND1/AMEND2 stop rule; flagged STOP_BASIS_UNVERIFIED /
  LAST_TRADE_DOC_INFERRED).  Targets, break-even locks, profit floors and clocks trigger on the
  executable side of completed book rows (LONG bid, SHORT ask) and fill the same way.
* An event is replayed only when the book (and trade prints) cover its whole window; otherwise it is
  ``R17_UNPROVEN`` and is excluded from every countable result.  Coverage is stamped per analysis.
* A replay cell is ``R17_UNPROVEN`` (STOP_UNOBSERVABLE_TRADE_GAP) when, before its exit, the book's
  executable side touches or crosses one of its stop levels during a trade-print gap longer than
  ``STOP_UNOBSERVABLE_TRADE_GAP_S`` (no trades heartbeat: quiet market and outage are indistinguishable).

The shared trigger helper (``quote_trigger.first_level_trigger`` / ``book_exit_price``) lands with
realism-v2 (#839/#841), which merges before this change; the rules here are the same rules and the
replay should switch to the helper once it is on master.
"""
from __future__ import annotations

import json
import math
import re
from bisect import bisect_right
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

import paper_fill as _pf

BOOK_FILE = "bfx_book_10l_1s.jsonl"
TRADES_FILE = "bfx_trades.jsonl"
LEVELS = 10
BASIS_BOOK_WALK = "BOOK10L_WALK_TRADE_STOP_V1"
R17_UNPROVEN = "R17_UNPROVEN"
STOP_TRIGGER_LABEL = "LAST_TRADE_DOC_INFERRED"
STOP_BASIS_UNVERIFIED = "STOP_BASIS_UNVERIFIED"
MAX_BOOK_GAP_S = _pf.MAX_BOOK_AGE_S  # 2 s: a longer gap between book rows breaks coverage
MAX_TRADE_GAP_S = 300.0  # no trade-print row for 5 min inside a window = trade collector outage
# HM #847 N3: bfx_book_10l / bfx_trades are collected for the Bitfinex perp only (bfx_book_top.SYMBOL).
TAPE_SYMBOL = "tBTCF0:USTF0"
# HM #847 C2: bfx_trades has no heartbeat, so a quiet tape and a trade-WS outage look the same.  A
# book row whose executable exit side sits inside a trade-print gap longer than this cannot prove
# that a last-trade stop at that level did NOT trigger; if it touches a stop, the cell is R17_UNPROVEN.
STOP_UNOBSERVABLE_TRADE_GAP_S = 5.0
STOP_UNOBSERVABLE_REASON = "STOP_UNOBSERVABLE_TRADE_GAP"
KIND_TRADE, KIND_BOOK = 0, 1
EXIT_FILL_DELAY_SOURCE = "FIRST_BOOK_ROW_AT_OR_AFTER_TRIGGER"

_S_RE = re.compile(r'"s"\s*:\s*(-?\d+(?:\.\d+)?)')


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def rotation_paths(data_dir: str | Path, name: str) -> list[Path]:
    """Oldest rotation first (``name.N`` ... ``name.1``), then the live file."""
    base = Path(data_dir) / name
    rotations = sorted(
        (p for p in base.parent.glob(base.name + ".*") if p.name[len(base.name) + 1:].isdigit()),
        key=lambda p: -int(p.name.rsplit(".", 1)[-1]),
    )
    return rotations + ([base] if base.is_file() else [])


def merge_intervals(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for lo, hi in sorted((float(a), float(b)) for a, b in intervals if b >= a):
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [(a, b) for a, b in out]


def _inside(intervals: list[tuple[float, float]], x: float) -> bool:
    if not intervals:
        return True
    i = bisect_right([a for a, _ in intervals], x) - 1
    return i >= 0 and x <= intervals[i][1]


class BookWalkTape:
    """Book rows and trade prints held as compact arrays (read-only research input)."""

    def __init__(self, book_s: np.ndarray, levels: np.ndarray, trade_ts: np.ndarray, trade_px: np.ndarray,
                 trade_recv_s: np.ndarray, receipt: dict[str, Any]) -> None:
        self.book_s = book_s          # int64, sorted, unique (last row per second wins)
        self.levels = levels          # float64 [n, 2, LEVELS, 2]: side (0 bid, 1 ask), level, (px, sz)
        self.trade_ts = trade_ts      # float64 exchange print time, sorted
        self.trade_px = trade_px
        self.trade_recv_s = trade_recv_s  # int64 collector second each print was written (sorted unique)
        self.receipt = receipt

    @classmethod
    def from_rows(cls, book_rows: Iterable[Mapping[str, Any]], trade_rows: Iterable[Mapping[str, Any]] = (),
                  *, receipt: dict[str, Any] | None = None) -> "BookWalkTape":
        by_s: dict[int, np.ndarray] = {}
        stats = {"book_rows_accepted": 0, "book_rows_rejected": 0, "trade_rows": 0, "trade_prints": 0}
        for row in book_rows:
            s = _num(row.get("s")) if isinstance(row, Mapping) else None
            arr = np.full((2, LEVELS, 2), np.nan)
            ok = s is not None
            for side, key in ((0, "b"), (1, "a")):
                for i, lv in enumerate(list((row or {}).get(key) or [])[:LEVELS]):
                    try:
                        px, sz = float(lv[0]), float(lv[1])
                    except (TypeError, ValueError, IndexError):
                        continue
                    if px > 0 and sz > 0 and math.isfinite(px) and math.isfinite(sz):
                        arr[side, i] = (px, sz)
            if ok and not (np.isnan(arr[0, 0, 0]) or np.isnan(arr[1, 0, 0]) or arr[1, 0, 0] < arr[0, 0, 0]):
                by_s[int(s)] = arr
                stats["book_rows_accepted"] += 1
            else:
                stats["book_rows_rejected"] += 1
        prints: list[tuple[float, float, int]] = []
        recv: set[int] = set()
        for row in trade_rows:
            s = _num(row.get("s")) if isinstance(row, Mapping) else None
            if s is None:
                continue
            stats["trade_rows"] += 1
            recv.add(int(s))
            for tr in row.get("tr") or []:
                try:
                    ms, px = float(tr[1]), float(tr[3])
                except (TypeError, ValueError, IndexError):
                    continue
                if px > 0 and math.isfinite(px) and math.isfinite(ms):
                    prints.append((ms / 1000.0, px, int(s)))
                    stats["trade_prints"] += 1
        prints.sort()
        secs = sorted(by_s)
        levels = np.stack([by_s[s] for s in secs]) if secs else np.empty((0, 2, LEVELS, 2))
        details = dict(receipt or {})
        details.update(stats)
        details.update({
            "schema": "replay_book_walk_tape_v1",
            "book_first_s": secs[0] if secs else None, "book_last_s": secs[-1] if secs else None,
            "book_seconds": len(secs),
            "trade_first_recv_s": min(recv) if recv else None, "trade_last_recv_s": max(recv) if recv else None,
            "price_source": "BFX_BOOK_10L", "stop_trigger_source": "BFX_TRADES",
            "never_priced_from": list(_pf.BANNED_SOURCES),
        })
        return cls(np.asarray(secs, dtype=np.int64), levels,
                   np.asarray([p[0] for p in prints], dtype=np.float64),
                   np.asarray([p[1] for p in prints], dtype=np.float64),
                   np.asarray(sorted(recv), dtype=np.int64), details)

    # ---------------------------------------------------------------- coverage
    def coverage(self, t0: float, t1: float) -> dict[str, Any]:
        """Whole-window coverage by real receive-stamped book rows and trade-print rows."""
        out: dict[str, Any] = {"window_start_ts": float(t0), "window_end_ts": float(t1)}
        s = self.book_s
        lo = int(np.searchsorted(s, math.floor(t0) - MAX_BOOK_GAP_S, side="left"))
        hi = int(np.searchsorted(s, math.ceil(t1) + MAX_BOOK_GAP_S, side="right"))
        window = s[lo:hi]
        if not len(window) or window[0] + 1 > t0 + MAX_BOOK_GAP_S or window[-1] + 1 < t1:
            out.update(covered=False, reason="BOOK_ROWS_DO_NOT_COVER_WINDOW")
            return out
        gap = float(np.max(np.diff(window))) if len(window) > 1 else 0.0
        out["book_max_gap_s"] = gap
        if gap > MAX_BOOK_GAP_S:
            out.update(covered=False, reason="BOOK_ROW_GAP_OVER_2S")
            return out
        r = self.trade_recv_s
        tlo = int(np.searchsorted(r, math.floor(t0) - MAX_TRADE_GAP_S, side="left"))
        thi = int(np.searchsorted(r, math.ceil(t1) + MAX_TRADE_GAP_S, side="right"))
        tw = r[tlo:thi]
        if not len(tw) or tw[0] > t0 or tw[-1] < t1:
            out.update(covered=False, reason="TRADE_PRINTS_DO_NOT_COVER_WINDOW")
            return out
        tgap = float(np.max(np.diff(tw))) if len(tw) > 1 else 0.0
        out["trade_max_gap_s"] = tgap
        if tgap > MAX_TRADE_GAP_S:
            out.update(covered=False, reason="TRADE_PRINT_GAP_OVER_300S")
            return out
        out.update(covered=True, reason=None)
        return out

    def window(self, t0: float, t1: float) -> "BookWalkWindow":
        lo = int(np.searchsorted(self.book_s, math.floor(t0) - MAX_BOOK_GAP_S - 1, side="left"))
        hi = int(np.searchsorted(self.book_s, math.ceil(t1) + MAX_BOOK_GAP_S + 1, side="right"))
        tlo = int(np.searchsorted(self.trade_ts, t0, side="left"))
        thi = int(np.searchsorted(self.trade_ts, t1, side="right"))
        return BookWalkWindow(self.book_s[lo:hi].copy(), self.levels[lo:hi].copy(),
                              self.trade_ts[tlo:thi].copy(), self.trade_px[tlo:thi].copy())


class BookWalkWindow:
    """One event's book rows and prints (small, picklable for replay workers)."""

    __slots__ = ("book_s", "levels", "trade_ts", "trade_px")

    def __init__(self, book_s, levels, trade_ts, trade_px) -> None:
        self.book_s, self.levels, self.trade_ts, self.trade_px = book_s, levels, trade_ts, trade_px

    def row(self, index: int) -> dict[str, Any]:
        lv = self.levels[index]
        return {"s": int(self.book_s[index]),
                "b": [[float(p), float(z)] for p, z in lv[0] if p == p],
                "a": [[float(p), float(z)] for p, z in lv[1] if p == p]}

    def first_row_ready_at_or_after(self, t: float) -> int | None:
        """Index of the first complete row (``s + 1 >= t``)."""
        index = int(np.searchsorted(self.book_s + 1, t, side="left"))
        return index if index < len(self.book_s) else None

    def timeline(self, direction: str, t0: float, t1: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(ts, px, kind) in (t0, t1]: trade prints and completed book rows' executable exit side.

        Ties at one instant put the trade first (a print is known at its own time, a row at ``s + 1``).
        """
        side = 0 if direction == "LONG" else 1
        ready = self.book_s.astype(np.float64) + 1.0
        bmask = (ready > t0) & (ready <= t1)
        tmask = (self.trade_ts > t0) & (self.trade_ts <= t1)
        ts = np.concatenate((self.trade_ts[tmask], ready[bmask]))
        px = np.concatenate((self.trade_px[tmask], self.levels[bmask, side, 0, 0]))
        kind = np.concatenate((np.full(int(tmask.sum()), KIND_TRADE, dtype=np.int8),
                               np.full(int(bmask.sum()), KIND_BOOK, dtype=np.int8)))
        order = np.lexsort((kind, ts))
        return ts[order], px[order], kind[order]

    def exit_fill(self, direction: str, qty: float, trigger_ts: float) -> dict[str, Any]:
        """Shared pricer on the first complete book row at or after ``trigger_ts``."""
        index = self.first_row_ready_at_or_after(trigger_ts)
        if index is None or float(self.book_s[index]) + 1.0 - trigger_ts > MAX_BOOK_GAP_S:
            return {"status": "REJECT_NO_BOOK", "evidence_status": "NO_FILL", "price": None}
        row = self.row(index)
        # HM #847 N5: the delay is the actual wait until this row is complete (s + 1), floored at the shared
        # pricer's MIN_DELAY_S, so fill_ts / delay_s stamp the row-ready time instead of a constant 0.3 s.
        ready_ts = float(self.book_s[index]) + 1.0
        delay_s = max(_pf.MIN_DELAY_S, ready_ts - float(trigger_ts))
        out = _pf.paper_fill(direction, qty, trigger_ts, delay_s, lambda _t: row, leg="exit",
                             delay_source=EXIT_FILL_DELAY_SOURCE)
        out["trigger_ts"] = float(trigger_ts)
        out["book_row_ready_ts"] = ready_ts
        return out


def load_book_walk(data_dir: str | Path, intervals: Iterable[tuple[float, float]]) -> BookWalkTape:
    """Read book rows and trade prints restricted to the merged replay windows."""
    # Trade-print coverage looks up to MAX_TRADE_GAP_S either side of a window, so read that margin too.
    windows = merge_intervals((math.floor(a) - MAX_TRADE_GAP_S - 2, math.ceil(b) + MAX_TRADE_GAP_S + 2)
                              for a, b in intervals)
    stats = {"book_lines_read": 0, "trade_lines_read": 0, "decode_errors": 0}

    def rows(name: str, counter: str):
        for path in rotation_paths(data_dir, name):
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    stats[counter] += 1
                    match = _S_RE.search(line)
                    if match is None or not _inside(windows, float(match.group(1))):
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        stats["decode_errors"] += 1
                        continue
                    if isinstance(row, dict):
                        yield row

    tape = BookWalkTape.from_rows(rows(BOOK_FILE, "book_lines_read"), rows(TRADES_FILE, "trade_lines_read"))
    tape.receipt.update(stats)
    tape.receipt.update({
        "symbol": TAPE_SYMBOL,
        "windows": len(windows),
        "book_source_files": [p.name for p in rotation_paths(data_dir, BOOK_FILE)],
        "trade_source_files": [p.name for p in rotation_paths(data_dir, TRADES_FILE)],
    })
    return tape


class _LazyExitMargins:
    """Margin return of the book fill for an exit triggered at event ``i`` (computed on demand)."""

    __slots__ = ("path",)

    def __init__(self, path: "PreparedBookWalkArrays") -> None:
        self.path = path

    def __getitem__(self, index: Any) -> float:
        return self.path.exit_margin(int(index))


def _in_trade_gap(ts: np.ndarray, trade_ts: np.ndarray,
                  max_gap_s: float = STOP_UNOBSERVABLE_TRADE_GAP_S) -> np.ndarray:
    """True where ``ts`` falls in a gap between consecutive trade prints longer than ``max_gap_s``.

    Before the first or after the last print in the window the gap is unbounded (True).
    """
    ts = np.asarray(ts, dtype=np.float64)
    prints = np.asarray(trade_ts, dtype=np.float64)
    if not len(ts):
        return np.zeros(0, dtype=bool)
    if not len(prints):
        return np.ones(len(ts), dtype=bool)
    after = np.searchsorted(prints, ts, side="right")  # index of the next print strictly after ts
    prev = np.where(after > 0, prints[np.maximum(after - 1, 0)], -np.inf)
    nxt = np.where(after < len(prints), prints[np.minimum(after, len(prints) - 1)], np.inf)
    return (nxt - prev) > max_gap_s


class PreparedBookWalkArrays:
    """Drop-in for ``PreparedReplayArrays`` on a trade + book timeline.

    * ``neg_mae`` (hard stop / ATR stop / thesis cut) runs over TRADE prints only.
    * ``mfe`` / ``cur`` (targets, partials, BE locks, floors) run over completed BOOK rows only.
    * ``exit_cur[i]`` is the margin return of ``paper_fill`` on the first complete book row at or
      after event ``i``; a missing or incomplete book fill raises ``ExitFillUnavailable``.
    """

    __slots__ = ("ts", "price", "kind", "cur", "mfe", "mae", "neg_mae", "underwater", "favorable", "age",
                 "age_sorted", "n", "floors", "seg_start", "seg_mfe", "seg_min_cur", "exit_cur",
                 "gap_neg_mae", "_window", "_direction", "_entry", "_leverage", "_qty", "_fills")

    def __init__(self, window: BookWalkWindow, *, direction: str, entry_price: float, leverage: float,
                 fill_ts: float, horizon_sec: float, qty: float) -> None:
        ts, px, kind = window.timeline(direction, fill_ts, fill_ts + horizon_sec)
        self.ts, self.price, self.kind = ts, px, kind
        self._window, self._direction, self._entry = window, direction, float(entry_price)
        self._leverage, self._qty, self._fills = float(leverage), float(qty), {}
        margin = self._margin(px)
        trade, book = kind == KIND_TRADE, kind == KIND_BOOK
        stop_cur = np.where(trade, margin, np.inf)
        self.neg_mae = np.maximum.accumulate(-stop_cur) if len(ts) else stop_cur
        self.mae = -self.neg_mae
        self.mfe = np.maximum.accumulate(np.where(book, margin, -np.inf)) if len(ts) else margin
        self.cur = np.where(book, margin, np.inf)
        self.underwater = np.cumsum(book & (margin < 0))
        self.favorable = np.cumsum(book & (margin > 0))
        self.age = ts - fill_ts
        self.age_sorted = True
        self.n = len(ts)
        self.floors = {}
        if self.n:
            self.seg_start = np.flatnonzero(np.concatenate(([True], self.mfe[1:] != self.mfe[:-1])))
            self.seg_mfe = self.mfe[self.seg_start]
            self.seg_min_cur = np.minimum.reduceat(self.cur, self.seg_start)
        else:
            self.seg_start = self.seg_mfe = self.seg_min_cur = np.empty(0)
        self.exit_cur = _LazyExitMargins(self)
        # Running max of the book-side loss over book rows that sit inside a trade-print gap longer than
        # STOP_UNOBSERVABLE_TRADE_GAP_S (HM #847 C2).  replay_cell compares it with every stop level.
        in_gap = book & _in_trade_gap(ts, window.trade_ts)
        gap_loss = np.where(in_gap, -margin, -np.inf)
        self.gap_neg_mae = np.maximum.accumulate(gap_loss) if len(ts) else gap_loss

    def _margin(self, px: Any) -> Any:
        raw = (np.asarray(px, dtype=np.float64) - self._entry) / self._entry * 100.0 * self._leverage
        return raw if self._direction == "LONG" else -raw

    def exit_fill(self, index: int) -> dict[str, Any]:
        fill = self._fills.get(index)
        if fill is None:
            fill = self._fills[index] = self._window.exit_fill(self._direction, self._qty, float(self.ts[index]))
        return fill

    def exit_margin(self, index: int) -> float:
        from research_v3_policy_replay import ExitFillUnavailable

        fill = self.exit_fill(index)
        if fill.get("evidence_status") != _pf.EVIDENCE_COMPLETE or fill.get("price") is None:
            raise ExitFillUnavailable(f"EXIT_BOOK_FILL_{fill.get('status') or 'MISSING'}")
        return float(self._margin(float(fill["price"])))


def coverage_summary(events_by_basis: Mapping[str, int], episodes_by_basis: Mapping[str, int],
                     unproven_reasons: Mapping[str, int], tape_receipt: Mapping[str, Any] | None,
                     *, horizon_sec: float, unproven_cells: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Per-analysis coverage stamp; R17_UNPROVEN events are excluded from every countable result.

    ``unproven_cells`` counts replay cells (policy x episode) made R17_UNPROVEN inside a covered event,
    e.g. STOP_UNOBSERVABLE_TRADE_GAP (HM #847 C2); they are never countable and keep the stamp off GREEN.
    """
    total = sum(int(v) for v in events_by_basis.values())
    unproven = int(events_by_basis.get(R17_UNPROVEN, 0))
    countable = total - unproven
    cells = {str(k): int(v) for k, v in (unproven_cells or {}).items() if int(v)}
    if not total:
        level, reason = "AMBER", "NO_REPLAYED_EVENTS"
    elif unproven * 2 > total:
        level, reason = "RED", "R17_UNPROVEN_MAJORITY"
    elif unproven:
        level, reason = "AMBER", "R17_UNPROVEN_EVENTS_EXCLUDED"
    elif cells:
        level, reason = "AMBER", "R17_UNPROVEN_CELLS_EXCLUDED"
    else:
        level, reason = "GREEN", None
    receipt = dict(tape_receipt or {})
    return {
        "schema": "replay_book_coverage_v1",
        "side_convention": ("Stops trigger on the first trade print at or through the level; targets, locks and "
                            "clocks on the executable book side (LONG bid, SHORT ask); every exit fills via "
                            "paper_fill on the first complete bfx_book_10l row at or after the trigger."),
        "stop_trigger_label": STOP_TRIGGER_LABEL,
        "stop_basis_flag": STOP_BASIS_UNVERIFIED,
        "price_source": "BFX_BOOK_10L",
        "symbol": receipt.get("symbol") or TAPE_SYMBOL,
        "never_priced_from": list(_pf.BANNED_SOURCES),
        "path_horizon_sec": horizon_sec,
        "events_by_basis": dict(sorted(events_by_basis.items())),
        "episodes_by_basis": dict(sorted(episodes_by_basis.items())),
        "events_total": total,
        "countable_events": countable,
        "r17_unproven_events": unproven,
        "r17_unproven_reasons": dict(sorted(unproven_reasons.items())),
        "r17_unproven_cells": sum(cells.values()),
        "r17_unproven_cell_reasons": dict(sorted(cells.items())),
        "stop_unobservable_trade_gap_s": STOP_UNOBSERVABLE_TRADE_GAP_S,
        # Kept for the window summary / dashboards that read the old mark-source keys.
        "fallback_events": unproven,
        "side_correct_ratio": round(countable / total, 6) if total else None,
        "coverage_book_first_s": receipt.get("book_first_s"),
        "coverage_book_last_s": receipt.get("book_last_s"),
        "alert_level": level,
        "reason": reason,
        "tape": receipt,
    }
