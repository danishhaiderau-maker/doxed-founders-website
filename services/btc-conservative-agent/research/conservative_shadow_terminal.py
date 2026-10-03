"""Fail-closed terminal replay for a conservatively filled shadow position."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Mapping, Sequence

from research.policy_evidence_schema import canonical_json, stable_hash
from research.quantity_execution import validate_signed_quantity_constraints
from research_v3_contract import canonical_hash
from research_v3_policy_replay import prepare_replay_price_path, replay_protected_policy


SCHEMA = "generation_bound_conservative_shadow_terminal_v1"
SIMULATION_MODEL = "SAFE_POLICY_REPLAY_V3_EXECUTABLE_EXIT_BBO_DEPTH"
GENERATION_FIELDS = (
    "manifest_entry_hash", "epoch_id", "source_revision", "deployed_revision",
    "tile_config_signature", "analyzer_revision", "evaluator_version", "generation_key",
)


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _finite(value: Any, *, positive: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def _quantity(value: Any, *, positive: bool = False) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not quantity.is_finite() or (positive and quantity <= 0):
        return None
    return quantity


def _quantity_text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _derived_lifecycle_trace(
    *, lifecycle_bindings: Mapping[str, Any] | None,
    entry_receipt: Mapping[str, Any], entry_receipt_sha256: str,
    fill_events: Sequence[tuple[float, float, float]],
    exact_exit_quantities: Mapping[int, Decimal], replay: Mapping[str, Any],
    normalized_rows: Sequence[Mapping[str, Any]], policy_spec: Mapping[str, Any],
    policy_signature: str,
    cost_treatment: Mapping[str, Any], coverage: Mapping[str, Any],
    horizon: float, conditional: bool,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Derive one closed event ledger from the already-supported replay.

    This does not reconstruct interleaved entry/protection behavior.  The
    source path starts only after the last accepted entry fill, matching the
    existing single-position terminal model.
    """
    blockers: list[str] = []
    bindings = dict(lifecycle_bindings) if isinstance(lifecycle_bindings, Mapping) else {}
    if bindings.get("schema") != "shadow_terminal_lifecycle_bindings_v1":
        blockers.append("LIFECYCLE_BINDINGS_SCHEMA_INVALID")
    required_text = (
        "parent_opportunity_id", "parent_episode_id", "source_episode_id",
        "baseline_id", "baseline_schedule_sha256", "terminal_schedule_sha256",
        "terminal_policy_signature", "direction", "entry_receipt_sha256",
    )
    for field in required_text:
        value = str(bindings.get(field) or "").strip()
        if not value or value.upper() in {"UNKNOWN", "UNAVAILABLE", "NONE", "NULL", "MISSING"}:
            blockers.append(f"LIFECYCLE_BINDING_MISSING:{field}")
    direction = str(entry_receipt.get("direction") or "").upper()
    if str(bindings.get("direction") or "").upper() != direction:
        blockers.append("LIFECYCLE_DIRECTION_MISMATCH")
    if bindings.get("entry_receipt_sha256") != entry_receipt_sha256:
        blockers.append("LIFECYCLE_ENTRY_RECEIPT_SHA256_MISMATCH")
    if bindings.get("baseline_schedule_sha256") != entry_receipt.get("schedule_sha256"):
        blockers.append("LIFECYCLE_BASELINE_SCHEDULE_SHA256_MISMATCH")
    if bindings.get("terminal_schedule_sha256") != _sha(policy_spec):
        blockers.append("LIFECYCLE_TERMINAL_SCHEDULE_SHA256_MISMATCH")
    if bindings.get("terminal_policy_signature") != policy_signature:
        blockers.append("LIFECYCLE_TERMINAL_POLICY_SIGNATURE_MISMATCH")
    for field in ("baseline_schedule_sha256", "terminal_schedule_sha256"):
        digest = str(bindings.get(field) or "").lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            blockers.append(f"LIFECYCLE_BINDING_INVALID:{field}")

    accepted_attempts = [
        attempt for attempt in entry_receipt.get("quantity_attempts") or []
        if isinstance(attempt, Mapping) and attempt.get("accepted") is True
    ]
    if len(accepted_attempts) != len(fill_events):
        blockers.append("LIFECYCLE_ENTRY_EVENT_COUNT_MISMATCH")
    entry_events = []
    cumulative = Decimal(0)
    for sequence, attempt in enumerate(sorted(
        accepted_attempts,
        key=lambda item: (
            _finite(item.get("trigger_bucket_ts"))
            if _finite(item.get("trigger_bucket_ts")) is not None else float("inf")
        ),
    ), start=1):
        quantity = _quantity(attempt.get("rounded_executable_quantity"), positive=True)
        timestamp = _finite(attempt.get("trigger_bucket_ts"))
        price = _finite(attempt.get("execution_price"), positive=True)
        if quantity is None or timestamp is None or int(timestamp) != timestamp or price is None:
            blockers.append("LIFECYCLE_ENTRY_EVENT_INVALID")
            continue
        cumulative += quantity
        entry_events.append({
            "sequence": sequence,
            "event_type": "ENTRY_FILL_ACCEPTED",
            "timestamp": int(timestamp),
            "price": price,
            "filled_quantity": _quantity_text(quantity),
            "cumulative_filled_quantity": _quantity_text(cumulative),
        })
    filled_quantity = _quantity(entry_receipt.get("filled_qty"), positive=True)
    if filled_quantity is None or cumulative != filled_quantity:
        blockers.append("LIFECYCLE_ENTRY_QUANTITY_NOT_RECONCILED")
    requested_raw = entry_receipt.get("requested_qty")
    requested_quantity = None if requested_raw is None else _quantity(requested_raw, positive=True)
    if requested_raw is not None and requested_quantity is None:
        blockers.append("LIFECYCLE_REQUESTED_ENTRY_QUANTITY_INVALID")
    if requested_quantity is not None and filled_quantity is not None and requested_quantity < filled_quantity:
        blockers.append("LIFECYCLE_REQUESTED_ENTRY_QUANTITY_BELOW_FILLED")
    unfilled_quantity = (
        requested_quantity - filled_quantity
        if requested_quantity is not None and filled_quantity is not None else None
    )

    trace_rows = replay.get("trace")
    if not isinstance(trace_rows, list) or not trace_rows:
        blockers.append("LIFECYCLE_EXIT_TRACE_MISSING")
        trace_rows = []
    trace_timestamps = []
    partial_exit_timestamps = set()
    for trace in trace_rows:
        if not isinstance(trace, Mapping):
            blockers.append("LIFECYCLE_EXIT_TRACE_INVALID")
            continue
        timestamp = _finite(trace.get("ts"))
        if timestamp is None or int(timestamp) != timestamp:
            blockers.append("LIFECYCLE_EXIT_TRACE_TIMESTAMP_INVALID")
            continue
        trace_timestamps.append(int(timestamp))
        partials = trace.get("partial_exits") or []
        if not isinstance(partials, list) or not all(isinstance(item, Mapping) for item in partials):
            blockers.append("LIFECYCLE_EXIT_TRACE_INVALID")
        elif partials:
            partial_exit_timestamps.add(int(timestamp))
    if trace_timestamps != sorted(trace_timestamps) or len(trace_timestamps) != len(set(trace_timestamps)):
        blockers.append("LIFECYCLE_EXIT_TRACE_NOT_STRICTLY_ORDERED")

    exit_prices = {int(row["ts"]): float(row["price"]) for row in normalized_rows}
    exit_events = []
    remaining = filled_quantity or Decimal(0)
    terminal_ts = int(float(replay.get("exit_ts"))) if _finite(replay.get("exit_ts")) is not None else None
    for sequence, (timestamp, quantity) in enumerate(sorted(exact_exit_quantities.items()), start=1):
        if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity <= 0:
            blockers.append("LIFECYCLE_EXIT_QUANTITY_INVALID")
            continue
        if timestamp not in exit_prices:
            blockers.append("LIFECYCLE_EXIT_PRICE_MISSING")
            continue
        remaining -= quantity
        if remaining < 0:
            blockers.append("LIFECYCLE_EXIT_EXCEEDS_POSITION")
        exit_events.append({
            "sequence": sequence,
            "event_type": (
                "PARTIAL_AND_TERMINAL_EXIT_FILL"
                if timestamp in partial_exit_timestamps and timestamp == terminal_ts else
                "PARTIAL_EXIT_FILL" if timestamp in partial_exit_timestamps else
                "TERMINAL_EXIT_FILL"
            ),
            "timestamp": timestamp,
            "price": exit_prices[timestamp],
            "filled_quantity": _quantity_text(quantity),
            "remaining_quantity": _quantity_text(remaining),
            "terminal_reason": replay.get("exit_reason") if timestamp == terminal_ts else None,
        })
    if filled_quantity is None or sum(exact_exit_quantities.values(), Decimal(0)) != filled_quantity:
        blockers.append("LIFECYCLE_EXIT_QUANTITY_NOT_RECONCILED")
    if remaining != 0:
        blockers.append("LIFECYCLE_FINAL_RESIDUAL_NOT_ZERO")
    if not exit_events or terminal_ts is None or exit_events[-1]["timestamp"] != terminal_ts:
        blockers.append("LIFECYCLE_TERMINAL_EXIT_EVENT_MISSING")
    if blockers:
        return None, blockers

    body = {
        "schema": "derived_shadow_terminal_lifecycle_v1",
        "status": "COMPLETE",
        "authority": (
            "DECLARED_SIMULATION_CONDITIONAL" if conditional else
            "DECLARED_SIMULATION" if cost_treatment.get("economics_evidence_basis") == "DECLARED_SIMULATION" else
            "SIMULATED_CONSERVATIVE_BBO_DEPTH"
        ),
        "scope": "ENTRY_PLUS_SINGLE_POSITION_EXIT_AFTER_LAST_ACCEPTED_ENTRY_FILL",
        "scope_limitations": [
            "PATH_STARTS_AFTER_LAST_ACCEPTED_ENTRY_FILL",
            "PROTECTIVE_EXITS_INTERLEAVED_WITH_STAGGERED_ENTRY_FILLS_NOT_MODELED",
            "ONE_BASELINE_FILLED_POSITION",
        ],
        "bindings": bindings,
        "bindings_sha256": _sha(bindings),
        "entry": {
            "classification": str(entry_receipt.get("final_classification") or "").upper(),
            "requested_quantity": _quantity_text(requested_quantity) if requested_quantity is not None else None,
            "filled_quantity": _quantity_text(filled_quantity),
            "unfilled_quantity": _quantity_text(unfilled_quantity) if unfilled_quantity is not None else None,
            "requested_quantity_coverage": (
                "PRESENT" if requested_quantity is not None else "UNAVAILABLE_IN_SOURCE_RECEIPT"
            ),
            "events": entry_events,
        },
        "exit": {
            "events": exit_events,
            "final_residual_quantity": "0",
            "terminal_reason": replay.get("exit_reason"),
        },
        "cost_treatment": dict(cost_treatment),
        "maturity": {
            "status": "TERMINAL_WITH_COMPLETE_REQUIRED_HORIZON_EVIDENCE",
            "required_horizon_end_ts": horizon,
            "terminal_exit_ts": terminal_ts,
        },
        "coverage": {
            "path_start_basis": coverage.get("path_start_basis"),
            "path_end_basis": coverage.get("path_end_basis"),
            "sampling_interval_sec": coverage.get("sampling_interval_sec"),
            "first_sample_offset_sec": coverage.get("first_sample_offset_sec"),
            "required_horizon_complete": True,
        },
    }
    body["lifecycle_sha256"] = _sha(body)
    return body, []


