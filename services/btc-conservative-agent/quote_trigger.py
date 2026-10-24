"""REALISM_V2 F2: the ONE shared trigger / quote helper (no per-tile code).

Every paper exit trigger, every entry quote-staleness check and every dual stamp goes through here.

Trigger rule (tracker "REALISM FIXES F1-F7" F2 + Rules Keeper, RK 17:24 BINDING):
- The exchange-resident stop triggers on the FIRST trade print at or through its level only
  (``trigger_basis = LAST_TRADE``, LAST_TRADE_DOC_INFERRED). A book-top touch never triggers it; the first touch is
  recorded as a comparison column (stop_book_touch_ts / _px / _s).
- Every bot-side exit (TP / TP1 / BE locks / bot hard stop) triggers on the executable book side of a completed row
  only (``trigger_basis = BOOK_TOP``: bid for a LONG exit, ask for a SHORT exit; ``exit_events(...,
  include_trades=False)``) - the same book_level_trigger the live close path calls.
- Stale threshold = paper_fill.MAX_BOOK_AGE_S (2 s). An entry whose book quote is stale is skipped with
  ``QUOTE_STALE``. With a stale book (> 2 s) the exit is marked ``STALE_BOOK`` with its price age; a price-level exit
  fires on the FIRST fresh book row (or, for the exchange-resident stop, on the real trades stream); only time exits
  fire without a book, stamped ``CLOCK`` and priced on the first fresh book row.
- RK 17:18 / 17:22 (PRIME RULE): NO TICKER ANYWHERE. The only trigger source is ``book`` (book + trades). A
  ``TRIGGER_PRICE_SOURCE=ticker`` setting is refused (counted in ``COUNTERS["ticker_refused"]``, the tile stays on
  book) and ``selected_quote`` raises ``TickerSourceRefused`` for any non-book source. Old rows without the field
  are pooled as legacy ``ticker`` rows (``pool_key``) so they can be listed and excluded; nothing is deleted.
- Book rows are paged over the WHOLE window (no 30 s / 120 s look-back skip). A row that has not landed yet within
  the late grace stops the cursor (it is read on the next pass); an older missing second is stamped as a gap.
- Dual stamp: book-top view + last-trade view at the trigger instant (measurement only).

Book row timing: a 1 s book row for second ``s`` is complete only at ``s + 1`` (paper_fill.BookTail); a trade print
is known at its own ms timestamp. Nothing in this module prices a fill; fills stay in paper_fill / tile_fill.
"""
from __future__ import annotations

import json
import math
import re
import os
import threading
from typing import Any, Callable, Iterable, Mapping, Optional

import paper_fill as _pf

STALE_S = _pf.MAX_BOOK_AGE_S  # 2.0 s
SOURCE_BOOK = "book"           # book + trades; the ONLY trigger source (RK 17:18: no ticker anywhere)
LEGACY_TICKER = "ticker"       # label for OLD rows only (pool_key); never a selectable source
SOURCES = (SOURCE_BOOK,)
DEFAULT_SOURCE = SOURCE_BOOK
ENV_KEY = "TRIGGER_PRICE_SOURCE"
TRIGGER_VERSION = "RV2_TRIGGER_V3"  # stamped on every trigger (trigger_version)

BASIS_LAST_TRADE = "LAST_TRADE"
BASIS_BOOK_TOP = "BOOK_TOP"
BASIS_CLOCK = "CLOCK"           # time exit: fired by the clock, priced on the first fresh book row
STALE_BOOK = "STALE_BOOK"       # no fresh book row (> 2 s): exit marked, price age recorded
STOP_BASIS_UNVERIFIED = "STOP_BASIS_UNVERIFIED"  # Bitfinex docs do not state the plain-stop trigger basis
# Per-tile resident-stop trigger basis (registry option ``resident_stop_trigger_basis``):
# RK 17:24 BINDING: the resident stop triggers on the FIRST trade print at or through the level ONLY. The BOOK_TOP
# half of KIND_STOP is removed; a book-top touch is kept as a comparison column (stop_book_touch_*), never a trigger.
STOP_BASIS_TRADES = "trades"
STOP_BASES = (STOP_BASIS_TRADES,)
STOP_TRIGGER_LABEL = "LAST_TRADE_DOC_INFERRED"  # Bitfinex D1/D4 imply last trade; not stated for derivatives
QUOTE_STALE = "QUOTE_STALE"
COUNTERS = {"ticker_refused": 0}
_CLOCK_REASON = re.compile(r"^(PATH_END_\d+M|TIME_STOP|TIME_BACKSTOP|TIME_EXIT|GS_TIME_BACKSTOP|MAX_HOLD.*)$")


