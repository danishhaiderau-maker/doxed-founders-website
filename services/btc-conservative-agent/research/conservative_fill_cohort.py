"""Descriptive-only bridge from immutable v2.2 events to fill receipts."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from conservative_limit_fill import evaluate_limit_fill
from microstructure_tape import validate_window


COHORT_SCHEMA = "conservative_fill_descriptive_cohort_v1"


def _unsupported(event: Mapping[str, Any], reason: str) -> dict[str, Any]:
    basis = event.get("research_execution_basis") or {}
    return {
        "schema": "conservative_limit_fill_receipt_v1",
        "event_id": event.get("event_id"),
        "outcome": "UNSUPPORTED",
        "supported": False,
        "requested_qty": basis.get("requested_qty"),
        "filled_qty": 0.0,
        "remaining_qty": basis.get("requested_qty"),
        "negative_reasons": [reason],
        "qualification": "DESCRIPTIVE_ONLY",
        "qualification_effect": "NONE",
    }


def build_conservative_fill_cohort(
    events: Iterable[Mapping[str, Any]],
    microstructure_rows: Iterable[Mapping[str, Any]],
    *,
    aggressor_window_sec: int = 3,
) -> dict[str, Any]:
    """Evaluate only auditable, complete inputs; never alter qualification."""
    tape = list(microstructure_rows)
    receipts = []
    for event in events:
        if event.get("schema") != "research_event_v2.2":
            receipts.append(_unsupported(event, "LEGACY_EVENT_UNSUPPORTED"))
            continue
        basis = event.get("research_execution_basis") or {}
        qty = basis.get("requested_qty")
        if not qty:
            receipts.append(_unsupported(event, "MISSING_REQUESTED_QTY"))
            continue
        schedule = event.get("research_chase_schedule") or {}
        if schedule.get("authoritative") is not True or not schedule.get("intervals"):
            receipts.append(_unsupported(event, "AUTHORITATIVE_CHASE_SCHEDULE_MISSING"))
            continue
        intervals = schedule.get("intervals") or []
        try:
            schedule_window = {
                "required_start_ts": min(int(float(row["start_ts"])) for row in intervals),
                "required_end_ts": max(int(float(row["end_ts"])) for row in intervals),
            }
        except (KeyError, TypeError, ValueError):
            receipts.append(_unsupported(event, "AUTHORITATIVE_CHASE_SCHEDULE_INVALID"))
            continue
        completeness = validate_window(tape, schedule_window)
        if completeness.get("eligible") is not True:
            receipts.append(_unsupported(event, "MICROSTRUCTURE_WINDOW_INCOMPLETE"))
            continue
        receipt = evaluate_limit_fill(
            tape,
            direction=event.get("executed_direction") or event.get("direction") or (event.get("envelope") or {}).get("executed_direction"),
            requested_qty=qty,
            chase_schedule=intervals,
            aggressor_window_sec=aggressor_window_sec,
            symbol=event.get("symbol") or (event.get("event_episode") or {}).get("symbol"),
        )
        receipt["event_id"] = event.get("event_id")
        receipt["requested_qty_provenance"] = basis.get("requested_qty_provenance")
        receipt["exchange_qty_claim"] = basis.get("exchange_qty_claim") is True
        receipt["qualification"] = "DESCRIPTIVE_ONLY"
        receipt["qualification_effect"] = "NONE"
        receipt["microstructure_completeness"] = completeness
        receipts.append(receipt)
    return {
        "schema": COHORT_SCHEMA,
        "qualification": "DESCRIPTIVE_ONLY",
        "qualification_effect": "NONE",
        "qualification_promotion_allowed": False,
        "conservative_execution_gate_changed": False,
        "receipts": receipts,
        "counts": {
            "events": len(receipts),
            "fill": sum(r.get("outcome") == "FILL" for r in receipts),
            "partial_fill": sum(r.get("outcome") == "PARTIAL_FILL" for r in receipts),
            "no_fill": sum(r.get("outcome") == "NO_FILL" for r in receipts),
            "unsupported": sum(r.get("outcome") == "UNSUPPORTED" for r in receipts),
        },
    }
