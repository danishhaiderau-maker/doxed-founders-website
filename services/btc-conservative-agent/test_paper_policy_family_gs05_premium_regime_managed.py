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
    d = decide(policy, engine_bar=QUIET, ai_feature=TRIG)
    assert d["action"] == "STAND_ASIDE" and d["reason"] == "REGIME_QUIET_STANDS_ASIDE"
    assert d["regime_at_entry"] == "QUIET" and d["limit_price"] is None
    shadow = d["shadow_would_have"]
    assert shadow["would_submit"] is True and shadow["action"] == "TAKER" and shadow["limit_price"] >= 60000.5
    blocked = decide(policy, engine_bar=QUIET, ai_feature=TRIG, bid=60000.0, ask=60030.0)
    assert blocked["action"] == "STAND_ASIDE" and blocked["reason"] == "REGIME_QUIET_STANDS_ASIDE"
    assert blocked["shadow_would_have"] == {"would_submit": False, "blocked_by": "SPREAD_ABOVE_MAX"}


def test_trend_and_violent_are_takers_with_their_profiles():
    for b, regime, profile in ((TREND, "TREND", "PREMIUM_TREND"), (VIOLENT, "VIOLENT", "PREMIUM_VIOLENT")):
        d = decide(policy, engine_bar=b, ai_feature=TRIG)
        assert d["action"] == "TAKER" and d["regime_at_entry"] == regime and d["exit_profile"] == profile
        assert d["trigger_kind"] == "CROSS_VENUE_PREMIUM" and "shadow_would_have" not in d
    ex = COMBO_LANE_SPECS[policy.LANE]["exit_policy"]["profiles"]
    assert ex["PREMIUM_TREND"]["time_sec"] == 3600 and ex["PREMIUM_VIOLENT"]["time_sec"] == 2700


def test_spread_above_three_bp_stands_aside_in_trend():
    d = decide(policy, engine_bar=TREND, ai_feature=TRIG, bid=60000.0, ask=60030.0)
    assert d["action"] == "STAND_ASIDE" and d["reason"] == "SPREAD_ABOVE_MAX" and "shadow_would_have" not in d


def _walk(decision, path, entry=60000.0, direction="LONG"):
    state, out = {}, []
    remaining, partials = 1.0, []
    for i, bp in enumerate(path):
        price = entry * (1 + (bp if direction == "LONG" else -bp) / 1e4)
        a = policy.exit_action(entry=entry, direction=direction, price=price, age_sec=float(i * 10),
                               remaining_fraction=remaining, completed_partials=tuple(partials),
                               policy_state=state, entry_decision=decision, fill_ts=0)
        if a:
            out.append(a)
            if a.partial_key:
                partials.append(a.partial_key)
            remaining = a.remaining_fraction
            if remaining <= 0:
                break
    return out


def test_ladder_tp1_books_half_then_lock_two_bp():
    d = decide(policy, engine_bar=bar(atr_pct=50.0, adx=30.0, atr_bp=4.0), ai_feature=TRIG)
    # ATR 4: TP1 = max(6, 4) = 6 bp, BE arm = max(6, 6) = 6 bp, final TP = max(10, 10) = 10 bp.
    acts = _walk(d, [0.0, 3.0, 6.5, 4.0, 1.5])
    assert [a.reason for a in acts] == ["GS_LADDER_TP1", "GS_BREAKEVEN_LOCK"]
    tp1 = acts[0]
    assert tp1.close_fraction == 0.5 and tp1.remaining_fraction == 0.5 and tp1.maker is True
    assert abs(tp1.book_price - 60000.0 * 1.0006) < 1e-6
    assert acts[1].remaining_fraction == 0.0


def test_final_take_profit_and_trail():
    d = decide(policy, engine_bar=bar(atr_pct=50.0, adx=30.0, atr_bp=4.0), ai_feature=TRIG)
    acts = _walk(d, [0.0, 7.0, 10.5])
    assert [a.reason for a in acts] == ["GS_LADDER_TP1", "GS_ATR_TAKE_PROFIT"]
    assert abs(acts[1].book_price - 60000.0 * 1.001) < 1e-6
    # Trail 1.5 ATR = 6 bp behind a 9.5 bp peak fires at <= 3.5 bp (before the +2 lock).
    acts = _walk(d, [0.0, 7.0, 9.5, 3.4])
    assert [a.reason for a in acts] == ["GS_LADDER_TP1", "GS_ATR_TRAIL"]


def test_hard_stop_thesis_cut_and_time():
    d = decide(policy, engine_bar=VIOLENT, ai_feature=TRIG)
    assert [a.reason for a in _walk(d, [0.0, -8.5])] == ["GS_THESIS_CUT"]
    state = {}
    a = policy.exit_action(entry=60000.0, direction="SHORT", price=60000.0 * 1.0036, age_sec=400,
                           policy_state=state, entry_decision=d, fill_ts=0)
    assert a.reason.startswith("PHYSICAL_HARD_STOP_35")
    a = policy.exit_action(entry=60000.0, direction="LONG", price=60000.0, age_sec=2700,
                           policy_state={}, entry_decision=d, fill_ts=0)
    assert a.reason == "PATH_END_45M"


def test_dashboard_discloses_the_rule():
    payload = assert_dashboard(policy, "GS-20261005-05", "Max 1 open position")
    assert payload["exit"]["max_open_positions"] == 1
    assert tile_number(policy.LANE) == 12
