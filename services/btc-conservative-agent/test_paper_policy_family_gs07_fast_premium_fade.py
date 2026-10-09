"""Dedicated contract for PHASE03 GS-07: fast cross-venue premium fade off its own 60 s mean, 20-min scalp."""
import paper_policy_family_gs07_fast_premium_fade as policy
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    CROSS_VENUE_SIGNAL_CLOCK,
    cross_venue_clock_lanes,
    validate_tile_registry,
)
from cross_venue_lead import LONG, SHORT
from cross_venue_premium import PremiumEvaluator
from gs_tile_contract_support import assert_dashboard
from regime_adaptive_binding import Gs7PremiumEvaluator

TS = 1_791_100_000.0


def test_registry_owns_a_paper_only_default_off_fast_premium_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[8] == policy.LANE
    assert policy.LANE in cross_venue_clock_lanes()
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["default_enabled"] is False
    assert spec["relay_capability"] == "BLOCKED_UNQUALIFIED"
    assert spec["id_prefix"] == "gs7" and spec["max_active_signals"] == 2
    assert spec["signal_clock"] == CROSS_VENUE_SIGNAL_CLOCK and spec["uses_shared_ai_direction"] is False
    assert spec["admission_treatment"] == "GS07_FAST_CROSS_VENUE_PREMIUM_FADE_NO_AI_V1"
    assert policy.POLICY_ID == spec["raw_policy_id"]
    entry = spec["entry_policy"]
    assert entry["direction_source"] == "CROSS_VENUE_PREMIUM" and entry["ai_decision_role"] == "NONE"
    assert entry["premium_mean_window_sec"] == 60 and entry["premium_min_mean_samples"] == 15
    assert (entry["premium_long_threshold_bps"], entry["premium_short_threshold_bps"]) == (1.75, -1.88)
    assert entry["min_submit_interval_sec"] == 60 and entry["max_submissions_per_hour"] == 60
    pre = spec["pre_registration"]
    assert pre["mid_epoch_addition"] is True and pre["hypothesis_id"] == "GS-20261007-07"
    assert pre["freeze_id"] == "FREEZE21B-20261004"


def test_rule_fades_a_sixty_second_mean_with_the_frozen_thresholds():
    rule = policy.RULE
    assert rule.mean_window_sec == 60 and rule.min_mean_samples == 15
    assert (rule.long_threshold_bps, rule.short_threshold_bps) == (1.75, -1.88)
    assert rule.hold_sec == 1200
    assert rule.side_for(2.0) == LONG and rule.side_for(-2.0) == SHORT and rule.side_for(0.5) is None


def test_own_evaluator_is_the_fast_premium_instance_without_a_shadow_file():
    evaluator = policy.make_evaluator()
    assert isinstance(evaluator, Gs7PremiumEvaluator) and isinstance(evaluator, PremiumEvaluator)
    assert evaluator.SHADOW_FILE is None and evaluator.ID_PREFIX == "gs7xvp"
    assert evaluator.TRIGGER_FEATURE_KEY == "gs7xvp_trigger"
    assert evaluator.policy_id == policy.POLICY_ID


def test_taker_at_signal_and_stand_aside_on_wide_spread():
    d = policy.decide_entry(direction="LONG", signal_ts=TS, bid=60000.0, ask=60000.5, bbo_ts=TS - 0.5)
    assert d["action"] == "TAKER" and d["direction"] == "LONG" and d["limit_price"] >= 60000.5
    assert d["direction_source"] == "CROSS_VENUE_PREMIUM"
    wide = policy.decide_entry(direction="LONG", signal_ts=TS, bid=60000.0, ask=60030.0, bbo_ts=TS - 0.5)
    assert wide["action"] == "STAND_ASIDE" and wide["reason"] == "SPREAD_ABOVE_MAX"


def test_exit_is_the_composite_fade_package_with_late_breakeven_and_trail():
    ex = COMBO_LANE_SPECS[policy.LANE]["exit_policy"]
    assert ex["family"] == "COMPOSITE_FIRST_TRIGGER_WINS"
    assert ex["max_duration_sec"] == 1200 and ex["hard_stop_bps"] == 40.0
    assert ex["breakeven"] == {"trigger_margin_pct": 20.0, "lock_margin_pct": 5.0}
    assert ex["trail"]["atr_k"] == 1.5 and ex["trail"]["arm_atr_k"] == 2.0
    assert ex["max_open_positions"] == 2
    assert ex["exit_order"] == ("HARD_STOP", "BREAKEVEN_LOCK", "ATR_TRAIL", "TIME_EXIT")


def test_dashboard_discloses_the_fast_premium_fade_and_capacity_two():
    payload = assert_dashboard(policy, "1-min mean", "Bitfinex convergence", "Max 2 open positions")
    assert "premium evaluator (no AI)" in payload["entry"]["trigger"]
