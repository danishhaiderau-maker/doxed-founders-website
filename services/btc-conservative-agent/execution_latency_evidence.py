"""Pure, fail-closed causal latency attribution for observed paper actions.

The evaluator never changes an order or schedule.  It compares the observed
action with the *same* action shifted earlier only by a separately proven
non-intentional delay.  Price impact remains attribution-only because gross
PnL calculated from actual execution prices already embeds it.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence


SCHEMA = "execution_latency_evidence_v1"
UNKNOWN = "UNKNOWN"
SUPPORTED = "SUPPORTED"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _unknown(*reasons: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": UNKNOWN,
        "attribution_only": True,
        "latency_cost_usd": None,
        "reasons": list(dict.fromkeys(str(reason) for reason in reasons if reason)),
    }


def _receipt_hash_valid(receipt: Mapping[str, Any]) -> bool:
    claimed = str(receipt.get("receipt_sha256") or "")
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    try:
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        return False
    return bool(claimed) and hashlib.sha256(canonical.encode("utf-8")).hexdigest() == claimed


def _snapshot(rows: Sequence[Mapping[str, Any]], ts: float, *, max_age_sec: float) -> tuple[Mapping[str, Any] | None, str | None]:
    eligible = []
    for row in rows:
        observed = _number(row.get("ts") if row.get("ts") is not None else row.get("observed_ts"))
        if observed is not None and observed <= ts:
            eligible.append((observed, row))
    if not eligible:
        return None, "BBO_MISSING"
    observed, row = max(eligible, key=lambda item: item[0])
    if ts - observed > max_age_sec:
        return None, "BBO_STALE"
    return row, None


def _executable(row: Mapping[str, Any], side: str, qty: float, limit: float | None) -> tuple[str, float | None]:
    price_key, qty_key = ("ask", "ask_qty") if side == "LONG" else ("bid", "bid_qty")
    price = _number(row.get(price_key))
    available = _number(row.get(qty_key))
    if price is None or price <= 0:
        return UNKNOWN, None
    if available is None or available + 1e-12 < qty:
        return "INSUFFICIENT_TOP_QTY", None
    if limit is not None:
        crosses = price <= limit if side == "LONG" else price >= limit
        if not crosses:
            return "NO_FILL", None
    return "FILL", price


def evaluate_execution_latency(
    receipt: Mapping[str, Any],
    market_rows: Sequence[Mapping[str, Any]],
    *,
    terminal: bool,
    market_tape_ref: Mapping[str, Any] | str | None = None,
    max_bbo_age_sec: float = 1.0,
    overlapping_actions: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Evaluate one action without inventing a delay or executable path.

    ``non_intentional_delay`` must be an independently produced proof with
    ``classification=PROVEN_NON_INTENTIONAL`` and a positive ``seconds``.
    Timing separation of one second or less is intentionally unresolved: the
    clock/order ambiguity is as large as the proposed counterfactual shift.
    """
    if receipt.get("schema") != "research_order_action_timing_v1":
        return _unknown("ACTION_TIMING_RECEIPT_MISSING")
    if not _receipt_hash_valid(receipt):
        return _unknown("ACTION_TIMING_RECEIPT_HASH_INVALID")
    if not receipt.get("book_ref") or not receipt.get("tape_ref"):
        return _unknown("BOOK_OR_TAPE_REFERENCE_MISSING")
    if market_tape_ref is None or market_tape_ref != receipt.get("tape_ref"):
        return _unknown("MARKET_TAPE_REFERENCE_MISMATCH")
    side = str(receipt.get("side") or "").upper()
    qty = _number(receipt.get("remaining_qty"))
    limit = _number(receipt.get("limit_price"))
    due = _number(receipt.get("policy_due_ts"))
    eligible = _number(receipt.get("eligibility_ts"))
    dispatch = _number(receipt.get("dispatch_start_ts"))
    if side not in {"LONG", "SHORT"} or qty is None or qty <= 0 or None in {due, eligible, dispatch}:
        return _unknown("ACTION_FIELDS_INCOMPLETE")
    delay_proof = receipt.get("non_intentional_delay")
    if not isinstance(delay_proof, Mapping) or delay_proof.get("classification") != "PROVEN_NON_INTENTIONAL":
        return _unknown("NON_INTENTIONAL_DELAY_UNPROVEN")
    if not str(delay_proof.get("cause") or "") or not str(delay_proof.get("evidence_ref") or ""):
        return _unknown("NON_INTENTIONAL_DELAY_PROOF_INCOMPLETE")
    delay = _number(delay_proof.get("seconds"))
    if delay is None or delay <= 1.0:
        return _unknown("ONE_SECOND_TIMING_AMBIGUITY")
    action_ready_ts = max(due, eligible)
    if delay > dispatch - action_ready_ts + 1e-9:
        return _unknown("DELAY_EXCEEDS_ACTION_READY_TO_DISPATCH")
    counterfactual_ts = dispatch - delay
    if counterfactual_ts < action_ready_ts - 1e-9:
        return _unknown("COUNTERFACTUAL_PRECEDES_POLICY_OR_ELIGIBILITY")
    generation = receipt.get("action_generation")
    for other in overlapping_actions:
        if other is receipt:
            continue
        if receipt.get("receipt_sha256") and other.get("receipt_sha256") == receipt.get("receipt_sha256"):
            continue
        start = _number(other.get("dispatch_start_ts"))
        end = _number(other.get("acknowledgement_ts")) or _number(other.get("fill_ts")) or start
        if start is not None and end is not None and start <= dispatch and end >= counterfactual_ts:
            return _unknown("OVERLAPPING_ACTION")
    actual_row, error = _snapshot(market_rows, dispatch, max_age_sec=max_bbo_age_sec)
    if error:
        return _unknown(f"ACTUAL_{error}")
    counterfactual_row, error = _snapshot(market_rows, counterfactual_ts, max_age_sec=max_bbo_age_sec)
    if error:
        return _unknown(f"COUNTERFACTUAL_{error}")
    if not actual_row.get("book_ref") or not counterfactual_row.get("book_ref"):
        return _unknown("SELECTED_BOOK_REFERENCE_MISSING")
    if actual_row.get("book_ref") != receipt.get("book_ref"):
        return _unknown("DISPATCH_BOOK_REFERENCE_MISMATCH")
    actual_state, actual_price = _executable(actual_row, side, qty, limit)
    counterfactual_state, counterfactual_price = _executable(counterfactual_row, side, qty, limit)
    if "INSUFFICIENT_TOP_QTY" in {actual_state, counterfactual_state}:
        return _unknown("INSUFFICIENT_TOP_QTY")
    if UNKNOWN in {actual_state, counterfactual_state}:
        return _unknown("BBO_INVALID")
    if actual_state != counterfactual_state:
        return _unknown("FILL_NO_FILL_DIVERGENCE_NOT_TERMINAL" if not terminal else "FILL_NO_FILL_DIVERGENCE")
    if actual_state == "NO_FILL":
        return _unknown("NO_FILL_NOT_TERMINAL" if not terminal else "TERMINAL_PATH_REPLAY_REQUIRED")
    else:
        fill_ts = _number(receipt.get("fill_ts"))
        fill_price = _number(receipt.get("fill_price"))
        filled_qty = _number(receipt.get("filled_qty"))
        if fill_ts is None or fill_price is None or filled_qty is None:
            return _unknown("OBSERVED_FILL_RECEIPT_INCOMPLETE")
        if abs(fill_ts - dispatch) > 1.0:
            return _unknown("ONE_SECOND_EXECUTION_CAUSALITY_AMBIGUITY")
        if abs(filled_qty - qty) > 1e-12:
            return _unknown("PARTIAL_OR_QUANTITY_DIVERGENT_FILL")
        if abs(fill_price - actual_price) > 1e-9:
            return _unknown("OBSERVED_FILL_PRICE_DIFFERS_FROM_EXECUTABLE_BBO")
        # Positive means waiting made the execution worse.  This is not a PnL
        # deduction; actual-price gross already contains the price movement.
        signed_delta = actual_price - counterfactual_price if side == "LONG" else counterfactual_price - actual_price
        signed_latency_impact = signed_delta * qty
        latency_cost = max(0.0, signed_latency_impact)
    return {
        "schema": SCHEMA,
        "status": SUPPORTED,
        "attribution_only": True,
        "must_not_be_subtracted_from_actual_price_gross_pnl": True,
        "latency_cost_usd": latency_cost,
        "signed_latency_impact_usd": signed_latency_impact,
        "action_generation": generation,
        "action_type": receipt.get("action_type"),
        "side": side,
        "remaining_qty": qty,
        "limit_price": limit,
        "observed_dispatch_ts": dispatch,
        "counterfactual_dispatch_ts": counterfactual_ts,
        "proven_non_intentional_delay_sec": delay,
        "actual_state": actual_state,
        "counterfactual_state": counterfactual_state,
        "actual_executable_price": actual_price,
        "observed_fill_price": fill_price,
        "counterfactual_executable_price": counterfactual_price,
        "counterfactual_action": {
            "action_generation": generation,
            "action_type": receipt.get("action_type"),
            "side": side,
            "remaining_qty": qty,
            "limit_price": limit,
        },
        "schedule_mutations": [],
        "actual_book_ref": actual_row.get("book_ref"),
        "counterfactual_book_ref": counterfactual_row.get("book_ref"),
        "reasons": [],
    }


