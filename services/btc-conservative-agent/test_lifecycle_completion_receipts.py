import copy

import pytest

from lifecycle_bundles import classify_completion
from lifecycle_completion_receipts import (
    COMPLETION_SCHEMA,
    TRANSFER_READY_SCHEMA,
    build_lifecycle_completion_receipt,
    build_lifecycle_transfer_ready_receipt,
)
from lifecycle_qualification_horizon import (
    ACTUAL_EXECUTION_GROSS_BASIS,
    NET_SUBTRACTION_BASIS,
)


NOW = 20_000.0


def proof(outcome="NO_FILL"):
    return {
        "entry_outcome": outcome,
        "terminal_schedule": {
            "authoritative": True,
            "schedule_lifecycle_final": True,
            "terminal_ts": 10_000.0,
            "terminal_reason": "TTL_EXPIRED" if outcome == "NO_FILL" else "FILLED",
            "schedule_sha256": "a" * 64,
        },
        "position_state": "NEVER_OPENED" if outcome in {"NO_FILL", "UNKNOWN"} else "CLOSED",
        "open_quantity": 0.0,
        "post_observation": {"complete": True, "gaps_absent": True, "complete_through_ts": 18_000.0},
    }


def filled_proof(outcome="FULL_FILL"):
    value = proof(outcome)
    value.update({
        "filled_quantity": 1.0,
        "requested_quantity": 2.0 if outcome == "PARTIAL_FILL" else 1.0,
        "exit_evidence": {"terminal": True, "close_ts": 11_000.0, "receipt_sha256": "b" * 64},
        "economics": {
            "gross_pnl_basis": ACTUAL_EXECUTION_GROSS_BASIS,
            "net_pnl_reconciliation_basis": NET_SUBTRACTION_BASIS,
            "gross_pnl_usd": 5.0,
            "trading_fees_usd": 1.0,
            "funding_fees_usd": 0.5,
            "slippage_cost_usd": 0.25,
            "latency_cost_usd": 0.25,
            "net_pnl_usd": 3.5,
        },
        "path_extrema": {"mfe_usd": 7.0, "mae_usd": -2.0},
    })
    return value


@pytest.mark.parametrize("outcome", ["NO_FILL", "FULL_FILL", "PARTIAL_FILL"])
def test_builds_classifier_compatible_receipt_only_from_complete_proof(outcome):
    source = proof(outcome) if outcome == "NO_FILL" else filled_proof(outcome)
    result = build_lifecycle_completion_receipt(source, now=NOW)
    assert result["ready"] is True
    receipt = result["receipt"]
    assert len(receipt["completion_receipt_sha256"]) == 64
    classified = classify_completion([{"bundle_completion": receipt}], now=NOW)
    assert classified["ready"] is True
    assert classified["classification"] == outcome


def test_unknown_is_explicit_and_never_synthesizes_no_fill():
    source = proof("UNKNOWN")
    missing = build_lifecycle_completion_receipt(source, now=NOW)
    assert missing["receipt"] is None
    assert missing["classification"] == "UNKNOWN"
    assert "UNKNOWN_REASON_MISSING" in missing["blockers"]
    source["unknown_reason"] = "MARKET_TAPE_GAP"
    complete = build_lifecycle_completion_receipt(source, now=NOW)
    assert complete["ready"] is True
    assert complete["receipt"]["entry_outcome"] == "UNKNOWN"


@pytest.mark.parametrize("mutation,blocker", [
    (lambda value: value["terminal_schedule"].update(authoritative=False), "TERMINAL_SCHEDULE_NOT_AUTHORITATIVE"),
    (lambda value: value["terminal_schedule"].update(schedule_lifecycle_final=False), "ENTRY_SCHEDULE_NOT_TERMINAL"),
    (lambda value: value.update(position_state="OPEN"), "POSITION_NOT_PROVEN_CLOSED"),
    (lambda value: value.update(open_quantity=0.01), "OPEN_QUANTITY_NONZERO"),
    (lambda value: value["post_observation"].update(gaps_absent=False), "POST_OBSERVATION_GAPS_UNKNOWN"),
    (lambda value: value["post_observation"].update(complete_through_ts=17_199), "LIFECYCLE_HORIZON_INCOMPLETE"),
])
def test_fails_closed_for_each_terminal_boundary(mutation, blocker):
    source = proof()
    mutation(source)
    result = build_lifecycle_completion_receipt(source, now=NOW)
    assert result["receipt"] is None
    assert blocker in result["blockers"]