def _signed(mapping: Any, *, schema: str, label: str) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(mapping, Mapping):
        return {}, [f"{label}_MISSING"]
    value = dict(mapping)
    blockers = []
    if value.get("schema") != schema:
        blockers.append(f"{label}_SCHEMA_INVALID")
    signature = str(value.get("signature") or "")
    unsigned = {key: item for key, item in value.items() if key != "signature"}
    if signature != stable_hash(label.lower().replace("_", "-"), unsigned):
        blockers.append(f"{label}_SIGNATURE_INVALID")
    return value, blockers


def _unknown(generation: Mapping[str, Any], blockers: Sequence[str], **extra: Any) -> dict[str, Any]:
    body = {
        "schema": SCHEMA,
        "status": "UNKNOWN",
        "generation": dict(generation) if isinstance(generation, Mapping) else {},
        "blockers": sorted(set(str(item) for item in blockers if item)),
        "profitability_supported": False,
        "ranking_eligible": False,
        "execution_support_status": "UNKNOWN",
        "qualification_status": "NOT_EVALUATED",
        "net_pnl_usd": None,
        "simulation_model": SIMULATION_MODEL,
        **extra,
    }
    body["receipt_sha256"] = _sha(body)
    return body


def evaluate_shadow_terminal(
    *, generation: Mapping[str, Any], entry_receipt: Mapping[str, Any],
    entry_receipt_sha256: str, future_path_rows: Sequence[Mapping[str, Any]],
    future_path_sha256: str, required_horizon_end_ts: Any,
    policy_spec: Mapping[str, Any], policy_signature: str,
    position_context: Mapping[str, Any], cost_model: Mapping[str, Any],
    coverage_policy: Mapping[str, Any],
    source_segment_receipts: Sequence[Mapping[str, Any]],
    source_segment_payloads: Sequence[bytes],
    lifecycle_bindings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay one signed policy on a hash-bound, complete executable path."""
    return _evaluate_shadow_terminal(**locals(), conditional=False)


def evaluate_conditional_shadow_terminal(**kwargs) -> dict[str, Any]:
    """Explicit venue-acceptance-conditional replay, never execution qualification."""
    kwargs.setdefault("lifecycle_bindings", None)
    body = _evaluate_shadow_terminal(**kwargs, conditional=True)
    body.pop("receipt_sha256", None)
    body.update(schema="generation_bound_conditional_shadow_terminal_v1",
        evidence_basis="DECLARED_SIMULATION_CONDITIONAL",
        economics_evidence_basis="DECLARED_SIMULATION_CONDITIONAL",
        qualification_eligible=False, live_qualification=False, venue_acceptance="UNKNOWN",
        ranking_eligible=False, profitability_supported=False,
        conditional_profitability_supported=body.get("status") == "COMPLETE",
        execution_support_status="CONDITIONAL_SIMULATION_ONLY",
        min_notional_treatment="UNMODELED_VENUE_ACCEPTANCE_CONDITIONAL",
        simulation_model=body.get("simulation_model", SIMULATION_MODEL) + ":CONDITIONAL_VENUE_ACCEPTANCE")
    body["receipt_sha256"] = _sha(body)
    return body


def _evaluate_shadow_terminal(*, generation, entry_receipt, entry_receipt_sha256,
        future_path_rows, future_path_sha256, required_horizon_end_ts, policy_spec,
        policy_signature, position_context, cost_model, coverage_policy,
        source_segment_receipts, source_segment_payloads, lifecycle_bindings, conditional):
    blockers: list[str] = []
    normalized_generation = {}
    for field in GENERATION_FIELDS:
        raw = generation.get(field) if isinstance(generation, Mapping) else None
        if isinstance(raw, (bool, Mapping, list, tuple, set)):
            blockers.append(f"GENERATION_INVALID:{field}")
            continue
        value = str(raw or "").strip()
        if isinstance(raw, float) and not math.isfinite(raw):
            blockers.append(f"GENERATION_INVALID:{field}")
            continue
        if not value or value.upper() in {"UNKNOWN", "UNAVAILABLE", "NONE", "NULL", "MISSING"}:
            blockers.append(f"GENERATION_MISSING:{field}")
            continue
        normalized_generation[field] = value
    if not isinstance(entry_receipt, Mapping):
        return _unknown(normalized_generation, [*blockers, "ENTRY_RECEIPT_MISSING"])
    if conditional:
        if (entry_receipt.get("schema") != "conditional_limit_fill_receipt_v1"
                or entry_receipt.get("evidence_basis") != "DECLARED_SIMULATION_CONDITIONAL"
                or entry_receipt.get("qualification_eligible") is not False
                or entry_receipt.get("venue_acceptance") != "UNKNOWN"):
            blockers.append("CONDITIONAL_ENTRY_AUTHORITY_INVALID")
    elif (entry_receipt.get("schema") == "conditional_limit_fill_receipt_v1"
            or entry_receipt.get("evidence_basis") == "DECLARED_SIMULATION_CONDITIONAL"):
        blockers.append("CONDITIONAL_ENTRY_NOT_STRICT")
    if str(entry_receipt_sha256 or "").lower() != _sha(entry_receipt):
        blockers.append("ENTRY_RECEIPT_SHA256_MISMATCH")
    classification = str(entry_receipt.get("final_classification") or "").upper()
    if entry_receipt.get("supported") is not True or classification not in {"FULL_FILL", "PARTIAL_FILL"}:
        blockers.append("ENTRY_RECEIPT_NOT_SUPPORTED_FILL")
    fill_ts = _finite(entry_receipt.get("trigger_bucket_ts"))
    fill_price = _finite(entry_receipt.get("fill_price"), positive=True)
    filled_qty = _finite(entry_receipt.get("filled_qty"), positive=True)
    horizon = _finite(required_horizon_end_ts)
    if fill_ts is None or fill_price is None or filled_qty is None:
        blockers.append("ENTRY_RECEIPT_FILL_FIELDS_INVALID")
    accepted_attempts = [
        attempt for attempt in entry_receipt.get("quantity_attempts") or []
        if isinstance(attempt, Mapping) and attempt.get("accepted") is True
    ]
    fill_events = []
    for attempt in accepted_attempts:
        event_qty = _finite(attempt.get("rounded_executable_quantity"), positive=True)
        event_price = _finite(attempt.get("execution_price"), positive=True)
        event_ts = _finite(attempt.get("trigger_bucket_ts"))
        if event_qty is None or event_price is None or event_ts is None or int(event_ts) != event_ts:
            blockers.append("ENTRY_ACCEPTED_FILL_EVENT_INVALID")
            continue
        fill_events.append((event_ts, event_price, event_qty))
    if not fill_events:
        blockers.append("ENTRY_ACCEPTED_FILL_EVENTS_MISSING")
    elif filled_qty is not None:
        event_qty_total = sum(event[2] for event in fill_events)
        if abs(event_qty_total - filled_qty) > 1e-12:
            blockers.append("ENTRY_ACCEPTED_FILL_QUANTITY_MISMATCH")
        else:
            fill_price = sum(price * qty for _ts, price, qty in fill_events) / event_qty_total
            fill_ts = max(ts for ts, _price, _qty in fill_events)
    if str(future_path_sha256 or "").lower() != _sha(list(future_path_rows or [])):
        blockers.append("FUTURE_PATH_SHA256_MISMATCH")
    if not isinstance(policy_spec, Mapping) or str(policy_signature or "") != canonical_hash("v3-policy", policy_spec):
        blockers.append("POLICY_SIGNATURE_INVALID")

    context, defects = _signed(
        position_context, schema="conservative_shadow_position_context_v1",
        label="CONSERVATIVE_SHADOW_POSITION_CONTEXT",
    )
    blockers.extend(defects)
    costs, defects = _signed(
        cost_model, schema="conservative_shadow_cost_model_v1",
        label="CONSERVATIVE_SHADOW_COST_MODEL",
    )
    blockers.extend(defects)
    coverage, defects = _signed(
        coverage_policy, schema="shadow_path_coverage_policy_v1",
        label="SHADOW_PATH_COVERAGE_POLICY",
    )
    blockers.extend(defects)
    atr = _finite(context.get("atr_pct_at_fill"), positive=True)
    leverage = _finite(context.get("leverage"), positive=True)
    margin = _finite(context.get("margin_usd"), positive=True)
    if None in (atr, leverage, margin) or not str(context.get("position_context_id") or ""):
        blockers.append("POSITION_CONTEXT_FIELDS_INVALID")
    if context.get("generation") != normalized_generation:
        blockers.append("POSITION_CONTEXT_GENERATION_MISMATCH")
    for label, signed_input in (
        ("POSITION_CONTEXT", context), ("COST_MODEL", costs), ("COVERAGE_POLICY", coverage),
    ):
        for field, expected in (
            ("entry_receipt_sha256", str(entry_receipt_sha256 or "")),
            ("future_path_sha256", str(future_path_sha256 or "")),
            ("policy_signature", str(policy_signature or "")),
        ):
            if signed_input.get(field) != expected:
                blockers.append(f"{label}_{field.upper()}_MISMATCH")
        if signed_input.get("generation") != normalized_generation:
            blockers.append(f"{label}_GENERATION_MISMATCH")
    try:
        raw_context_qty = (
            Decimal(str(context.get("margin_usd")))
            * Decimal(str(context.get("leverage")))
            / Decimal(str(fill_price))
        )
        validator = validate_signed_quantity_constraints
        if conditional:
            from research.conditional_quantity_execution import validate_conditional_constraints
            validator = validate_conditional_constraints
        constraints, constraint_reasons = validator(
            entry_receipt.get("quantity_constraints"), symbol=entry_receipt.get("symbol"),
        )
        if constraint_reasons or constraints is None:
            blockers.extend(f"POSITION_CONTEXT_{reason}" for reason in constraint_reasons)
        else:
            step = Decimal(constraints["quantity_step"])
            context_qty = (raw_context_qty / step).to_integral_value(rounding=ROUND_DOWN) * step
            if context_qty != Decimal(str(entry_receipt.get("filled_qty"))):
                blockers.append("POSITION_CONTEXT_QUANTITY_MISMATCH")
    except (InvalidOperation, ArithmeticError, TypeError, ValueError):
        blockers.append("POSITION_CONTEXT_QUANTITY_MISMATCH")
    cost_fields = {}
    declared_rates = costs.get("calculation_mode") == "DECLARED_EXECUTION_RATE_MODEL_V1"
    if declared_rates:
        from research.declared_shadow_model import validate_contract
        try:
            validate_contract(costs.get("declared_contract"), normalized_generation)
            if costs.get("cost_provenance") != ("DECLARED_SIMULATION_CONDITIONAL" if conditional else "DECLARED_SIMULATION"):
                blockers.append("DECLARED_COST_PROVENANCE_INVALID")
        except ValueError as exc:
            blockers.append(str(exc))
    else:
        if conditional:
            blockers.append("CONDITIONAL_DECLARED_COST_MODEL_REQUIRED")
        for field in ("trading_fees_usd", "funding_usd", "latency_cost_usd"):
            cost_fields[field] = _finite(costs.get(field))
            if cost_fields[field] is None or (field != "funding_usd" and cost_fields[field] < 0):
                blockers.append(f"COST_MODEL_FIELD_INVALID:{field}")
    if not str(costs.get("cost_model_id") or ""):
        blockers.append("COST_MODEL_ID_MISSING")
    if costs.get("spread_slippage_basis") != "EMBEDDED_IN_ENTRY_AND_EXECUTABLE_EXIT_PRICES":
        blockers.append("COST_MODEL_SPREAD_SLIPPAGE_BASIS_INVALID")

    sampling_interval = _finite(coverage.get("sampling_interval_sec"), positive=True)
    first_sample_offset = _finite(coverage.get("first_sample_offset_sec"), positive=True)
    sampling_valid = sampling_interval is not None and int(sampling_interval) == sampling_interval
    offset_valid = (first_sample_offset is not None and int(first_sample_offset) == first_sample_offset
                    and sampling_interval is not None and first_sample_offset <= sampling_interval)
    if (not sampling_valid or not offset_valid
            or coverage.get("require_fresh_bbo") is not True
            or coverage.get("require_trade_fields") is not True
            or coverage.get("path_start_basis") != "FIRST_COMPLETE_SAMPLE_AFTER_ENTRY_FILL"
            or coverage.get("path_end_basis") != "DECLARED_REQUIRED_HORIZON"
            or coverage.get("row_schema") != "market_microstructure_1s_v1"
            or coverage.get("source_segment_schema") != "market_segment_v3"):
        blockers.append("COVERAGE_POLICY_FIELDS_INVALID")
        sampling_interval = None
        first_sample_offset = None
    segment_hashes = []
    for receipt in source_segment_receipts or []:
        digest = str(receipt.get("sha256") or "").lower() if isinstance(receipt, Mapping) else ""
        receipt_unsigned = (
            {key: value for key, value in receipt.items() if key != "receipt_sha256"}
            if isinstance(receipt, Mapping) else {}
        )
        if (not isinstance(receipt, Mapping) or receipt.get("schema") != "market_segment_v3"
                or receipt.get("verification_status") != "CHECKSUM_VERIFIED"
                or not str(receipt.get("verifier_version") or "")
                or receipt.get("generation") != normalized_generation
                or receipt.get("receipt_sha256") != _sha(receipt_unsigned)
                or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
            blockers.append("SOURCE_SEGMENT_RECEIPT_INVALID")
            continue
        segment_hashes.append(digest)
    if not segment_hashes:
        blockers.append("SOURCE_SEGMENT_RECEIPTS_MISSING")
    verified_receipts = {
        str(receipt.get("sha256") or "").lower(): receipt
        for receipt in source_segment_receipts or [] if isinstance(receipt, Mapping)
    }
    derived_rows: list[Mapping[str, Any]] = []
    payload_hashes = []
    for payload in source_segment_payloads or []:
        if not isinstance(payload, bytes):
            blockers.append("SOURCE_SEGMENT_PAYLOAD_NOT_BYTES")
            continue
        digest = hashlib.sha256(payload).hexdigest()
        payload_hashes.append(digest)
        if digest not in verified_receipts:
            blockers.append("SOURCE_SEGMENT_PAYLOAD_RECEIPT_MISSING")
            continue
        try:
            envelope = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError):
            blockers.append("SOURCE_SEGMENT_PAYLOAD_INVALID_JSON")
            continue
        if not isinstance(envelope, Mapping) or envelope.get("schema") != "market_segment_v3":
            blockers.append("SOURCE_SEGMENT_PAYLOAD_SCHEMA_INVALID")
            continue
        rows = envelope.get("rows")
        if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
            blockers.append("SOURCE_SEGMENT_PAYLOAD_ROWS_INVALID")
            continue
        derived_rows.extend(rows)
    if sorted(payload_hashes) != sorted(set(segment_hashes)):
        blockers.append("SOURCE_SEGMENT_PAYLOAD_SET_MISMATCH")
    replay_start_ts = fill_ts + first_sample_offset if fill_ts is not None and first_sample_offset else None
    derived_window_rows = []
    if replay_start_ts is not None and horizon is not None:
        for row in derived_rows:
            row_ts = _finite(row.get("bucket_ts")) if isinstance(row, Mapping) else None
            if row_ts is not None and replay_start_ts <= row_ts <= horizon:
                derived_window_rows.append(row)
    if canonical_json(list(future_path_rows or [])) != canonical_json(derived_window_rows):
        blockers.append("FUTURE_PATH_NOT_DERIVED_FROM_VERIFIED_SEGMENTS")

    direction = str(entry_receipt.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        blockers.append("ENTRY_DIRECTION_INVALID")
    normalized_rows: list[dict[str, Any]] = []
    timestamps: list[int] = []
    for raw in future_path_rows or []:
        if not isinstance(raw, Mapping) or raw.get("schema") != "market_microstructure_1s_v1":
            blockers.append("FUTURE_PATH_ROW_SCHEMA_INVALID")
            continue
        ts = _finite(raw.get("bucket_ts"))
        bid = _finite(raw.get("bid"), positive=True)
        ask = _finite(raw.get("ask"), positive=True)
        bid_qty = _finite(raw.get("bid_qty"))
        ask_qty = _finite(raw.get("ask_qty"))
        if (ts is None or int(ts) != ts or raw.get("fresh") is not True
                or raw.get("valid_bbo") is not True or bid is None or ask is None
                or bid_qty is None or ask_qty is None or bid_qty < 0 or ask_qty < 0
                or _finite(raw.get("trade_count")) is None or _finite(raw.get("trade_count")) < 0
                or _finite(raw.get("buy_qty")) is None or _finite(raw.get("buy_qty")) < 0
                or _finite(raw.get("sell_qty")) is None or _finite(raw.get("sell_qty")) < 0
                or ask < bid):
            blockers.append("FUTURE_PATH_ROW_NOT_EXECUTABLE")
            continue
        timestamps.append(int(ts))
        normalized_rows.append({"ts": ts, "price": bid if direction == "LONG" else ask,
                                "exit_visible_qty": bid_qty if direction == "LONG" else ask_qty})
    if (replay_start_ts is not None and int(replay_start_ts) == replay_start_ts and horizon is not None
            and int(horizon) == horizon and sampling_interval is not None):
        span = int(horizon) - int(replay_start_ts)
        step = int(sampling_interval)
        divisible = span >= 0 and span % step == 0
        expected_count = span // step + 1 if divisible else None
        monotonic = all(
            current - previous == step
            for previous, current in zip(timestamps, timestamps[1:])
        )
        if (not divisible or len(timestamps) != expected_count
                or not timestamps or timestamps[0] != int(replay_start_ts)
                or timestamps[-1] != int(horizon) or not monotonic):
            blockers.append("FUTURE_PATH_REQUIRED_HORIZON_INCOMPLETE")
    else:
        blockers.append("REQUIRED_HORIZON_INVALID")
    if blockers:
        return _unknown(normalized_generation, blockers,
                        entry_receipt_sha256=str(entry_receipt_sha256 or ""),
                        future_path_sha256=str(future_path_sha256 or ""),
                        normalized_future_path_sha256=_sha(normalized_rows),
                        source_segment_hashes=sorted(set(segment_hashes)),
                        policy_signature=str(policy_signature or ""))

    # The verified path deliberately begins at the first complete sample after
    # the last entry fill, but time-based policy ages are measured from the
    # actual last-fill timestamp.  Using replay_start_ts as the policy clock
    # delays thesis/time stops by one sampling offset.
    prepared = prepare_replay_price_path(normalized_rows, fill_ts=fill_ts)
    effective_filled_margin = filled_qty * fill_price / leverage
    replay = replay_protected_policy(
        normalized_rows, direction=direction, entry_price=fill_price, fill_ts=fill_ts,
        atr_pct_at_fill=atr, leverage=leverage, margin_usd=effective_filled_margin,
        policy_spec=policy_spec, funding_usd=0.0, slippage_usd=0.0,
        prepared_price_path=prepared, collect_trace=True,
    )
    if replay.get("status") != "COMPLETE" or replay.get("ranking_eligible") is not True:
        return _unknown(normalized_generation,
                        [f"EXIT_REPLAY_{reason}" for reason in replay.get("reasons") or [replay.get("status")]],
                        entry_receipt_sha256=entry_receipt_sha256,
                        future_path_sha256=future_path_sha256,
                        normalized_future_path_sha256=_sha(normalized_rows),
                        source_segment_hashes=sorted(set(segment_hashes)),
                        policy_signature=policy_signature)

    depth_required: dict[int, float] = defaultdict(float)
    for trace in replay.get("trace") or []:
        for event in trace.get("partial_exits") or []:
            depth_required[int(float(trace["ts"]))] += float(event["fraction"]) * filled_qty
    depth_required[int(float(replay["exit_ts"]))] += float(replay["remaining_fraction_at_terminal"]) * filled_qty
    from research.declared_shadow_model import exact_replay_exit_quantities
    try:
        exact_exit_quantities = exact_replay_exit_quantities(
            replay, policy_spec, entry_receipt["filled_qty"]
        )
    except (ValueError, KeyError, InvalidOperation) as exc:
        return _unknown(normalized_generation, [str(exc)])
    depth_required = {ts: float(qty) for ts, qty in exact_exit_quantities.items()}
    visible = {int(row["ts"]): float(row["exit_visible_qty"]) for row in normalized_rows}
    if any(visible.get(ts, -1.0) + 1e-12 < qty for ts, qty in depth_required.items()):
        return _unknown(normalized_generation, ["EXIT_VISIBLE_DEPTH_INSUFFICIENT"],
                        entry_receipt_sha256=entry_receipt_sha256,
                        future_path_sha256=future_path_sha256,
                        normalized_future_path_sha256=_sha(normalized_rows),
                        source_segment_hashes=sorted(set(segment_hashes)), policy_signature=policy_signature,
                        exit_depth_required_by_ts=dict(sorted(depth_required.items())))

    declared_economics = {}
    if declared_rates:
        from research.declared_shadow_model import calculate_declared_costs
        exit_prices = {int(row["ts"]): float(row["price"]) for row in normalized_rows}
        exit_events = [(ts, exit_prices[ts], qty) for ts, qty in sorted(exact_exit_quantities.items()) if qty > 0]
        try:
            declared_economics = calculate_declared_costs(
                costs["declared_contract"], generation=normalized_generation,
                entry_events=fill_events, exit_events=exit_events, direction=direction)
        except ValueError as exc:
            return _unknown(normalized_generation, [str(exc)])
        cost_fields = {key: declared_economics[key] for key in
                       ("trading_fees_usd", "funding_usd", "latency_cost_usd")}
    total_cost = sum(float(value) for value in cost_fields.values())
    gross = float(replay["gross_pnl_usd"])
    cost_treatment = {
        "economics_evidence_basis": declared_economics.get("economics_evidence_basis") or (
            "DECLARED_SIMULATION_CONDITIONAL" if conditional else "SIGNED_INPUT_COST_MODEL"
        ),
        "trading_fees_usd": cost_fields["trading_fees_usd"],
        "funding_usd": cost_fields["funding_usd"],
        "latency_cost_usd": cost_fields["latency_cost_usd"],
        "spread_slippage_usd": 0.0,
        "spread_slippage_basis": "EMBEDDED_IN_ENTRY_AND_EXECUTABLE_EXIT_PRICES",
        "total_cost_usd": round(total_cost, 8),
    }
    lifecycle_trace, lifecycle_blockers = _derived_lifecycle_trace(
        lifecycle_bindings=lifecycle_bindings,
        entry_receipt=entry_receipt,
        entry_receipt_sha256=entry_receipt_sha256,
        fill_events=fill_events,
        exact_exit_quantities=exact_exit_quantities,
        replay=replay,
        normalized_rows=normalized_rows,
        policy_spec=policy_spec,
        policy_signature=policy_signature,
        cost_treatment=cost_treatment,
        coverage=coverage,
        horizon=horizon,
        conditional=conditional,
    )
    if lifecycle_blockers:
        return _unknown(
            normalized_generation, lifecycle_blockers,
            entry_receipt_sha256=entry_receipt_sha256,
            future_path_sha256=future_path_sha256,
            normalized_future_path_sha256=_sha(normalized_rows),
            source_segment_hashes=sorted(set(segment_hashes)),
            policy_signature=policy_signature,
            lifecycle_trace_status="UNKNOWN",
        )
    body = {
        "schema": SCHEMA, "status": "COMPLETE", "generation": normalized_generation,
        "blockers": [], "profitability_supported": True, "ranking_eligible": False,
        "execution_support_status": "SUPPORTED_CONSERVATIVE_SHADOW_ONLY",
        "qualification_status": "NOT_EVALUATED",
        "entry_receipt_sha256": entry_receipt_sha256,
        "future_path_sha256": future_path_sha256,
        "normalized_future_path_sha256": _sha(normalized_rows),
        "source_segment_hashes": sorted(set(segment_hashes)),
        "source_segment_authenticity_basis": "CALLER_SUPPLIED_CHECKSUM_VERIFIED_RECEIPTS",
        "coverage_policy_signature": coverage["signature"],
        "policy_signature": policy_signature,
        "position_context_id": context["position_context_id"],
        "position_context_signature": context["signature"],
        "cost_model_id": costs["cost_model_id"], "cost_model_signature": costs["signature"],
        # The same fee scenario does not make held signal ATR comparable to
        # measured fill-time ATR. Existing cohort grouping uses this identity.
        "simulation_model": (SIMULATION_MODEL + ":DECLARED_SIGNAL_ATR_HOLD_CONSTANT"
                             if context.get("atr_basis") == "DECLARED_SIGNAL_ATR_HOLD_CONSTANT" else SIMULATION_MODEL)
                            + (":DECLARED_DELAYED_SUBMISSION:" + str(context.get('timing_model_sha256'))
                               if context.get('timing_basis') == 'DECLARED_DELAYED_SUBMISSION_REPLAY' else ''),
        "atr_treatment": context.get("atr_basis"), "filled_qty": filled_qty,
        "entry_fill_event_count": len(fill_events),
        "entry_vwap": round(fill_price, 12),
        "entry_complete_ts": fill_ts,
        "replay_start_ts": replay_start_ts,
        "declared_position_margin_usd": margin,
        "effective_filled_margin_usd": round(effective_filled_margin, 12),
        "exit_depth_required_by_ts": dict(sorted(depth_required.items())),
        "gross_pnl_usd": round(gross, 8), **cost_fields,
        "spread_slippage_usd": 0.0,
        "spread_slippage_basis": "EMBEDDED_IN_ENTRY_AND_EXECUTABLE_EXIT_PRICES",
        "total_cost_usd": round(total_cost, 8),
        "net_pnl_usd": round(gross - total_cost, 8),
        "exit_ts": replay["exit_ts"], "exit_price": replay["exit_price"],
        "exit_reason": replay["exit_reason"], "partial_exit_count": replay["partial_exit_count"],
        "mfe_pct": replay["mfe_pct"], "mae_pct": replay["mae_pct"],
        "required_horizon_end_ts": horizon,
        "lifecycle_trace": lifecycle_trace,
        "lifecycle_trace_sha256": lifecycle_trace["lifecycle_sha256"],
        **declared_economics,
    }
    body["receipt_sha256"] = _sha(body)
    return body
