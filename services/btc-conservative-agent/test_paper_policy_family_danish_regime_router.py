"""Dedicated contract for PHASE03 Danish regime router: the regime decides the thesis (fade vs premium)."""
import paper_policy_family_danish_regime_router as policy
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    validate_tile_registry,
)
from cross_venue_premium import PremiumEvaluator
from gs_tile_contract_support import asia_ts, assert_dashboard, bar, decide
from regime_adaptive_binding import DanishRouterPremiumEvaluator

QUIET = bar(atr_pct=50.0, adx=15.0)
TREND = bar(atr_pct=50.0, adx=30.0)
VIOLENT = bar(atr_pct=90.0, adx=15.0)


def test_registry_owns_a_paper_only_default_off_regime_router():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[9] == policy.LANE
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["default_enabled"] is False
    assert spec["relay_capability"] == "BLOCKED_UNQUALIFIED"
    assert spec["id_prefix"] == "dnr" and spec["max_active_signals"] == 3
    assert spec["admission_treatment"] == "DANISH_REGIME_ROUTER_DYNAMIC_V1"
    assert policy.POLICY_ID == spec["raw_policy_id"]
    entry = spec["entry_policy"]
    assert entry["direction_source"] == "INVERTED_SCORE_LED_SIDE" and entry["mode"] == "REGIME_ADAPTIVE"
    assert entry["regime_trigger"] == {"QUIET": "COMMITTED_FADE", "TREND": "COMMITTED_FADE",
                                       "VIOLENT": "CROSS_VENUE_PREMIUM"}
    pre = spec["pre_registration"]
    assert pre["mid_epoch_addition"] is True and pre["hypothesis_id"] == "GS-20261007-DNR"
    assert pre["freeze_id"] == "FREEZE21B-20261004"


def test_exit_routes_fade_to_90m_and_premium_to_60m():
    ex = COMBO_LANE_SPECS[policy.LANE]["exit_policy"]
    assert ex["family"] == "REGIME_ADAPTIVE_FIRST_TRIGGER_WINS"
    assert ex["regime_profiles"] == {"QUIET": "FADE", "TREND": "FADE", "VIOLENT": "PREMIUM"}
    fade, premium = ex["profiles"]["FADE"], ex["profiles"]["PREMIUM"]
    assert (fade["hard_bp"], fade["cut_bp"], fade["cut_win_sec"], fade["be_floor"], fade["lock_bp"]) == (
        40.0, 12.0, 300, 20.0, 5.0)
    assert (fade["trail_atr"], fade["trail_arm_atr"], fade["time_sec"]) == (1.5, 1.5, 5400)
    assert premium["time_sec"] == 3600 and premium["hard_bp"] == 40.0
    assert ex["max_open_positions"] == 3
    assert ex["exit_order"] == ("HARD_STOP", "THESIS_CUT", "BREAKEVEN_LOCK", "ATR_TRAIL", "TIME_BACKSTOP")


def test_quiet_and_trend_trade_the_committed_fade_and_violent_refuses_it():
    for b, regime in ((QUIET, "QUIET"), (TREND, "TREND")):
        d = decide(policy, direction="SHORT", engine_bar=b, ts=asia_ts())
        assert d["action"] == "TAKER" and d["regime"] == regime
        assert d["trigger_kind"] == "COMMITTED_FADE" and d["exit_profile"] == "FADE"
    violent = decide(policy, direction="SHORT", engine_bar=VIOLENT, ts=asia_ts())
    assert violent["action"] == "STAND_ASIDE"
    assert violent["reason"] == "REGIME_VIOLENT_TRADES_CROSS_VENUE_PREMIUM_ONLY"


def test_violent_trades_the_premium_and_quiet_refuses_it():
    d = decide(policy, direction="LONG", engine_bar=VIOLENT, ts=asia_ts(), ai_feature={"dnrxvp_trigger_id": "x"})
    assert d["action"] == "TAKER" and d["trigger_kind"] == "CROSS_VENUE_PREMIUM" and d["exit_profile"] == "PREMIUM"
    quiet = decide(policy, direction="LONG", engine_bar=QUIET, ts=asia_ts(), ai_feature={"dnrxvp_trigger_id": "x"})
    assert quiet["action"] == "STAND_ASIDE" and quiet["reason"] == "REGIME_QUIET_TRADES_COMMITTED_FADE_ONLY"


def test_own_evaluator_is_the_premium_instance():
    evaluator = policy.make_evaluator()
    assert isinstance(evaluator, DanishRouterPremiumEvaluator) and isinstance(evaluator, PremiumEvaluator)
    assert evaluator.ID_PREFIX == "dnrxvp" and evaluator.TRIGGER_FEATURE_KEY == "dnrxvp_trigger"
    assert evaluator.SHADOW_FILE is None


def _exit(d, bp, age, state, entry=60000.0):
    return policy.exit_action(entry=entry, direction="LONG", price=entry * (1 + bp / 1e4), age_sec=age,
                              policy_state=state, entry_decision=d, fill_ts=0)


def test_fade_profile_cuts_breaks_even_and_times_out_at_90m():
    d = decide(policy, direction="LONG", engine_bar=TREND, ts=asia_ts())
    state = {}
    assert _exit(d, -11.5, 60.0, state) is None
    assert _exit(d, -12.5, 120.0, state).reason == "GS_THESIS_CUT"
    assert _exit(d, -40.5, 900.0, {"schema": "gs_regime_exit_state_v1", "ticks": 3}).reason.startswith(
        "PHYSICAL_HARD_STOP_40")
    assert _exit(d, 3.0, 5399.0, {}) is None
    assert _exit(d, 3.0, 5400.0, {}).reason == "PATH_END_90M"


def test_dashboard_discloses_the_regime_routing_and_capacity_three():
    payload = assert_dashboard(policy, "Max 3 open positions")
    assert "fade" in payload["entry"]["trigger"].lower() and "VIOLENT" in payload["entry"]["trigger"]
