from lifecycle_bundles import LifecycleKey
from lifecycle_completion_reconciler import (
    evaluate_lifecycle_completion,
    evaluate_lifecycle_transfer_ready,
)
from lifecycle_qualification_horizon import canonical_terminal_economics


KEY = LifecycleKey("epoch-1", "episode-1", "policy-1", "CONTINUOUS")
PROV = {"source_revision": "src", "deployed_revision": "dep", "tile_config_signature": "tile"}


def row(ledger, record_id, **extra):
    return {"ledger": ledger, "record_id": record_id, "event_id": "trade-1", **PROV, **extra}


def no_fill_rows():
    return [
        row("order_intent", "schedule", intent_kind="AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL",
            schedule_lifecycle_final=True, chase_schedule_authoritative=True,
            schedule_sha256="a" * 64,
            chase_schedule={"terminal_ts": 10_000.0, "terminal_reason": "TTL_EXPIRED"}),
        row("lifecycle", "terminal", terminal=True, terminal_no_fill=True),
        row("market_segment", "post", context_role="POST_EXIT_PATH",
            coverage={"complete": True, "gaps_absent": True, "complete_through_ts": 18_000.0}),
    ]


def production_audit_rows():
    binding = {
        "epoch_id": KEY.collection_epoch_id,
        "episode_id": KEY.episode_id,
        "policy_signature": KEY.policy_signature,
        "research_lane": KEY.research_lane,
        "shared_ai_call_id": "scan-1",
    }
    return [
        row("decision", "decision:episode-1:policy-1:LANE_POLICY_VERDICT",
            event_id="lane-decision:CONTINUOUS:scan-1", **binding),
        row("lifecycle", "lifecycle:episode-1:policy-1:CONTINUOUS:lane-entry:submitted",
            event_id="lane-entry:CONTINUOUS:scan-1", entry_resolution="ORDER_SUBMITTED",
            **binding),
    ]


def bind_execution_rows(rows):
    return [dict(item, shared_ai_call_id="scan-1") for item in rows]


def test_no_fill_requires_all_explicit_terminal_proofs():
    result = evaluate_lifecycle_completion(KEY, no_fill_rows(), now=20_000.0)
    assert result["ready"] is True
    assert result["receipt"]["entry_outcome"] == "NO_FILL"


def test_terminal_label_without_post_observation_remains_not_ready():
    result = evaluate_lifecycle_completion(KEY, no_fill_rows()[:-1], now=20_000.0)
    assert result["ready"] is False
    assert "POST_OBSERVATION_MISSING" in result["blockers"]


def test_no_fill_is_transfer_ready_before_two_hour_qualification():
    result = evaluate_lifecycle_transfer_ready(KEY, no_fill_rows()[:-1], now=10_100.0)
    assert result["ready"] is True
    assert result["classification"] == "NO_FILL"
    assert result["qualification_ready"] is False
    assert result["receipt"]["profitability_supported"] is False
    assert result["receipt"]["source_cleanup_authorized"] is False


def test_production_lane_audit_ids_do_not_ambiguous_no_fill_execution_id():
    rows = production_audit_rows() + bind_execution_rows(no_fill_rows()[:-1])
    transfer = evaluate_lifecycle_transfer_ready(KEY, rows, now=10_100.0)
    qualification = evaluate_lifecycle_completion(KEY, rows, now=10_100.0)
    assert transfer["ready"] is True
    assert transfer["event_id"] == "trade-1"
    assert transfer["classification"] == "NO_FILL"
    assert qualification["ready"] is False
    assert "LIFECYCLE_HORIZON_INCOMPLETE" in qualification["blockers"]
    assert "EVENT_ID_MISSING_OR_AMBIGUOUS" not in qualification["blockers"]


