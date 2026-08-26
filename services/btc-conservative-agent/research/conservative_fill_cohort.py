"""Descriptive-only bridge from immutable v2.2 events to fill receipts."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Mapping

try:
    from .conservative_limit_fill import evaluate_limit_fill
except ImportError:  # direct script/test execution
    from conservative_limit_fill import evaluate_limit_fill
from microstructure_tape import validate_window


COHORT_SCHEMA = "conservative_fill_descriptive_cohort_v1"
_TAPE_SYMBOL_ALIASES = {
    "BTCUSD": "tBTCF0:USTF0",
    "BTC/USD": "tBTCF0:USTF0",
    "BTC/USDT:USDT": "tBTCF0:USTF0",
    "TBTCF0:USTF0": "tBTCF0:USTF0",
}


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


def _market_microstructure_symbol(event: Mapping[str, Any]) -> str | None:
    basis = event.get("research_execution_basis") or {}
    exact = str(basis.get("market_microstructure_symbol") or "").strip()
    if exact:
        return exact
    strategy_symbol = str(
        event.get("symbol")
        or (event.get("event_episode") or {}).get("symbol")
        or (event.get("envelope") or {}).get("symbol")
        or ""
    ).strip()
    if not strategy_symbol:
        return None
    return _TAPE_SYMBOL_ALIASES.get(strategy_symbol.upper(), strategy_symbol)


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
        tape_symbol = _market_microstructure_symbol(event)
        if not tape_symbol:
            receipts.append(_unsupported(event, "MARKET_MICROSTRUCTURE_SYMBOL_MISSING"))
            continue
        completeness = validate_window(tape, schedule_window)
        receipt = evaluate_limit_fill(
            tape,
            direction=event.get("executed_direction") or event.get("direction") or (event.get("envelope") or {}).get("executed_direction"),
            requested_qty=qty,
            chase_schedule=intervals,
            aggressor_window_sec=aggressor_window_sec,
            symbol=tape_symbol,
        )
        receipt["event_id"] = event.get("event_id")
        receipt["requested_qty_provenance"] = basis.get("requested_qty_provenance")
        receipt["exchange_qty_claim"] = basis.get("exchange_qty_claim") is True
        receipt["qualification"] = "DESCRIPTIVE_ONLY"
        receipt["qualification_effect"] = "NONE"
        receipt["microstructure_completeness"] = completeness
        receipt["market_microstructure_symbol"] = tape_symbol
        if completeness.get("eligible") is not True:
            if receipt.get("supported") is True and receipt.get("outcome") in {"FILL", "PARTIAL_FILL"}:
                receipt["fill_time_semantics"] = "LATEST_PROVEN_TRIGGER_BUCKET_NOT_EARLIEST_FILL"
                receipt["window_integrity_scope"] = "TRIGGER_PROOF_ONLY"
            else:
                receipt["negative_reasons"] = list(dict.fromkeys([
                    "MICROSTRUCTURE_WINDOW_INCOMPLETE",
                    *(receipt.get("negative_reasons") or []),
                ]))
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


def _complete_v3_schedule(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    schedule = row.get("chase_schedule") or {}
    intervals = schedule.get("intervals") or []
    if not intervals:
        return []
    required = ("bucket_id", "start_ts", "end_ts", "limit_price")
    if not all(all(item.get(key) is not None for key in required) for item in intervals):
        return []
    normalized = [dict(item) for item in intervals]
    # V3 stores exact fractional boundaries alongside integer bucket labels.
    # Intermediate generations hand the boundary second to the next interval;
    # the terminal generation must include its fractional terminal second.
    # Treating integer end_ts as exclusive previously dropped the exact second
    # in which a venue-proven paper fill occurred.
    for index, item in enumerate(normalized):
        if item.get("start_ts_exact") is not None:
            item["start_ts"] = math.floor(float(item["start_ts_exact"]))
        if item.get("end_ts_exact") is not None:
            exact_end = float(item["end_ts_exact"])
            item["end_ts"] = (
                math.ceil(exact_end)
                if index == len(normalized) - 1
                else math.floor(exact_end)
            )
    return normalized


def build_v3_conservative_fill_cohort(
    order_intents: Iterable[Mapping[str, Any]],
    microstructure_rows: Iterable[Mapping[str, Any]],
    *,
    aggressor_window_sec: int = 3,
) -> dict[str, Any]:
    """Evaluate one finalized, identity-preserving V3.1 intent per event.

    V3.1 writes the submit-time intent before its chase intervals have terminal
    ``end_ts`` values, then appends a finalized intent row for the same event.
    The legacy cohort builder cannot consume either schema and previously
    received an empty tuple, producing a misleading all-zero report.  This
    adapter selects only a finalized authoritative schedule and never converts
    V3 identity into a fabricated legacy event.
    """
    tape = list(microstructure_rows)
    by_event: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in order_intents:
        if row.get("schema") != "research_evidence_v3" or row.get("ledger") != "order_intent":
            continue
        event_id = str(row.get("event_id") or "").strip()
        if event_id:
            by_event[event_id].append(row)

    receipts: list[dict[str, Any]] = []
    excluded_non_actual = 0
    for event_id, versions in by_event.items():
        actual_versions = [
            row for row in versions
            if row.get("intent_kind") == "ACTUAL_PAPER_LIMIT_SUBMIT"
        ]
        if not actual_versions:
            # CONTROL_V1 policy-multiverse rows describe counterfactual entry
            # children; they are not submitted orders and must not inflate an
            # actual conservative execution denominator.
            excluded_non_actual += 1
            continue
        identity_row = max(actual_versions, key=lambda item: str(item.get("record_id") or ""))
        finalized = [row for row in versions if _complete_v3_schedule(row)]
        if not finalized:
            receipt = _unsupported(identity_row, "FINALIZED_AUTHORITATIVE_CHASE_SCHEDULE_MISSING")
            receipt.update({
                "event_id": event_id,
                "source_schema": "research_evidence_v3",
                "policy_id": identity_row.get("policy_id"),
                "policy_signature": identity_row.get("policy_signature"),
                "epoch_id": identity_row.get("epoch_id"),
            })
            receipts.append(receipt)
            continue

        # Prefer the richest finalized row.  This is deterministic and favors
        # the terminal schedule carrying exact quantity provenance.
        row = max(finalized, key=lambda item: (
            len(_complete_v3_schedule(item)),
            bool(item.get("execution_basis")),
            str(item.get("record_id") or ""),
        ))
        basis = row.get("execution_basis") or {}
        qty = basis.get("requested_qty") or row.get("requested_qty")
        if not qty:
            receipt = _unsupported(row, "MISSING_REQUESTED_QTY")
        else:
            tape_symbol = str(basis.get("market_microstructure_symbol") or "tBTCF0:USTF0").strip()
            intervals = _complete_v3_schedule(row)
            schedule_window = {
                "required_start_ts": min(int(float(item["start_ts"])) for item in intervals),
                "required_end_ts": max(int(float(item["end_ts"])) for item in intervals),
            }
            completeness = validate_window(tape, schedule_window)
            receipt = evaluate_limit_fill(
                tape,
                direction=row.get("executed_direction") or (row.get("chase_schedule") or {}).get("direction"),
                requested_qty=qty,
                chase_schedule=intervals,
                aggressor_window_sec=aggressor_window_sec,
                symbol=tape_symbol,
            )
            receipt["microstructure_completeness"] = completeness
            receipt["market_microstructure_symbol"] = tape_symbol
            if completeness.get("eligible") is not True:
                if receipt.get("supported") is True and receipt.get("outcome") in {"FILL", "PARTIAL_FILL"}:
                    receipt["fill_time_semantics"] = "LATEST_PROVEN_TRIGGER_BUCKET_NOT_EARLIEST_FILL"
                    receipt["window_integrity_scope"] = "TRIGGER_PROOF_ONLY"
                else:
                    receipt["negative_reasons"] = list(dict.fromkeys([
                        "MICROSTRUCTURE_WINDOW_INCOMPLETE",
                        *(receipt.get("negative_reasons") or []),
                    ]))
                    receipt["outcome"] = "UNSUPPORTED"
                    receipt["supported"] = False

        receipt.update({
            "event_id": event_id,
            "episode_id": identity_row.get("episode_id") or row.get("episode_id"),
            "epoch_id": identity_row.get("epoch_id") or row.get("epoch_id"),
            "opportunity_id": identity_row.get("opportunity_id") or row.get("opportunity_id"),
            "policy_epoch_id": identity_row.get("policy_epoch_id") or row.get("policy_epoch_id"),
            "policy_id": identity_row.get("policy_id") or row.get("policy_id"),
            "policy_signature": identity_row.get("policy_signature") or row.get("policy_signature"),
            "schedule_id": identity_row.get("schedule_id") or row.get("schedule_id"),
            "tape_id": identity_row.get("tape_id") or row.get("tape_id"),
            "fill_id": (
                f"fill:{identity_row.get('epoch_id') or row.get('epoch_id')}:{event_id}:paper-primary"
                if receipt.get("outcome") in {"FILL", "PARTIAL_FILL"}
                and (identity_row.get("epoch_id") or row.get("epoch_id"))
                and event_id
                else None
            ),
            "research_lane": identity_row.get("research_lane") or row.get("research_lane"),
            "source_schema": "research_evidence_v3",
            "source_record_id": row.get("record_id"),
            "source_submit_record_id": identity_row.get("record_id"),
            "requested_qty_provenance": basis.get("requested_qty_provenance"),
            "exchange_qty_claim": basis.get("exchange_qty_claim") is True,
            "qualification": "DESCRIPTIVE_ONLY",
            "qualification_effect": "NONE",
        })
        receipts.append(receipt)

    return {
        "schema": "conservative_fill_descriptive_cohort_v3_1",
        "qualification": "DESCRIPTIVE_ONLY",
        "qualification_effect": "NONE",
        "qualification_promotion_allowed": False,
        "conservative_execution_gate_changed": False,
        "source_intent_rows": sum(len(rows) for rows in by_event.values()),
        "distinct_events": len(by_event),
        "actual_order_events": len(receipts),
        "excluded_counterfactual_only_events": excluded_non_actual,
        "receipts": receipts,
        "counts": {
            "events": len(receipts),
            "fill": sum(r.get("outcome") == "FILL" for r in receipts),
            "partial_fill": sum(r.get("outcome") == "PARTIAL_FILL" for r in receipts),
            "no_fill": sum(r.get("outcome") == "NO_FILL" for r in receipts),
            "unsupported": sum(r.get("outcome") == "UNSUPPORTED" for r in receipts),
        },
    }