class TickerSourceRefused(ValueError):
    """RK 17:18: a ticker price may never trigger or quote anything."""
NO_BOOK = "NO_BOOK"
MAX_EVENTS = 64
TRADES_FILE = "bfx_trades.jsonl"

KIND_STOP = "stop"
KIND_TARGET = "target"

DUAL_STAMP_KEYS = ("dual_book_px", "dual_book_s", "dual_book_age_s",
                   "dual_last_trade_px", "dual_last_trade_ts", "dual_last_trade_age_s")


def _f(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _pos(v: Any) -> Optional[float]:
    x = _f(v)
    return x if x is not None and x > 0 else None


def _side(direction: Any) -> Optional[str]:
    s = str(direction or "").upper()
    if s in ("LONG", "BUY"):
        return "LONG"
    if s in ("SHORT", "SELL"):
        return "SHORT"
    return None


# ------------------------------------------------------------------ per-tile source flag
def refuse_ticker(source: Any) -> str:
    """Return the (book) source or raise TickerSourceRefused for anything else (ticker included)."""
    v = str(source or "").strip().lower()
    if v != SOURCE_BOOK:
        raise TickerSourceRefused(f"trigger source {source!r} refused: book + trades only (RK 17:18)")
    return v


def trigger_price_source(lane: Any = None, env: Optional[Mapping[str, str]] = None) -> str:
    """Always ``book`` (book + trades). A ``TRIGGER_PRICE_SOURCE`` env naming ``ticker`` (globally, ``*=`` or for
    this lane) is refused: counted in COUNTERS["ticker_refused"] and ignored (the tile stays on book)."""
    raw = str((env if env is not None else os.environ).get(ENV_KEY) or "").strip()
    if raw:
        lane_u = str(lane or "").strip().upper()
        asked = []
        for part in raw.split(","):
            k, _, v = part.partition("=") if "=" in part else ("*", "=", part)
            if k.strip() == "*" or (lane_u and k.strip().upper() == lane_u):
                asked.append(v.strip().lower())
        if any(v and v != SOURCE_BOOK for v in asked):
            COUNTERS["ticker_refused"] += 1
    return SOURCE_BOOK


def pool_key(row: Mapping[str, Any]) -> str:
    """Analyzer pool key: rows are never pooled across trigger sources. Rows without the field (pre-flag) or with a
    ticker source are legacy ``ticker`` rows: listed separately and excluded, never deleted."""
    v = str((row or {}).get("trigger_price_source") or LEGACY_TICKER).lower()
    return SOURCE_BOOK if v == SOURCE_BOOK else LEGACY_TICKER


def is_clock_exit_reason(reason: Any) -> bool:
    """Time exits (PATH_END_<n>M, TIME_STOP, TIME_BACKSTOP, ...): the only exits that may fire without a fresh book."""
    return bool(_CLOCK_REASON.match(str(reason or "").upper()))


# ------------------------------------------------------------------ book helpers
def _top(book: Any, key: str) -> Optional[float]:
    if not isinstance(book, Mapping):
        return None
    lv = book.get(key)
    if not isinstance(lv, (list, tuple)) or not lv:
        return None
    t = lv[0]
    return _pos(t[0] if isinstance(t, (list, tuple)) and t else t)


def exec_side(direction: Any, leg: str) -> Optional[str]:
    """Book side an order would execute against: LONG exit / SHORT entry -> bid ("b"); else ask ("a")."""
    d = _side(direction)
    if d is None:
        return None
    sells = (d == "LONG") == (str(leg).lower() == "exit")
    return "b" if sells else "a"


def exec_px(direction: Any, leg: str, book: Any) -> Optional[float]:
    k = exec_side(direction, leg)
    return _top(book, k) if k else None


def book_ready_ts(book: Any) -> Optional[float]:
    s = _f(book.get("s")) if isinstance(book, Mapping) else None
    return s + 1.0 if s is not None else None


# ------------------------------------------------------------------ trades tail (bfx_trades.jsonl)
class TradesTail:
    """Trade prints from the collector's bfx_trades.jsonl rows {"s", "tr": [[id, ms, signed_amount, price], ...]}.

    Tail read only (no archive, no new stored data). ``between(t0, t1)`` returns prints with t0 < ts <= t1, oldest
    first, as dicts {"ts", "px", "amt", "id", "recv_s"} (id = Bitfinex trade id, recv_s = the collector second the
    print was written in, i.e. when this process could first know it). Read-only.
    """

    def __init__(self, path: str, keep_s: int = 600, tail_bytes: int = 1024 * 1024) -> None:
        self.path, self.keep_s, self.tail_bytes = path, keep_s, tail_bytes
        self._lock = threading.Lock()
        self._by_s: dict = {}
        self._max_s: Optional[int] = None

    def _refresh(self) -> None:
        try:
            size = os.path.getsize(self.path)
            with open(self.path, "rb") as fh:
                fh.seek(max(0, size - self.tail_bytes))
                chunk = fh.read()
        except OSError:
            return
        lines = chunk.splitlines()
        for line in (lines[1:] if size > self.tail_bytes else lines):
            try:
                r = json.loads(line)
                sec = int(r["s"])
            except (ValueError, KeyError, TypeError):
                continue
            prints = []
            for tr in r.get("tr") or []:
                try:
                    ms, amt, px = float(tr[1]), float(tr[2]), float(tr[3])
                except (TypeError, ValueError, IndexError):
                    continue
                if px > 0 and math.isfinite(px) and math.isfinite(ms):
                    prints.append({"ts": ms / 1000.0, "px": px, "amt": amt, "id": tr[0], "recv_s": sec})
            self._by_s[sec] = prints
        if self._by_s:
            hi = max(self._by_s)
            for sec in [s for s in self._by_s if s < hi - self.keep_s]:
                del self._by_s[sec]
            self._max_s = hi

    def between(self, t0: float, t1: float) -> list:
        with self._lock:
            if self._max_s is None or self._max_s + 1 < t1 - 0.5:
                self._refresh()
            out = [p for s in range(int(math.floor(t0)) - 1, int(math.floor(t1)) + 1)
                   for p in self._by_s.get(s, ()) if t0 < p["ts"] <= t1]
        out.sort(key=lambda p: p["ts"])
        return out

    def received_after(self, t0: float, t1: float, recv_after: float) -> list:
        """HM C3: prints with t0 < ts <= t1 written in a collector second > recv_after (i.e. not yet seen by a scan
        that read the tail up to recv_after) - late prints are returned, never skipped. Oldest first."""
        with self._lock:
            if self._max_s is None or self._max_s + 1 < t1 - 0.5:
                self._refresh()
            out = [p for s, ps in self._by_s.items() if s > recv_after for p in ps if t0 < p["ts"] <= t1]
        out.sort(key=lambda p: p["ts"])
        return out

    def coverage(self) -> Optional[dict]:
        """HM C3: the collector seconds the in-memory tail holds (tail = last 1 MB, keep 600 s): {"lo_s", "hi_s",
        "n_s"} or None when the tail is empty / the file is missing."""
        with self._lock:
            if self._max_s is None:
                self._refresh()  # otherwise between()/received_after() keep the tail current (no extra 1 MB read)
            if not self._by_s:
                return None
            return {"lo_s": min(self._by_s), "hi_s": max(self._by_s), "n_s": len(self._by_s)}


_TRADES_TAIL: Optional[TradesTail] = None


def trades_between(t0: float, t1: float) -> list:
    """Default tail over the collector's bfx_trades.jsonl in the working directory (same as paper_fill.book10l_at)."""
    global _TRADES_TAIL
    if _TRADES_TAIL is None:
        _TRADES_TAIL = TradesTail(os.path.join(os.getcwd(), TRADES_FILE))
    try:
        return _TRADES_TAIL.between(t0, t1)
    except Exception:  # noqa: BLE001 - a missing tape means no LAST_TRADE events, never a crash
        return []


def _trades_tail() -> TradesTail:
    global _TRADES_TAIL
    if _TRADES_TAIL is None:
        _TRADES_TAIL = TradesTail(os.path.join(os.getcwd(), TRADES_FILE))
    return _TRADES_TAIL


def trades_received_after(t0: float, t1: float, recv_after: float) -> list:
    try:
        return _trades_tail().received_after(t0, t1, recv_after)
    except Exception:  # noqa: BLE001
        return []


def trades_coverage() -> Optional[dict]:
    try:
        return _trades_tail().coverage()
    except Exception:  # noqa: BLE001
        return None


STOP_COVERAGE_GAP = "STOP_COVERAGE_GAP"
STOP_COVERAGE_UNVERIFIED = "STOP_COVERAGE_UNVERIFIED"  # the resident-stop scan never covered the hold
STOP_COVERAGE_OK = "STOP_COVERAGE_OK"  # every second of the hold was covered by the trades tail
STOP_COVERAGE_VALUES = (STOP_COVERAGE_OK, STOP_COVERAGE_GAP, STOP_COVERAGE_UNVERIFIED)
LATE_PRINT_S = STALE_S  # a print known (end of its collector second) more than 2 s after its own time is LATE


def stop_scan_coverage(t0: float, t1: float, cov: Optional[dict]) -> dict:
    """HM C3: is the resident-stop scan window (t0, t1] covered by the trades tail? Gap seconds = the part of the
    window older than the oldest second the tail holds (or the whole window when the tail is empty). A gap means a
    stop crossing may have been missed -> STOP_COVERAGE_GAP / UNVERIFIED."""
    t0, t1 = float(t0), float(t1)
    if t1 <= t0:
        return {"gap_s": 0.0, "lo_s": (cov or {}).get("lo_s"), "hi_s": (cov or {}).get("hi_s")}
    if not cov:
        return {"gap_s": round(t1 - t0, 3), "lo_s": None, "hi_s": None}
    gap = max(0.0, min(float(cov["lo_s"]), t1) - t0)
    return {"gap_s": round(gap, 3), "lo_s": cov["lo_s"], "hi_s": cov["hi_s"]}


DERIV_FILE = "bfx_deriv_status_1s.jsonl"  # the collector's Bitfinex deriv status rows {"s", "mark", ...}


class MarkTail:
    """The REAL Bitfinex mark (BFX Composite Index, deriv status MARK_PRICE) from bfx_deriv_status_1s.jsonl, tail
    read only. ``between(t0, t1)`` -> [{"s", "mark"}] with t0 < s + 1 <= t1 (a row is known at the end of its
    second). Used ONLY for the liquidation check (HM M10) and the real-mark stamp (M6); never for a trigger."""

    def __init__(self, path: str, keep_s: int = 3 * 3600, tail_bytes: int = 4 * 1024 * 1024) -> None:
        self.path, self.keep_s, self.tail_bytes = path, keep_s, tail_bytes
        self._lock = threading.Lock()
        self._by_s: dict = {}
        self._max_s: Optional[int] = None

    def _refresh(self) -> None:
        try:
            size = os.path.getsize(self.path)
            with open(self.path, "rb") as fh:
                fh.seek(max(0, size - self.tail_bytes))
                chunk = fh.read()
        except OSError:
            return
        lines = chunk.splitlines()
        for line in (lines[1:] if size > self.tail_bytes else lines):
            try:
                r = json.loads(line)
                sec, mark = int(r["s"]), float(r.get("mark"))
            except (ValueError, KeyError, TypeError):
                continue
            if mark > 0 and math.isfinite(mark):
                self._by_s[sec] = mark
        if self._by_s:
            hi = max(self._by_s)
            for sec in [x for x in self._by_s if x < hi - self.keep_s]:
                del self._by_s[sec]
            self._max_s = hi

    def between(self, t0: float, t1: float) -> list:
        with self._lock:
            if self._max_s is None or self._max_s + 1 < t1 - 0.5:
                self._refresh()
            return [{"s": x, "mark": self._by_s[x]} for x in sorted(self._by_s) if t0 < x + 1 <= t1]


_MARK_TAIL: Optional[MarkTail] = None


def marks_between(t0: float, t1: float) -> Optional[list]:
    """Real BFXCI marks in (t0, t1]; None when the deriv-status stream is unavailable (=> liquidation UNVERIFIED)."""
    global _MARK_TAIL
    if _MARK_TAIL is None:
        _MARK_TAIL = MarkTail(os.path.join(os.getcwd(), DERIV_FILE))
    try:
        if not os.path.exists(_MARK_TAIL.path):
            return None
        return _MARK_TAIL.between(t0, t1)
    except Exception:  # noqa: BLE001
        return None


MAX_WINDOW_S = 6 * 3600  # hard bound on one paging pass (a position older than this pages from its cursor)


def book_rows_window(t0: float, t1: float, book_at: Callable[[float], Any], *, late_grace_s: float = STALE_S) -> dict:
    """Page EVERY 1 s book row that became complete in (t0, t1] (ready = s + 1), oldest first. No look-back skip.

    A second whose row is missing and whose ready time is within ``late_grace_s`` of ``t1`` may still land: paging
    stops there and ``checked_to`` (the resume cursor) stays before it, so a late row is read on the next pass. An
    older missing second is a real gap: it is listed in ``gap_seconds`` and paging continues past it.
    Returns {"rows", "checked_to", "gap_seconds", "window": [t0, t1]}.
    """
    rows, gaps = [], []
    t0, t1 = float(t0), float(t1)
    lo = int(math.floor(t0))
    if t1 - t0 > MAX_WINDOW_S:
        gaps.append(("WINDOW_TRUNCATED", lo, int(math.floor(t1 - MAX_WINDOW_S))))
        lo = int(math.floor(t1 - MAX_WINDOW_S))
    checked_to = t0
    for sec in range(lo, int(math.floor(t1)) + 1):
        ready = sec + 1.0
        if ready <= t0 or ready > t1:
            continue
        try:
            b = book_at(ready)
        except Exception:  # noqa: BLE001
            b = None
        if isinstance(b, Mapping) and b.get("s") is not None and int(b["s"]) == sec:
            rows.append(b)
            checked_to = ready
            continue
        if ready > t1 - float(late_grace_s):
            break  # may still land: resume here next pass
        gaps.append(sec)
        checked_to = ready
    return {"rows": rows, "checked_to": checked_to, "gap_seconds": gaps, "window": [t0, t1]}


def book_rows_between(t0: float, t1: float, book_at: Callable[[float], Any]) -> list:
    """1 s book rows that became complete in (t0, t1] (s + 1 in that window), oldest first, whole window paged."""
    return book_rows_window(t0, t1, book_at, late_grace_s=0.0)["rows"]


# ------------------------------------------------------------------ level triggers
def level_reached(direction: Any, kind: str, level: Any, px: Any) -> bool:
    """Exit-side test: LONG stop px <= level, LONG target px >= level; SHORT mirrored. At-or-through counts."""
    d, lv, p = _side(direction), _pos(level), _pos(px)
    if d is None or lv is None or p is None:
        return False
    adverse = kind == KIND_STOP
    if d == "LONG":
        return p <= lv if adverse else p >= lv
    return p >= lv if adverse else p <= lv


def exit_events(direction: Any, t0: float, t1: float, *, book_rows: Iterable[Mapping] = (),
                trades: Iterable[Mapping] = (), include_trades: bool = True, include_book: bool = True,
                max_events: int = MAX_EVENTS) -> list:
    """Chronological exit trigger events in (t0, t1]: executable book top per completed row (+ trade prints).

    Each event is {"ts", "px", "basis", "book_s"|"trade_ts"/"trade_id"/"trade_recv_s"}. Ties at the same instant put
    the trade first (a print is known at its own ms time; a row only at s + 1). Consecutive equal prices of the same
    basis are deduped (the earlier one is kept, so the first crossing never changes). No compression (RK 17:24, M8).
    """
    ev = []
    if include_book:
        for b in book_rows or ():
            px, ready = exec_px(direction, "exit", b), book_ready_ts(b)
            if px is not None and ready is not None and t0 < ready <= t1:
                ev.append({"ts": ready, "px": px, "basis": BASIS_BOOK_TOP, "book_s": int(b["s"])})
    if include_trades:
        for tr in trades or ():
            ts, px = _f(tr.get("ts")), _pos(tr.get("px"))
            if ts is not None and px is not None and t0 < ts <= t1:
                ev.append({"ts": ts, "px": px, "basis": BASIS_LAST_TRADE, "trade_ts": ts,
                           "trade_id": tr.get("id"), "trade_recv_s": tr.get("recv_s")})
    ev.sort(key=lambda e: (e["ts"], 0 if e["basis"] == BASIS_LAST_TRADE else 1))
    dedup = []
    for e in ev:
        if dedup and dedup[-1]["px"] == e["px"] and dedup[-1]["basis"] == e["basis"]:
            continue
        dedup.append(e)
    # RK 17:24 / HM M8: every event is evaluated in order - no merge above 64 (``max_events`` is accepted for
    # call compatibility and ignored). Dropping a consecutive equal price of the same basis cannot change the first
    # trigger: the earlier event at that price is kept and fires first.
    return dedup


def trigger_stamp(event: Mapping[str, Any], t0: float, t1: float, *, kind: Optional[str] = None,
                  level: Any = None, stop_basis: Optional[str] = None) -> dict:
    """The full trigger stamp (Strategist §12 item 5): basis, time, price, trade id + receive second (trade events)
    or book second (book events), the evaluation window and the helper version."""
    out = {"trigger_ts": event["ts"], "trigger_px": event["px"], "trigger_basis": event["basis"],
           "trigger_window_t0": float(t0), "trigger_window_t1": float(t1), "trigger_version": TRIGGER_VERSION}
    if kind is not None:
        out.update(trigger_level=_pos(level), trigger_kind=kind)
    if "book_s" in event:
        out["trigger_event_book_s"] = event["book_s"]
    else:
        out.update(trigger_event_trade_ts=event.get("trade_ts"), trigger_trade_id=event.get("trade_id"),
                   trigger_trade_recv_s=event.get("trade_recv_s"))
    if stop_basis is not None:
        out["stop_trigger_basis"] = stop_basis
        out["stop_basis_unverified"] = True  # Bitfinex docs: plain STOP basis not documented (tracker 17:22)
    return out


def first_level_trigger(direction: Any, kind: str, level: Any, t0: float, t1: float, *,
                        book_rows: Iterable[Mapping] = (), trades: Iterable[Mapping] = (),
                        stop_basis: str = STOP_BASIS_TRADES) -> Optional[dict]:
    """First event at/through ``level`` in (t0, t1]; None if none.

    Stops (RK 17:24 BINDING): the FIRST trade print at or through the level, stamped LAST_TRADE_DOC_INFERRED; a book
    top touch never triggers - the first book touch in the window is returned as a comparison column only
    (stop_book_touch_ts / _px / _s). Targets (TP / BE locks / bot hard stop): the executable book side of each
    completed row - book_level_trigger, the one function the live close path also calls.
    """
    if kind == KIND_STOP:
        hit = None
        for e in exit_events(direction, t0, t1, trades=trades, include_trades=True, include_book=False):
            if level_reached(direction, kind, level, e["px"]):
                hit = trigger_stamp(e, t0, t1, kind=kind, level=level, stop_basis=STOP_BASIS_TRADES)
                break
        touch = None
        for e in exit_events(direction, t0, t1, book_rows=book_rows, include_trades=False, include_book=True):
            if level_reached(direction, kind, level, e["px"]):
                touch = e
                break
        if hit is not None:
            hit["stop_trigger_label"] = STOP_TRIGGER_LABEL
            hit.update(stop_book_touch_ts=(touch or {}).get("ts"), stop_book_touch_px=(touch or {}).get("px"),
                       stop_book_touch_s=(touch or {}).get("book_s"))
        return hit
    for e in exit_events(direction, t0, t1, book_rows=book_rows, include_trades=False, include_book=True):
        if level_reached(direction, kind, level, e["px"]):
            return trigger_stamp(e, t0, t1, kind=kind, level=level)
    return None


def book_exit_price(direction: Any, now: float, book_at: Callable[[float], Optional[Mapping]]) -> dict:
    """THE exit-trigger price for TP / TP1 / BE / bot hard stop, paper AND live (RK 17:24 BINDING; replaces the live
    WS-ticker BBO in get_mark_price): the executable book side (LONG best bid, SHORT best ask) of the latest complete
    1 s book row at ``now``. Stale (> 2 s) or missing -> px None + STALE_BOOK (no price-level trigger, no fallback)."""
    try:
        book = book_at(float(now))
    except Exception:  # noqa: BLE001
        book = None
    s = _f(book.get("s")) if isinstance(book, Mapping) else None
    age = _age(float(now), s + 1.0 if s is not None else None)
    px = exec_px(direction, "exit", book) if isinstance(book, Mapping) else None
    stale = px is None or is_stale(age)
    return {"px": None if stale else px, "book_s": s, "price_age_s": age, "stale": stale,
            "flag": STALE_BOOK if stale else None, "basis": BASIS_BOOK_TOP, "trigger_version": TRIGGER_VERSION,
            "last_book_px": px}


def live_tp_be_trigger(direction: Any, level: Any, now: float, book: Any) -> Optional[dict]:
    """The TP / BE-lock level test on the latest complete book row known at ``now`` (same rule as paper).

    TEST-ONLY reference (HM R2 N-b): no production caller. The live TP/BE trigger in production is bot.get_mark_price
    (book_exit_mark, book-only, 0.0 on a stale book); test_quote_trigger pins that this helper and the paper driver
    apply the same first_level_trigger rule. Do not add a second trigger path through it (R4)."""
    ready = book_ready_ts(book)
    if ready is None or ready > float(now) or is_stale(_age(float(now), _f(book.get("s")))):
        return None
    return first_level_trigger(direction, KIND_TARGET, level, ready - 0.5, ready, book_rows=[book])


# ------------------------------------------------------------------ quotes + staleness
def _age(now: Optional[float], then: Optional[float]) -> Optional[float]:
    if now is None or then is None or then <= 0:
        return None
    a = now - then
    return round(a, 3) if a >= 0 else None


def is_stale(age: Optional[float]) -> bool:
    return age is None or age > STALE_S


def dual_stamp(direction: Any, leg: str, now: Any, *, book: Any = None, last_trade: Optional[Mapping] = None) -> dict:
    """Book-top view + last-trade view at one instant. Measurement fields only (no ticker: RK 17:18)."""
    t = _f(now)
    lt = last_trade if isinstance(last_trade, Mapping) else {}
    lt_ts = _f(lt.get("ts"))
    return {
        "dual_book_px": exec_px(direction, leg, book),
        "dual_book_s": _f(book.get("s")) if isinstance(book, Mapping) else None,
        "dual_book_age_s": _age(t, _f(book.get("s")) if isinstance(book, Mapping) else None),
        "dual_last_trade_px": _pos(lt.get("px")), "dual_last_trade_ts": lt_ts,
        "dual_last_trade_age_s": _age(t, lt_ts),
    }


def selected_quote(direction: Any, leg: str, now: Any, source: str = SOURCE_BOOK, *, book: Any = None) -> dict:
    """The book quote at ``now`` + its age + the 2 s stale verdict. Any non-book source raises TickerSourceRefused."""
    source = refuse_ticker(source)
    t = _f(now)
    px = exec_px(direction, leg, book)
    age = _age(t, _f(book.get("s")) if isinstance(book, Mapping) else None)
    stale = px is None or is_stale(age)
    return {"trigger_price_source": source, "quote_px": px, "quote_age_s": age, "quote_stale": stale,
            "quote_reason": (QUOTE_STALE if px is not None else NO_BOOK) if stale else None}


def entry_quote_gate(direction: Any, now: Any, source: str = SOURCE_BOOK, *, book: Any = None) -> Optional[str]:
    """None when an entry may proceed; ``QUOTE_STALE`` when the book quote is older than 2 s (or absent)."""
    q = selected_quote(direction, "entry", now, source, book=book)
    return QUOTE_STALE if q["quote_stale"] else None
