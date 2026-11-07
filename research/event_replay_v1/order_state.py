"""Order lifecycle with quantity conservation.

Duplicate acknowledgements are idempotent. Cancels do not apply until their
acknowledgement time, so an in-flight partial can still fill. Quantity is
never created or dropped: filled + cancelled + rejected + open = original.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


class OrderError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Order:
    order_id: str
    signal_id: str
    strategy_id: str
    role: str
    side: str
    original: Decimal
    style: str
    created_us: int
    limit_price: Decimal | None = None
    rounded_up: bool = False
    queue_ahead: Decimal | None = None
    parent_order_id: str | None = None
    filled: Decimal = field(default_factory=lambda: Decimal(0))
    cancelled: Decimal = field(default_factory=lambda: Decimal(0))
    rejected: Decimal = field(default_factory=lambda: Decimal(0))
    open_qty: Decimal = field(default_factory=lambda: Decimal(0))
    state: str = "PENDING_ARRIVAL"
    reason: str | None = None
    ack_ids: set[str] = field(default_factory=set)
    history: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.open_qty = self.original
        self.note(self.created_us, "PENDING_ARRIVAL", "CREATE")

    def conserved(self) -> bool:
        return self.filled + self.cancelled + self.rejected + self.open_qty == self.original

    def note(self, time_us: int, state: str, reason: str) -> None:
        self.state = state
        self.reason = reason
        self.history.append({"time_us": int(time_us), "state": state, "reason": reason})

    def apply_ack(self, ack_id: str, time_us: int) -> str:
        if ack_id in self.ack_ids:
            return "DUPLICATE_IGNORED"
        self.ack_ids.add(ack_id)
        if self.state == "PENDING_ARRIVAL":
            self.note(time_us, "WORKING", "ACK")
        return "ACK"

    def apply_fill(self, qty: Decimal, time_us: int) -> None:
        if qty <= 0 or qty > self.open_qty:
            raise OrderError("FILL_QTY")
        self.open_qty -= qty
        self.filled += qty
        if self.open_qty == 0:
            self.note(time_us, "FILLED", "FILL")
        else:
            self.note(time_us, "PARTIAL", "FILL")

    def cancel_open(self, time_us: int, reason: str) -> Decimal:
        qty = self.open_qty
        self.cancelled += qty
        self.open_qty = Decimal(0)
        self.note(time_us, "CANCELLED", reason)
        return qty

    def reject_open(self, time_us: int, reason: str) -> Decimal:
        qty = self.open_qty
        self.rejected += qty
        self.open_qty = Decimal(0)
        self.note(time_us, "REJECTED", reason)
        return qty
