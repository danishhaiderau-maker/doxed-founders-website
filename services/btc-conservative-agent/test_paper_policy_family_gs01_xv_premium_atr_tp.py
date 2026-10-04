"""Dedicated contract for FREEZE21B GS-01: cross-venue premium follow with the ATR maker take-profit."""
import paper_policy_family_gs01_xv_premium_atr_tp as policy
from combo_pathway_config import COMBO_LANE_SPECS, CROSS_VENUE_SIGNAL_CLOCK, cross_venue_clock_lanes
from cross_venue_premium import PremiumEvaluator
from gs_tile_contract_support import assert_dashboard, assert_paper_only_registry_tile, bar, decide
from regime_adaptive_binding import GsPremiumEvaluator


def test_registry_owns_a_paper_only_non_ai_premium_tile():
    spec = assert_paper_only_registry_tile(policy, index=4, prefix="gs1", hypothesis_id="GS-20261004-01",
                                           bonferroni_k=4)
    assert spec["signal_clock"] == CROSS_VENUE_SIGNAL_CLOCK and spec["uses_shared_ai_direction"] is False
    assert policy.LANE in cross_venue_clock_lanes()
    entry = spec["entry_policy"]
    assert entry["direction_source"] == "CROSS_VENUE_PREMIUM" and entry["max_spread_bps"] == 3.0
    assert spec["pre_registration"]["kill"]["latency_p50_above_sec"] == 5.0


def test_own_evaluator_reuses_the_premium_rule_without_a_shadow_file():
    evaluator = policy.make_evaluator()
    assert isinstance(evaluator, GsPremiumEvaluator) and isinstance(evaluator, PremiumEvaluator)
    assert evaluator.SHADOW_FILE is None and evaluator.ID_PREFIX == "gsxvp"
    assert evaluator.policy_id == policy.POLICY_ID


def test_premium_trigger_is_a_taker_in_every_regime():
    for b in (bar(), bar(atr_pct=95.0), bar(adx=40.0)):
        d = decide(policy, engine_bar=b, ai_feature={"gsxvp_trigger_id": "x1"})
        assert d["action"] == "TAKER" and d["trigger_kind"] == "CROSS_VENUE_PREMIUM" and d["exit_profile"] == "ALL"
        assert d["limit_price"] >= 60000.5


def test_spread_above_three_bp_stands_aside():
    d = decide(policy, engine_bar=bar(), ai_feature={"gsxvp_trigger_id": "x"}, bid=60000.0, ask=60030.0)
    assert d["action"] == "STAND_ASIDE" and d["reason"] == "SPREAD_ABOVE_MAX"


def test_exit_take_profit_books_the_maker_target():
    ex = COMBO_LANE_SPECS[policy.LANE]["exit_policy"]["profiles"]["ALL"]
    assert (ex["tp_atr"], ex["tp_floor"], ex["be_atr"], ex["lock_bp"], ex["hard_bp"]) == (2.5, 8.0, 2.0, 1.0, 35.0)
    decision = decide(policy, engine_bar=bar(atr_bp=4.0), ai_feature={"gsxvp_trigger_id": "x"})
    state = {}
    entry = 60000.0
    assert policy.exit_action(entry=entry, direction="LONG", price=entry * 1.0005, age_sec=5,
                              policy_state=state, entry_decision=decision, fill_ts=0) is None
    action = policy.exit_action(entry=entry, direction="LONG", price=entry * 1.0011, age_sec=6,
                                policy_state=state, entry_decision=decision, fill_ts=0)
    assert action.reason == "GS_ATR_TAKE_PROFIT" and action.maker is True
    assert abs(action.book_price - entry * 1.001) < 1e-6 and action.remaining_fraction == 0.0


def test_dashboard_discloses_the_rule():
    assert_dashboard(policy, "GS-20261004-01")
