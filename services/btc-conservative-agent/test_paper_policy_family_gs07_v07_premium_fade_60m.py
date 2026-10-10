"""Tile 14 · GS-07 V07: premium fade on the 60-min mean, +/-2.5 bp, 300 s / 12 per hour, 6 open."""
import paper_policy_family_gs07_v07_premium_fade_60m as policy
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    CROSS_VENUE_SIGNAL_CLOCK,
    GS07_V07_ADMISSION_POLICY_ID,
    cross_venue_clock_lanes,
    validate_tile_registry,
)
from cross_venue_premium import PremiumEvaluator


def test_registry_spec_matches_v07():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert policy.LANE in ACTIVE_TILE_ORDER and policy.LANE in cross_venue_clock_lanes()
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["default_enabled"] is False
    assert spec["signal_clock"] == CROSS_VENUE_SIGNAL_CLOCK and spec["uses_shared_ai_direction"] is False
    assert spec["admission_treatment"] == GS07_V07_ADMISSION_POLICY_ID
    assert spec["path_end_sec"] == 3600 and spec["max_active_signals"] == 6
    entry = spec["entry_policy"]
    assert entry["premium_mean_window_sec"] == 3600
    assert entry["premium_long_threshold_bps"] == 2.5 and entry["premium_short_threshold_bps"] == -2.5
    assert entry["min_submit_interval_sec"] == 300 and entry["max_submissions_per_hour"] == 12


def test_rule_and_evaluator():
    assert policy.RULE.hold_sec == 3600
    assert isinstance(policy.make_evaluator(), PremiumEvaluator)
