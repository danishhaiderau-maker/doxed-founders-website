"""Dedicated contract for FREEZE21B B1: CVD divergence with regime execution and the MOM exit sets."""
import paper_policy_family_gsb1_cvd_div_regime as policy
from combo_pathway_config import COMBO_LANE_SPECS, PARTIAL_EXIT_RELAY_CAPABILITY
from gs_tile_contract_support import assert_dashboard, assert_paper_only_registry_tile, bar, decide

CVD = {"cvd_trigger_id": "cvd-1-long"}


def test_registry_owns_a_bar_clock_regime_tile():
    spec = assert_paper_only_registry_tile(policy, index=4, prefix="gb1", hypothesis_id="GS-20261004-B1",
                                           bonferroni_k=3)
    assert spec["relay_capability"] == PARTIAL_EXIT_RELAY_CAPABILITY
    pre = spec["pre_registration"]["kill"]
    assert pre["worst_trade_below_bp"] == -45.0 and pre["be_armed_negative_share_above"] == 0.1


def test_regime_execution_quiet_touch_trend_aside_violent_deep():
    quiet = decide(policy, engine_bar=bar(), ai_feature=CVD)
    assert quiet["action"] == "MAKER" and quiet["exec"]["kind"] == "TOUCH" and quiet["exit_profile"] == "MOM_QUIET"
    assert quiet["limit_price"] == 60000.0 and quiet["entry_ttl_sec"] == 660
    assert quiet["reprice_ages"] == [60, 120, 180, 240, 300, 360, 420, 480, 540]
    trend = decide(policy, engine_bar=bar(adx=30.0), ai_feature=CVD)
    assert trend["action"] == "STAND_ASIDE" and trend["regime"] == "TREND"
    violent = decide(policy, engine_bar=bar(atr_pct=85.0, atr_bp=6.0), ai_feature=CVD)
    assert violent["exec"]["kind"] == "DEEP" and violent["exit_profile"] == "MOM_VIOLENT"
    assert violent["limit_price"] <= 60000.25 * (1 - 0.75 * 6.0 / 1e4) + 1e-6
    assert violent["flip_indicator"] == "CVD_DIVERGENCE_20"


def test_touch_fallback_is_a_guarded_taker():
    d = decide(policy, engine_bar=bar(atr_bp=4.0), ai_feature=CVD)
    order = {"limit_price": d["limit_price"]}
    fallback = policy.regime_entry_action(order=order, decision=d, bid=60000.0, ask=60000.5, now=600.0,
                                          created_ts=0.0)
    assert fallback["action"] == "MARKET" and fallback["limit_price"] >= 60000.5
    assert policy.regime_entry_action(order={**order, "regime_fallback_done": True}, decision=d, bid=60000.0,
                                      ask=60000.5, now=601.0, created_ts=0.0)["action"] == "HOLD"
    drifted = policy.regime_entry_action(order=order, decision=d, bid=60100.0, ask=60100.5, now=600.0,
                                         created_ts=0.0)
    assert drifted["action"] == "DROP"


def test_ladder_tp1_is_a_maker_partial():
    d = decide(policy, engine_bar=bar(atr_bp=4.0), ai_feature=CVD)
    state = {}
    action = policy.exit_action(entry=60000.0, direction="LONG", price=60000.0 * 1.00065, age_sec=30,
                                policy_state=state, entry_decision=d, fill_ts=0)
    assert action.reason == "GS_LADDER_TP1" and action.partial_key == "ladder_tp1" and action.maker
    assert action.close_fraction == 0.5 and action.remaining_fraction == 0.5
    assert abs(action.book_price - 60000.0 * 1.0006) < 1e-6
    ex = COMBO_LANE_SPECS[policy.LANE]["exit_policy"]
    assert ex["regime_profiles"]["TREND"] is None and ex["profiles"]["MOM_QUIET"]["hard_bp"] == 30.0


def test_dashboard_discloses_the_rule():
    assert_dashboard(policy, "GS-20261004-B1")
