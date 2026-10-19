"""Exit-trigger measurement (issue #807, measurement only, no behaviour change).

trigger_vs_book_bp: the price an exit fired on vs the best level of the 1 s order book at the trigger second
(exit_depth_snapshot_signal {"b": [[px, qty], ...], "a": [...]}). LONG exits compare against the best bid, SHORT
against the best ask. Sign: positive = the real book was BETTER for the position than the trigger price (the exit
fired on a price that looked worse than the market, e.g. a stale ticker BBO).

trigger_tape_age_s: age of the ticker BBO (state bbo_ts) the trigger was evaluated on, at the trigger time.
Pure functions; never raise; None when an input is missing or not finite.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _best(book: Any, key: str) -> Optional[float]:
    if not isinstance(book, Mapping):
        return None
    levels = book.get(key)
    if not isinstance(levels, (list, tuple)) or not levels:
        return None
    top = levels[0]
    px = _f(top[0] if isinstance(top, (list, tuple)) and top else top)
    return px if px and px > 0 else None


def trigger_vs_book_bp(side: Any, trigger_price: Any, book_signal: Any) -> Optional[float]:
    side_u = str(side or "").upper()
    if side_u not in ("LONG", "SHORT"):
        return None
    trig = _f(trigger_price)
    if not trig or trig <= 0:
        return None
    touch = _best(book_signal, "b" if side_u == "LONG" else "a")
    if touch is None:
        return None
    sign = 1.0 if side_u == "LONG" else -1.0
    return round((touch / trig - 1.0) * 1e4 * sign, 3)


def trigger_tape_age_s(trigger_ts: Any, bbo_ts: Any) -> Optional[float]:
    t, b = _f(trigger_ts), _f(bbo_ts)
    if t is None or b is None or b <= 0:
        return None
    return round(max(0.0, t - b), 3)


def fields(*, side: Any, trigger_price: Any, trigger_ts: Any, bbo_ts: Any, book_signal: Any) -> dict:
    return {"trigger_tape_age_s": trigger_tape_age_s(trigger_ts, bbo_ts),
            "trigger_vs_book_bp": trigger_vs_book_bp(side, trigger_price, book_signal)}
