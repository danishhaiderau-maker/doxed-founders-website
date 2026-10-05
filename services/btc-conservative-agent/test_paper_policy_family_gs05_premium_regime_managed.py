"""Dedicated contract for FREEZE21B mid-epoch GS-05: premium trigger, regime-gated taker, ladder exit stack."""
import paper_policy_family_gs05_premium_regime_managed as policy
import paper_policy_family_gs01_xv_premium_atr_tp as gs01
from combo_pathway_config import COMBO_LANE_SPECS, CROSS_VENUE_SIGNAL_CLOCK, cross_venue_clock_lanes, tile_number
from cross_venue_premium import PremiumEvaluator
from gs_tile_contract_support import assert_dashboard, bar, decide
from regime_adaptive_binding import Gs5PremiumEvaluator, GsPremiumEvaluator
from test_paper_policy_family_gs06_committed_fade_atr_tp import assert_mid_epoch_tile

QUIET = bar(atr_pct=50.0, adx=15.0)
TREND = bar(atr_pct=50.0, adx=30.0)
VIOLENT = bar(atr_pct=90.0, adx=15.0)
TRIG = {"gs5xvp_trigger_id": "gs5xvp-1"}


def test_registry_owns_a_paper_only_non_ai_premium_tile_12():
    spec = assert_mid_epoch_tile(policy, number=12, prefix="gs5", hypothesis_id="GS-20261005-05", cap=1)
    assert spec["signal_clock"] == CROSS_VENUE_SIGNAL_CLOCK and spec["uses_shared_ai_direction"] is False
    assert policy.LANE in cross_venue_clock_lanes()
    entry, gs1 = spec["entry_policy"], COMBO_LANE_SPECS[gs01.LANE]["entry_policy"]
    for key in ("direction_source", "leader_venues", "premium_mean_window_sec", "premium_min_mean_samples",
                "premium_long_threshold_bps", "premium_short_threshold_bps", "max_spread_bps", "max_bbo_age_sec",
                "max_venue_age_sec", "allowed_sessions", "min_submit_interval_sec", "max_submissions_per_hour",
                "taker_protection_bps", "taker_ttl_sec", "fill_model", "regime_source"):
        assert entry[key] == gs1[key], key
    assert entry["premium_long_threshold_bps"] == 1.75 and entry["premium_short_threshold_bps"] == -1.88
    assert entry["regime_classifier"] == {"violent_atr_pct_gte": 80.0, "violent_spread_bp_gte": 3.0,
                                          "trend_adx_gte": 25.0}
    assert spec["pre_registration"]["kill"]["benchmark_lane"] == "FAMILY_PREMIUM_REVERSION_60M"


def test_own_evaluator_instance_and_trigger_namespace():
    evaluator = policy.make_evaluator()
    assert isinstance(evaluator, Gs5PremiumEvaluator) and isinstance(evaluator, PremiumEvaluator)
    assert evaluator.SHADOW_FILE is None and evaluator.ID_PREFIX == "gs5xvp"
    assert evaluator.policy_id == policy.POLICY_ID
    assert type(gs01.make_evaluator()) is GsPremiumEvaluator and gs01.make_evaluator().ID_PREFIX == "gsxvp"


def test_quiet_stands_aside_with_a_shadow_would_have_row():
    policy._BINDING._cadence_log.clear()
    d = decide(policy, engine_bar=QUIET, ai_feature=TRIG)
    assert d["action"] == "STAND_ASIDE" and d["reason"] == "REGIME_QUIET_STANDS_ASIDE"
    assert d["regime_at_entry"] == "QUIET" and d["limit_price"] is None
    shadow = d["shadow_would_have"]
    assert shadow["would_submit"] is True and shadow["action"] == "TAKER" and shadow["limit_price"] >= 60000.5
    blocked = decide(policy, engine_bar=QUIET, ai_feature=TRIG, bid=60000.0, ask=60030.0)
    assert blocked["action"] == "STAND_ASIDE" and blocked["reason"] == "REGIME_QUIET_STANDS_ASIDE"
    assert blocked["shadow_would_have"] == {"would_submit": False, "blocked_by": "SPREAD_ABOVE_MAX"}


def test_trend_and_violent_are_takers_with_their_profiles():
    for b, regime, profile in ((TREND, "TREND", "HC_TREND"), (VIOLENT, "VIOLENT", "GS01_VIOLENT")):
        policy._BINDING._cadence_log.clear()
        d = decide(policy, engine_bar=b, ai_feature=TRIG)
        assert d["action"] == "TAKER" and d["regime_at_entry"] == d["regime_at_signal"] == regime
        assert d["exit_profile"] == profile and d["trigger_kind"] == "CROSS_VENUE_PREMIUM"
        assert "shadow_would_have" not in d and d["pre60_side_bp"] is not None and d["atr_bp"] > 0


