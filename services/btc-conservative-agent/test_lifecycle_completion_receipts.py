import copy

import pytest

from lifecycle_bundles import classify_completion
from lifecycle_completion_receipts import build_lifecycle_completion_receipt


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
            "gross_pnl_usd": 5.0,
            "trading_fees_usd": 1.0,
            "funding_fees_usd": 0.5,
            "slippage_cost_usd": 0.25,
            "latency_cost_usd": 0.25,
            "net_pnl_usd": 3.0,
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
