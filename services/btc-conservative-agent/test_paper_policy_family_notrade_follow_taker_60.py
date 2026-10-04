"""Dedicated contract for FREEZE21 H-B: NO_TRADE-lean follow with a taker entry, 60-min hold."""
import copy

import pytest

import bot
import paper_policy_family_notrade_follow_taker_60 as policy
from adaptive_regime_entry import ACTION_STAND_ASIDE, ACTION_TAKER
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    NOTRADE_FOLLOW_TAKER_ADMISSION_POLICY_ID,
    RETIRED_POLICY_IDENTITIES,
    RETIRED_TILE_LANES,
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
    return {"raw_direction": raw_direction, "raw_decision": "APPROVE", "direction": raw_direction,
            "decision": "APPROVE", "long_score": long_score, "short_score": short_score, **extra}


def test_registry_owns_a_paper_only_taker_tile_replacing_the_zero_fill_maker():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[1] == policy.LANE
    assert "FAMILY_NOTRADE_FOLLOW_MAKER_60" in RETIRED_TILE_LANES
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["default_enabled"] is True
    assert spec["id_prefix"] == "ntt" and spec["max_active_signals"] == 10
    assert spec["entry_ttl_sec"] == 3 and spec["path_end_sec"] == 3600
    assert spec["admission_treatment"] == NOTRADE_FOLLOW_TAKER_ADMISSION_POLICY_ID
    assert policy.POLICY_ID == spec["raw_policy_id"] not in RETIRED_POLICY_IDENTITIES
    entry = spec["entry_policy"]
    assert entry["mode"] == "TAKER_AT_SIGNAL" and entry["direction_source"] == "SCORE_LED_SIDE"
    assert entry["trades_only_raw_ai_no_trade"] is True
    pre = spec["pre_registration"]
    assert pre["role"] == "HYPOTHESIS" and pre["target"]["min_distinct_hours"] == 150
    assert pre["kill"]["k1_after_distinct_hours"] == 80 and pre["day21"]["decision_day"] == 21


@pytest.mark.parametrize("raw,accepted,direction,reason", [
    (_ai(70, 30), True, "LONG", "LANE_ADMISSION_SCORE_LED_SIDE"),
    (_ai(30, 70), True, "SHORT", "LANE_ADMISSION_SCORE_LED_SIDE"),
    (_ai(70, 30, raw_direction="LONG"), False, "NO_TRADE", "LANE_ADMISSION_RAW_AI_COMMITTED"),
    (_ai(70, 30, raw_direction="SHORT"), False, "NO_TRADE", "LANE_ADMISSION_RAW_AI_COMMITTED"),
])
def test_admission_trades_only_ai_no_trade_calls_on_the_score_led_side(raw, accepted, direction, reason):
    verdict = policy.lane_admission(raw, _admission(raw))
    assert (verdict["accepted"], verdict["direction"], verdict["reason"]) == (accepted, direction, reason)


@pytest.mark.parametrize("raw", [_ai(50, 50), _ai(70, 30, ai_error=True)])
def test_ties_and_errors_refuse(raw):
    assert policy.lane_admission(raw, _admission(raw))["accepted"] is False


def test_decide_entry_is_a_capped_taker_not_a_maker():
    decision = policy.decide_entry(direction="LONG", signal_ts=NOW, bid=100_000.0, ask=100_001.0,
                                   bbo_ts=NOW - 1.0, reference_price=100_000.5)
    assert decision["action"] == ACTION_TAKER and decision["entry_ttl_sec"] == 3
    assert 100_001.0 <= decision["limit_price"] <= 100_001.0 * 1.0005
    wide = policy.decide_entry(direction="LONG", signal_ts=NOW, bid=100_000.0, ask=100_040.0,
                               bbo_ts=NOW - 1.0, reference_price=100_020.0)
    assert wide["action"] == ACTION_STAND_ASIDE


def test_exit_and_dashboard_disclose_no_trade_only_taker():
    assert policy.EXIT["max_duration_sec"] == 3600 and policy.EXIT["hard_stop_bps"] == 40.0
    payload = policy.dashboard_policy()
    chips = " ".join(payload["filter_chips"])
    assert "PAPER ONLY" in chips and "Only AI NO_TRADE calls" in chips
    assert "Never trades an explicit AI LONG/SHORT" in chips and "Taker cap 5bps" in chips
    assert "only when the raw AI returned NO_TRADE" in payload["entry"]["trigger"]


def test_runtime_tile_view_follows_score_led_side_only_on_no_trade():
    for raw, expected in ((_ai(70, 30, raw_direction="LONG"), "NO_TRADE"), (_ai(30, 70), "SHORT")):
        shared = dict(copy.deepcopy(raw), direction="LONG", decision="APPROVE")
        snapshot = copy.deepcopy(shared)
        _, direction, _, _ = bot._tile_view_of_shared_call(policy.LANE, raw, shared, _admission(raw), "LONG", 4)
        assert direction == expected and shared == snapshot