def evaluate_schedule_execution_latency(
    receipts: Sequence[Mapping[str, Any]],
    market_rows: Sequence[Mapping[str, Any]],
    *,
    terminal: bool,
    market_tape_ref: Mapping[str, Any] | str | None = None,
    max_bbo_age_sec: float = 1.0,
) -> dict[str, Any]:
    """Replay every immutable action in one schedule, changing timing only."""
    if not receipts:
        return _unknown("ACTION_TIMING_RECEIPTS_MISSING")
    identities = [(row.get("action_generation"), row.get("action_type")) for row in receipts]
    if len(set(identities)) != len(identities):
        return _unknown("ACTION_TIMING_IDENTITY_DUPLICATE")
    ordered = sorted(receipts, key=lambda row: (_number(row.get("dispatch_start_ts")) or float("inf")))
    results = [
        evaluate_execution_latency(
            row, market_rows, terminal=terminal, market_tape_ref=market_tape_ref,
            max_bbo_age_sec=max_bbo_age_sec,
            overlapping_actions=ordered,
        )
        for row in ordered
    ]
    unsupported = [reason for result in results if result["status"] != SUPPORTED for reason in result["reasons"]]
    if unsupported:
        result = _unknown(*unsupported)
        result["action_results"] = results
        return result
    return {
        "schema": SCHEMA,
        "status": SUPPORTED,
        "attribution_only": True,
        "must_not_be_subtracted_from_actual_price_gross_pnl": True,
        "latency_cost_usd": sum(float(result["latency_cost_usd"]) for result in results),
        "signed_latency_impact_usd": sum(float(result["signed_latency_impact_usd"]) for result in results),
        "action_results": results,
        "schedule_replay": "IDENTICAL_ACTIONS_LIMITS_QUANTITIES_ONLY_PROVEN_DELAY_REMOVED",
        "schedule_mutations": [],
        "reasons": [],
    }
