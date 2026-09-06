"""Explicit, opt-in scenario economics; never invents execution observations."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Mapping
from decimal import Decimal, InvalidOperation

from research.policy_evidence_schema import canonical_json, stable_hash

SCHEMA = "declared_shadow_model_v1"
MODE = "DECLARED_EXECUTION_RATE_MODEL_V1"
LABEL = "declared-shadow-model"
GENERATION_FIELDS = ("manifest_entry_hash", "epoch_id", "source_revision", "deployed_revision",
                     "tile_config_signature", "analyzer_revision", "evaluator_version", "generation_key")


def sha(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _number(value, *, minimum=None, maximum=None):
    if isinstance(value, bool): raise ValueError("DECLARED_MODEL_NUMBER_INVALID")
    try: result = float(value)
    except (TypeError, ValueError, OverflowError) as exc: raise ValueError("DECLARED_MODEL_NUMBER_INVALID") from exc
    if not math.isfinite(result) or minimum is not None and result < minimum or maximum is not None and result > maximum:
        raise ValueError("DECLARED_MODEL_NUMBER_INVALID")
    return result


def validate_contract(contract, generation):
    if not isinstance(contract, Mapping): raise ValueError("DECLARED_MODEL_MISSING")
    body = {key: value for key, value in contract.items() if key != "signature"}
    if (contract.get("schema") != SCHEMA or contract.get("evidence_basis") != "DECLARED_SIMULATION"
            or contract.get("signature") != stable_hash(LABEL, body)):
        raise ValueError("DECLARED_MODEL_SIGNATURE_OR_SCHEMA_INVALID")
    if (contract.get("generation") != generation
            or any(not isinstance(generation.get(k), str) or not generation[k] for k in GENERATION_FIELDS)):
        raise ValueError("DECLARED_MODEL_GENERATION_MISMATCH")
    for field in ("model_id", "provenance"):
        if not isinstance(contract.get(field), str) or not contract[field].strip():
            raise ValueError("DECLARED_MODEL_PROVENANCE_MISSING")
    config_hash = contract.get("source_config_sha256")
    if not isinstance(config_hash, str) or len(config_hash) != 64 or any(c not in "0123456789abcdef" for c in config_hash):
        raise ValueError("DECLARED_MODEL_CONFIG_HASH_INVALID")
    rates = contract.get("fee_rates")
    if not isinstance(rates, Mapping): raise ValueError("DECLARED_MODEL_FEES_MISSING")
    _number(rates.get("entry"), minimum=0, maximum=1)
    _number(rates.get("exit"), minimum=0, maximum=1)
    funding = contract.get("funding")
    if not isinstance(funding, Mapping): raise ValueError("DECLARED_MODEL_FUNDING_MISSING")
    if funding.get("treatment") == "ZERO_SCENARIO":
        if "rate_per_hour" in funding and _number(funding["rate_per_hour"]) != 0:
            raise ValueError("DECLARED_MODEL_FUNDING_CONFLICT")
    elif funding.get("treatment") == "CONSTANT_ENTRY_NOTIONAL_RATE":
        _number(funding.get("rate_per_hour"), minimum=-1, maximum=1)
    else: raise ValueError("DECLARED_MODEL_FUNDING_UNSUPPORTED")
    latency = contract.get("latency")
    if (not isinstance(latency, Mapping) or latency.get("treatment") != "PRESERVE_BASELINE_TIMING"
            or _number(latency.get("additional_latency_sec"), minimum=0) != 0):
        raise ValueError("DECLARED_MODEL_LATENCY_UNSUPPORTED")
    return dict(contract)


def load_declared_shadow_model(path, *, expected_sha256, expected_generation):
    """Explicit caller-selected file and checksum; no implicit default activation."""
    file = Path(path)
    if file.stat().st_size > 2 * 1024 * 1024: raise ValueError("DECLARED_MODEL_FILE_LIMIT")
    with file.open("rb") as stream: raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("DECLARED_MODEL_FILE_HASH_MISMATCH")
    return validate_contract(json.loads(raw), expected_generation)


def _baseline_context(entry, generation):
    context = entry.get("execution_model_context")
    if not isinstance(context, Mapping): raise ValueError("BASELINE_EXECUTION_MODEL_CONTEXT_MISSING")
    body = {key: value for key, value in context.items() if key != "signature"}
    receipt = entry.get("conservative_receipt")
    if (context.get("schema") != "baseline_execution_model_context_v1"
            or context.get("signature") != stable_hash("baseline-execution-model-context", body)
            or context.get("generation") != generation
            or context.get("entry_receipt_sha256") != sha(receipt)):
        raise ValueError("BASELINE_EXECUTION_MODEL_CONTEXT_BINDING_INVALID")
    # The signed baseline is authority for leverage, ATR, coverage and timing;
    # the declared cost scenario is not allowed to create or replace them.
    for key in ("atr_pct_at_fill", "leverage", "margin_usd"):
        if _number(context.get(key), minimum=0) <= 0: raise ValueError("BASELINE_POSITION_INPUT_MISSING")
    if context.get("atr_basis") != "EXPLICIT_AT_FILL_OBSERVATION":
        if (context.get("atr_basis") != "DECLARED_SIGNAL_ATR_HOLD_CONSTANT"
                or context.get("context_evidence_basis") != "DECLARED_SIMULATION"
                or context.get("measured_fill_atr") is not None
                or not isinstance(context.get("research_context_declaration_sha256"), str)
                or len(context["research_context_declaration_sha256"]) != 64
                or not isinstance(context.get("directional_capture_signature"), str)
                or not context["directional_capture_signature"].startswith("directional-entry-capture-")):
            raise ValueError("BASELINE_ATR_BASIS_UNSUPPORTED")
    if context.get('timing_basis') == 'DECLARED_DELAYED_SUBMISSION_REPLAY':
        from research.latency_schedule_replay import _strict_sha256
        timing=context.get('timing_declaration')
        replay=context.get('delayed_replay_receipt')
        if (not isinstance(timing,Mapping) or not isinstance(replay,Mapping)
                or context.get('timing_declaration_sha256') != sha(timing)
                or context.get('timing_model_sha256') != sha({key:timing.get(key) for key in
                    ('schema','delay_sec','ordering_treatment','evidence_basis')})
                or replay.get('status') != 'ENTRY_REPLAY_SUPPORTED'
                or context.get('delayed_replay_receipt_sha256') != replay.get('replay_receipt_sha256')
                or replay.get('replay_receipt_sha256') != _strict_sha256({k:v for k,v in replay.items() if k!='replay_receipt_sha256'})
                or context.get('delayed_replay_input_sha256') != replay.get('replay_input_sha256')
                or context.get('derived_schedule_sha256') != receipt.get('schedule_sha256')
                or {**(replay.get('entry_receipt') or {}),'symbol':receipt.get('symbol')} != receipt):
            raise ValueError('BASELINE_DELAYED_REPLAY_BINDING_INVALID')
    elif context.get('timing_basis') != 'BASELINE_EXECUTION_TIMESTAMPS_UNCHANGED':
        raise ValueError('BASELINE_LATENCY_COMPATIBILITY_MISSING')
    if not isinstance(context.get("latency_provenance"), str) or not context["latency_provenance"].strip():
        raise ValueError("BASELINE_LATENCY_COMPATIBILITY_MISSING")
    hashes = context.get("source_evidence_sha256")
    if (not isinstance(hashes, list) or not hashes
            or any(not isinstance(h, str) or len(h) != 64 or any(c not in "0123456789abcdef" for c in h) for h in hashes)):
        raise ValueError("BASELINE_SOURCE_EVIDENCE_BINDING_MISSING")
    if not isinstance(receipt, Mapping) or not isinstance(receipt.get("quantity_constraints"), Mapping):
        raise ValueError("BASELINE_QUANTITY_EVIDENCE_MISSING")
    return dict(context)


def build_declared_research_model(contract, *, baseline_report, policy_candidates, expected_generation):
    """Validate once per baseline; composite binding stays lazy in report fanout."""
    contract = validate_contract(contract, expected_generation)
    if baseline_report.get("generation") != expected_generation:
        raise ValueError("DECLARED_BASELINE_GENERATION_MISMATCH")
    contexts = []
    seen_contexts = {}
    for episode in baseline_report.get("episode_receipts") or []:
        for entry in episode.get("results") or []:
            if entry.get("supported") is not True or entry.get("outcome_state") not in {"FULL_FILL", "PARTIAL_FILL"}: continue
            key = {"episode_id": episode.get("episode_id"), "opportunity_id": episode.get("opportunity_id"),
                   "baseline_id": entry.get("baseline_id")}
            try:
                context = _baseline_context(entry, expected_generation)
                context = {k: v for k, v in context.items() if k not in
                           ("signature", "schema", "composite_policy_signature", "trading_fees_usd", "funding_usd", "latency_cost_usd")}
                context.update(key)
                context.update({"cost_model_id": contract["model_id"], "cost_provenance": "DECLARED_SIMULATION",
                                "calculation_mode": MODE, "declared_contract": contract,
                                "spread_slippage_basis": "EMBEDDED_IN_ENTRY_AND_EXECUTABLE_EXIT_PRICES"})
            except ValueError as exc:
                context = {**key, "input_blockers": [str(exc)], "calculation_mode": MODE}
            identity_key = tuple(key.values())
            if identity_key in seen_contexts:
                if seen_contexts[identity_key] != context: raise ValueError("DECLARED_CONTEXT_CONFLICT")
                continue
            seen_contexts[identity_key] = context
            contexts.append(context)
    body = {"schema": "conservative_shadow_research_model_v1", "generation": dict(expected_generation),
            "model_id": contract["model_id"], "provenance": "DECLARED_SIMULATION",
            "declared_contract_sha256": sha(contract), "declared_contract": contract,
            "context_binding_mode": "PER_BASELINE_LAZY_COMPOSITE", "contexts": contexts}
    return {**body, "signature": stable_hash("conservative-shadow-research-model", body)}


def calculate_declared_costs(contract, *, generation, entry_events, exit_events, direction):
    """Fees use each actual replay fill; scenario funding uses FIFO quantity-time.

    Funding is explicitly constant entry-notional rate, not a claim of measured
    venue funding. Positive rates charge longs and credit shorts. Latency adds
    no synthetic timing: baseline execution timestamps remain unchanged.
    """
    contract = validate_contract(contract, generation)
    if direction not in ("LONG", "SHORT"): raise ValueError("DECLARED_COST_DIRECTION_INVALID")
    entries = [(float(t), _number(p, minimum=0), _number(q, minimum=0)) for t, p, q in entry_events]
    exits = [(float(t), _number(p, minimum=0), _number(q, minimum=0)) for t, p, q in exit_events]
    # Decimal quantity accounting has no fixed absolute tolerance that could
    # forgive an entire small venue lot. Callers retain exact event quantities.
    entry_quantities = [Decimal(str(event[2])) for event in entry_events]
    exit_quantities = [Decimal(str(event[2])) for event in exit_events]
    if (not entries or not exits or any(not math.isfinite(t) or p <= 0 or q <= 0 for t, p, q in entries + exits)
            or sum(entry_quantities) != sum(exit_quantities)):
        raise ValueError("DECLARED_COST_QUANTITY_INVALID")
    entries = [(t, p, quantity) for (t, p, _), quantity in zip(entries, entry_quantities)]
    exits = [(t, p, quantity) for (t, p, _), quantity in zip(exits, exit_quantities)]
    entries.sort(); exits.sort()
    entry_notional = sum(p*float(q) for _, p, q in entries)
    exit_notional = sum(p*float(q) for _, p, q in exits)
    fees = entry_notional * float(contract["fee_rates"]["entry"]) + exit_notional * float(contract["fee_rates"]["exit"])
    lots = [[t, p, Decimal(str(q))] for t, p, q in entries]
    notional_hours = 0.0
    for exit_ts, _, quantity in exits:
        quantity = Decimal(str(quantity))
        for lot in lots:
            if quantity == 0: break
            amount = min(quantity, lot[2])
            if amount <= 0: continue
            if exit_ts < lot[0]: raise ValueError("DECLARED_COST_CAUSAL_TIMING_INVALID")
            notional_hours += float(amount) * lot[1] * (exit_ts-lot[0]) / 3600
            lot[2] -= amount; quantity -= amount
        if quantity != 0: raise ValueError("DECLARED_COST_QUANTITY_INVALID")
    funding = contract["funding"]
    funding_usd = 0.0 if funding["treatment"] == "ZERO_SCENARIO" else notional_hours * float(funding["rate_per_hour"]) * (1 if direction == "LONG" else -1)
    return {"trading_fees_usd": fees, "funding_usd": funding_usd, "latency_cost_usd": 0.0,
            "economics_evidence_basis": "DECLARED_SIMULATION", "declared_contract_sha256": sha(contract),
            "assumptions": {"fee_rates": contract["fee_rates"], "funding": funding,
                            "latency": contract["latency"], "source_config_sha256": contract["source_config_sha256"]},
            "entry_notional_usd": entry_notional, "exit_notional_usd": exit_notional,
            "funding_entry_notional_hours": notional_hours,
            "measured_trading_fees_usd": None, "measured_funding_usd": None,
            "measured_ack_latency_sec": None}


def exact_replay_exit_quantities(replay, policy_spec, filled_qty):
    """Recover quantities from signed policy fractions, not float products.

    Replay trace fractions are checked against each executed policy rung. The
    terminal remainder is derived only after that exact policy accounting;
    it is not a tolerance for missing observed or externally supplied lots.
    """
    quantity = Decimal(str(filled_qty))
    if not quantity.is_finite() or quantity <= 0: raise ValueError("DECLARED_COST_QUANTITY_INVALID")
    rungs = [(Decimal(str(rung[0])), Decimal(str(rung[1])))
             for rung in policy_spec["profit_protection"].get("partial_take_profits") or []]
    remaining = Decimal(1)
    used = set()
    result = {}
    allocated = Decimal(0)
    for trace in replay.get("trace") or []:
        for event in trace.get("partial_exits") or []:
            trigger = Decimal(str(event["trigger_atr_k"]))
            index = next((i for i, (value, _) in enumerate(rungs) if i not in used and value == trigger), None)
            if index is None: raise ValueError("DECLARED_EXIT_FRACTION_NOT_IN_POLICY")
            intended = min(rungs[index][1], remaining)
            actual = float(event["fraction"])
            expected = float(intended)
            if (not intended.is_finite() or intended <= 0 or not math.isfinite(actual)
                    or not math.isclose(actual, expected, rel_tol=0, abs_tol=4*math.ulp(expected))):
                raise ValueError("DECLARED_EXIT_FRACTION_MISMATCH")
            used.add(index)
            remaining -= intended
            part = quantity * intended
            allocated += part
            timestamp = int(trace["ts"])
            result[timestamp] = result.get(timestamp, Decimal(0)) + part
    reported_remaining = float(replay["remaining_fraction_at_terminal"])
    # replay_protected_policy documents/returns an eight-decimal projection;
    # compare that projection, then account using the exact signed fractions.
    if (remaining < 0 or remaining > 1 or not math.isfinite(reported_remaining)
            or reported_remaining != round(float(remaining), 8)):
        raise ValueError("DECLARED_TERMINAL_FRACTION_MISMATCH")
    terminal_quantity = quantity - allocated
    if terminal_quantity != quantity * remaining:
        raise ValueError("DECLARED_EXIT_QUANTITY_NOT_CLOSED")
    timestamp = int(replay["exit_ts"])
    result[timestamp] = result.get(timestamp, Decimal(0)) + terminal_quantity
    return result
