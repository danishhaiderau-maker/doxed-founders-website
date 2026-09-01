"""Pure metadata capture for authoritative Showcase paper-order intervals.

These helpers do not decide, submit, reprice, fill, or cancel an order. They are
called only after those lifecycle actions have already succeeded.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Mapping


SCHEMA = "research_chase_schedule_v1"
QUANTITY_SCHEMA = "research_order_quantity_evidence_v1"
ACTION_TIMING_SCHEMA = "research_order_action_timing_v1"


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _offset_pct(reference_price: float, limit_price: float) -> float | None:
    if not reference_price:
        return None
    return round(abs(limit_price - reference_price) / reference_price * 100.0, 8)


def _quantity_state(order: Mapping[str, Any], *, event: str, now: float) -> dict:
    """Snapshot quantity evidence without deriving or changing an order size."""
    requested = _positive(order.get("requested_qty")) or _positive(order.get("qty"))
    filled = _positive(order.get("filled_qty"))
    fill_sim = order.get("fill_sim") if isinstance(order.get("fill_sim"), Mapping) else {}
    filled = filled or _positive(fill_sim.get("filled_qty"))
    if filled is None and str(order.get("status") or "").upper() == "FILLED" and not order.get("partial_fill"):
        filled = requested
    remaining = max(0.0, requested - filled) if requested is not None and filled is not None else None
    constraints = order.get("signed_quantity_constraints")
    constraints = copy.deepcopy(dict(constraints)) if isinstance(constraints, Mapping) else None
    gate = order.get("venue_fill_gate")
    gate = gate if isinstance(gate, Mapping) else {}
    return {
        "schema": QUANTITY_SCHEMA, "event": str(event), "observed_ts": float(now),
        "requested_qty": requested,
        "requested_qty_provenance": "SOURCE_TICKET_QTY" if requested is not None else "MISSING",
        "raw_partial_qty": filled if order.get("partial_fill") else None,
        "rounded_executable_qty": filled, "accumulated_filled_qty": filled or 0.0,
        "remaining_qty": remaining, "partial_fill": bool(order.get("partial_fill")),
        "available_quantity": _positive(gate.get("visible_executable_qty")),
        "queue_adjusted_available_quantity": _positive(gate.get("queue_adjusted_available_qty")),
        "quantity_step": constraints.get("quantity_step") if constraints else None,
        "minimum_lot": constraints.get("min_lot") if constraints else None,
        "minimum_notional": constraints.get("min_notional") if constraints else None,
        "minimum_lot_decision": "UNKNOWN_REQUIRES_CONSERVATIVE_EVALUATOR",
        "minimum_notional_decision": "UNKNOWN_REQUIRES_CONSERVATIVE_EVALUATOR",
        "signed_quantity_constraints": constraints,
        "quantity_constraints_status": copy.deepcopy(order.get("quantity_constraints_status")),
    }


def _append_quantity_event(schedule: dict, order: Mapping[str, Any], *, event: str, now: float) -> None:
    state = _quantity_state(order, event=event, now=now)
    events = schedule.setdefault("quantity_events", [])
    identity = (state["event"], state["observed_ts"])
    if not any((row.get("event"), row.get("observed_ts")) == identity for row in events if isinstance(row, Mapping)):
        events.append(state)
    schedule["requested_qty"] = state["requested_qty"] or schedule.get("requested_qty")
    schedule["requested_qty_provenance"] = "SOURCE_TICKET_QTY" if schedule.get("requested_qty") else "MISSING"


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nonnegative(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number >= 0 else None


def _action_receipt_sha(receipt: Mapping[str, Any]) -> str:
    body = {key: copy.deepcopy(value) for key, value in receipt.items() if key != "receipt_sha256"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_action_timing_receipt(
    order: dict,
    signal: dict | None,
    *,
    action_generation: int,
    action_type: str,
    policy_due_ts: float,
    eligibility_ts: float,
    dispatch_start_ts: float,
    acknowledgement_ts: float | None = None,
    fill_ts: float | None = None,
    fill_price: float | None = None,
    filled_qty: float | None = None,
    remaining_qty: float | None = None,
    limit_price: float | None = None,
    book_ref: Mapping[str, Any] | str | None = None,
    tape_ref: Mapping[str, Any] | str | None = None,
    non_intentional_delay: Mapping[str, Any] | None = None,
) -> dict | None:
    """Append an immutable observation receipt for an already-taken action.

    This is evidence capture only.  It never sleeps, submits, reprices, fills,
    or changes the schedule.  A duplicate identity is accepted only when its
    canonical payload is byte-for-byte equivalent; conflicting observations
    are rejected fail-closed.
    """
    schedule = order.get("research_chase_schedule") if isinstance(order, dict) else None
    if not isinstance(schedule, dict) or schedule.get("authoritative") is not True:
        return None
    side = str(order.get("signal_dir") or order.get("dir") or (signal or {}).get("final_direction") or "").upper()
    action = str(action_type or "").upper()
    due = _finite(policy_due_ts)
    eligible = _finite(eligibility_ts)
    dispatched = _finite(dispatch_start_ts)
    acknowledged = _finite(acknowledgement_ts)
    filled = _finite(fill_ts)
    generation_number = _finite(action_generation)
    observed_fill_price = _positive(fill_price)
    observed_filled_qty = _positive(filled_qty)
    qty = _nonnegative(remaining_qty)
    if qty is None:
        qty = _nonnegative(order.get("remaining_qty"))
    if qty is None:
        qty = _positive(order.get("qty"))
    limit = _positive(limit_price) or _positive(order.get("limit_price"))
    if (
        side not in {"LONG", "SHORT"} or not action or due is None or eligible is None
        or dispatched is None or generation_number is None or generation_number < 0
        or not generation_number.is_integer()
    ):
        return None
    if dispatched < max(due, eligible):
        return None
    if acknowledged is not None and acknowledged < dispatched:
        return None
    if filled is not None and filled < dispatched:
        return None
    delay_proof = copy.deepcopy(dict(non_intentional_delay)) if isinstance(non_intentional_delay, Mapping) else None
    if delay_proof is not None and _finite(delay_proof.get("seconds")) is not None:
        delay_proof["seconds"] = _finite(delay_proof["seconds"])
    payload = {
        "schema": ACTION_TIMING_SCHEMA,
        "trade_id": schedule.get("trade_id") or order.get("trade_id"),
        "action_generation": int(generation_number),
        "action_type": action,
        "side": side,
        "policy_due_ts": due,
        "eligibility_ts": eligible,
        "dispatch_start_ts": dispatched,
        "acknowledgement_ts": acknowledged,
        "fill_ts": filled,
        "fill_price": observed_fill_price,
        "filled_qty": observed_filled_qty,
        "remaining_qty": qty,
        "limit_price": limit,
        "book_ref": copy.deepcopy(book_ref),
        "tape_ref": copy.deepcopy(tape_ref),
        "non_intentional_delay": delay_proof,
        "attribution_only": True,
    }
    payload["receipt_sha256"] = _action_receipt_sha(payload)
    identity = (payload["action_generation"], payload["action_type"])
    receipts = schedule.setdefault("action_timing_receipts", [])
    for existing in receipts:
        if not isinstance(existing, Mapping):
            continue
        if (existing.get("action_generation"), existing.get("action_type")) != identity:
            continue
        if existing.get("receipt_sha256") != _action_receipt_sha(existing):
            return None
        return existing if existing.get("receipt_sha256") == payload["receipt_sha256"] else None
    receipts.append(payload)
    return payload


def _attach(schedule: dict, order: dict, signal: dict | None) -> dict:
    order["research_chase_schedule"] = schedule
    order["chase_schedule_authoritative"] = True
    if isinstance(signal, dict):
        signal["research_chase_schedule"] = schedule
        signal["chase_schedule_authoritative"] = True
    return schedule


def initialize_order_schedule(
    order: dict,
    signal: dict | None,
    *,
    now: float,
    registered: bool,
    reason: str = "ORDER_REGISTERED",
) -> dict | None:
    """Start an interval only for a successfully registered real paper order."""
    if not registered or not isinstance(order, dict):
        return None
    if str(order.get("status") or "").upper() != "PENDING":
        return None
    if order.get("bitfinex_order_id") or order.get("bitfinex_live_entry"):
        return None
    if str(order.get("adopt_source") or "").upper() == "RECONCILE":
        return None
    existing = order.get("research_chase_schedule")
    if isinstance(existing, dict) and existing.get("authoritative") is True:
        order.setdefault("requested_qty", existing.get("requested_qty") or order.get("qty"))
        return _attach(existing, order, signal)
    prior = (signal or {}).get("research_chase_schedule") if isinstance(signal, dict) else None
    if isinstance(prior, dict) and prior.get("authoritative") is True:
        intervals = prior.get("intervals") or []
        if intervals and intervals[-1].get("end_ts") is None:
            return _attach(prior, order, signal)
        limit_price = _positive(order.get("limit_price"))
        if limit_price is None:
            return None
        reference = _positive(order.get("signal_price")) or _positive(signal.get("signal_price")) or limit_price
        step = int(order.get("limit_chase_count") or 0)
        intervals.append({
            "bucket_id": f"{prior.get('trade_id') or 'unknown'}:chase:{step}:{len(intervals)}",
            "start_ts": int(float(now)),
            "start_ts_exact": float(now),
            "end_ts": None,
            "chase_step_index": step,
            "generation": step,
            "reference_price": reference,
            "limit_price": limit_price,
            "offset_pct": _offset_pct(reference, limit_price),
            "reason": reason,
        })
        prior["terminal_reason"] = None
        prior["terminal_ts"] = None
        prior["terminal_ts_exact"] = None
        prior["requested_qty"] = prior.get("requested_qty") or _positive(order.get("qty"))
        order.setdefault("requested_qty", prior.get("requested_qty"))
        _append_quantity_event(prior, order, event=reason, now=now)
        return _attach(prior, order, signal)
    limit_price = _positive(order.get("limit_price"))
    if limit_price is None:
        return None
    reference = (
        _positive(order.get("signal_price"))
        or _positive((signal or {}).get("signal_price"))
        or limit_price
    )
    step = int(order.get("limit_chase_count") or 0)
    trade_id = str(order.get("trade_id") or "unknown")
    schedule = {
        "schema": SCHEMA,
        "authoritative": True,
        "source": "SHOWCASE_PAPER_PENDING_ORDER",
        "trade_id": order.get("trade_id"),
        "direction": order.get("signal_dir") or order.get("dir") or (signal or {}).get("final_direction"),
        "requested_qty": _positive(order.get("qty")),
        "requested_qty_provenance": "SOURCE_TICKET_QTY" if _positive(order.get("qty")) else "MISSING",
        "intervals": [{
            "bucket_id": f"{trade_id}:chase:{step}:0",
            "start_ts": int(float(now)),
            "start_ts_exact": float(now),
            "end_ts": None,
            "chase_step_index": step,
            "generation": step,
            "reference_price": reference,
            "limit_price": limit_price,
            "offset_pct": _offset_pct(reference, limit_price),
            "reason": reason,
        }],
        "terminal_reason": None,
        "terminal_ts": None,
    }
    order.setdefault("requested_qty", schedule.get("requested_qty"))
    _append_quantity_event(schedule, order, event=reason, now=now)
    return _attach(schedule, order, signal)


def append_reprice_interval(
    order: dict,
    signal: dict | None,
    *,
    now: float,
    chase_step_index: int,
    reference_price: float,
    limit_price: float,
    reason: str,
) -> dict | None:
    schedule = order.get("research_chase_schedule") if isinstance(order, dict) else None
    if not isinstance(schedule, dict) or schedule.get("authoritative") is not True:
        return None
    intervals = schedule.get("intervals")
    if not isinstance(intervals, list) or not intervals:
        return None
    current = intervals[-1]
    if current.get("end_ts") is None:
        current["end_ts"] = int(float(now))
        current["end_ts_exact"] = float(now)
    reference = _positive(reference_price) or _positive(current.get("reference_price")) or _positive(limit_price)
    limit = _positive(limit_price)
    if limit is None:
        return None
    step = int(chase_step_index)
    intervals.append({
        "bucket_id": f"{schedule.get('trade_id') or 'unknown'}:chase:{step}:{len(intervals)}",
        "start_ts": int(float(now)),
        "start_ts_exact": float(now),
        "end_ts": None,
        "chase_step_index": step,
        "generation": step,
        "reference_price": reference,
        "limit_price": limit,
        "offset_pct": _offset_pct(reference, limit),
        "reason": str(reason or "LIMIT_REPRICE"),
    })
    _append_quantity_event(schedule, order, event=str(reason or "LIMIT_REPRICE"), now=now)
    return _attach(schedule, order, signal)


def close_order_schedule(
    order: dict,
    signal: dict | None,
    *,
    now: float,
    reason: str,
) -> dict | None:
    schedule = order.get("research_chase_schedule") if isinstance(order, dict) else None
    if not isinstance(schedule, dict) or schedule.get("authoritative") is not True:
        return None
    intervals = schedule.get("intervals") or []
    if intervals and intervals[-1].get("end_ts") is None:
        intervals[-1]["end_ts"] = int(float(now))
        intervals[-1]["end_ts_exact"] = float(now)
    schedule["terminal_ts"] = int(float(now))
    schedule["terminal_ts_exact"] = float(now)
    schedule["terminal_reason"] = str(reason)
    _append_quantity_event(schedule, order, event=f"TERMINAL_{reason}", now=now)
    schedule["final_quantity_state"] = copy.deepcopy(schedule["quantity_events"][-1])
    return _attach(schedule, order, signal)


def schedule_snapshot(source: Mapping[str, Any] | None) -> dict | None:
    schedule = (source or {}).get("research_chase_schedule")
    return copy.deepcopy(schedule) if isinstance(schedule, dict) else None
