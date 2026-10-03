"""Dedicated contract for Tile H9: score-led follow on AI NO_TRADE calls, chased maker entry, 60-min hold."""
import copy

import pytest

import bot
import paper_policy_family_notrade_follow_maker_60 as policy
from adaptive_regime_entry import ACTION_MAKER, ACTION_STAND_ASIDE
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    NOTRADE_FOLLOW_MAKER_ADMISSION_POLICY_ID,
    RETIRED_POLICY_IDENTITIES,
    RETIRED_TILE_LANES,
    chasing_tile_lanes,
    resolve_score_led_paper_admission,
    validate_tile_registry,
)

NOW = 1_790_000_000.0


def _admission(ai):
    return resolve_score_led_paper_admission(
        ai, score_led_enabled=True, research_mode=True, forced_paper=True,
        live_armed=False, bitfinex_live_enabled=False,
    )


def _ai(long_score=70, short_score=30, raw_direction="NO_TRADE", **extra):
    return {"raw_direction": raw_direction, "raw_decision": "REJECT" if raw_direction == "NO_TRADE" else "APPROVE",
            "direction": raw_direction, "decision": "REJECT" if raw_direction == "NO_TRADE" else "APPROVE",
            "long_score": long_score, "short_score": short_score, **extra}


def test_registry_owns_a_paper_only_relay_ineligible_default_off_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[6] == policy.LANE
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["relay_capability"] == "BLOCKED_UNQUALIFIED"
    assert spec["default_enabled"] is False
    assert spec["id_prefix"] == "ntf" and spec["max_active_signals"] == 10
    assert spec["requested_margin_usd"] == 0.25 and spec["entry_ttl_sec"] == 3600
    assert spec["path_end_sec"] == 3600
    assert spec["implementation_modules"] == ("paper_policy_family_notrade_follow_maker_60.py",)
    assert spec["dedicated_test_modules"] == ("test_paper_policy_family_notrade_follow_maker_60.py",)
    assert spec["admission_treatment"] == NOTRADE_FOLLOW_MAKER_ADMISSION_POLICY_ID
    assert policy.POLICY_ID == spec["raw_policy_id"] not in RETIRED_POLICY_IDENTITIES
    assert policy.LANE not in RETIRED_TILE_LANES
    others = {COMBO_LANE_SPECS[lane]["policy_signature"] for lane in ACTIVE_TILE_ORDER if lane != policy.LANE}
    assert policy.POLICY_SIGNATURE == spec["policy_signature"] not in others
    entry = spec["entry_policy"]
    assert entry["chase_windows"] == (2, 3, 4) and entry["remaining_gap_step_pct"] == 25.0
    assert entry["reprice_sec"] == 180 and entry["maker_ttl_sec"] == 3600 and entry["offset_pct"] == 0.15
    assert entry["marketable_fallback"] is False and entry["trades_only_raw_ai_no_trade"] is True
    assert policy.LANE in chasing_tile_lanes()


def test_pre_registration_has_keep_and_kill_gates():
    spec = COMBO_LANE_SPECS[policy.LANE]
    pre = spec["pre_registration"]
    assert pre["hypothesis_id"] == "H9_NOTRADE_FOLLOW_MAKER_60_20261004"
    assert pre["evidence_world"] == "REALISTIC_V1" and pre["ci_method"] == "1H_CLUSTER_BOOTSTRAP_95"
    assert pre["promotion"]["min_fills"] == 500 and pre["promotion"]["min_utc_days"] == 7
    assert pre["promotion"]["min_sessions_each"] == 3 and pre["promotion"]["both_halves_positive"] is True
    assert pre["promotion"]["max_single_day_profit_share"] == 0.30
    assert pre["promotion"]["max_replay_parity_gap_bp"] == 1.0
    assert pre["kill"]["k1_after_fills"] == 300 and pre["kill"]["k4_max_drawdown_usd"] == 3.0
    assert pre["kill"]["k5_max_days_without_promotion"] == 21
    assert pre["variants_tried"] == 1079
    assert spec["promotion_criteria"] == pre["promotion_summary"]
    assert spec["kill_criteria"] == pre["kill_summary"]


@pytest.mark.parametrize("long_score,short_score,ours", [(70, 30, "LONG"), (30, 70, "SHORT"), (52, 48, "LONG")])
def test_no_trade_call_follows_the_score_led_side(long_score, short_score, ours):
    raw = _ai(long_score, short_score)
    original = copy.deepcopy(raw)
    view = policy.lane_admission(raw, _admission(raw))
    assert raw == original
    assert (view["accepted"], view["direction"]) == (True, ours)
    assert view["lane_ai"]["effective_research_admission_policy_id"] == NOTRADE_FOLLOW_MAKER_ADMISSION_POLICY_ID


