"""Shared book-priced paper fill (all-tile book-pricing plan, HM-approved 10:25; Danish 09:08 / 10:26).

THE single fill function every tile must use (new-tile rule, CI-enforced):

    paper_fill(side, qty, signal_ts, delay_s, book_at) -> dict

* Source: the Bitfinex 10-level book snapshot (bfx_book_10l, 1 s) at t = signal_ts + delay_s.
  Walks depth for the full qty; insufficient depth -> partial (filled_qty < qty) or no fill.
* Fallback: the WS book top (bfx_book_top) only when the 10-level snapshot is missing.
* Never the ticker and never book_tape_1s / market_microstructure_1s (Strategist 10:37: it lags
  ~1.2 s and overstates fast tiles by ~3 bp). No book -> REJECT_NO_BOOK (no fill).
* Long entries / short exits take the ask; short entries / long exits take the bid.

Every result carries fill_model, fill_model_version, ask_age_ms (book age at fill time), delay_s,
delay_source, book_s and a depth snapshot so the tile-integrity monitor can re-price it.
"""
from __future__ import annotations

import statistics
import threading
from collections import deque
from typing import Any, Callable, Mapping, Optional

FILL_MODEL = "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY"
FILL_MODEL_VERSION = 1
DEFAULT_DELAY_S = 1.5
MIN_DELAY_S = 0.3
MIN_SAMPLES = 10
MAX_BOOK_AGE_S = 2.0
BANNED_SOURCES = ("ticker", "book_tape_1s", "market_microstructure_1s")


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _walk(levels, qty: float):
    left, cost, filled = qty, 0.0, 0.0
    for lv in levels or ():
        px, sz = _f(lv[0]), _f(lv[1])
        if not px or not sz or sz <= 0:
            continue
        take = min(left, sz)
        cost += take * px
        filled += take
        left -= take
        if left <= 1e-12:
            break
    return (cost / filled if filled > 0 else None), filled


def taker_side_levels(side: str, leg: str, book: Mapping[str, Any]):
    """Entry LONG / exit SHORT buy at the ask; entry SHORT / exit LONG sell at the bid."""
    buy = (str(side).upper() == "LONG") == (leg == "entry")
    return book.get("a") if buy else book.get("b")


def paper_fill(side: str, qty: float, signal_ts: float, delay_s: Optional[float],
               book_at: Callable[[float], Optional[Mapping[str, Any]]], *, leg: str = "entry",
               delay_source: str = "lane_p50", top_at: Optional[Callable[[float], Optional[tuple]]] = None) -> dict:
    """book_at(t) -> {"s": sec, "b": [[px, sz]...], "a": [...]} for the latest snapshot at or before t."""
    d = max(MIN_DELAY_S, _f(delay_s) or DEFAULT_DELAY_S)
    t = float(signal_ts) + d
    q = _f(qty) or 0.0
    base = {"fill_model": FILL_MODEL, "fill_model_version": FILL_MODEL_VERSION, "delay_s": round(d, 3),
            "delay_source": delay_source if delay_s else "default", "fill_ts": t, "leg": leg, "qty": q}
    book = book_at(t) if book_at else None
    if book and _f(book.get("s")) is not None and t - float(book["s"]) <= MAX_BOOK_AGE_S:
        levels = taker_side_levels(side, leg, book)
        px, filled = _walk(levels, q)
        if px is None:
            return {**base, "status": "NO_FILL_NO_DEPTH", "price": None, "filled_qty": 0.0,
                    "book_s": int(book["s"]), "book_age_ms": round((t - float(book["s"])) * 1000)}
        return {**base, "status": "FILLED" if filled + 1e-12 >= q else "PARTIAL", "price": px,
                "filled_qty": round(filled, 8), "book_s": int(book["s"]),
                "book_age_ms": round((t - float(book["s"])) * 1000),
                "ask_age_ms": round((t - float(book["s"])) * 1000),
                "depth_snapshot": {"b": list(book.get("b") or [])[:10], "a": list(book.get("a") or [])[:10]},
                "price_source": "BFX_BOOK_10L"}
    if top_at is not None:
        top = top_at(t)
        if top and top[0] and top[1] and top[2] and t - top[2] <= MAX_BOOK_AGE_S:
            bid, ask, ts = top
            buy = (str(side).upper() == "LONG") == (leg == "entry")
            return {**base, "status": "FILLED_TOP_ONLY", "price": ask if buy else bid, "filled_qty": q,
                    "book_s": None, "book_age_ms": round((t - ts) * 1000), "ask_age_ms": round((t - ts) * 1000),
                    "depth_snapshot": None, "price_source": "BFX_BOOK_TOP_WS"}
    return {**base, "status": "REJECT_NO_BOOK", "price": None, "filled_qty": 0.0, "price_source": None}


class DelaySampler:
    """Rolling measured live delay per lane and leg (entry: signal->exchange_ack; exit: trigger->exit ack)."""

    def __init__(self, maxlen: int = 200) -> None:
        self._lock = threading.Lock()
        self._d: dict = {}
        self.maxlen = maxlen

    def add(self, lane: str, leg: str, delay_s: float) -> None:
        v = _f(delay_s)
        if v is None or v < 0 or v > 30:
            return
        with self._lock:
            self._d.setdefault((lane, leg), deque(maxlen=self.maxlen)).append(v)

    def delay(self, lane: str, leg: str = "entry") -> tuple:
        with self._lock:
            xs = list(self._d.get((lane, leg)) or ())
        if len(xs) < MIN_SAMPLES:
            return DEFAULT_DELAY_S, "default_until_n10"
        return max(MIN_DELAY_S, statistics.median(xs)), f"lane_p50_n{len(xs)}"

    def snapshot(self) -> dict:
        with self._lock:
            return {f"{k[0]}|{k[1]}": {"n": len(v), "p50": statistics.median(v) if v else None}
                    for k, v in self._d.items()}