def test_trend_arm_copies_h_c_exits_and_cadence_and_violent_copies_gs01():
    hc_entry = COMBO_LANE_SPECS["FAMILY_PREMIUM_REVERSION_60M"]["entry_policy"]
    hc_exit = COMBO_LANE_SPECS["FAMILY_PREMIUM_REVERSION_60M"]["exit_policy"]
    ex = COMBO_LANE_SPECS[policy.LANE]["exit_policy"]
    trend = ex["profiles"]["HC_TREND"]
    assert trend["hard_bp"] == hc_exit["hard_stop_bps"] == 40.0 and trend["time_sec"] == hc_exit["max_duration_sec"]
    assert not hc_exit["breakeven"] and not hc_exit["trail"] and not hc_exit["take_profit"] and not hc_exit["ladder"]
    for key in ("tp_atr", "tp1_atr", "be_atr", "trail_atr", "cut_bp"):
        assert trend.get(key) is None, key
    cell = COMBO_LANE_SPECS[policy.LANE]["entry_policy"]["regime_exec"]["TREND"]
    assert cell["min_submit_interval_sec"] == hc_entry["min_submit_interval_sec"] == 900
    assert cell["max_submissions_per_hour"] == hc_entry["max_submissions_per_hour"] == 4
    gs1 = COMBO_LANE_SPECS[gs01.LANE]["exit_policy"]["profiles"]["ALL"]
    assert ex["profiles"]["GS01_VIOLENT"] == gs1
    assert not ex.get("partial_take_profits") and COMBO_LANE_SPECS[policy.LANE]["relay_capability"] == "BLOCKED_UNQUALIFIED"


def test_trend_cadence_is_one_per_15_minutes_violent_is_not_spaced():
    policy._BINDING._cadence_log.clear()
    t0 = 1_800_000_000.0
    assert decide(policy, engine_bar=TREND, ai_feature=TRIG, ts=t0)["action"] == "TAKER"
    blocked = decide(policy, engine_bar=TREND, ai_feature=TRIG, ts=t0 + 600)
    assert blocked["action"] == "STAND_ASIDE" and blocked["reason"] == "TREND_MIN_SUBMIT_INTERVAL"
    assert decide(policy, engine_bar=VIOLENT, ai_feature=TRIG, ts=t0 + 601)["action"] == "TAKER"
    assert decide(policy, engine_bar=VIOLENT, ai_feature=TRIG, ts=t0 + 602)["action"] == "TAKER"
    for k in (1, 2, 3):
        assert decide(policy, engine_bar=TREND, ai_feature=TRIG, ts=t0 + 900 * k)["action"] == "TAKER"
    capped = decide(policy, engine_bar=TREND, ai_feature=TRIG, ts=t0 + 3599)
    assert capped["reason"] in ("TREND_MIN_SUBMIT_INTERVAL", "TREND_HOURLY_SUBMISSION_CAP")
    policy._BINDING._cadence_log.clear()


def test_spread_above_three_bp_stands_aside_in_trend():
    policy._BINDING._cadence_log.clear()
    d = decide(policy, engine_bar=TREND, ai_feature=TRIG, bid=60000.0, ask=60030.0)
    assert d["action"] == "STAND_ASIDE" and d["reason"] == "SPREAD_ABOVE_MAX" and "shadow_would_have" not in d


def _exit(decision, bp, age, state, direction="LONG", entry=60000.0):
    price = entry * (1 + (bp if direction == "LONG" else -bp) / 1e4)
    return policy.exit_action(entry=entry, direction=direction, price=price, age_sec=age,
                              policy_state=state, entry_decision=decision, fill_ts=0)


def test_trend_exit_is_hold_60_minutes_with_a_40bp_stop_only():
    policy._BINDING._cadence_log.clear()
    d = decide(policy, engine_bar=bar(atr_pct=50.0, adx=30.0, atr_bp=4.0), ai_feature=TRIG)
    state = {}
    for i, bp in enumerate([0.0, 15.0, 30.0, 2.0, -12.0, -39.0]):
        assert _exit(d, bp, 10.0 * i, state) is None
    assert _exit(d, -40.5, 100.0, state).reason.startswith("PHYSICAL_HARD_STOP_40")
    assert _exit(d, 5.0, 3600.0, {}).reason == "PATH_END_60M"


def test_violent_exit_is_gs01_without_ladder_or_trail():
    d = decide(policy, engine_bar=bar(atr_pct=90.0, adx=15.0, atr_bp=4.0), ai_feature=TRIG)
    state = {}
    assert _exit(d, 0.0, 0.0, state) is None and _exit(d, 7.0, 10.0, state) is None
    a = _exit(d, 10.5, 20.0, state)
    assert a.reason == "GS_ATR_TAKE_PROFIT" and a.remaining_fraction == 0.0 and a.partial_key is None
    state = {}
    _exit(d, 0.0, 0.0, state)
    assert _exit(d, -8.5, 10.0, state).reason == "GS_THESIS_CUT"
    state = {}
    _exit(d, 0.0, 0.0, state)
    assert _exit(d, 8.5, 10.0, state) is None  # BE armed at max(6, 2 ATR) = 8
    assert _exit(d, 0.9, 20.0, state).reason == "GS_BREAKEVEN_LOCK"
    assert _exit(d, 0.0, 3600.0, {}).reason == "PATH_END_60M"


def test_dashboard_discloses_the_rule():
    payload = assert_dashboard(policy, "GS-20261005-05", "Max 1 open position")
    assert payload["exit"]["max_open_positions"] == 1
    assert tile_number(policy.LANE) == 12
