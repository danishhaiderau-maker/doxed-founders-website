"""Dedicated contract for Tile H11: committed-call fade with a taker entry, 90-min hold."""
import copy

import pytest

import bot
import paper_policy_family_committed_fade_maker_90 as maker
import paper_policy_family_committed_fade_taker_90 as policy
from adaptive_regime_entry import ACTION_STAND_ASIDE, ACTION_TAKER
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    COMMITTED_FADE_TAKER_ADMISSION_POLICY_ID,
    RETIRED_POLICY_IDENTITIES,
    resolve_score_led_paper_admission,
    validate_tile_registry,
)

NOW = 1_790_000_000.0


def _admission(ai):
    return resolve_score_led_paper_admission(
        ai, score_led_enabled=True, research_mode=True, forced_paper=True,
        live_armed=False, bitfinex_live_enabled=False,
    )


def _ai(long_score=70, short_score=30, raw_direction=None, **extra):
    if raw_direction is None:
        raw_direction = "LONG" if long_score >= short_score else "SHORT"
    return {"raw_direction": raw_direction, "raw_decision": "APPROVE", "direction": raw_direction,
            "decision": "APPROVE", "long_score": long_score, "short_score": short_score, **extra}


def test_registry_owns_a_paper_only_relay_ineligible_default_off_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[5] == policy.LANE
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["default_enabled"] is False
    assert spec["id_prefix"] == "cft" and spec["max_active_signals"] == 3
    assert spec["entry_ttl_sec"] == 3 and spec["path_end_sec"] == 5400
    assert spec["admission_treatment"] == COMMITTED_FADE_TAKER_ADMISSION_POLICY_ID
    assert policy.POLICY_ID == spec["raw_policy_id"] not in RETIRED_POLICY_IDENTITIES
    assert policy.POLICY_ID != maker.POLICY_ID and policy.POLICY_SIGNATURE != maker.POLICY_SIGNATURE
    assert spec["entry_policy"]["trades_raw_ai_no_trade"] is False
    assert spec["entry_policy"]["direction_source"] == "INVERTED_SCORE_LED_SIDE"
    assert spec["pre_registration"]["kill"]["k1_after_fills"] == 80
    assert spec["pre_registration"]["kill"]["k4_max_drawdown_usd"] == 1.0
    assert spec["pre_registration"]["promotion"]["min_fills"] == 150


@pytest.mark.parametrize("raw", [_ai(70, 30), _ai(30, 70), _ai(55, 45),
                                 _ai(62, 38, raw_direction="NO_TRADE"), _ai(70, 30, raw_direction="SHORT"),
                                 _ai(50, 50), _ai(70, 30, ai_error=True)])
def test_admission_matches_the_committed_fade_maker_call_for_call(raw):
    ours = policy.lane_admission(raw, _admission(raw))
    theirs = maker.lane_admission(raw, _admission(raw))
    assert (ours["accepted"], ours["direction"], ours["reason"]) == (
        theirs["accepted"], theirs["direction"], theirs["reason"])
    if ours["accepted"]:
        assert ours["lane_ai"]["effective_research_admission_policy_id"] == COMMITTED_FADE_TAKER_ADMISSION_POLICY_ID


def test_decide_entry_takes_a_capped_marketable_limit():
    decision = policy.decide_entry(direction="SHORT", signal_ts=NOW, bid=100_000.0, ask=100_001.0,
                                   bbo_ts=NOW - 1.0, reference_price=100_000.5)
    assert decision["action"] == ACTION_TAKER and decision["entry_ttl_sec"] == 3
    assert 100_000.0 * (1 - 0.0005) <= decision["limit_price"] <= 100_000.0
    assert policy.chase_due(created_ts=NOW, last_chase_ts=None, now=NOW + 2) is False
    wide = policy.decide_entry(direction="SHORT", signal_ts=NOW, bid=100_000.0, ask=100_040.0,
                               bbo_ts=NOW - 1.0, reference_price=100_020.0)
    assert wide["action"] == ACTION_STAND_ASIDE and wide["reason"] == "SPREAD_ABOVE_MAX"
    stale = policy.decide_entry(direction="SHORT", signal_ts=NOW, bid=100_000.0, ask=100_001.0,
                                bbo_ts=NOW - 6.0, reference_price=100_000.5)
    assert stale["action"] == ACTION_STAND_ASIDE and stale["reason"] == "BBO_STALE"


def test_exit_and_dashboard_disclose_inverted_committed_taker():
    assert policy.EXIT["max_duration_sec"] == 5400 and policy.EXIT["hard_stop_bps"] == 40.0
    payload = policy.dashboard_policy()
    chips = " ".join(payload["filter_chips"])
    assert "PAPER ONLY" in chips and "HINT" in chips
    assert "Side = opposite of score-led AI side" in chips
    assert "Only committed calls" in chips and "Never fades NO_TRADE" in chips
    assert "Taker cap 5bps" in chips and "90m time exit" in chips and "Max 3 open positions" in chips
    assert "opposite of the AI's committed side" in payload["entry"]["trigger"]
    assert payload["pre_registration"]["hypothesis_id"] == "H11_COMMITTED_FADE_TAKER_90_20261004"


def test_runtime_tile_view_refuses_uncommitted_and_inverts_committed():
    for raw, expected in ((_ai(62, 38, raw_direction="NO_TRADE"), "NO_TRADE"), (_ai(80, 20), "SHORT")):
        shared = dict(copy.deepcopy(raw), direction="LONG", decision="APPROVE")
        snapshot = copy.deepcopy(shared)
        ai, direction, spread, _ = bot._tile_view_of_shared_call(
            policy.LANE, raw, shared, _admission(raw), "LONG", 4,
        )
        assert direction == expected and shared == snapshot
