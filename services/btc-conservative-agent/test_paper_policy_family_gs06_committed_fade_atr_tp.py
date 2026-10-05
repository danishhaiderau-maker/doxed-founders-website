"""Dedicated contract for FREEZE21B mid-epoch GS-06: H-A committed fade entry, VIOLENT aside, GS-01 exits."""
import paper_policy_family_gs06_committed_fade_atr_tp as policy
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    COMMITTED_FADE_TAKER_ADMISSION_POLICY_ID,
    RESEARCH_LANE_FAMILY_COMMITTED_FADE_TAKER_90,
    RESEARCH_LANE_FAMILY_GS01_XV_PREMIUM_ATR_TP,
    RETIRED_POLICY_IDENTITIES,
    evaluator_loop_lanes,
    tile_max_active_signals,
    validate_tile_registry,
)
from gs_tile_contract_support import GS_SCHEMA, asia_ts, assert_dashboard, bar, decide

QUIET = bar(atr_pct=50.0, adx=15.0)
TREND = bar(atr_pct=50.0, adx=30.0)
VIOLENT = bar(atr_pct=90.0, adx=15.0)


def assert_mid_epoch_tile(policy, *, number: int, prefix: str, hypothesis_id: str, cap: int) -> dict:
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[number - 1] == policy.LANE
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["default_enabled"] is True
    assert spec["execution_scope"] == "PAPER_ONLY" and spec["lifecycle_state"] == "PAPER_ONLY"
    assert spec["requested_margin_usd"] == 0.25 and spec["account_risk_pct"] == 0.5
    assert spec["id_prefix"] == prefix and spec["max_active_signals"] == cap == tile_max_active_signals(policy.LANE)
    assert spec["exit_policy"]["max_open_positions"] == cap
    assert policy.POLICY_ID == spec["raw_policy_id"] not in RETIRED_POLICY_IDENTITIES
    assert policy.POLICY_SIGNATURE == spec["policy_signature"]
    assert policy.ADAPTIVE_ENTRY is True and policy.MARKET_EXIT_CONTEXT is True
    assert policy.SKIP_FILL_REVALIDATION is True
    pre = spec["pre_registration"]
    assert pre["schema"] == GS_SCHEMA and pre["hypothesis_id"] == hypothesis_id and pre["mid_epoch_addition"] is True
    assert pre["freeze_id"] == "FREEZE21B-20261004" and pre["role"] == "HYPOTHESIS"
    assert pre["target"]["min_fills"] == 30 and pre["kill"]["harm_mean_bp_at_or_below"] == -2.0
    assert pre["kill"]["giveback_rate_above"] == 0.25 and pre["kill"]["latency_p50_above_sec"] == 5.0
    assert pre["kill"]["benchmark_min_edge_bp"] == 1.0
    assert spec["entry_policy"]["regime_audit"] is True
    return spec


def test_registry_owns_a_paper_only_committed_fade_tile_13_with_capacity_two():
    spec = assert_mid_epoch_tile(policy, number=13, prefix="gs6", hypothesis_id="GS-20261005-06", cap=2)
    assert spec["admission_treatment"] == COMMITTED_FADE_TAKER_ADMISSION_POLICY_ID
    assert "signal_clock" not in spec and spec["uses_shared_ai_direction"] is True
    assert policy.LANE not in evaluator_loop_lanes()
    entry, ha = spec["entry_policy"], COMBO_LANE_SPECS[RESEARCH_LANE_FAMILY_COMMITTED_FADE_TAKER_90]["entry_policy"]
    for key in ("direction_source", "commit_rule", "ai_decision_role", "allowed_sessions", "max_spread_bps",
                "max_bbo_age_sec", "taker_protection_bps", "taker_ttl_sec", "trades_raw_ai_no_trade", "refuse_on"):
        assert entry[key] == ha[key], key
    assert entry["fade_allowed_sessions"] == ("ASIA", "EU") and entry["fade_max_spread_bps"] == 3.0
    assert not entry.get("flip_indicator") and not entry.get("bar_clock_trigger")


def test_exit_is_gs01_simple_stack_with_a_90_minute_backstop_and_no_ladder():
    ex = COMBO_LANE_SPECS[policy.LANE]["exit_policy"]
    gs1 = COMBO_LANE_SPECS[RESEARCH_LANE_FAMILY_GS01_XV_PREMIUM_ATR_TP]["exit_policy"]["profiles"]["ALL"]
    prof = ex["profiles"]["ALL"]
    assert {k: v for k, v in prof.items() if k != "time_sec"} == {k: v for k, v in gs1.items() if k != "time_sec"}
    assert prof["time_sec"] == 5400 and ex["max_duration_sec"] == 5400
    assert not ex.get("partial_take_profits") and not prof.get("tp1_atr")
    assert ex["exit_order"] == ("HARD_STOP", "BREAKEVEN_LOCK", "THESIS_CUT", "ATR_TAKE_PROFIT", "TIME_BACKSTOP")
    for banned in ("INDICATOR_FLIP", "VOL_SHOCK", "MFE_GIVEBACK"):
        assert banned not in ex["exit_order"]


