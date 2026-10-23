"""REALISM_V2 F2: the ONE shared trigger / quote helper (no per-tile code).

Every paper exit trigger, every entry quote-staleness check and every dual stamp goes through here.

Trigger rule (tracker "REALISM FIXES F1-F7" F2 + Rules Keeper):
- A price level (stop / target) is reached at the FIRST of
    * a trade printing at or through the level (``trigger_basis = LAST_TRADE``), or
    * the executable book side reaching it (``trigger_basis = BOOK_TOP``): bid for a LONG exit, ask for a SHORT exit.
  Stops trigger on book top PLUS last trade; every other exit triggers on the executable book side only
  (``exit_events(..., include_trades=False)``).
- Stale threshold = paper_fill.MAX_BOOK_AGE_S (2 s) for BOTH sources (ticker and book). An entry whose selected
  quote is stale is skipped with ``QUOTE_STALE``. A stale exit still fires; it is then priced on the book, else it is
  ``NO_BOOK`` / UNVERIFIED (never a ticker fallback - that is paper_fill's job, not this module's).
- ``TRIGGER_PRICE_SOURCE`` is a per-tile flag (env), default ``ticker``. Rows carry ``trigger_price_source``; pools
  never mix sources (``pool_key``).
- Dual stamp: every trigger records the ticker view AND the book/last-trade view at the trigger instant, whichever
  source drove it, so the flip can be judged on the same rows.

Book row timing: a 1 s book row for second ``s`` is complete only at ``s + 1`` (paper_fill.BookTail); a trade print
is known at its own ms timestamp. Nothing in this module prices a fill; fills stay in paper_fill / tile_fill.
"""
from __future__ import annotations

import json
import math
import os
import threading
from typing import Any, Callable, Iterable, Mapping, Optional

import paper_fill as _pf

STALE_S = _pf.MAX_BOOK_AGE_S  # 2.0 s, both sources
SOURCE_TICKER = "ticker"
SOURCE_BOOK = "book"
SOURCES = (SOURCE_TICKER, SOURCE_BOOK)
DEFAULT_SOURCE = SOURCE_TICKER
ENV_KEY = "TRIGGER_PRICE_SOURCE"

BASIS_LAST_TRADE = "LAST_TRADE"
BASIS_BOOK_TOP = "BOOK_TOP"
BASIS_TICKER = "TICKER"
BASIS_TICKER_STALE_BOOK = "TICKER_BOOK_STALE"  # book mode, no fresh book/trade event: time exits still fire, flagged
QUOTE_STALE = "QUOTE_STALE"
NO_BOOK = "NO_BOOK"
MAX_EVENTS = 64
TRADES_FILE = "bfx_trades.jsonl"

KIND_STOP = "stop"
KIND_TARGET = "target"