@pytest.mark.parametrize("mutation,blocker", [
    (lambda value: value["exit_evidence"].pop("receipt_sha256"), "EXIT_EVIDENCE_INCOMPLETE"),
    (lambda value: value["economics"].pop("funding_fees_usd"), "COST_EVIDENCE_INCOMPLETE"),
    (lambda value: value["economics"].update(net_pnl_usd=99), "NET_PNL_UNRECONCILED"),
    (lambda value: value["path_extrema"].pop("mae_usd"), "MFE_MAE_INCOMPLETE"),
    (lambda value: value.update(filled_quantity=0), "POSITIVE_FILLED_QUANTITY_MISSING"),
])
def test_fill_receipt_requires_exact_economics_extrema_and_quantity(mutation, blocker):
    source = filled_proof()
    mutation(source)
    result = build_lifecycle_completion_receipt(source, now=NOW)
    assert result["receipt"] is None
    assert blocker in result["blockers"]


def test_nonzero_slippage_and_latency_are_attribution_not_double_subtracted():
    source = filled_proof()
    source["economics"].update(slippage_cost_usd=1.25, latency_cost_usd=0.75)
    result = build_lifecycle_completion_receipt(source, now=NOW)
    assert result["ready"] is True
    economics = result["receipt"]["economics"]
    assert economics["net_pnl_usd"] == 3.5
    assert economics["attribution_only_not_subtracted"] == [
        "slippage_cost_usd", "latency_cost_usd",
    ]


def test_missing_or_unsupported_accounting_basis_fails_closed():
    source = filled_proof()
    source["economics"].pop("gross_pnl_basis")
    result = build_lifecycle_completion_receipt(source, now=NOW)
    assert result["ready"] is False
    assert "GROSS_PNL_BASIS_MISSING_OR_UNSUPPORTED" in result["blockers"]


def test_partial_fill_must_be_strictly_less_than_requested_quantity():
    source = filled_proof("PARTIAL_FILL")
    source["filled_quantity"] = source["requested_quantity"]
    result = build_lifecycle_completion_receipt(source, now=NOW)
    assert result["receipt"] is None
    assert "PARTIAL_FILL_QUANTITY_NOT_PARTIAL" in result["blockers"]


def test_receipt_is_deterministic_and_does_not_mutate_proof():
    source = filled_proof()
    frozen = copy.deepcopy(source)
    first = build_lifecycle_completion_receipt(source, now=NOW)
    second = build_lifecycle_completion_receipt(source, now=NOW)
    assert first == second
    assert source == frozen


def transfer_proof(outcome="NO_FILL"):
    value = proof(outcome)
    if outcome == "NO_FILL":
        value["filled_quantity"] = 0.0
    elif outcome in {"FULL_FILL", "PARTIAL_FILL"}:
        value["filled_quantity"] = 1.0
        value["requested_quantity"] = 2.0 if outcome == "PARTIAL_FILL" else 1.0
    elif outcome == "UNKNOWN":
        value["unknown_reason"] = "EXECUTION_EVIDENCE_UNAVAILABLE"
    return value


