from execution_latency_evidence import evaluate_execution_latency, evaluate_schedule_execution_latency
from research_order_schedule import append_action_timing_receipt, initialize_order_schedule


def _receipt(**overrides):
    side = overrides.pop("side", "LONG")
    order = {
        "trade_id": "paper-1", "status": "PENDING", "signal_dir": side,
        "qty": 2.0, "remaining_qty": 2.0, "limit_price": 101.0, "signal_price": 100.0,
    }
    signal = {"trade_id": "paper-1", "final_direction": side}
    initialize_order_schedule(order, signal, now=95.0, registered=True)
    args = {
        "action_generation": 0, "action_type": "INITIAL_SUBMIT",
        "policy_due_ts": 100.0, "eligibility_ts": 100.0,
        "dispatch_start_ts": 103.0, "acknowledgement_ts": 103.2,
        "fill_ts": 103.2, "fill_price": 101.0, "filled_qty": 2.0,
        "remaining_qty": 2.0, "limit_price": 101.0,
        "book_ref": "b103", "tape_ref": "tape-1",
        "non_intentional_delay": {
            "classification": "PROVEN_NON_INTENTIONAL", "seconds": 3.0,
            "cause": "SERIALIZED_RUNTIME_QUEUE", "evidence_ref": "runtime-event:1",
        },
    }
    args.update(overrides)
    return append_action_timing_receipt(order, signal, **args), order, signal


def _rows():
    return [
        {"ts": 100.0, "bid": 99.0, "ask": 100.0, "bid_qty": 5, "ask_qty": 5, "book_ref": "b100"},
        {"ts": 103.0, "bid": 100.0, "ask": 101.0, "bid_qty": 5, "ask_qty": 5, "book_ref": "b103"},
    ]


def test_action_receipt_is_immutable_idempotent_and_attached_to_schedule():
    receipt, order, signal = _receipt()
    duplicate = append_action_timing_receipt(
        order, signal, action_generation=0, action_type="INITIAL_SUBMIT",
        policy_due_ts=100, eligibility_ts=100, dispatch_start_ts=103,
        acknowledgement_ts=103.2, remaining_qty=2, limit_price=101,
        fill_ts=103.2, fill_price=101, filled_qty=2,
        book_ref="b103", tape_ref="tape-1",
        non_intentional_delay={"classification": "PROVEN_NON_INTENTIONAL", "seconds": 3,
                               "cause": "SERIALIZED_RUNTIME_QUEUE", "evidence_ref": "runtime-event:1"},
    )
    conflict = append_action_timing_receipt(
        order, signal, action_generation=0, action_type="INITIAL_SUBMIT",
        policy_due_ts=100, eligibility_ts=100, dispatch_start_ts=104,
    )
    assert duplicate is receipt
    assert conflict is None
    assert signal["research_chase_schedule"] is order["research_chase_schedule"]
    assert receipt["receipt_sha256"]
    assert receipt["attribution_only"] is True


def test_mutated_stored_receipt_is_rejected_instead_of_accepted_as_idempotent():
    receipt, order, signal = _receipt()
    receipt["limit_price"] = 77
    duplicate = append_action_timing_receipt(
        order, signal, action_generation=0, action_type="INITIAL_SUBMIT",
        policy_due_ts=100, eligibility_ts=100, dispatch_start_ts=103,
        acknowledgement_ts=103.2, fill_ts=103.2, fill_price=101, filled_qty=2,
        remaining_qty=2, limit_price=101, book_ref="b103", tape_ref="tape-1",
        non_intentional_delay={"classification": "PROVEN_NON_INTENTIONAL", "seconds": 3,
                               "cause": "SERIALIZED_RUNTIME_QUEUE", "evidence_ref": "runtime-event:1"},
    )
    assert duplicate is None


def test_identical_action_replay_reports_attribution_without_pnl_deduction():
    receipt, _, _ = _receipt()
    result = evaluate_execution_latency(receipt, _rows(), terminal=True, market_tape_ref="tape-1")
    assert result["status"] == "SUPPORTED"
    assert result["latency_cost_usd"] == 2.0
    assert result["signed_latency_impact_usd"] == 2.0
    assert result["actual_executable_price"] == 101.0
    assert result["counterfactual_executable_price"] == 100.0
    assert result["must_not_be_subtracted_from_actual_price_gross_pnl"] is True


def test_latency_benefit_is_signed_but_not_reported_as_adverse_cost():
    receipt, _, _ = _receipt(fill_price=100, limit_price=102)
    rows = [
        {"ts": 100, "bid": 100, "ask": 101, "bid_qty": 5, "ask_qty": 5, "book_ref": "b100"},
        {"ts": 103, "bid": 99, "ask": 100, "bid_qty": 5, "ask_qty": 5, "book_ref": "b103"},
    ]
    result = evaluate_execution_latency(receipt, rows, terminal=True, market_tape_ref="tape-1")
    assert result["signed_latency_impact_usd"] == -2.0
    assert result["latency_cost_usd"] == 0.0


