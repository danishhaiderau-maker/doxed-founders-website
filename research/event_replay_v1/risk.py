"""Fixture risk identity. Not a live liquidation policy and not venue proof.

Maintenance and mark semantics run only when the fixture supplies them.
Unknown maintenance invalidates that scenario. Size is never rounded up.
Margin is not maximum loss.
"""

from __future__ import annotations

from decimal import Decimal


class RiskError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def liquidation_price(side: str, entry: Decimal, leverage: Decimal, mmr: Decimal) -> Decimal:
    """Isolated fixture identity using entry notional.

    long: entry * (1 - 1/leverage + mmr)
    short: entry * (1 + 1/leverage - mmr)
    """

    if leverage <= 0 or mmr < 0:
        raise RiskError("BAD_MAINTENANCE_TERMS")
    imr = Decimal(1) / leverage
    if side == "LONG":
        return entry * (Decimal(1) - imr + mmr)
    if side == "SHORT":
        return entry * (Decimal(1) + imr - mmr)
    raise RiskError("SIDE")


def margin_matches(qty: Decimal, entry: Decimal, leverage: Decimal, margin: Decimal) -> bool:
    if leverage <= 0 or qty <= 0:
        return False
    return margin == (qty * entry) / leverage


def size_decision(qty: Decimal, min_qty: Decimal, *, increase_size: bool) -> tuple[str, Decimal, bool]:
    if qty <= 0 or qty < min_qty:
        if increase_size and min_qty > qty:
            return "OK", min_qty, True
        return "MIN_SIZE", qty, False
    return "OK", qty, False
