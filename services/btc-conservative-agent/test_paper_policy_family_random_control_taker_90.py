"""Dedicated contract for the FREEZE21 random-direction control: H-A's calls and exits, coin side."""
import hashlib

import pytest

import paper_policy_family_committed_fade_taker_90 as fade
import paper_policy_family_random_control_taker_90 as policy
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    RANDOM_CONTROL_ADMISSION_POLICY_ID,
    RETIRED_POLICY_IDENTITIES,
    resolve_score_led_paper_admission,
    validate_tile_registry,
)
from taker_time_exit_binding import coin_side

IDENTITY_FREE = {"direction_source", "coin_salt", "coin_rule", "refuse_on", "ai_decision_role"}


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


def test_registry_owns_the_fourth_tile_as_a_never_promoted_control():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[3] == policy.LANE  # FREEZE21B: the GS/B tiles follow the control
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["default_enabled"] is True
    assert spec["id_prefix"] == "rnd" and spec["admission_treatment"] == RANDOM_CONTROL_ADMISSION_POLICY_ID
    assert policy.POLICY_ID == spec["raw_policy_id"] not in RETIRED_POLICY_IDENTITIES
    assert policy.POLICY_SIGNATURE != fade.POLICY_SIGNATURE
    pre = spec["pre_registration"]
    assert pre["role"] == "CONTROL" and pre["promotion"]["meaning"] == "NEVER_PROMOTED_CONTROL"
    assert pre["kill"]["k1_after_distinct_hours"] is None and pre["kill"]["k4_max_drawdown_usd"] is None


def test_entry_and_exits_are_identical_to_h_a_apart_from_the_side_source():
    ours = {k: v for k, v in policy.ENTRY.items() if k not in IDENTITY_FREE}
    theirs = {k: v for k, v in fade.ENTRY.items() if k not in IDENTITY_FREE}
    assert ours == theirs
    assert policy.EXIT == fade.EXIT
    assert COMBO_LANE_SPECS[policy.LANE]["max_active_signals"] == COMBO_LANE_SPECS[fade.LANE]["max_active_signals"]
    assert policy.ENTRY["direction_source"] == "RANDOM_COIN_ON_COMMITTED_CALL"


def test_coin_is_deterministic_and_balanced():
    salt = policy.ENTRY["coin_salt"]
    first = hashlib.sha256(f"{salt}|call-1".encode()).digest()[0]
    assert coin_side(salt, "call-1") == ("LONG" if first % 2 == 0 else "SHORT")
    assert coin_side(salt, "call-1") == coin_side(salt, "call-1")
    assert coin_side(salt, "") is None and coin_side(salt, None) is None
    sides = [coin_side(salt, f"call-{i}") for i in range(2000)]
    assert 900 < sides.count("LONG") < 1100


@pytest.mark.parametrize("raw", [_ai(70, 30), _ai(30, 70), _ai(55, 45),
                                 _ai(62, 38, raw_direction="NO_TRADE"), _ai(70, 30, raw_direction="SHORT"),
                                 _ai(50, 50), _ai(70, 30, ai_error=True)])
def test_admits_exactly_the_calls_h_a_admits(raw):
    raw = dict(raw, shared_ai_call_id="call-42")
    ours = policy.lane_admission(raw, _admission(raw))
    theirs = fade.lane_admission(raw, _admission(raw))
    assert ours["accepted"] == theirs["accepted"]
    if ours["accepted"]:
        assert ours["direction"] == coin_side(policy.ENTRY["coin_salt"], "call-42")
        assert ours["lane_ai"]["coin_side"] == ours["direction"]
        assert ours["lane_ai"]["effective_research_admission_policy_id"] == RANDOM_CONTROL_ADMISSION_POLICY_ID


def test_missing_call_id_refuses_instead_of_guessing():
    raw = _ai(70, 30)
    verdict = policy.lane_admission(raw, _admission(raw))
    assert verdict["accepted"] is False and verdict["reason"] == "LANE_ADMISSION_NO_CALL_ID_FOR_COIN"
    with_trade = policy.lane_admission(dict(raw, trade_id="t-1"), _admission(raw))
    assert with_trade["accepted"] is True


def test_dashboard_discloses_the_random_side():
    payload = policy.dashboard_policy()
    chips = " ".join(payload["filter_chips"])
    assert "FREEZE21 control" in chips and "deterministic coin" in chips
    assert "random side" in payload["entry"]["trigger"]
