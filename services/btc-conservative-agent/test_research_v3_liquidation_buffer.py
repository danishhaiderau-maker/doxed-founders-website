import copy

from research_v3_liquidation_buffer import (
    build_liquidation_buffer_receipt,
    verify_liquidation_buffer_receipt,
)


def observation(episode_id="e-1", *, direction="LONG"):
    mark, liquidation = (95.0, 80.0) if direction == "LONG" else (105.0, 120.0)
    buffer_pct = abs(mark - liquidation) / mark * 100.0
    return {
        "schema": "exchange_liquidation_buffer_observation_v1",
        "episode_id": episode_id,
        "policy_id": "p",
        "direction": direction,
        "leverage": 10.0,
        "margin_usd": 100.0,
        "equity_usd": 1000.0,
        "entry_price": 100.0,
        "worst_adverse_mark_price": mark,
        "exchange_liquidation_price": liquidation,
        "maintenance_margin_rate_pct": 0.5,
        "max_adverse_excursion_pct": 5.0,
        "max_drawdown_usd": 5.0,
        "observed_buffer_pct": buffer_pct,
        "source_receipt_ids": [f"bitfinex-position-{episode_id}"],
    }


def receipt(*rows, minimum=10.0):
    return build_liquidation_buffer_receipt(
        policy_id="p", observations=rows,
        minimum_required_buffer_pct=minimum,
    )


def test_long_and_short_exchange_reported_buffers_pass():
    result = verify_liquidation_buffer_receipt(
        receipt(observation("e-long"), observation("e-short", direction="SHORT")),
        policy_id="p", executed_episode_ids=["e-long", "e-short"],
    )
    assert result["passed"] is True
    assert result["no_guessed_liquidation_math"] is True


def test_missing_or_boolean_evidence_fails_closed():
    missing = verify_liquidation_buffer_receipt(
        None, policy_id="p", executed_episode_ids=["e-1"],
    )
    asserted = verify_liquidation_buffer_receipt(
        True, policy_id="p", executed_episode_ids=["e-1"],
    )
    assert missing["passed"] is False
    assert asserted["passed"] is False
    assert "LIQUIDATION_BUFFER_RECEIPT_MISSING" in missing["defects"]


def test_tampered_receipt_and_policy_or_episode_mismatch_fail_closed():
    original = receipt(observation())
    tampered = copy.deepcopy(original)
    tampered["observations"][0]["leverage"] = 100.0
    assert verify_liquidation_buffer_receipt(
        tampered, policy_id="p", executed_episode_ids=["e-1"],
    )["passed"] is False
    wrong_policy = verify_liquidation_buffer_receipt(
        original, policy_id="other", executed_episode_ids=["e-1"],
    )
    wrong_cohort = verify_liquidation_buffer_receipt(
        original, policy_id="p", executed_episode_ids=["e-2"],
    )
    assert "LIQUIDATION_BUFFER_POLICY_ID_MISMATCH" in wrong_policy["defects"]
    assert "LIQUIDATION_BUFFER_EPISODE_COHORT_MISMATCH" in wrong_cohort["defects"]


def test_missing_bound_field_source_and_wrong_direction_fail_closed():
    row = observation()
    row.pop("maintenance_margin_rate_pct")
    row["source_receipt_ids"] = []
    row["exchange_liquidation_price"] = 110.0
    result = verify_liquidation_buffer_receipt(
        receipt(row), policy_id="p", executed_episode_ids=["e-1"],
    )
    assert result["passed"] is False
    assert "OBSERVATION_0_MAINTENANCE_MARGIN_RATE_PCT_INVALID" in result["defects"]
    assert "OBSERVATION_0_SOURCE_RECEIPT_REQUIRED" in result["defects"]
    assert "OBSERVATION_0_LIQUIDATION_SIDE_INCONSISTENT" in result["defects"]


def test_buffer_math_mismatch_and_below_minimum_fail_closed():
    wrong_math = observation()
    wrong_math["observed_buffer_pct"] = 99.0
    result = verify_liquidation_buffer_receipt(
        receipt(wrong_math), policy_id="p", executed_episode_ids=["e-1"],
    )
    assert "OBSERVATION_0_OBSERVED_BUFFER_MISMATCH" in result["defects"]
    below = verify_liquidation_buffer_receipt(
        receipt(observation(), minimum=20.0),
        policy_id="p", executed_episode_ids=["e-1"],
    )
    assert "OBSERVATION_0_MINIMUM_BUFFER_NOT_MET" in below["defects"]


def test_mae_margin_and_maintenance_evidence_are_consistency_checked():
    row = observation()
    row["max_adverse_excursion_pct"] = 4.0
    row["margin_usd"] = 1001.0
    row["maintenance_margin_rate_pct"] = 101.0
    result = verify_liquidation_buffer_receipt(
        receipt(row), policy_id="p", executed_episode_ids=["e-1"],
    )
    assert "OBSERVATION_0_MAX_ADVERSE_EXCURSION_MISMATCH" in result["defects"]
    assert "OBSERVATION_0_MARGIN_EXCEEDS_EQUITY" in result["defects"]
    assert "OBSERVATION_0_MAINTENANCE_MARGIN_RATE_INVALID" in result["defects"]
