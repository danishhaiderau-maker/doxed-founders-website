"""Dedicated contract for FREEZE21B GS-02: NO_TRADE follow with the regime-adaptive entry."""
import paper_policy_family_gs02_notrade_regime_entry as policy
from combo_pathway_config import COMBO_LANE_SPECS
from gs_tile_contract_support import assert_dashboard, assert_paper_only_registry_tile, bar, decide
from regime_adaptive_binding import reprice_ages


def test_registry_owns_a_shared_ai_no_trade_tile():
    spec = assert_paper_only_registry_tile(policy, index=5, prefix="gs2", hypothesis_id="GS-20261004-02",
                                           bonferroni_k=4)
    assert spec["uses_shared_ai_direction"] is True and not spec.get("signal_clock")
    entry = spec["entry_policy"]
    assert entry["direction_source"] == "SCORE_LED_SIDE" and entry["trades_only_raw_ai_no_trade"] is True
    assert entry["regime_classifier"] == {"violent_atr_pct_gte": 66.0, "violent_spread_bp_gte": 2.0,
                                          "trend_adx_gte": None}


def test_admits_only_raw_no_trade_calls():
    no_trade = {"decision": "APPROVE", "direction": "SHORT", "raw_decision": "NO_TRADE",
                "raw_direction": "NO_TRADE", "long_score": 40, "short_score": 70}
    committed = {**no_trade, "raw_decision": "SHORT", "raw_direction": "SHORT"}
    adm = {"applied": True, "accepted": True, "effective_direction": "SHORT"}
    assert policy.lane_admission(no_trade, adm)["accepted"] is True
    assert policy.lane_admission(committed, adm)["accepted"] is False


def test_quiet_is_taker_and_violent_is_a_one_atr_post_only_limit():
    quiet = decide(policy, engine_bar=bar(atr_pct=50.0, spread=0.5))
    assert quiet["regime"] == "QUIET" and quiet["action"] == "TAKER"
    violent = decide(policy, engine_bar=bar(atr_pct=70.0, atr_bp=5.0), bid=60000.0, ask=60000.5)
    assert violent["regime"] == "VIOLENT" and violent["action"] == "MAKER"
    assert violent["limit_price"] <= 60000.25 * (1 - 5.0 / 1e4) + 1e-6
    assert violent["entry_ttl_sec"] == 1800
    assert violent["reprice_ages"] == [300, 480, 600, 780]
    wide = decide(policy, engine_bar=bar(atr_pct=10.0, spread=2.5))
    assert wide["regime"] == "VIOLENT"


def test_violent_limit_chases_a_quarter_gap_then_drops_at_ttl():
    d = decide(policy, engine_bar=bar(atr_pct=70.0, atr_bp=5.0))
    order = {"limit_price": d["limit_price"]}
    assert policy.regime_entry_action(order=order, decision=d, bid=60000.0, ask=60000.5, now=100.0,
                                      created_ts=0.0)["action"] == "HOLD"
    step = policy.regime_entry_action(order=order, decision=d, bid=60000.0, ask=60000.5, now=301.0, created_ts=0.0)
    assert step["action"] == "REPRICE" and step["step_index"] == 1
    assert d["limit_price"] < step["limit_price"] <= 60000.0
    drop = policy.regime_entry_action(order=order, decision=d, bid=60000.0, ask=60000.5, now=1800.0, created_ts=0.0)
    assert drop["action"] == "DROP"


def test_exits_have_no_take_profit_and_an_armed_trail():
    ex = COMBO_LANE_SPECS[policy.LANE]["exit_policy"]["profiles"]["ALL"]
    assert ex["tp_atr"] is None and (ex["be_atr"], ex["trail_atr"], ex["trail_arm_atr"]) == (1.5, 2.0, 2.0)
    assert reprice_ages(COMBO_LANE_SPECS[policy.LANE]["entry_policy"]["regime_exec"]["VIOLENT"]) == [300, 480, 600, 780]


def test_dashboard_discloses_the_rule():
    assert_dashboard(policy, "GS-20261004-02")
