"""Dedicated contract for FREEZE21 H-C: non-AI cross-venue premium reversion, 60-min hold."""
import paper_policy_family_premium_reversion_60m as policy
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    CROSS_VENUE_SIGNAL_CLOCK,
    PREMIUM_REVERSION_ADMISSION_POLICY_ID,
    RETIRED_POLICY_IDENTITIES,
    cross_venue_clock_lanes,
    validate_tile_registry,
)
from cross_venue_lead import LONG, SHORT
from cross_venue_premium import PremiumEvaluator, PremiumRule


def test_registry_owns_a_paper_only_non_ai_clock_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[2] == policy.LANE
    assert cross_venue_clock_lanes() == (policy.LANE, "FAMILY_GS01_XV_PREMIUM_ATR_TP", "FAMILY_GS05_PREMIUM_REGIME_MANAGED")
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["default_enabled"] is True
    assert spec["id_prefix"] == "pmr" and spec["max_active_signals"] == 3
    assert spec["signal_clock"] == CROSS_VENUE_SIGNAL_CLOCK and spec["uses_shared_ai_direction"] is False
    assert spec["path_end_sec"] == 3600 and spec["entry_ttl_sec"] == 3
    assert spec["admission_treatment"] == PREMIUM_REVERSION_ADMISSION_POLICY_ID
    assert policy.POLICY_ID == spec["raw_policy_id"] not in RETIRED_POLICY_IDENTITIES
    entry = spec["entry_policy"]
    assert entry["direction_source"] == "CROSS_VENUE_PREMIUM" and entry["ai_decision_role"] == "NONE"
    assert entry["min_submit_interval_sec"] == 900 and entry["max_submissions_per_hour"] == 4
    pre = spec["pre_registration"]
    assert pre["role"] == "HYPOTHESIS" and pre["target"]["min_distinct_hours"] == 150
    assert pre["kill"]["k1_after_distinct_hours"] == 80 and pre["day21"]["decision_day"] == 21


def test_rule_keeps_the_frozen_premium_thresholds_with_a_sixty_minute_hold():
    rule = policy.RULE
    default = PremiumRule()
    assert rule.hold_sec == 3600
    assert (rule.long_threshold_bps, rule.short_threshold_bps) == (default.long_threshold_bps,
                                                                   default.short_threshold_bps)
    assert rule.mean_window_sec == default.mean_window_sec == 3600
    assert rule.side_for(2.0) == LONG and rule.side_for(-2.0) == SHORT and rule.side_for(0.5) is None


def test_evaluator_writes_the_registered_premium_shadow_file():
    evaluator = policy.make_evaluator()
    assert isinstance(evaluator, PremiumEvaluator)
    assert evaluator.SHADOW_FILE == "xvp_shadow_signals.jsonl"
    assert evaluator.policy_id == policy.POLICY_ID and evaluator.policy_signature == policy.POLICY_SIGNATURE


def test_dashboard_discloses_no_ai_premium_trigger():
    payload = policy.dashboard_policy()
    chips = " ".join(payload["filter_chips"])
    assert "PAPER ONLY" in chips and "Bitfinex convergence" in chips
    assert "premium evaluator (no AI)" in payload["entry"]["trigger"]