DUAL_STAMP_KEYS = ("dual_ticker_px", "dual_ticker_ts", "dual_ticker_age_s",
                   "dual_book_px", "dual_book_s", "dual_book_age_s",
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
def trigger_price_source(lane: Any = None, env: Optional[Mapping[str, str]] = None) -> str:
    """Per-tile TRIGGER_PRICE_SOURCE. Env forms: ``book`` (all tiles) or ``LANE=book,LANE2=ticker`` (``*`` = all).

    Anything unparseable or unknown is the default ``ticker`` (fail closed to the current behaviour).
    """
    raw = str((env if env is not None else os.environ).get(ENV_KEY) or "").strip()
    if not raw:
        return DEFAULT_SOURCE
    lane_u = str(lane or "").strip().upper()
    if "=" not in raw:
        v = raw.lower()
        return v if v in SOURCES else DEFAULT_SOURCE
    star = None
    for part in raw.split(","):
        if "=" not in part:
            continue
        k, v = (x.strip() for x in part.split("=", 1))
        v = v.lower()
        if v not in SOURCES:
            continue
        if k == "*":
            star = v
        elif lane_u and k.upper() == lane_u:
            return v
    return star or DEFAULT_SOURCE


def pool_key(row: Mapping[str, Any]) -> str:
    """Analyzer pool key: rows are never pooled across trigger sources (pre-flag rows count as ticker)."""
    v = str((row or {}).get("trigger_price_source") or DEFAULT_SOURCE).lower()
    return v if v in SOURCES else DEFAULT_SOURCE


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
    first, as dicts {"ts", "px", "amt"}. Read-only.
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
                    prints.append({"ts": ms / 1000.0, "px": px, "amt": amt})
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


def book_rows_between(t0: float, t1: float, book_at: Callable[[float], Any]) -> list:
    """1 s book rows that became complete in (t0, t1] (s + 1 in that window), oldest first."""
    rows, seen = [], set()
    lo, hi = int(math.floor(t0)), int(math.floor(t1))
    for sec in range(max(lo, hi - 120), hi + 1):
        ready = sec + 1.0
        if ready <= t0 or ready > t1:
            continue
        try:
            b = book_at(ready)
        except Exception:  # noqa: BLE001
            b = None
        if isinstance(b, Mapping) and b.get("s") is not None and int(b["s"]) not in seen and int(b["s"]) == sec:
            seen.add(int(b["s"]))
            rows.append(b)
    return rows


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
                trades: Iterable[Mapping] = (), include_trades: bool = True, max_events: int = MAX_EVENTS) -> list:
    """Chronological exit trigger events in (t0, t1]: executable book top per completed row (+ trade prints).

    Each event is {"ts", "px", "basis", "book_s"|"trade_ts"}. Consecutive equal prices of the same basis are
    deduped; above ``max_events`` the stream is compressed keeping each chunk's adverse and favourable extremes in
    time order (so the first stop / target crossing in a chunk is never lost).
    """
    ev = []
    for b in book_rows or ():
        px, ready = exec_px(direction, "exit", b), book_ready_ts(b)
        if px is not None and ready is not None and t0 < ready <= t1:
            ev.append({"ts": ready, "px": px, "basis": BASIS_BOOK_TOP, "book_s": int(b["s"])})
    if include_trades:
        for tr in trades or ():
            ts, px = _f(tr.get("ts")), _pos(tr.get("px"))
            if ts is not None and px is not None and t0 < ts <= t1:
                ev.append({"ts": ts, "px": px, "basis": BASIS_LAST_TRADE, "trade_ts": ts})
    ev.sort(key=lambda e: (e["ts"], 0 if e["basis"] == BASIS_LAST_TRADE else 1))
    dedup = []
    for e in ev:
        if dedup and dedup[-1]["px"] == e["px"] and dedup[-1]["basis"] == e["basis"]:
            continue
        dedup.append(e)
    if len(dedup) <= max_events:
        return dedup
    n_chunks = max(1, max_events // 2)
    size = int(math.ceil(len(dedup) / n_chunks))
    out = []
    for i in range(0, len(dedup), size):
        chunk = dedup[i:i + size]
        lo = min(chunk, key=lambda e: (e["px"], e["ts"]))
        hi = max(chunk, key=lambda e: (e["px"], -e["ts"]))
        out.extend(sorted({id(lo): lo, id(hi): hi}.values(), key=lambda e: e["ts"]))
    return out


def first_level_trigger(direction: Any, kind: str, level: Any, t0: float, t1: float, *,
                        book_rows: Iterable[Mapping] = (), trades: Iterable[Mapping] = ()) -> Optional[dict]:
    """First of LAST_TRADE at/through ``level`` or BOOK_TOP reaching it in (t0, t1]; None if neither.

    Stops use both bases; targets use the executable book side only (Rules Keeper).
    """
    include = kind == KIND_STOP
    for e in exit_events(direction, t0, t1, book_rows=book_rows, trades=trades, include_trades=include,
                         max_events=10 ** 9):
        if level_reached(direction, kind, level, e["px"]):
            return {"trigger_ts": e["ts"], "trigger_px": e["px"], "trigger_basis": e["basis"],
                    "trigger_level": _pos(level), "trigger_kind": kind,
                    **({"trigger_event_book_s": e["book_s"]} if "book_s" in e else {"trigger_event_trade_ts": e["trade_ts"]})}
    return None


# ------------------------------------------------------------------ quotes + staleness
def _age(now: Optional[float], then: Optional[float]) -> Optional[float]:
    if now is None or then is None or then <= 0:
        return None
    a = now - then
    return round(a, 3) if a >= 0 else None


def is_stale(age: Optional[float]) -> bool:
    return age is None or age > STALE_S


def dual_stamp(direction: Any, leg: str, now: Any, *, ticker_px: Any = None, ticker_ts: Any = None,
               book: Any = None, last_trade: Optional[Mapping] = None) -> dict:
    """Ticker view + book-top view + last-trade view at one instant. Measurement fields only."""
    t = _f(now)
    lt = last_trade if isinstance(last_trade, Mapping) else {}
    lt_ts = _f(lt.get("ts"))
    return {
        "dual_ticker_px": _pos(ticker_px), "dual_ticker_ts": _f(ticker_ts),
        "dual_ticker_age_s": _age(t, _f(ticker_ts)),
        "dual_book_px": exec_px(direction, leg, book),
        "dual_book_s": _f(book.get("s")) if isinstance(book, Mapping) else None,
        "dual_book_age_s": _age(t, _f(book.get("s")) if isinstance(book, Mapping) else None),
        "dual_last_trade_px": _pos(lt.get("px")), "dual_last_trade_ts": lt_ts,
        "dual_last_trade_age_s": _age(t, lt_ts),
    }


def selected_quote(direction: Any, leg: str, now: Any, source: str, *, ticker_px: Any = None,
                   ticker_ts: Any = None, book: Any = None) -> dict:
    """The quote the selected source gives at ``now`` + its age + the 2 s stale verdict (same rule both sources)."""
    t = _f(now)
    if source == SOURCE_BOOK:
        px = exec_px(direction, leg, book)
        age = _age(t, _f(book.get("s")) if isinstance(book, Mapping) else None)
    else:
        source = SOURCE_TICKER
        px, age = _pos(ticker_px), _age(t, _f(ticker_ts))
    stale = px is None or is_stale(age)
    return {"trigger_price_source": source, "quote_px": px, "quote_age_s": age, "quote_stale": stale,
            "quote_reason": (QUOTE_STALE if px is not None or source == SOURCE_TICKER else NO_BOOK) if stale else None}


def entry_quote_gate(direction: Any, now: Any, source: str, *, ticker_px: Any = None, ticker_ts: Any = None,
                     book: Any = None) -> Optional[str]:
    """None when an entry may proceed; ``QUOTE_STALE`` when the selected source is older than 2 s (or absent)."""
    q = selected_quote(direction, "entry", now, source, ticker_px=ticker_px, ticker_ts=ticker_ts, book=book)
    return QUOTE_STALE if q["quote_stale"] else None
