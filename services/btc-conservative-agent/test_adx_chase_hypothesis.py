"""Tests for pre-registered high-ADX farther-chase hypothesis protocol."""
from research_adx_chase_hypothesis import (
    HYPOTHESIS_ID,
    PRE_REGISTERED_HYPOTHESIS,
    evaluate_or_unknown,
    hypothesis_signature,
)
from bitfinex_live_checklist import checklist_receipt


def test_hypothesis_is_pre_registered_and_fail_closed(tmp_path):
    assert PRE_REGISTERED_HYPOTHESIS["hypothesis_id"] == HYPOTHESIS_ID
    assert PRE_REGISTERED_HYPOTHESIS["promotion_gates"]["auto_arm_bitfinex"] is False
    assert hypothesis_signature().startswith("hyp-")
    receipt = evaluate_or_unknown(
        complete_episode_count=5,
        episode_count=889,
        data_root=tmp_path,
    )
    assert receipt["status"] == "UNKNOWN"
    assert "INSUFFICIENT_COMPLETE_EPISODES_FOR_PURGED_WF" in receipt["blockers"]
    assert receipt["bitfinex_arm_allowed"] is False
    assert (tmp_path / "diagnostics" / "adx_chase_hypothesis_receipt.json").is_file()


def test_bitfinex_checklist_never_arms():
    receipt = checklist_receipt(checks={"FORCE_PAPER_STILL_ON": True})
    assert receipt["live_armed"] is False
    assert receipt["bitfinex_arm_allowed"] is False
    assert receipt["status"] == "NOT_ARMED"
    assert "EXPLICIT_USER_ARM_AUTH" in receipt["blockers"]
