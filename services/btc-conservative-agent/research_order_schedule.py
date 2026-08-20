"""Pure metadata capture for authoritative Showcase paper-order intervals.

These helpers do not decide, submit, reprice, fill, or cancel an order. They are
called only after those lifecycle actions have already succeeded.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping


SCHEMA = "research_chase_schedule_v1"


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
        prior["requested_qty"] = _positive(order.get("qty")) or prior.get("requested_qty")
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
    return _attach(schedule, order, signal)


def schedule_snapshot(source: Mapping[str, Any] | None) -> dict | None:
    schedule = (source or {}).get("research_chase_schedule")
    return copy.deepcopy(schedule) if isinstance(schedule, dict) else None