def test_quiet_and_trend_are_takers_in_session():
    for b, regime in ((QUIET, "QUIET"), (TREND, "TREND")):
        d = decide(policy, direction="SHORT", engine_bar=b, ts=asia_ts())
        assert d["action"] == "TAKER" and d["regime_at_entry"] == regime and d["exit_profile"] == "ALL"
        assert d["trigger_kind"] == "COMMITTED_FADE" and d["limit_price"] <= 60000.0


def test_violent_stands_aside_with_a_shadow_would_have_row():
    d = decide(policy, direction="SHORT", engine_bar=VIOLENT, ts=asia_ts())
    assert d["action"] == "STAND_ASIDE" and d["reason"] == "REGIME_VIOLENT_STANDS_ASIDE"
    assert d["regime_at_entry"] == "VIOLENT" and d["shadow_would_have"]["would_submit"] is True
    us = decide(policy, direction="SHORT", engine_bar=VIOLENT, ts=asia_ts() + 17 * 3600)
    assert us["reason"] == "REGIME_VIOLENT_STANDS_ASIDE"
    assert us["shadow_would_have"] == {"would_submit": False, "blocked_by": "FADE_SESSION_GATED"}


def test_us_session_and_wide_spread_refuse():
    us = decide(policy, direction="LONG", engine_bar=QUIET, ts=asia_ts() + 17 * 3600)
    assert us["action"] == "STAND_ASIDE" and us["reason"] == "FADE_SESSION_GATED"
    wide = decide(policy, direction="LONG", engine_bar=QUIET, ts=asia_ts(), bid=60000.0, ask=60030.0)
    assert wide["action"] == "STAND_ASIDE" and wide["reason"] == "SPREAD_ABOVE_MAX"


def test_admission_refuses_no_trade_and_ties_and_fades_the_committed_side():
    ok = policy.lane_admission({"raw_direction": "LONG", "shared_ai_call_id": "c1"},
                               {"applied": True, "accepted": True, "effective_direction": "LONG"})
    assert ok["accepted"] is True and ok["direction"] == "SHORT"
    no_trade = policy.lane_admission({"raw_direction": "NO_TRADE"},
                                     {"applied": True, "accepted": True, "effective_direction": "LONG"})
    assert no_trade["accepted"] is False and no_trade["reason"].endswith("RAW_AI_NO_TRADE")
    tie = policy.lane_admission({"raw_direction": "LONG"},
                                {"applied": True, "accepted": False, "reason": "SCORE_TIE"})
    assert tie["accepted"] is False and tie["reason"].endswith("SCORE_TIE")


def test_take_profit_and_break_even_lock():
    d = decide(policy, direction="LONG", engine_bar=bar(atr_pct=50.0, adx=30.0, atr_bp=4.0), ts=asia_ts())
    state, entry = {}, 60000.0
    assert policy.exit_action(entry=entry, direction="LONG", price=entry * 1.0009, age_sec=5,
                              policy_state=state, entry_decision=d, fill_ts=0) is None
    a = policy.exit_action(entry=entry, direction="LONG", price=entry * 1.0011, age_sec=6,
                           policy_state=state, entry_decision=d, fill_ts=0)
    assert a.reason == "GS_ATR_TAKE_PROFIT" and abs(a.book_price - entry * 1.001) < 1e-6
    state = {}
    assert policy.exit_action(entry=entry, direction="LONG", price=entry * 1.00085, age_sec=5,
                              policy_state=state, entry_decision=d, fill_ts=0) is None
    a = policy.exit_action(entry=entry, direction="LONG", price=entry * 1.00009, age_sec=60,
                           policy_state=state, entry_decision=d, fill_ts=0)
    assert a.reason == "GS_BREAKEVEN_LOCK"
    a = policy.exit_action(entry=entry, direction="LONG", price=entry, age_sec=5400,
                           policy_state={}, entry_decision=d, fill_ts=0)
    assert a.reason == "PATH_END_90M"


def test_dashboard_discloses_capacity_two():
    payload = assert_dashboard(policy, "GS-20261005-06", "Max 2 open positions")
    assert payload["exit"]["max_open_positions"] == 2
