"""Dedicated contract for PHASE03 fade pool: committed fade OR premium reversion, pooled exits, any regime."""
import paper_policy_family_fade_pool as policy
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    validate_tile_registry,
)
from cross_venue_premium import PremiumEvaluator
from gs_tile_contract_support import asia_ts, assert_dashboard, bar, decide
from regime_adaptive_binding import FadePoolPremiumEvaluator

QUIET = bar(atr_pct=50.0, adx=15.0)
VIOLENT = bar(atr_pct=90.0, adx=15.0)


def test_registry_owns_a_paper_only_default_off_pooled_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[10] == policy.LANE
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["default_enabled"] is False
    assert spec["relay_capability"] == "BLOCKED_UNQUALIFIED"
    assert spec["id_prefix"] == "fdp" and spec["max_active_signals"] == 3
    assert spec["admission_treatment"] == "FADE_POOL_COMMITTED_AND_PREMIUM_V1"
    assert policy.POLICY_ID == spec["raw_policy_id"]
    entry = spec["entry_policy"]
    assert entry["direction_source"] == "INVERTED_SCORE_LED_SIDE" and entry["mode"] == "REGIME_ADAPTIVE"
    # Any regime: no classifier, no per-regime trigger gate.
    assert not entry.get("regime_classifier") and not entry.get("regime_trigger")
    pre = spec["pre_registration"]
    assert pre["mid_epoch_addition"] is True and pre["hypothesis_id"] == "GS-20261007-FDP"
    assert pre["freeze_id"] == "FREEZE21B-20261004"


def test_exit_is_the_single_composite_profile_for_every_regime():
    ex = COMBO_LANE_SPECS[policy.LANE]["exit_policy"]
    assert ex["family"] == "REGIME_ADAPTIVE_FIRST_TRIGGER_WINS"
    assert ex["regime_profiles"] == {"QUIET": "ALL", "TREND": "ALL", "VIOLENT": "ALL"}
    prof = ex["profiles"]["ALL"]
    assert (prof["hard_bp"], prof["cut_bp"], prof["cut_win_sec"], prof["be_floor"], prof["lock_bp"]) == (
        40.0, 12.0, 300, 20.0, 5.0)
    assert (prof["trail_atr"], prof["trail_arm_atr"], prof["time_sec"]) == (1.5, 2.0, 5400)
    assert ex["max_open_positions"] == 3
    assert ex["exit_order"] == ("HARD_STOP", "THESIS_CUT", "BREAKEVEN_LOCK", "ATR_TRAIL", "TIME_BACKSTOP")


def test_committed_fade_and_premium_both_trade_in_any_regime():
    fade = decide(policy, direction="SHORT", engine_bar=QUIET, ts=asia_ts())
    assert fade["action"] == "TAKER" and fade["trigger_kind"] == "COMMITTED_FADE" and fade["exit_profile"] == "ALL"
    premium = decide(policy, direction="LONG", engine_bar=VIOLENT, ts=asia_ts(), ai_feature={"fdpxvp_trigger_id": "x"})
    assert premium["action"] == "TAKER" and premium["trigger_kind"] == "CROSS_VENUE_PREMIUM"
    assert premium["exit_profile"] == "ALL"
    # A violent bar is not its own regime: the pool has no classifier, so it is QUIET everywhere.
    assert premium["regime"] == "QUIET"


def test_own_evaluator_is_the_premium_instance():
    evaluator = policy.make_evaluator()
    assert isinstance(evaluator, FadePoolPremiumEvaluator) and isinstance(evaluator, PremiumEvaluator)
    assert evaluator.ID_PREFIX == "fdpxvp" and evaluator.TRIGGER_FEATURE_KEY == "fdpxvp_trigger"
    assert evaluator.SHADOW_FILE is None


def _exit(d, bp, age, state, entry=60000.0):
    return policy.exit_action(entry=entry, direction="LONG", price=entry * (1 + bp / 1e4), age_sec=age,
                              policy_state=state, entry_decision=d, fill_ts=0)


def test_composite_profile_cuts_breaks_even_and_times_out_at_90m():
    d = decide(policy, direction="LONG", engine_bar=QUIET, ts=asia_ts())
    state = {}
    assert _exit(d, -11.5, 60.0, state) is None
    assert _exit(d, -12.5, 120.0, state).reason == "GS_THESIS_CUT"
    assert _exit(d, -40.5, 900.0, {"schema": "gs_regime_exit_state_v1", "ticks": 3}).reason.startswith(
        "PHYSICAL_HARD_STOP_40")
    assert _exit(d, 3.0, 5399.0, {}) is None
    assert _exit(d, 3.0, 5400.0, {}).reason == "PATH_END_90M"


def test_dashboard_discloses_the_pooled_book_and_capacity_three():
    payload = assert_dashboard(policy, "Max 3 open positions")
    assert "fade" in payload["entry"]["trigger"].lower() and "premium" in payload["entry"]["trigger"].lower()
