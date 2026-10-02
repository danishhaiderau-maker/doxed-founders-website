"""Pure, bounded paper evidence reservations. Caller publishes returned target atomically.

No IO, entry/order actions, or trading controls live here. Never apply the
reservation and lifecycle mutation as separate durable writes.
"""
import copy
import re

FIELD = "paper_evidence_capacity"
QUEUE = "paper_research_obligations"
RESERVED = "RESERVED"
KINDS = {"PAPER_FILL": "fill", "PAPER_CLOSE": "close", "PAPER_CANCEL": "fill"}


class CapacityError(ValueError):
    pass


def _trade(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value.encode()) > 512:
        raise CapacityError("INVALID_TRADE_ID")
    return value


def _obligation(row):
    trade = _trade((row.get("position") or {}).get("trade_id"))
    slot = KINDS.get(row.get("kind"))
    oid = row.get("id")
    if slot is None or not isinstance(oid, str) or not re.fullmatch("[0-9a-f]{64}", oid):
        raise CapacityError("INVALID_OBLIGATION_IDENTITY")
    return trade, slot, oid


def validate(target):
    state = target.get(FIELD)
    if not isinstance(state, dict) or state.get("schema") != "paper_evidence_capacity_v1":
        raise CapacityError("RESERVATION_STATE_MISSING")
    cap = state.get("capacity")
    if type(cap) is not int or not 2 <= cap <= 4096:
        raise CapacityError("INVALID_CAPACITY")
    trades = state.get("trades")
    if not isinstance(trades, dict):
        raise CapacityError("INVALID_RESERVATIONS")
    queued = {}
    for row in target.get(QUEUE, []):
        trade, slot, oid = _obligation(row)
        if (trade, slot) in queued or oid in queued.values():
            raise CapacityError("DUPLICATE_OBLIGATION")
        queued[trade, slot] = oid
    held = 0
    actual = {}
    for trade, slots in trades.items():
        _trade(trade)
        if not isinstance(slots, dict) or not slots or set(slots) - {"fill", "close"}:
            raise CapacityError("INVALID_RESERVED_SLOTS")
        for slot, value in slots.items():
            held += 1
            if value != RESERVED:
                actual[trade, slot] = value
    if actual != queued:
        raise CapacityError("RESERVATION_QUEUE_MISMATCH")
    if held > cap:
        raise CapacityError("CAPACITY_OVERCOMMITTED")
    return {"held": held, "queued": len(queued), "reserved": held - len(queued), "available": cap - held}


def initialize_legacy(target, *, capacity=128):
    """Count every pending/open trade and outstanding obligation before admission.

    Over-capacity is a migration failure, not authorization to discard positions.
    Caller must leave entries disabled and arrange capacity before activation.
    """
    if FIELD in target:
        validate(target)
        return copy.deepcopy(target)
    result = copy.deepcopy(target)
    trades = {}
    for row in result.get("pending_orders", []):
        trade = _trade(row.get("trade_id"))
        if trade in trades:
            raise CapacityError("DUPLICATE_ACTIVE_TRADE")
        trades[trade] = {"fill": RESERVED, "close": RESERVED}
    for row in result.get("positions", []):
        trade = _trade(row.get("trade_id"))
        if trade in trades:
            raise CapacityError("DUPLICATE_ACTIVE_TRADE")
        trades[trade] = {"close": RESERVED}
    for row in result.get(QUEUE, []):
        trade, slot, oid = _obligation(row)
        slots = trades.setdefault(trade, {})
        if slots.get(slot, RESERVED) != RESERVED:
            raise CapacityError("DUPLICATE_OBLIGATION")
        slots[slot] = oid
    result[FIELD] = {"schema": "paper_evidence_capacity_v1", "capacity": capacity, "trades": trades}
    validate(result)
    return result


def reserve_entry(target, trade_id):
    stats = validate(target)
    trade_id = _trade(trade_id)
    if trade_id in target[FIELD]["trades"]:
        raise CapacityError("TRADE_ALREADY_RESERVED")
    if stats["available"] < 2:
        raise CapacityError("NEW_ENTRY_EVIDENCE_CAPACITY_EXHAUSTED")
    result = copy.deepcopy(target)
    result[FIELD]["trades"][trade_id] = {"fill": RESERVED, "close": RESERVED}
    validate(result)
    return result


