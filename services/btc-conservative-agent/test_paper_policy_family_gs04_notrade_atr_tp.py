"""Dedicated contract for FREEZE21B GS-04: NO_TRADE follow as a taker with GS-01's exits (entry-isolation control)."""
import paper_policy_family_gs04_notrade_atr_tp as policy
from combo_pathway_config import COMBO_LANE_SPECS
from gs_tile_contract_support import assert_dashboard, assert_paper_only_registry_tile, bar, decide


def test_registry_owns_a_shared_ai_taker_tile():
    spec = assert_paper_only_registry_tile(policy, index=7, prefix="gs4", hypothesis_id="GS-20261004-04",
                                           bonferroni_k=4)
    assert spec["entry_policy"]["direction_source"] == "SCORE_LED_SIDE"
    assert spec["entry_policy"]["trades_only_raw_ai_no_trade"] is True


def test_same_exits_as_gs01_and_taker_in_every_regime():
    gs01 = COMBO_LANE_SPECS["FAMILY_GS01_XV_PREMIUM_ATR_TP"]["exit_policy"]["profiles"]
    assert COMBO_LANE_SPECS[policy.LANE]["exit_policy"]["profiles"] == gs01
    for b in (bar(), bar(atr_pct=99.0), bar(spread=4.0)):
        assert decide(policy, engine_bar=b)["action"] == "TAKER"


def test_break_even_lock_after_arming():
    d = decide(policy, engine_bar=bar(atr_bp=4.0))
    state = {}
    entry = 60000.0
    assert policy.exit_action(entry=entry, direction="SHORT", price=entry * (1 - 0.0009), age_sec=10,
                              policy_state=state, entry_decision=d, fill_ts=0) is None  # arms BE at 8 bp
    action = policy.exit_action(entry=entry, direction="SHORT", price=entry * (1 - 0.00005), age_sec=11,
                                policy_state=state, entry_decision=d, fill_ts=0)
    assert action.reason == "GS_BREAKEVEN_LOCK" and action.maker is False


def test_dashboard_discloses_the_rule():
    assert_dashboard(policy, "GS-20261004-04")
