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
) -> dict[str, Any]:
    """Replay one signed policy on a hash-bound, complete executable path."""
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
        constraints, constraint_reasons = validate_signed_quantity_constraints(
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
            if costs.get("cost_provenance") != "DECLARED_SIMULATION":
                blockers.append("DECLARED_COST_PROVENANCE_INVALID")
        except ValueError as exc:
            blockers.append(str(exc))
    else:
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
    exact_exit_quantities = None
    if declared_rates:
        from research.declared_shadow_model import exact_replay_exit_quantities
        try:
            exact_exit_quantities = exact_replay_exit_quantities(replay, policy_spec, entry_receipt["filled_qty"])
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
        **declared_economics,
    }
    body["receipt_sha256"] = _sha(body)
    return body