@pytest.mark.parametrize("outcome", ["NO_FILL", "FULL_FILL", "PARTIAL_FILL", "UNKNOWN"])
def test_transfer_ready_is_distinct_terminal_flat_and_never_profitability_or_cleanup_evidence(outcome):
    result = build_lifecycle_transfer_ready_receipt(transfer_proof(outcome), now=NOW)
    assert result["ready"] is True
    receipt = result["receipt"]
    assert receipt["schema"] == TRANSFER_READY_SCHEMA
    assert receipt["schema"] != COMPLETION_SCHEMA
    assert receipt["entry_outcome"] == outcome
    assert receipt["profitability_supported"] is False
    assert receipt["profitability_blocker"] == "TRANSFER_RECEIPT_IS_NOT_PROFITABILITY_EVIDENCE"
    assert receipt["source_cleanup_authorized"] is False
    assert len(receipt["transfer_receipt_sha256"]) == 64


def test_transfer_ready_exposes_qualification_blockers_without_requiring_two_hour_completion():
    source = transfer_proof("FULL_FILL")
    source.pop("post_observation")
    result = build_lifecycle_transfer_ready_receipt(source, now=10_100.0)
    assert result["ready"] is True
    assert result["qualification_ready"] is False
    assert "POST_OBSERVATION_INCOMPLETE" in result["qualification_blockers"]
    assert "COST_EVIDENCE_INCOMPLETE" in result["qualification_blockers"]
    assert result["receipt"]["qualification_blockers"] == result["qualification_blockers"]


def test_transfer_ready_can_report_qualification_ready_without_becoming_a_profitability_claim():
    source = filled_proof()
    result = build_lifecycle_transfer_ready_receipt(source, now=NOW)
    assert result["ready"] is True
    assert result["qualification_ready"] is True
    assert result["qualification_blockers"] == []
    assert result["receipt"]["profitability_supported"] is False


@pytest.mark.parametrize("mutation,blocker", [
    (lambda value: value["terminal_schedule"].update(authoritative=False), "TERMINAL_SCHEDULE_NOT_AUTHORITATIVE"),
    (lambda value: value["terminal_schedule"].update(schedule_lifecycle_final=False), "ENTRY_SCHEDULE_NOT_TERMINAL"),
    (lambda value: value.update(position_state="OPEN"), "POSITION_NOT_PROVEN_CLOSED"),
    (lambda value: value.update(open_quantity=0.1), "OPEN_QUANTITY_NONZERO"),
    (lambda value: value.update(entry_outcome=""), "ENTRY_OUTCOME_INVALID"),
])
def test_transfer_ready_fails_closed_on_terminal_flat_and_outcome_boundaries(mutation, blocker):
    source = transfer_proof()
    mutation(source)
    result = build_lifecycle_transfer_ready_receipt(source, now=NOW)
    assert result["ready"] is False
    assert result["receipt"] is None
    assert blocker in result["blockers"]


@pytest.mark.parametrize("outcome,mutation,blocker", [
    ("NO_FILL", lambda value: value.update(filled_quantity=0.1), "NO_FILL_QUANTITY_NONZERO"),
    ("NO_FILL", lambda value: value.pop("filled_quantity"), "FILLED_QUANTITY_MISSING"),
    ("FULL_FILL", lambda value: value.update(filled_quantity=0.5), "FULL_FILL_QUANTITY_MISMATCH"),
    ("PARTIAL_FILL", lambda value: value.update(filled_quantity=2.0), "PARTIAL_FILL_QUANTITY_NOT_PARTIAL"),
    ("UNKNOWN", lambda value: value.pop("unknown_reason"), "UNKNOWN_REASON_MISSING"),
])
def test_transfer_ready_does_not_infer_entry_outcomes(outcome, mutation, blocker):
    source = transfer_proof(outcome)
    mutation(source)
    result = build_lifecycle_transfer_ready_receipt(source, now=NOW)
    assert result["ready"] is False
    assert blocker in result["blockers"]


def test_transfer_receipt_is_deterministic_and_does_not_mutate_proof():
    source = transfer_proof("PARTIAL_FILL")
    frozen = copy.deepcopy(source)
    first = build_lifecycle_transfer_ready_receipt(source, now=NOW)
    second = build_lifecycle_transfer_ready_receipt(source, now=NOW)
    assert first == second
    assert source == frozen
