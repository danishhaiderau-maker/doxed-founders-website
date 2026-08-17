from research.shadow_outcome_reconstruction import (
    EXECUTABLE_AFTER_EXPIRY,
    EXECUTABLE_AT_ORIGINAL_LIMIT,
    EXECUTABLE_BUT_BLOCKED,
    EXECUTABLE_COUNTERFACTUAL,
    EXECUTABLE_ONLY_AFTER_CHASE,
    ESTIMATED_FILL_PROBABILITY,
    NEVER_EXECUTABLE,
    PARTIALLY_EXECUTABLE,
    reconstruct_fill_origin,
    reconstruct_row,
    wilson,
)


def _obs(**kwargs):
    row = {
        "schema": "source_order_market_observation_v1",
        "direction": "LONG",
        "requested_quantity": 0.03,
        "original_limit_price": 63600.0,
        "current_limit_price": 63600.0,
        "limit_price": 63600.0,
        "limit_generation": 0,
        "best_bid": 63590.0,
        "best_ask": 63650.0,
        "side_correct_executable_quote": 63650.0,
        "visible_executable_qty": 0.001,
        "recent_executable_aggressor_qty": 0.0,
        "fill_gate_verdict": "INSUFFICIENT_EXECUTABLE_DEPTH",
        "observed_at": "2026-08-17T10:00:00+00:00",
        "observed_at_ts": 1_000_000.0,
        "market_last": 63620.0,
    }
    row.update(kwargs)
    return row


def _row(observations, **extra):
    payload = {
        "trade_id": extra.pop("trade_id", "cont-test"),
        "executed": False,
        "filled": False,
        "direction": "LONG",
        "source_order_market_evidence": {
            "schema": "source_order_market_evidence_v1",
            "original_limit_price": observations[0]["original_limit_price"],
            "current_limit_price": observations[-1]["current_limit_price"],
            "requested_quantity": observations[0]["requested_quantity"],
            "observations": observations,
        },
    }
    payload.update(extra)
    return payload


def _ticks(fill_rel=0.0, hours=2.1, entry=63600.0):
    ticks = []
    t = fill_rel
    end = fill_rel + hours * 3600
    price = entry
    while t <= end:
        ticks.append({"t": t, "price": price, "best_bid": price - 5, "best_ask": price + 5})
        t += 60
        price += 2
    return ticks


def test_never_executable_is_not_break_even():
    observations = [
        _obs(observed_at_ts=1_000_000 + i, fill_gate_verdict="INSUFFICIENT_EXECUTABLE_DEPTH")
        for i in range(30)
    ]
    record = reconstruct_row(_row(observations, exit_reason="NO_FILL"))
    assert record["fill_origin"]["classification"] == NEVER_EXECUTABLE
    assert record["not_a_trade"] is True
    assert record["avoided_exposure"] is True
    assert record["net_pnl_usd"] is None
    assert record["fill_origin"]["net_pnl_usd"] is None
    assert record["replay_complete"] is False


def test_original_limit_becomes_executable():
    observations = [
        _obs(
            fill_gate_verdict="EXECUTABLE",
            side_correct_executable_quote=63590.0,
            visible_executable_qty=0.04,
            recent_executable_aggressor_qty=0.05,
            limit_generation=0,
        )
    ]
    origin = reconstruct_fill_origin(_row(observations))
    assert origin["classification"] == EXECUTABLE_AT_ORIGINAL_LIMIT
    assert origin["label"] == EXECUTABLE_COUNTERFACTUAL
    assert origin["not_a_trade"] is False
    assert origin["partial"] is False


def test_chased_limit_fill_is_not_original():
    observations = [
        _obs(limit_generation=0, limit_price=63600.0, current_limit_price=63600.0, fill_gate_verdict="INSUFFICIENT_EXECUTABLE_DEPTH"),
        _obs(
            observed_at_ts=1_000_060,
            limit_generation=2,
            limit_price=63640.0,
            current_limit_price=63640.0,
            original_limit_price=63600.0,
            fill_gate_verdict="EXECUTABLE",
            side_correct_executable_quote=63630.0,
            visible_executable_qty=0.04,
            recent_executable_aggressor_qty=0.05,
        ),
    ]
    origin = reconstruct_fill_origin(_row(observations))
    assert origin["classification"] == EXECUTABLE_ONLY_AFTER_CHASE
    assert origin["price"] == 63640.0


def test_partial_executable_is_not_full_fill():
    observations = [
        _obs(
            fill_gate_verdict="EXECUTABLE",
            requested_quantity=0.03,
            visible_executable_qty=0.01,
            recent_executable_aggressor_qty=0.02,
            side_correct_executable_quote=63590.0,
        )
    ]
    origin = reconstruct_fill_origin(_row(observations))
    assert origin["classification"] == PARTIALLY_EXECUTABLE
    assert origin["partial"] is True
    assert origin["full"] is False