def test_production_lane_audit_ids_do_not_ambiguous_filled_execution_id():
    economics = canonical_terminal_economics({
        "gross_pnl_usd": 5.0, "trading_fees_usd": 1.0,
        "funding_fees_usd": 0.5, "slippage_cost_usd": 1.25,
        "latency_cost_usd": 0.75, "net_pnl_usd": 3.5,
    })
    filled = [
        row("order_intent", "schedule", requested_qty=1.0,
            intent_kind="AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL",
            schedule_lifecycle_final=True, chase_schedule_authoritative=True,
            schedule_sha256="a" * 64,
            chase_schedule={"terminal_ts": 10_000.0, "terminal_reason": "FILLED"}),
        row("lifecycle", "filled", observation_status="PAPER_POSITION_OPEN",
            outcome_state="FULL_FILL"),
        row("execution", "execution:trade-1:primary-fill"),
        row("execution", "execution:trade-1:paper-close", close_ts=10_050.0,
            filled_qty=1.0, canonical_economics=economics,
            path_extrema={"mfe_usd": 7.0, "mae_usd": -2.0}),
        row("lifecycle", "closed", terminal=True,
            observation_status="PAPER_POSITION_CLOSED"),
    ]
    rows = production_audit_rows() + bind_execution_rows(filled)
    transfer = evaluate_lifecycle_transfer_ready(KEY, rows, now=10_100.0)
    qualification = evaluate_lifecycle_completion(KEY, rows, now=10_100.0)
    assert transfer["ready"] is True
    assert transfer["event_id"] == "trade-1"
    assert transfer["classification"] == "FULL_FILL"
    assert qualification["ready"] is False
    assert "LIFECYCLE_HORIZON_INCOMPLETE" in qualification["blockers"]


def test_conflicting_terminal_execution_event_ids_fail_closed():
    execution = bind_execution_rows([
        row("order_intent", "schedule", requested_qty=1.0,
            intent_kind="AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL",
            schedule_lifecycle_final=True, chase_schedule_authoritative=True,
            schedule_sha256="a" * 64,
            chase_schedule={"terminal_ts": 10_000.0, "terminal_reason": "FILLED"}),
        row("lifecycle", "filled", observation_status="PAPER_POSITION_OPEN",
            outcome_state="FULL_FILL"),
        row("execution", "execution:trade-1:primary-fill"),
        row("execution", "execution:trade-1:paper-close", close_ts=10_050.0,
            filled_qty=1.0),
        row("lifecycle", "closed", terminal=True,
            observation_status="PAPER_POSITION_CLOSED"),
    ])
    for conflicting_index in (0, 2, 3):
        conflict = [dict(item) for item in execution]
        conflict[conflicting_index]["event_id"] = "different-trade"
        result = evaluate_lifecycle_transfer_ready(
            KEY, production_audit_rows() + conflict, now=10_100.0,
        )
        assert result["ready"] is False
        assert "EVENT_ID_MISSING_OR_AMBIGUOUS" in result["blockers"]


def test_lane_audit_rows_must_retain_composite_and_shared_ai_binding():
    execution = bind_execution_rows(no_fill_rows()[:-1])
    wrong_call = production_audit_rows()
    wrong_call[0]["shared_ai_call_id"] = "scan-other"
    call_result = evaluate_lifecycle_transfer_ready(
        KEY, wrong_call + execution, now=10_100.0,
    )
    assert "SHARED_AI_CALL_ID_MISSING_OR_AMBIGUOUS" in call_result["blockers"]

    wrong_composite = production_audit_rows()
    wrong_composite[0]["research_lane"] = "FAMILY_ATR_TRAIL"
    composite_result = evaluate_lifecycle_transfer_ready(
        KEY, wrong_composite + execution, now=10_100.0,
    )
    assert "LANE_AUDIT_COMPOSITE_BINDING_MISMATCH" in composite_result["blockers"]


def test_historical_terminal_schedule_without_outcome_remains_unknown():
    rows = production_audit_rows() + bind_execution_rows(no_fill_rows()[:1])
    result = evaluate_lifecycle_transfer_ready(KEY, rows, now=10_100.0)
    assert result["ready"] is False
    assert result["classification"] == "UNKNOWN"
    assert "UNIQUE_ENTRY_OUTCOME_NOT_PROVEN" in result["blockers"]


def test_multiple_terminal_schedules_fail_closed():
    rows = no_fill_rows()
    rows.append(row("order_intent", "schedule-2",
        intent_kind="AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL",
        schedule_lifecycle_final=True, chase_schedule_authoritative=True,
        schedule_sha256="b" * 64,
        chase_schedule={"terminal_ts": 10_000.0, "terminal_reason": "TTL_EXPIRED"}))
    result = evaluate_lifecycle_completion(KEY, rows, now=20_000.0)
    assert result["ready"] is False
    assert "UNIQUE_TERMINAL_SCHEDULE_NOT_PROVEN" in result["blockers"]