def enqueue_reserved(target, obligation):
    validate(target)
    trade, slot, oid = _obligation(obligation)
    current = target[FIELD]["trades"].get(trade, {}).get(slot)
    if current == oid:
        if obligation not in target.get(QUEUE, []):
            raise CapacityError("OBLIGATION_CONTENT_CONFLICT")
        return copy.deepcopy(target)
    if current != RESERVED:
        raise CapacityError("OBLIGATION_NOT_RESERVED")
    result = copy.deepcopy(target)
    result[FIELD]["trades"][trade][slot] = oid
    result.setdefault(QUEUE, []).append(copy.deepcopy(obligation))
    validate(result)
    return result


def enqueue_cancel_reserved(target, obligation, *, accounting_receipt):
    """Retain terminal cancellation evidence while releasing only unused close capacity."""
    trade, slot, oid = _obligation(obligation)
    if obligation.get("kind") != "PAPER_CANCEL":
        raise CapacityError("CANCEL_OBLIGATION_REQUIRED")
    # Reuse strict zero-fill/accounting checks without publishing their release.
    release_cancelled(target, trade, accounting_receipt=accounting_receipt)
    result = enqueue_reserved(target, obligation)
    del result[FIELD]["trades"][trade]["close"]
    validate(result)
    return result


def replace_prepared(target, old_obligation, prepared_obligation):
    """Attach a frozen write plan without losing the reservation's exact ID."""
    validate(target)
    trade, slot, old_id = _obligation(old_obligation)
    next_trade, next_slot, new_id = _obligation(prepared_obligation)
    if (trade, slot) != (next_trade, next_slot):
        raise CapacityError("PREPARED_OBLIGATION_IDENTITY_CHANGED")
    if (target[FIELD]["trades"].get(trade, {}).get(slot) != old_id
            or old_obligation not in target.get(QUEUE, [])):
        raise CapacityError("PREPARED_OBLIGATION_NOT_CURRENT")
    for key, value in old_obligation.items():
        if key not in {"id", "write_plan"} and prepared_obligation.get(key) != value:
            raise CapacityError("PREPARED_OBLIGATION_CONTENT_CHANGED")
    result = copy.deepcopy(target)
    result[QUEUE] = [copy.deepcopy(prepared_obligation) if r == old_obligation else r
                     for r in result[QUEUE]]
    result[FIELD]["trades"][trade][slot] = new_id
    validate(result)
    return result


def acknowledge(target, trade_id, kind, obligation_id):
    """Caller must first prove mandatory durable write receipts for this ID."""
    validate(target)
    trade_id = _trade(trade_id)
    slot = KINDS.get(kind)
    if slot is None or target[FIELD]["trades"].get(trade_id, {}).get(slot) != obligation_id or obligation_id == RESERVED:
        raise CapacityError("ACK_IDENTITY_MISMATCH")
    result = copy.deepcopy(target)
    result[QUEUE] = [r for r in result.get(QUEUE, []) if r["id"] != obligation_id]
    del result[FIELD]["trades"][trade_id][slot]
    if not result[FIELD]["trades"][trade_id]:
        del result[FIELD]["trades"][trade_id]
    validate(result)
    return result


def release_cancelled(target, trade_id, *, accounting_receipt):
    """Release unused slots only for an explicitly reconciled, zero-fill cancel."""
    validate(target)
    trade_id = _trade(trade_id)
    if (accounting_receipt.get("trade_id") != trade_id
            or accounting_receipt.get("terminal_state") not in {"CANCELLED", "EXPIRED"}
            or accounting_receipt.get("reconciled") is not True
            or type(accounting_receipt.get("filled_qty")) not in (int, float)
            or accounting_receipt["filled_qty"] != 0):
        raise CapacityError("CANCELLATION_ACCOUNTING_UNPROVEN")
    slots = target[FIELD]["trades"].get(trade_id)
    if slots != {"fill": RESERVED, "close": RESERVED}:
        raise CapacityError("CANCELLATION_HAS_EVIDENCE_OR_OPEN_EXPOSURE")
    result = copy.deepcopy(target)
    del result[FIELD]["trades"][trade_id]
    validate(result)
    return result