def test_expired_order_later_executable():
    observations = [
        _obs(observed_at_ts=1_000_000, fill_gate_verdict="INSUFFICIENT_EXECUTABLE_DEPTH"),
        _obs(
            observed_at_ts=1_000_000 + 1000,
            fill_gate_verdict="EXECUTABLE",
            side_correct_executable_quote=63590.0,
            visible_executable_qty=0.04,
            recent_executable_aggressor_qty=0.05,
        ),
    ]
    origin = reconstruct_fill_origin(_row(observations, block_reason="SHADOW_TTL"), ttl_sec=900)
    assert origin["classification"] == EXECUTABLE_AFTER_EXPIRY


def test_cluster_blocked_executable_stays_blocked_class():
    observations = [
        _obs(
            fill_gate_verdict="EXECUTABLE",
            side_correct_executable_quote=63590.0,
            visible_executable_qty=0.04,
            recent_executable_aggressor_qty=0.05,
        )
    ]
    origin = reconstruct_fill_origin(_row(observations, block_reason="WOULD_BLOCK_CLUSTER_ENTRY"))
    assert origin["classification"] == EXECUTABLE_BUT_BLOCKED
    assert origin["cluster_blocked"] is True
    assert origin["label"] == EXECUTABLE_COUNTERFACTUAL


def test_insufficient_aggressor_is_estimate_not_certain_fill():
    observations = [
        _obs(
            fill_gate_verdict="INSUFFICIENT_RECENT_EXECUTION",
            side_correct_executable_quote=63590.0,
            visible_executable_qty=0.04,
            recent_executable_aggressor_qty=0.0,
        )
    ]
    origin = reconstruct_fill_origin(_row(observations))
    assert origin["label"] == ESTIMATED_FILL_PROBABILITY
    assert origin["not_a_trade"] is True
    assert origin["net_pnl_usd"] is None


def test_complete_120m_replay_can_qualify():
    observations = [
        _obs(
            fill_gate_verdict="EXECUTABLE",
            side_correct_executable_quote=63590.0,
            visible_executable_qty=0.04,
            recent_executable_aggressor_qty=0.05,
        )
    ]
    record = reconstruct_row(_row(observations), ticks=_ticks())
    assert record["exit_replay"]["required_horizons_complete"] is True
    assert record["replay_complete"] is True
    assert record["net_pnl_usd"] is not None
    assert record["eligibility_status"] == "QUALIFIED"


def test_incomplete_replay_cannot_qualify():
    observations = [
        _obs(
            fill_gate_verdict="EXECUTABLE",
            side_correct_executable_quote=63590.0,
            visible_executable_qty=0.04,
            recent_executable_aggressor_qty=0.05,
        )
    ]
    ticks = [{"t": 0, "price": 63600.0}, {"t": 60, "price": 63610.0}]
    record = reconstruct_row(_row(observations), ticks=ticks)
    assert record["replay_complete"] is False
    assert record["eligibility_status"] == "NOT_QUALIFIED"


def test_synthetic_bps_offset_is_quote_touch_estimate():
    observations = [
        _obs(
            fill_gate_verdict="INSUFFICIENT_EXECUTABLE_DEPTH",
            side_correct_executable_quote=63650.0,
            limit_price=63600.0,
        )
    ]
    record = reconstruct_row(_row(observations))
    closer = next(item for item in record["alternative_entries"]["rows"] if item["name"] == "8_bps_closer")
    assert closer["kind"] == "synthetic_bps"
    assert closer["certainty"] == ESTIMATED_FILL_PROBABILITY
    assert closer["live_recommendation"] is False
    assert record["alternative_entries"]["live_recommendation"] == "not qualified"


def test_later_executable_beats_earlier_estimated_aggressor_gap():
    observations = [
        _obs(
            fill_gate_verdict="INSUFFICIENT_RECENT_EXECUTION",
            side_correct_executable_quote=63590.0,
            visible_executable_qty=0.04,
            recent_executable_aggressor_qty=0.0,
        ),
        _obs(
            observed_at_ts=1_000_030,
            fill_gate_verdict="EXECUTABLE",
            side_correct_executable_quote=63590.0,
            visible_executable_qty=0.04,
            recent_executable_aggressor_qty=0.05,
        ),
    ]
    origin = reconstruct_fill_origin(_row(observations))
    assert origin["classification"] == EXECUTABLE_AT_ORIGINAL_LIMIT
    assert origin["label"] == EXECUTABLE_COUNTERFACTUAL
    assert origin["not_a_trade"] is False


def test_wilson_unknown_when_sample_missing():
    estimate = wilson(None, 0)
    assert estimate["status"] == "UNKNOWN"
    assert estimate["p"] is None
    estimate = wilson(9, 57)
    assert estimate["n"] == 57
    assert estimate["k"] == 9
    assert estimate["status"] == "EMPIRICAL_ESTIMATE"
    assert 0 < estimate["lo"] < estimate["p"] < estimate["hi"] < 1
    assert abs(estimate["p"] - (9 / 57)) < 1e-6
