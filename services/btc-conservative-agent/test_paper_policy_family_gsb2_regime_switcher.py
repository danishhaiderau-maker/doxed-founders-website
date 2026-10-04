"""Dedicated contract for FREEZE21B B2: regime switcher (CVD divergence when quiet/violent, committed fade when trending)."""
import paper_policy_family_gsb2_regime_switcher as policy
from combo_pathway_config import evaluator_loop_lanes, is_evaluator_clock_lane
from gs_tile_contract_support import asia_ts, assert_dashboard, assert_paper_only_registry_tile, bar, decide

CVD = {"cvd_trigger_id": "cvd-1-long"}


def test_registry_owns_a_shared_ai_tile_that_also_rides_the_bar_clock():
    spec = assert_paper_only_registry_tile(policy, index=9, prefix="gb2", hypothesis_id="GS-20261004-B2",
                                           bonferroni_k=3)
    assert spec["uses_shared_ai_direction"] is True
    assert not is_evaluator_clock_lane(policy.LANE) and policy.LANE in evaluator_loop_lanes()
    assert spec["entry_policy"]["bar_clock_trigger"]


def test_each_regime_trades_only_its_own_trigger():
    assert decide(policy, engine_bar=bar(), ai_feature=CVD)["action"] == "MAKER"
    quiet_fade = decide(policy, engine_bar=bar(), ts=asia_ts())
    assert quiet_fade["action"] == "STAND_ASIDE" and quiet_fade["trigger_kind"] == "COMMITTED_FADE"
    trend_fade = decide(policy, engine_bar=bar(adx=30.0), ts=asia_ts())
    assert trend_fade["action"] == "MAKER" and trend_fade["exec"]["kind"] == "OFFSET"
    assert trend_fade["exit_profile"] == "MOM_TREND" and trend_fade["flip_indicator"] == "CVD_TREND_20"
    assert trend_fade["reprice_ages"] == [120, 240, 300, 420, 540, 600, 720, 840]
    assert decide(policy, engine_bar=bar(adx=30.0), ai_feature=CVD)["action"] == "STAND_ASIDE"
    violent = decide(policy, engine_bar=bar(atr_pct=90.0), ai_feature=CVD)
    assert violent["exec"]["kind"] == "DEEP" and violent["exit_profile"] == "REV_VIOLENT"


def test_committed_fade_follows_h_a_sessions():
    us = asia_ts() + 14 * 3600  # 17:00 UTC
    assert decide(policy, engine_bar=bar(adx=30.0), ts=us)["reason"] == "FADE_SESSION_GATED"


def test_rev_thesis_cut_runs_on_sixty_second_closes():
    d = decide(policy, engine_bar=bar(atr_bp=4.0), ai_feature=CVD)
    state = {}
    kw = dict(entry=60000.0, direction="LONG", policy_state=state, entry_decision=d, fill_ts=0)
    assert policy.exit_action(price=60000.0 * (1 - 0.001), age_sec=30, **kw) is None  # -10 bp, not a close yet
    action = policy.exit_action(price=60000.0 * (1 - 0.001), age_sec=60.5, **kw)
    assert action.reason == "GS_THESIS_CUT"


def test_dashboard_discloses_the_rule():
    payload = assert_dashboard(policy, "GS-20261004-B2")
    assert "CVD evaluator" in payload["entry"]["cadence_label"]
