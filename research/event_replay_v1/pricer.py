"""Single research quote/trigger/fill pricer.

Every fixture strategy and lifecycle action uses this module. Marketable
orders walk executable opposite-side depth. Passive orders fill at the
resting limit only after traded-through or queue-consumption evidence.
Books, queues, and delays consumed here are SYNTHETIC. They are not proof
that a historical venue book existed. Ticker, mid, last, candle, and
book_tape_1s prices are not fill prices.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

PRICER_ID = "research.event_replay_v1.pricer.v1"


class PriceError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def D(value: Any) -> Decimal:
    return Decimal(str(value))


def S(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def sort_levels(levels: list, *, bids: bool) -> list[tuple[Decimal, Decimal]]:
    parsed = [(D(price), D(qty)) for price, qty in levels]
    if any(price <= 0 or qty <= 0 for price, qty in parsed):
        raise PriceError("NONPOSITIVE_LEVEL")
    return sorted(parsed, key=lambda item: item[0], reverse=bids)


def select_book(books: list[dict], time_us: int, *, admit_future: bool) -> dict | None:
    """Latest visible book whose validity interval covers time_us.

    Future admission is a killed mutation. The correct path refuses books
    whose receive time or availability is after time_us. Stale books and
    books outside their validity interval never fill. No interpolation and
    no venue switch.
    """

    eligible = []
    for book in books:
        if book.get("stale") is True:
            continue
        if not admit_future:
            if int(book["availability_us"]) > time_us:
                continue
            if int(book["received_us"]) > time_us:
                continue
        if int(book["valid_from_us"]) <= time_us < int(book["valid_to_us"]):
            eligible.append(book)
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda book: (
            int(book["availability_us"]),
            int(book["received_us"]),
            int(book["seq"]),
        ),
    )


def walk_levels(levels: list[tuple[Decimal, Decimal]], qty: Decimal) -> dict:
    remaining = qty
    filled = Decimal(0)
    notional = Decimal(0)
    parts: list[dict[str, str]] = []
    for price, level_qty in levels:
        if remaining <= 0:
            break
        take = level_qty if level_qty < remaining else remaining
        parts.append({"price": S(price), "qty": S(take)})
        notional += price * take
        filled += take
        remaining -= take
    return {
        "parts": parts,
        "filled": filled,
        "residual": remaining,
        "notional": notional,
        "vwap": (notional / filled) if filled > 0 else None,
        "touch": levels[0][0] if levels else None,
    }


def depth_slippage(buys: bool, vwap: Decimal, touch: Decimal, qty: Decimal) -> Decimal:
    if buys:
        return (vwap - touch) * qty
    return (touch - vwap) * qty


def allocate_resting(
    side: str,
    limit_price: Decimal,
    queue_ahead: Decimal | None,
    trade_price: Decimal,
    trade_remaining: Decimal,
    order_open: Decimal,
) -> dict:
    """Consume one shared trade print in price-time order.

    Queue-ahead, which is SYNTHETIC, is consumed before any resting fill.
    Touch alone fills zero. A through print fills at the resting limit only
    for the quantity still remaining after that queue, and never as a taker
    walk. Missing queue evidence does not consume the print.
    """

    if queue_ahead is None:
        return {
            "evidence": "UNKNOWN",
            "fill_qty": Decimal(0),
            "queue_ahead": None,
            "trade_remaining": trade_remaining,
            "price": limit_price,
        }
    buy = side == "BUY"
    through = trade_price < limit_price if buy else trade_price > limit_price
    at_level = trade_price == limit_price
    if not through and not at_level:
        return {
            "evidence": "NO_EVIDENCE",
            "fill_qty": Decimal(0),
            "queue_ahead": queue_ahead,
            "trade_remaining": trade_remaining,
            "price": limit_price,
        }
    consume = queue_ahead if queue_ahead < trade_remaining else trade_remaining
    queue_ahead -= consume
    trade_remaining -= consume
    if trade_remaining <= 0 or order_open <= 0:
        evidence = "TOUCH_ONLY" if at_level else "QUEUE_AHEAD_CONSUMED"
        return {
            "evidence": evidence,
            "fill_qty": Decimal(0),
            "queue_ahead": queue_ahead,
            "trade_remaining": trade_remaining,
            "price": limit_price,
        }
    fill = order_open if order_open < trade_remaining else trade_remaining
    trade_remaining -= fill
    return {
        "evidence": "TRADED_THROUGH" if through else "QUEUE_CONSUMPTION",
        "fill_qty": fill,
        "queue_ahead": queue_ahead,
        "trade_remaining": trade_remaining,
        "price": limit_price,
    }


def passive_match(
    side: str,
    limit_price: Decimal,
    queue_ahead: Decimal,
    trade_price: Decimal,
    trade_qty: Decimal,
) -> dict:
    """Single-order view of allocate_resting. Fill quantity is `qty`."""

    allocated = allocate_resting(
        side, limit_price, queue_ahead, trade_price, trade_qty, trade_qty
    )
    return {
        "evidence": allocated["evidence"],
        "qty": allocated["fill_qty"],
        "price": allocated["price"],
        "queue_ahead": allocated["queue_ahead"],
        "trade_remaining": allocated["trade_remaining"],
    }


def exit_walk(levels: list[tuple[Decimal, Decimal]], qty: Decimal, trigger_price: Decimal | None) -> dict:
    """Depth walk for an exit. The trigger price is not a fill price."""

    del trigger_price
    return walk_levels(levels, qty)


def cancel_ack_delay_us(spec: dict) -> int:
    return int(spec["cancel_ack_delay_us"])


def signal_denominator(signals: list) -> int:
    return len(signals)
