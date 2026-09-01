"""Pure, fail-closed construction of lifecycle bundle completion receipts.

This module does not write evidence and does not infer outcomes.  Runtime code
must pass exact terminal proofs collected by the existing bridge.  A missing or
ambiguous proof produces blockers and no receipt, so it cannot make a lifecycle
bundle-ready accidentally.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping

from research_v3_contract import canonical_json
from lifecycle_qualification_horizon import (
    ACTUAL_EXECUTION_GROSS_BASIS,
    NET_SUBTRACTION_BASIS,
)


COMPLETION_SCHEMA = "lifecycle_bundle_completion_v1"
TRANSFER_READY_SCHEMA = "lifecycle_bundle_transfer_ready_v1"
ENTRY_OUTCOMES = frozenset({"FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN"})
FLAT_POSITION_STATES = frozenset({"CLOSED", "NEVER_OPENED"})


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_timestamp(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number > 0 else None


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text if text and text.upper() != "UNKNOWN" else None


def _valid_sha256(value: Any) -> str | None:
    text = str(value or "").lower()
    if len(text) == 64 and all(char in "0123456789abcdef" for char in text):
        return text
    return None


def build_lifecycle_completion_receipt(
    proof: Mapping[str, Any], *, now: float,
    lifecycle_horizon_sec: float = 7200.0,
    reconciliation_allowance_sec: float = 180.0,
    pnl_tolerance_usd: float = 1e-8,
) -> dict[str, Any]:
    """Validate exact terminal proof and return a deterministic receipt.

    The returned ``receipt`` is ``None`` whenever any blocker exists.  In
    particular, absent fill evidence is never interpreted as ``NO_FILL``.
    ``now`` is explicit to keep restart/replay tests deterministic.
    """
    blockers: list[str] = []
    outcome = str(proof.get("entry_outcome") or "").upper()
    if outcome not in ENTRY_OUTCOMES:
        blockers.append("ENTRY_OUTCOME_INVALID")

    schedule = proof.get("terminal_schedule")
    if not isinstance(schedule, Mapping):
        blockers.append("TERMINAL_SCHEDULE_MISSING")
        schedule = {}
    if schedule.get("authoritative") is not True:
        blockers.append("TERMINAL_SCHEDULE_NOT_AUTHORITATIVE")
    if schedule.get("schedule_lifecycle_final") is not True:
        blockers.append("ENTRY_SCHEDULE_NOT_TERMINAL")
    terminal_ts = _positive_timestamp(schedule.get("terminal_ts"))
    if terminal_ts is None:
        blockers.append("TERMINAL_TIMESTAMP_MISSING")
    if not _text(schedule.get("terminal_reason")):
        blockers.append("TERMINAL_REASON_MISSING")
    schedule_sha256 = _valid_sha256(schedule.get("schedule_sha256"))
    if schedule_sha256 is None:
        blockers.append("SCHEDULE_SHA256_MISSING_OR_INVALID")

    position_state = str(proof.get("position_state") or "").upper()
    if position_state not in FLAT_POSITION_STATES:
        blockers.append("POSITION_NOT_PROVEN_CLOSED")
    open_quantity = _finite(proof.get("open_quantity"))
    if open_quantity is None:
        blockers.append("OPEN_QUANTITY_MISSING")
    elif abs(open_quantity) > 1e-12:
        blockers.append("OPEN_QUANTITY_NONZERO")

    observation = proof.get("post_observation")
    if not isinstance(observation, Mapping):
        blockers.append("POST_OBSERVATION_MISSING")
        observation = {}
    horizon_complete_ts = _positive_timestamp(observation.get("complete_through_ts"))
    if observation.get("complete") is not True:
        blockers.append("POST_OBSERVATION_INCOMPLETE")
    if observation.get("gaps_absent") is not True:
        blockers.append("POST_OBSERVATION_GAPS_UNKNOWN")
    required_horizon = (terminal_ts + max(0.0, float(lifecycle_horizon_sec))) if terminal_ts else None
    if horizon_complete_ts is None or required_horizon is None or horizon_complete_ts < required_horizon:
        blockers.append("LIFECYCLE_HORIZON_INCOMPLETE")
    now_value = _positive_timestamp(now)
    if now_value is None:
        blockers.append("CURRENT_TIMESTAMP_INVALID")
    elif horizon_complete_ts is None or now_value < horizon_complete_ts + max(0.0, float(reconciliation_allowance_sec)):
        blockers.append("RECONCILIATION_ALLOWANCE_ACTIVE")

    unknown_reason = _text(proof.get("unknown_reason"))
    if outcome == "UNKNOWN" and unknown_reason is None:
        blockers.append("UNKNOWN_REASON_MISSING")

    economics: dict[str, float] | None = None
    extrema: dict[str, float] | None = None
    exit_evidence: dict[str, Any] | None = None
    if outcome in {"FULL_FILL", "PARTIAL_FILL"}:
        filled_qty = _finite(proof.get("filled_quantity"))
        if filled_qty is None or filled_qty <= 0:
            blockers.append("POSITIVE_FILLED_QUANTITY_MISSING")
        if outcome == "PARTIAL_FILL":
            requested_qty = _finite(proof.get("requested_quantity"))
            if requested_qty is None or requested_qty <= 0:
                blockers.append("REQUESTED_QUANTITY_MISSING")
            elif filled_qty is not None and filled_qty >= requested_qty:
                blockers.append("PARTIAL_FILL_QUANTITY_NOT_PARTIAL")

        exit_source = proof.get("exit_evidence")
        if not isinstance(exit_source, Mapping):
            blockers.append("EXIT_EVIDENCE_INCOMPLETE")
            exit_source = {}
        exit_close_ts = _positive_timestamp(exit_source.get("close_ts"))
        exit_receipt_sha256 = _valid_sha256(exit_source.get("receipt_sha256"))
        if exit_source.get("terminal") is not True or exit_close_ts is None or exit_receipt_sha256 is None:
            blockers.append("EXIT_EVIDENCE_INCOMPLETE")
        else:
            exit_evidence = {
                "terminal": True,
                "close_ts": exit_close_ts,
                "receipt_sha256": exit_receipt_sha256,
            }

        economics_source = proof.get("economics")
        if not isinstance(economics_source, Mapping):
            blockers.append("COST_EVIDENCE_INCOMPLETE")
            economics_source = {}
        names = (
            "gross_pnl_usd", "trading_fees_usd", "funding_fees_usd",
            "slippage_cost_usd", "latency_cost_usd", "net_pnl_usd",
        )
        parsed = {name: _finite(economics_source.get(name)) for name in names}
        gross_basis = economics_source.get("gross_pnl_basis")
        net_basis = economics_source.get("net_pnl_reconciliation_basis")
        if gross_basis != ACTUAL_EXECUTION_GROSS_BASIS:
            blockers.append("GROSS_PNL_BASIS_MISSING_OR_UNSUPPORTED")
        if net_basis != NET_SUBTRACTION_BASIS:
            blockers.append("NET_PNL_RECONCILIATION_BASIS_MISSING_OR_UNSUPPORTED")
        if any(value is None for value in parsed.values()):
            blockers.append("COST_EVIDENCE_INCOMPLETE")
        else:
            economics = {name: float(value) for name, value in parsed.items() if value is not None}
            economics.update({
                "gross_pnl_basis": ACTUAL_EXECUTION_GROSS_BASIS,
                "net_pnl_reconciliation_basis": NET_SUBTRACTION_BASIS,
                "separately_subtracted_from_gross": ["trading_fees_usd", "funding_fees_usd"],
                "attribution_only_not_subtracted": ["slippage_cost_usd", "latency_cost_usd"],
            })
            expected_net = (
                economics["gross_pnl_usd"] - economics["trading_fees_usd"]
                - economics["funding_fees_usd"]
            )
            if abs(expected_net - economics["net_pnl_usd"]) > max(0.0, float(pnl_tolerance_usd)):
                blockers.append("NET_PNL_UNRECONCILED")

        extrema_source = proof.get("path_extrema")
        if not isinstance(extrema_source, Mapping):
            blockers.append("MFE_MAE_INCOMPLETE")
            extrema_source = {}
        mfe = _finite(extrema_source.get("mfe_usd"))
        mae = _finite(extrema_source.get("mae_usd"))
        if mfe is None or mae is None:
            blockers.append("MFE_MAE_INCOMPLETE")
        else:
            extrema = {"mfe_usd": mfe, "mae_usd": mae}

    blockers = sorted(set(blockers))
    if blockers:
        return {"ready": False, "classification": outcome if outcome in ENTRY_OUTCOMES else "UNKNOWN", "blockers": blockers, "receipt": None}

    receipt: dict[str, Any] = {
        "schema": COMPLETION_SCHEMA,
        "terminal": True,
        "entry_outcome": outcome,
        "entry_schedule_terminal": True,
        "position_closed_or_never_opened": True,
        "post_observation_complete": True,
        "terminal_ts": terminal_ts,
        "horizon_complete_ts": horizon_complete_ts,
        "schedule_sha256": schedule_sha256,
        "terminal_reason": _text(schedule.get("terminal_reason")),
        "position_state": position_state,
        "open_quantity": open_quantity,
    }
    if unknown_reason is not None:
        receipt["unknown_reason"] = unknown_reason
    if outcome in {"FULL_FILL", "PARTIAL_FILL"}:
        receipt.update({
            "filled_quantity": float(proof["filled_quantity"]),
            "exit_evidence_complete": True,
            "costs_complete": True,
            "mfe_mae_complete": True,
            "net_pnl_reconciled": True,
            "exit_evidence": exit_evidence,
            "economics": economics,
            "path_extrema": extrema,
        })
        if outcome == "PARTIAL_FILL":
            receipt["requested_quantity"] = float(proof["requested_quantity"])
    receipt["completion_receipt_sha256"] = hashlib.sha256(
        canonical_json(receipt).encode("utf-8")
    ).hexdigest()
    return {"ready": True, "classification": outcome, "blockers": [], "receipt": receipt}


def build_lifecycle_transfer_ready_receipt(
    proof: Mapping[str, Any], *, now: float,
    lifecycle_horizon_sec: float = 7200.0,
    reconciliation_allowance_sec: float = 180.0,
    pnl_tolerance_usd: float = 1e-8,
) -> dict[str, Any]:
    """Build a terminal, flat transfer receipt without claiming qualification.

    Transfer readiness is deliberately narrower than qualification readiness:
    it proves that an entry lifecycle has an authoritative terminal schedule,
    an explicit outcome, and no remaining position.  It does *not* authorize
    source cleanup and it is never profitability evidence.  The qualification
    validator remains the sole authority for the two-hour horizon, exit,
    economics, latency/slippage, and MFE/MAE requirements; its exact blockers
    are embedded so missing evidence remains visible rather than being inferred.
    """
    blockers: list[str] = []
    outcome = str(proof.get("entry_outcome") or "").upper()
    if outcome not in ENTRY_OUTCOMES:
        blockers.append("ENTRY_OUTCOME_INVALID")

    schedule = proof.get("terminal_schedule")
    if not isinstance(schedule, Mapping):
        blockers.append("TERMINAL_SCHEDULE_MISSING")
        schedule = {}
    if schedule.get("authoritative") is not True:
        blockers.append("TERMINAL_SCHEDULE_NOT_AUTHORITATIVE")
    if schedule.get("schedule_lifecycle_final") is not True:
        blockers.append("ENTRY_SCHEDULE_NOT_TERMINAL")
    terminal_ts = _positive_timestamp(schedule.get("terminal_ts"))
    if terminal_ts is None:
        blockers.append("TERMINAL_TIMESTAMP_MISSING")
    terminal_reason = _text(schedule.get("terminal_reason"))
    if terminal_reason is None:
        blockers.append("TERMINAL_REASON_MISSING")
    schedule_sha256 = _valid_sha256(schedule.get("schedule_sha256"))
    if schedule_sha256 is None:
        blockers.append("SCHEDULE_SHA256_MISSING_OR_INVALID")

    position_state = str(proof.get("position_state") or "").upper()
    if position_state not in FLAT_POSITION_STATES:
        blockers.append("POSITION_NOT_PROVEN_CLOSED")
    open_quantity = _finite(proof.get("open_quantity"))
    if open_quantity is None:
        blockers.append("OPEN_QUANTITY_MISSING")
    elif abs(open_quantity) > 1e-12:
        blockers.append("OPEN_QUANTITY_NONZERO")

    transfer_ts = _positive_timestamp(now)
    if transfer_ts is None:
        blockers.append("CURRENT_TIMESTAMP_INVALID")

    requested_quantity = _finite(proof.get("requested_quantity"))
    filled_quantity = _finite(proof.get("filled_quantity"))
    unknown_reason = _text(proof.get("unknown_reason"))
    if outcome == "FULL_FILL":
        if position_state != "CLOSED":
            blockers.append("FILLED_OUTCOME_POSITION_NOT_CLOSED")
        if requested_quantity is None or requested_quantity <= 0:
            blockers.append("REQUESTED_QUANTITY_MISSING")
        if filled_quantity is None or filled_quantity <= 0:
            blockers.append("POSITIVE_FILLED_QUANTITY_MISSING")
        elif requested_quantity is not None and requested_quantity > 0 and abs(
            filled_quantity - requested_quantity
        ) > 1e-12:
            blockers.append("FULL_FILL_QUANTITY_MISMATCH")
    elif outcome == "PARTIAL_FILL":
        if position_state != "CLOSED":
            blockers.append("FILLED_OUTCOME_POSITION_NOT_CLOSED")
        if requested_quantity is None or requested_quantity <= 0:
            blockers.append("REQUESTED_QUANTITY_MISSING")
        if filled_quantity is None or filled_quantity <= 0:
            blockers.append("POSITIVE_FILLED_QUANTITY_MISSING")
        elif requested_quantity is not None and requested_quantity > 0 and filled_quantity >= requested_quantity:
            blockers.append("PARTIAL_FILL_QUANTITY_NOT_PARTIAL")
    elif outcome == "NO_FILL":
        if position_state != "NEVER_OPENED":
            blockers.append("NO_FILL_POSITION_STATE_INVALID")
        if filled_quantity is None:
            blockers.append("FILLED_QUANTITY_MISSING")
        elif abs(filled_quantity) > 1e-12:
            blockers.append("NO_FILL_QUANTITY_NONZERO")
    elif outcome == "UNKNOWN" and unknown_reason is None:
        blockers.append("UNKNOWN_REASON_MISSING")

    qualification = build_lifecycle_completion_receipt(
        proof,
        now=now,
        lifecycle_horizon_sec=lifecycle_horizon_sec,
        reconciliation_allowance_sec=reconciliation_allowance_sec,
        pnl_tolerance_usd=pnl_tolerance_usd,
    )
    qualification_blockers = list(qualification["blockers"])

    blockers = sorted(set(blockers))
    classification = outcome if outcome in ENTRY_OUTCOMES else "UNKNOWN"
    if blockers:
        return {
            "ready": False,
            "classification": classification,
            "blockers": blockers,
            "qualification_ready": bool(qualification["ready"]),
            "qualification_blockers": qualification_blockers,
            "receipt": None,
        }

    receipt: dict[str, Any] = {
        "schema": TRANSFER_READY_SCHEMA,
        "transfer_ready": True,
        "terminal": True,
        "entry_outcome": outcome,
        "entry_schedule_terminal": True,
        "position_closed_or_never_opened": True,
        "terminal_ts": terminal_ts,
        "transfer_receipt_ts": transfer_ts,
        "schedule_sha256": schedule_sha256,
        "terminal_reason": terminal_reason,
        "position_state": position_state,
        "open_quantity": open_quantity,
        "qualification_schema": COMPLETION_SCHEMA,
        "qualification_ready": bool(qualification["ready"]),
        "qualification_blockers": qualification_blockers,
        "profitability_supported": False,
        "profitability_blocker": "TRANSFER_RECEIPT_IS_NOT_PROFITABILITY_EVIDENCE",
        "source_cleanup_authorized": False,
    }
    if requested_quantity is not None:
        receipt["requested_quantity"] = requested_quantity
    if filled_quantity is not None:
        receipt["filled_quantity"] = filled_quantity
    if unknown_reason is not None:
        receipt["unknown_reason"] = unknown_reason
    receipt["transfer_receipt_sha256"] = hashlib.sha256(
        canonical_json(receipt).encode("utf-8")
    ).hexdigest()
    return {
        "ready": True,
        "classification": outcome,
        "blockers": [],
        "qualification_ready": bool(qualification["ready"]),
        "qualification_blockers": qualification_blockers,
        "receipt": receipt,
    }