def test_provenance_mismatch_fails_closed():
    rows = no_fill_rows()
    rows[-1]["deployed_revision"] = "other"
    result = evaluate_lifecycle_completion(KEY, rows, now=20_000.0)
    assert result["ready"] is False
    assert "DEPLOYED_REVISION_AMBIGUOUS" in result["blockers"]


def test_transfer_ready_rejects_unknown_provenance_sentinels():
    rows = no_fill_rows()[:-1]
    for item in rows:
        item["source_revision"] = "UNKNOWN"
    result = evaluate_lifecycle_transfer_ready(KEY, rows, now=10_100.0)
    assert result["ready"] is False
    assert "SOURCE_REVISION_MISSING_OR_SENTINEL" in result["blockers"]
    assert result.get("receipt") is None


def test_unknown_requires_an_explicit_reason_and_is_never_inferred_from_absence():
    rows = no_fill_rows()
    rows[1].pop("terminal_no_fill")
    result = evaluate_lifecycle_completion(KEY, rows, now=20_000.0)
    assert result["ready"] is False
    assert result["classification"] == "UNKNOWN"
    assert "UNIQUE_ENTRY_OUTCOME_NOT_PROVEN" in result["blockers"]


def test_unknown_transfer_requires_explicit_flat_position_proof():
    rows = no_fill_rows()[:-1]
    rows[1] = row(
        "lifecycle", "unknown", terminal=True, entry_outcome="UNKNOWN",
        unknown_reason="RESTART_EVIDENCE_GAP",
    )
    blocked = evaluate_lifecycle_transfer_ready(KEY, rows, now=10_100.0)
    assert blocked["ready"] is False
    assert "POSITION_NOT_PROVEN_CLOSED" in blocked["blockers"]
    rows[1].update(position_state="NEVER_OPENED", open_quantity=0.0)
    ready = evaluate_lifecycle_transfer_ready(KEY, rows, now=10_100.0)
    assert ready["ready"] is True
    assert ready["classification"] == "UNKNOWN"


def test_filled_path_requires_cost_and_extrema_fields_not_present_in_old_ledgers():
    rows = no_fill_rows()
    rows[1] = row("lifecycle", "filled", observation_status="PAPER_POSITION_OPEN", outcome_state="FULL_FILL")
    rows.extend([
        row("execution", "execution:trade-1:primary-fill"),
        row("execution", "execution:trade-1:paper-close", close_ts=11_000.0,
            filled_qty=1.0, gross_pnl_usd=5.0, trading_fees_usd=1.0,
            funding_fees_usd=0.0, net_pnl_usd=4.0, path_extrema={}),
        row("lifecycle", "closed", terminal=True, observation_status="PAPER_POSITION_CLOSED"),
    ])
    result = evaluate_lifecycle_completion(KEY, rows, now=20_000.0)
    assert result["ready"] is False
    assert "COST_EVIDENCE_INCOMPLETE" in result["blockers"]
    assert "MFE_MAE_INCOMPLETE" in result["blockers"]


def test_filled_path_uses_canonical_basis_and_does_not_double_subtract_attribution():
    rows = no_fill_rows()
    rows[1] = row("lifecycle", "filled", observation_status="PAPER_POSITION_OPEN", outcome_state="FULL_FILL")
    economics = canonical_terminal_economics({
        "gross_pnl_usd": 5.0, "trading_fees_usd": 1.0,
        "funding_fees_usd": 0.5, "slippage_cost_usd": 1.25,
        "latency_cost_usd": 0.75, "net_pnl_usd": 3.5,
    })
    rows.extend([
        row("execution", "execution:trade-1:primary-fill"),
        row("execution", "execution:trade-1:paper-close", close_ts=11_000.0,
            filled_qty=1.0, canonical_economics=economics,
            path_extrema={"mfe_usd": 7.0, "mae_usd": -2.0}),
        row("lifecycle", "closed", terminal=True, observation_status="PAPER_POSITION_CLOSED"),
    ])
    result = evaluate_lifecycle_completion(KEY, rows, now=20_000.0)
    assert result["ready"] is True
    assert result["receipt"]["economics"]["net_pnl_usd"] == 3.5
    assert result["receipt"]["economics"]["slippage_cost_usd"] == 1.25
