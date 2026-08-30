"""Pure, research-only conservative limit-fill evaluation.

This module never submits or changes an order.  It evaluates a serialized chase
schedule against ``market_microstructure_1s_v1`` rows and emits an auditable
counterfactual receipt.  The model deliberately makes no queue-position or
price-improvement claim: a supported fill is booked at the declared limit only.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence

try:
    from .quantity_execution import (
        apply_quantity_constraints,
        validate_signed_quantity_constraints,
    )
except ImportError:  # direct script/test execution
    from quantity_execution import apply_quantity_constraints, validate_signed_quantity_constraints


EVIDENCE_SCHEMA = "market_microstructure_1s_v1"
RECEIPT_SCHEMA = "conservative_limit_fill_receipt_v2"
EVALUATOR_VERSION = "public-tape-conservative-v3-quantity-aware"
MAX_AGGRESSOR_WINDOW_SEC = 5


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _base_receipt(direction: str, qty: Any, window: Any) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "evaluator_version": EVALUATOR_VERSION,
        "evidence_schema": EVIDENCE_SCHEMA,
        "outcome": "UNSUPPORTED",
        "supported": False,
        "direction": str(direction).upper(),
        "requested_qty": qty,
        "raw_partial_qty": 0.0,
        "rounded_executable_qty": 0.0,
        "filled_qty": 0.0,
        "accumulated_qty": 0.0,
        "remaining_qty": qty,
        "minimum_lot_decision": "UNKNOWN",
        "minimum_notional_decision": "UNKNOWN",
        "quantity_constraints": None,
        "quantity_attempts": [],
        "final_classification": "UNSUPPORTED",
        "aggressor_window_sec": window,
        "schedule_sha256": None,
        "chase_bucket_id": None,
        "chase_interval": None,
        "evidence_bucket_ids": [],
        "trigger_bucket_ts": None,
        "limit_price": None,
        "side_correct_quote": None,
        "visible_executable_qty": 0.0,
        "matching_aggressor_qty": 0.0,
        "aggressor_corroborated": False,
        "fill_price": None,
        "queue_position_model": "NONE",
        "scope": "PUBLIC_TAPE_COUNTERFACTUAL_NOT_EXCHANGE_CONFIRMATION",
        "negative_reasons": [],
        "diagnostics": {},
    }


def _unsupported(receipt: dict[str, Any], *reasons: str) -> dict[str, Any]:
    receipt["negative_reasons"] = list(dict.fromkeys(reasons))
    return receipt


def _normalise_schedule(schedule: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    normalised: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in schedule:
        bucket_id = str(raw.get("bucket_id", "")).strip()
        start = _finite_positive(raw.get("start_ts"))
        end = _finite_positive(raw.get("end_ts"))
        limit = _finite_positive(raw.get("limit_price"))
        if not bucket_id or bucket_id in seen or start is None or end is None or limit is None or end <= start:
            raise ValueError("INVALID_CHASE_SCHEDULE")
        # The evidence tape has one-second buckets; sub-second interval edges
        # cannot be assigned without inventing ordering inside a bucket.
        if not start.is_integer() or not end.is_integer():
            raise ValueError("SUBSECOND_CHASE_BOUNDARY_UNSUPPORTED")
        seen.add(bucket_id)
        normalised.append({
            "bucket_id": bucket_id,
            "start_ts": int(start),
            "end_ts": int(end),
            "limit_price": limit,
            "generation": raw.get("generation"),
        })
    normalised.sort(key=lambda item: item["start_ts"])
    if not normalised:
        raise ValueError("EMPTY_CHASE_SCHEDULE")
    if any(right["start_ts"] < left["end_ts"] for left, right in zip(normalised, normalised[1:])):
        raise ValueError("OVERLAPPING_CHASE_INTERVALS")
    encoded = json.dumps(normalised, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return normalised, hashlib.sha256(encoded).hexdigest()


def evaluate_limit_fill(
    rows: Iterable[Mapping[str, Any]],
    *,
    direction: str,
    requested_qty: float,
    chase_schedule: Sequence[Mapping[str, Any]],
    aggressor_window_sec: int = 3,
    symbol: str | None = None,
    quantity_constraints: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic fill/no-fill/partial/unsupported receipt.

    LONG represents a buy limit: a fresh ask at/below the limit is immediately
    executable against visible ask depth. SHORT is the exact mirror using bid
    depth. Same-bucket aggressor prints are retained as corroboration, but are
    not required once the opposite BBO itself is marketable. A candle/last-price
    touch is never sufficient. A partial receipt never promotes to a full
    simulated fill.
    """

    receipt = _base_receipt(direction, requested_qty, aggressor_window_sec)
    side = str(direction).upper()
    qty = _finite_positive(requested_qty)
    if side not in {"LONG", "SHORT"}:
        return _unsupported(receipt, "INVALID_DIRECTION")
    if qty is None:
        return _unsupported(receipt, "INVALID_REQUESTED_QTY")
    normalized_constraints, constraint_reasons = validate_signed_quantity_constraints(
        quantity_constraints, symbol=symbol,
    )
    if normalized_constraints is None:
        return _unsupported(receipt, *constraint_reasons)
    receipt["quantity_constraints"] = normalized_constraints
    if not isinstance(aggressor_window_sec, int) or not 1 <= aggressor_window_sec <= MAX_AGGRESSOR_WINDOW_SEC:
        return _unsupported(receipt, "INVALID_AGGRESSOR_WINDOW")
    receipt["requested_qty"] = qty
    receipt["remaining_qty"] = qty
    try:
        schedule, schedule_hash = _normalise_schedule(chase_schedule)
    except (TypeError, ValueError) as exc:
        return _unsupported(receipt, str(exc))
    receipt["schedule_sha256"] = schedule_hash

    by_ts: dict[int, Mapping[str, Any]] = {}
    duplicate = False
    wrong_schema = False
    wrong_symbol = False
    for row in rows:
        if row.get("schema") != EVIDENCE_SCHEMA:
            wrong_schema = True
            continue
        if symbol is not None and row.get("symbol") != symbol:
            wrong_symbol = True
            continue
        raw_ts = row.get("bucket_ts")
        try:
            ts_float = float(raw_ts)
        except (TypeError, ValueError):
            wrong_schema = True
            continue
        if not math.isfinite(ts_float) or not ts_float.is_integer():
            wrong_schema = True
            continue
        ts = int(ts_float)
        if ts in by_ts:
            duplicate = True
        by_ts[ts] = row
    if duplicate:
        return _unsupported(receipt, "DUPLICATE_EVIDENCE_BUCKET")
    if not by_ts:
        reasons = ["NO_MATCHING_EVIDENCE"]
        if wrong_schema:
            reasons.append("EVIDENCE_SCHEMA_MISMATCH")
        if wrong_symbol:
            reasons.append("SYMBOL_MISMATCH")
        return _unsupported(receipt, *reasons)

    interval_by_ts: dict[int, dict[str, Any]] = {}
    expected_ts: list[int] = []
    for interval in schedule:
        for ts in range(interval["start_ts"], interval["end_ts"]):
            interval_by_ts[ts] = interval
            expected_ts.append(ts)

    incomplete: set[str] = set()
    counters = {"bbo_not_crossed": 0, "insufficient_visible_qty": 0, "no_matching_aggressor": 0}
    best_by_interval: dict[str, dict[str, Any]] = {}
    for ts in expected_ts:
        row = by_ts.get(ts)
        interval = interval_by_ts[ts]
        if row is None:
            incomplete.add("EVIDENCE_GAP")
            continue
        if row.get("fresh") is not True:
            incomplete.add("STALE_EVIDENCE_BUCKET")
            continue
        if row.get("valid_bbo") is not True:
            incomplete.add("INVALID_BBO_BUCKET")
            continue

        quote = _finite_positive(row.get("ask" if side == "LONG" else "bid"))
        visible = _finite_positive(row.get("ask_qty" if side == "LONG" else "bid_qty")) or 0.0
        limit = interval["limit_price"]
        crossed = quote is not None and (quote <= limit if side == "LONG" else quote >= limit)
        if not crossed:
            counters["bbo_not_crossed"] += 1
            continue

        window_ts = list(range(ts - aggressor_window_sec + 1, ts + 1))
        window_rows: list[Mapping[str, Any]] = []
        window_invalid = False
        for candidate_ts in window_ts:
            candidate = by_ts.get(candidate_ts)
            if candidate is None:
                incomplete.add("EVIDENCE_GAP_IN_AGGRESSOR_WINDOW")
                window_invalid = True
                break
            if interval_by_ts.get(candidate_ts) is not interval:
                incomplete.add("CHASE_INTERVAL_WINDOW_AMBIGUOUS")
                window_invalid = True
                break
            if candidate.get("fresh") is not True or candidate.get("valid_bbo") is not True:
                incomplete.add("STALE_OR_INVALID_AGGRESSOR_WINDOW")
                window_invalid = True
                break
            window_rows.append(candidate)
        if window_invalid:
            continue

        # Do not transport volume or depth across time. The trigger bucket's
        # fresh opposite BBO is the executable offer for this hypothetical
        # incoming limit. Requiring another aggressor print would double-count
        # proof: a LONG limit with ask <= limit can take that displayed ask
        # immediately (and vice versa for SHORT). Prints remain corroboration
        # only. Earlier buckets are bounded completeness/context evidence.
        aggressor_qty = 0.0
        ambiguous = False
        qty_field, vwap_field = (("sell_qty", "sell_vwap") if side == "LONG" else ("buy_qty", "buy_vwap"))
        amount = _finite_positive(row.get(qty_field)) or 0.0
        if amount > 0:
            opposite_field = "buy_qty" if qty_field == "sell_qty" else "sell_qty"
            opposite = _finite_positive(row.get(opposite_field)) or 0.0
            try:
                trade_count = int(row.get("trade_count"))
            except (TypeError, ValueError):
                trade_count = -1
            # v1 stores side VWAP rather than price-stratified volume. One
            # single-sided print is exact; multiple/mixed prints could let a
            # VWAP hide volume on the wrong side of the limit, so fail closed.
            if trade_count != 1 or opposite > 0:
                ambiguous = True
            else:
                vwap = _finite_positive(row.get(vwap_field))
                if vwap is None:
                    ambiguous = True
                elif side == "LONG" and vwap <= limit:
                    aggressor_qty = amount
                elif side == "SHORT" and vwap >= limit:
                    aggressor_qty = amount
        if aggressor_qty <= 0:
            counters["no_matching_aggressor"] += 1
        if visible < qty:
            counters["insufficient_visible_qty"] += 1

        # The supported quantity is bounded by contemporaneous visible
        # opposite top-of-book depth. We intentionally do not accumulate the
        # same displayed quantity across seconds or claim queue priority.
        filled = min(qty, visible)
        evidence = {
            "interval": interval,
            "ts": ts,
            "quote": quote,
            "visible": visible,
            "aggressor": aggressor_qty,
            "aggressor_ambiguous": ambiguous,
            "filled": filled,
            "bucket_ids": window_ts,
        }
        if filled > 0:
            key = str(interval["bucket_id"])
            current = best_by_interval.get(key)
            if current is None or filled > current["filled"]:
                best_by_interval[key] = evidence

    receipt["diagnostics"] = {**counters, "evidence_bucket_count": len(by_ts), "schedule_bucket_count": len(expected_ts)}
    if best_by_interval:
        # A later BBO snapshot can repeat the same displayed liquidity.  In
        # the absence of exchange order IDs or a defensible depletion then
        # replenishment receipt, summing snapshots across chase intervals
        # double-counts quantity.  Use the single strongest contemporaneous
        # observation across the complete schedule.
        strongest = max(
            best_by_interval.values(),
            key=lambda item: (float(item["filled"]), -int(item["ts"])),
        )
        accumulated = 0.0
        attempts: list[dict[str, Any]] = []
        accepted_evidence: list[dict[str, Any]] = []
        interval = strongest["interval"]
        decision = apply_quantity_constraints(
            requested_qty=qty,
            raw_partial_qty=float(strongest["filled"]),
            execution_price=interval["limit_price"],
            accumulated_qty=0,
            constraints=quantity_constraints,
            symbol=symbol,
        )
        decision["chase_bucket_id"] = interval["bucket_id"]
        decision["trigger_bucket_ts"] = strongest["ts"]
        decision["accumulation_basis"] = "MAX_SINGLE_OBSERVATION_NO_CROSS_SNAPSHOT_SUM"
        attempts.append(decision)
        if decision["accepted"]:
            accumulated = float(decision["accumulated_quantity_after"])
            accepted_evidence.append(strongest)
        receipt["quantity_attempts"] = attempts
        receipt["raw_partial_qty"] = sum(float(item["raw_partial_quantity"]) for item in attempts)
        receipt["rounded_executable_qty"] = sum(
            float(item["rounded_executable_quantity"]) for item in attempts if item["accepted"]
        )
        receipt["accumulated_qty"] = accumulated
        if not accepted_evidence:
            unsupported_attempt_reasons = [
                reason for item in attempts
                if item.get("final_classification") == "UNSUPPORTED"
                for reason in item.get("reasons", [])
            ]
            if unsupported_attempt_reasons:
                return _unsupported(receipt, *unsupported_attempt_reasons)
            if incomplete:
                return _unsupported(receipt, *sorted(incomplete))
            receipt.update({
                "outcome": "NO_FILL",
                "supported": True,
                "final_classification": "NO_FILL",
                "minimum_lot_decision": attempts[-1]["minimum_lot_decision"],
                "minimum_notional_decision": attempts[-1]["minimum_notional_decision"],
                "negative_reasons": list(dict.fromkeys(
                    reason for item in attempts for reason in item["reasons"]
                )),
            })
            return receipt
        best_partial = accepted_evidence[-1]
        interval = best_partial["interval"]
        is_full = accumulated >= qty
        filled = qty if is_full else accumulated
        last_decision = attempts[-1]
        receipt.update({
            "outcome": "FILL" if is_full else "PARTIAL_FILL",
            "supported": True,
            "final_classification": "FULL_FILL" if is_full else "PARTIAL_FILL",
            "filled_qty": filled,
            "accumulated_qty": filled,
            "remaining_qty": round(max(0.0, qty - filled), 12),
            "minimum_lot_decision": last_decision["minimum_lot_decision"],
            "minimum_notional_decision": last_decision["minimum_notional_decision"],
            "chase_bucket_id": interval["bucket_id"],
            "chase_interval": dict(interval),
            "evidence_bucket_ids": best_partial["bucket_ids"],
            "trigger_bucket_ts": best_partial["ts"],
            "limit_price": interval["limit_price"],
            "side_correct_quote": best_partial["quote"],
            "visible_executable_qty": best_partial["visible"],
            "matching_aggressor_qty": best_partial["aggressor"],
            "aggressor_corroborated": bool(best_partial["aggressor"] > 0),
            "fill_price": interval["limit_price"],
            "negative_reasons": [] if is_full else ["PARTIAL_ONLY_INSUFFICIENT_PROVABLE_QTY"],
        })
        return receipt

    if incomplete:
        return _unsupported(receipt, *sorted(incomplete))
    receipt["outcome"] = "NO_FILL"
    receipt["supported"] = True
    receipt["final_classification"] = "NO_FILL"
    reasons = []
    if counters["bbo_not_crossed"]:
        reasons.append("BBO_NEVER_CROSSED_LIMIT")
    if counters["insufficient_visible_qty"]:
        reasons.append("INSUFFICIENT_VISIBLE_TOP_OF_BOOK_QTY")
    if counters["no_matching_aggressor"]:
        reasons.append("NO_MATCHING_AGGRESSOR_AT_OR_THROUGH_LIMIT")
    receipt["negative_reasons"] = reasons or ["NO_PROVABLE_FILL"]
    return receipt
