"""Synthetic bfx_book_10l / bfx_trades rows for protection-replay tests (no production use)."""
from __future__ import annotations

from functools import lru_cache
from typing import Callable

from research_v3_book_walk import BookWalkTape


def book_row(s: int, bid: float, ask: float, *, size: float = 5.0, step: float = 0.05) -> dict:
    return {
        "s": s,
        "b": [[round(bid - i * step, 8), size] for i in range(10)],
        "a": [[round(ask + i * step, 8), size] for i in range(10)],
    }


def trade_row(s: int, px: float, *, ms: int | None = None, amount: float = 0.01) -> dict:
    return {"s": s, "tr": [[s, int(s * 1000 if ms is None else ms), amount, px]]}


def build_tape(start: int, end: int, *, bid: Callable[[int], float] | float = 100.0, spread: float = 0.05,
               trade_every: int = 60, trade_px: Callable[[int], float] | None = None,
               skip_book: Callable[[int], bool] | None = None) -> BookWalkTape:
    bid_at = bid if callable(bid) else (lambda _s: float(bid))
    books = [book_row(s, bid_at(s), bid_at(s) + spread) for s in range(start, end + 1)
             if not (skip_book and skip_book(s))]
    trades = [trade_row(s, (trade_px or (lambda t: bid_at(t) + spread / 2))(s))
              for s in range(start, end + 1, trade_every)]
    return BookWalkTape.from_rows(books, trades, receipt={"fixture": True})


@lru_cache(maxsize=None)
def flat_tape(start: int = 900, end: int = 8800) -> BookWalkTape:
    """A flat 100.00 / 100.05 book covering every fixture signal plus the 7200 s replay horizon."""
    return build_tape(start, end)