@pytest.mark.parametrize("raw,reason", [
    (_ai(70, 30, raw_direction="LONG"), "RAW_AI_COMMITTED"),
    (_ai(70, 30, raw_direction="SHORT"), "RAW_AI_COMMITTED"),
    (_ai(50, 50), "SCORE_LED_TRUE_TIE"),
    (_ai(70, 30, ai_error=True), "AI_ERROR"),
])
def test_committed_ties_and_errors_are_refused(raw, reason):
    view = policy.lane_admission(raw, _admission(raw))
    assert view["accepted"] is False and view["direction"] == "NO_TRADE"
    assert view["reason"].endswith(reason)
    assert view["lane_ai"]["approved"] is False


@pytest.mark.parametrize("direction,limit", [("LONG", 99_850.0), ("SHORT", 100_150.0)])
def test_decide_entry_rests_a_maker_limit_then_chases_in_windows_two_to_four(direction, limit):
    decision = policy.decide_entry(direction=direction, signal_ts=NOW, bid=99_999.0, ask=100_001.0,
                                   bbo_ts=NOW - 1.0, reference_price=100_000.0)
    assert decision["action"] == ACTION_MAKER and decision["liquidity_intent"] == "MAKER"
    assert decision["limit_price"] == limit and decision["entry_ttl_sec"] == 3600
    assert policy.decision_is_executable(decision, direction)
    assert policy.chase_due(created_ts=NOW, last_chase_ts=NOW, now=NOW + 540) is False
    assert policy.chase_due(created_ts=NOW, last_chase_ts=NOW, now=NOW + 600) is True
    assert policy.chase_due(created_ts=NOW, last_chase_ts=NOW + 600, now=NOW + 700) is False
    assert policy.chase_due(created_ts=NOW, last_chase_ts=NOW + 1300, now=NOW + 1500) is False
    assert policy.CHASE_STEP == 0.25
    assert policy.marketable_quote_at_limit(direction="LONG", limit_price=100.0, bid=99.0, ask=100.0)
    assert not policy.marketable_quote_at_limit(direction="LONG", limit_price=99.0, bid=99.0, ask=100.0)


def test_chase_moves_a_quarter_of_the_remaining_gap_and_never_crosses():
    new, reason = bot._compute_limit_chase_target("LONG", 99_850.0, 100_000.0, 99_850.0, step_pct=policy.CHASE_STEP)
    assert reason == "LIMIT_CHASE" and 99_850.0 < new < 100_000.0
    assert new == pytest.approx(99_887.5, abs=1.0)


@pytest.mark.parametrize("kwargs,reason", [
    ({"direction": "NO_TRADE"}, "NO_DIRECTION"),
    ({"bid": 0.0}, "BBO_UNAVAILABLE"),
    ({"bbo_ts": NOW - 6.0}, "BBO_STALE"),
])
def test_decide_entry_stands_aside_without_a_fresh_book(kwargs, reason):
    args = {"direction": "LONG", "signal_ts": NOW, "bid": 99_999.0, "ask": 100_001.0,
            "bbo_ts": NOW - 1.0, "reference_price": 100_000.0, **kwargs}
    decision = policy.decide_entry(**args)
    assert decision["action"] == ACTION_STAND_ASIDE and decision["reason"] == reason


def test_exit_is_stop_late_breakeven_late_trail_then_time():
    assert policy.EXIT["max_duration_sec"] == 3600 and policy.EXIT["hard_stop_bps"] == 40.0
    assert policy.exit_config("sync-1")["hard_stop_bps"] == 40.0
    assert policy.SPEC.entry_offset_pct == 0.15 and policy.SPEC.chase_windows == (2, 3, 4)
    assert policy.SPEC.thesis_cut_margin_pct is None
    assert (policy.SPEC.breakeven_trigger_margin_pct, policy.SPEC.breakeven_lock_margin_pct) == (20.0, 5.0)
    assert (policy.SPEC.trail_atr_k, policy.SPEC.trail_activation_atr_k) == (1.5, 2.0)
    assert policy.EXIT["exit_order"] == ("HARD_STOP", "BREAKEVEN_LOCK", "ATR_TRAIL", "TIME_EXIT")


def test_dashboard_discloses_abstention_gate_maker_and_chase():
    payload = policy.dashboard_policy()
    chips = " ".join(payload["filter_chips"])
    assert "PAPER ONLY" in chips and "HINT" in chips
    assert "Only AI NO_TRADE calls" in chips and "Never trades an explicit AI LONG/SHORT" in chips
    assert "Maker limit 0.15%" in chips and "Chase 25%" in chips and "10-15m/15-20m/20-25m" in chips
    assert "60m time exit" in chips and "Stop 40bp" in chips and "Max 10 signals" in chips
    assert payload["pre_registration"]["hypothesis_id"] == "H9_NOTRADE_FOLLOW_MAKER_60_20261004"


def test_runtime_tile_view_follows_no_trade_and_refuses_committed():
    for raw, expected in ((_ai(70, 30), "LONG"), (_ai(80, 20, raw_direction="LONG"), "NO_TRADE")):
        shared = dict(copy.deepcopy(raw), direction="LONG", decision="APPROVE")
        snapshot = copy.deepcopy(shared)
        ai, direction, spread, _ = bot._tile_view_of_shared_call(
            policy.LANE, raw, shared, _admission(raw), "LONG", 4,
        )
        assert direction == expected and shared == snapshot