def test_short_adverse_latency_has_positive_cost():
    receipt, _, _ = _receipt(side="SHORT", fill_price=100, limit_price=99)
    rows = [
        {"ts": 100, "bid": 101, "ask": 102, "bid_qty": 5, "ask_qty": 5, "book_ref": "b100"},
        {"ts": 103, "bid": 100, "ask": 101, "bid_qty": 5, "ask_qty": 5, "book_ref": "b103"},
    ]
    result = evaluate_execution_latency(receipt, rows, terminal=True, market_tape_ref="tape-1")
    assert result["signed_latency_impact_usd"] == 2.0
    assert result["latency_cost_usd"] == 2.0


def test_book_and_tape_references_must_match_selected_evidence():
    receipt, _, _ = _receipt()
    assert evaluate_execution_latency(receipt, _rows(), terminal=True, market_tape_ref="other")["reasons"] == [
        "MARKET_TAPE_REFERENCE_MISMATCH"
    ]
    rows = [dict(row, book_ref="wrong") if row["ts"] == 103 else row for row in _rows()]
    assert evaluate_execution_latency(receipt, rows, terminal=True, market_tape_ref="tape-1")["reasons"] == [
        "DISPATCH_BOOK_REFERENCE_MISMATCH"
    ]


def test_one_second_or_unproven_delay_is_unknown():
    receipt, _, _ = _receipt(non_intentional_delay={
        "classification": "PROVEN_NON_INTENTIONAL", "seconds": 1.0,
        "cause": "QUEUE", "evidence_ref": "runtime:1",
    })
    assert evaluate_execution_latency(receipt, _rows(), terminal=True, market_tape_ref="tape-1")["reasons"] == [
        "ONE_SECOND_TIMING_AMBIGUITY"
    ]
    receipt, _, _ = _receipt(non_intentional_delay={"classification": "INFERRED", "seconds": 3})
    assert evaluate_execution_latency(receipt, _rows(), terminal=True, market_tape_ref="tape-1")["reasons"] == [
        "NON_INTENTIONAL_DELAY_UNPROVEN"
    ]


def test_eligibility_may_precede_policy_due_and_intentional_wait_is_not_latency():
    receipt, _, _ = _receipt(
        policy_due_ts=100, eligibility_ts=95, dispatch_start_ts=103,
        non_intentional_delay={"classification": "PROVEN_NON_INTENTIONAL", "seconds": 3,
                               "cause": "QUEUE", "evidence_ref": "runtime:1"},
    )
    assert receipt is not None
    result = evaluate_execution_latency(receipt, _rows(), terminal=True, market_tape_ref="tape-1")
    assert result["status"] == "SUPPORTED"
    assert result["counterfactual_dispatch_ts"] == 100


def test_boolean_timing_is_rejected():
    receipt, _, _ = _receipt(policy_due_ts=True)
    assert receipt is None
    receipt, _, _ = _receipt(action_generation=True)
    assert receipt is None


def test_stale_or_missing_bbo_and_insufficient_top_quantity_are_unknown():
    receipt, _, _ = _receipt()
    stale = [{"ts": 90, "bid": 99, "ask": 100, "bid_qty": 5, "ask_qty": 5}]
    assert "ACTUAL_BBO_STALE" in evaluate_execution_latency(receipt, stale, terminal=True, market_tape_ref="tape-1")["reasons"]
    shallow = [dict(row, ask_qty=1) for row in _rows()]
    assert evaluate_execution_latency(receipt, shallow, terminal=True, market_tape_ref="tape-1")["reasons"] == ["INSUFFICIENT_TOP_QTY"]


def test_overlap_and_fill_no_fill_divergence_fail_closed():
    receipt, _, _ = _receipt(limit_price=100.5)
    overlap = [{"action_generation": 1, "dispatch_start_ts": 101, "acknowledgement_ts": 104}]
    assert evaluate_execution_latency(receipt, _rows(), terminal=True, market_tape_ref="tape-1", overlapping_actions=overlap)["reasons"] == [
        "OVERLAPPING_ACTION"
    ]
    result = evaluate_execution_latency(receipt, _rows(), terminal=False, market_tape_ref="tape-1")
    assert result["status"] == "UNKNOWN"
    assert result["reasons"] == ["FILL_NO_FILL_DIVERGENCE_NOT_TERMINAL"]


def test_terminal_identical_no_fill_stays_unknown_until_full_path_replay_exists():
    receipt, _, _ = _receipt(limit_price=99.0)
    result = evaluate_execution_latency(receipt, _rows(), terminal=True, market_tape_ref="tape-1")
    assert result["status"] == "UNKNOWN"
    assert result["reasons"] == ["TERMINAL_PATH_REPLAY_REQUIRED"]


def test_schedule_replay_aggregates_identical_action_attribution():
    receipt, _, _ = _receipt()
    result = evaluate_schedule_execution_latency([receipt], _rows(), terminal=True, market_tape_ref="tape-1")
    assert result["status"] == "SUPPORTED"
    assert result["latency_cost_usd"] == 2.0
    assert result["schedule_mutations"] == []
