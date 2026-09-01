from lifecycle_bundles import LifecycleKey
from lifecycle_completion_reconciler import evaluate_lifecycle_completion
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


def test_no_fill_requires_all_explicit_terminal_proofs():
    result = evaluate_lifecycle_completion(KEY, no_fill_rows(), now=20_000.0)
    assert result["ready"] is True
    assert result["receipt"]["entry_outcome"] == "NO_FILL"


def test_terminal_label_without_post_observation_remains_not_ready():
    result = evaluate_lifecycle_completion(KEY, no_fill_rows()[:-1], now=20_000.0)
    assert result["ready"] is False
    assert "POST_OBSERVATION_MISSING" in result["blockers"]


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


def test_unknown_requires_an_explicit_reason_and_is_never_inferred_from_absence():
    rows = no_fill_rows()
    rows[1].pop("terminal_no_fill")
    result = evaluate_lifecycle_completion(KEY, rows, now=20_000.0)
    assert result["ready"] is False
    assert result["classification"] == "UNKNOWN"
    assert "UNIQUE_ENTRY_OUTCOME_NOT_PROVEN" in result["blockers"]


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
