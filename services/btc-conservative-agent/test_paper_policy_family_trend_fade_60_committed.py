"""Dedicated contract for Tile 2: Trend Fade 60 on committed AI calls only (explicit side, gap >= 30)."""
import copy

import pytest

import bot
import paper_policy_family_trend_fade_60 as tile1
import paper_policy_family_trend_fade_60_committed as policy
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    COMMITTED_FADE_MIN_SCORE_GAP,
    INVERTED_COMMITTED_ADMISSION_POLICY_ID,
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


def _ai(long_score=70, short_score=30, raw_direction=None, **extra):
    if raw_direction is None:
        raw_direction = "LONG" if long_score >= short_score else "SHORT"
    return {"raw_direction": raw_direction, "raw_decision": "APPROVE", "direction": raw_direction,
            "decision": "APPROVE", "long_score": long_score, "short_score": short_score, **extra}


def test_registry_owns_a_paper_only_relay_ineligible_commit_only_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[:2] == (tile1.LANE, policy.LANE)
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["default_enabled"] is False
    assert spec["id_prefix"] == "ftc" and spec["max_active_signals"] == 1
    assert spec["requested_margin_usd"] == 0.25
    assert spec["implementation_modules"] == ("paper_policy_family_trend_fade_60_committed.py",)
    assert spec["dedicated_test_modules"] == ("test_paper_policy_family_trend_fade_60_committed.py",)
    assert spec["admission_treatment"] == INVERTED_COMMITTED_ADMISSION_POLICY_ID
    assert policy.POLICY_ID == spec["raw_policy_id"] not in RETIRED_POLICY_IDENTITIES
    assert policy.POLICY_SIGNATURE == spec["policy_signature"] != COMBO_LANE_SPECS[tile1.LANE]["policy_signature"]
    assert policy.LANE not in RETIRED_TILE_LANES
    assert "FAMILY_TREND_FADE_60_LADDER" in RETIRED_TILE_LANES
    assert "FAMILY_TREND_FADE_60_LADDER" not in ACTIVE_TILE_ORDER
    assert spec["entry_policy"]["min_score_gap"] == COMMITTED_FADE_MIN_SCORE_GAP == 30.0
    assert spec["entry_policy"]["trades_raw_ai_no_trade"] is False


def test_exit_and_execution_are_identical_to_tile_1():
    ours, base = COMBO_LANE_SPECS[policy.LANE], COMBO_LANE_SPECS[tile1.LANE]
    assert ours["exit_policy"] == base["exit_policy"]
    assert ours["risk_limits"] == base["risk_limits"] and ours["path_end_sec"] == base["path_end_sec"]
    differing = {k for k in set(ours["entry_policy"]) | set(base["entry_policy"])
                 if ours["entry_policy"].get(k) != base["entry_policy"].get(k)}
    assert differing <= {"refuse_on", "trades_raw_ai_no_trade", "min_score_gap", "commit_rule"}


def test_pre_registration_has_keep_and_kill_gates():
    pre = COMBO_LANE_SPECS[policy.LANE]["pre_registration"]
    assert pre["promotion"]["min_fills"] == 150
    assert pre["kill"]["k5_max_days_without_promotion"] == 21


@pytest.mark.parametrize("long_score,short_score,ours", [(70, 30, "SHORT"), (30, 70, "LONG"), (65, 35, "SHORT")])
def test_committed_call_with_gap_at_least_30_is_faded(long_score, short_score, ours):
    raw = _ai(long_score, short_score)
    original = copy.deepcopy(raw)
    view = policy.lane_admission(raw, _admission(raw))
    assert raw == original
    assert (view["accepted"], view["direction"]) == (True, ours)
    assert view["lane_ai"]["effective_research_admission_policy_id"] == INVERTED_COMMITTED_ADMISSION_POLICY_ID


@pytest.mark.parametrize("raw,reason", [
    (_ai(62, 38, raw_direction="NO_TRADE", direction="NO_TRADE", decision="REJECT"), "RAW_AI_NO_TRADE"),
    (_ai(80, 10, raw_direction="NO_TRADE", explicit_abstain=True), "RAW_AI_NO_TRADE"),
    (_ai(70, 30, raw_direction="SHORT"), "SCORE_DIRECTION_MISMATCH"),
    (_ai(70, 30, score_direction_mismatch=True), "SCORE_DIRECTION_MISMATCH"),
    (_ai(64, 35), "SCORE_GAP_BELOW_MIN"),
    (_ai(51, 49), "SCORE_GAP_BELOW_MIN"),
    (_ai(50, 50), "SCORE_LED_TRUE_TIE"),
    (_ai(70, 30, ai_error=True), "AI_ERROR"),
])
def test_uncommitted_calls_are_never_faded(raw, reason):
    view = policy.lane_admission(raw, _admission(raw))
    assert view["accepted"] is False and view["direction"] == "NO_TRADE"
    assert view["reason"].endswith(reason)


def test_tile_1_still_fades_no_trade_and_small_gaps():
    for raw in (_ai(62, 38, raw_direction="NO_TRADE"), _ai(51, 49)):
        view = tile1.lane_admission(raw, _admission(raw))
        assert view["accepted"] is True


def test_dashboard_discloses_commit_rule():
    payload = policy.dashboard_policy()
    chips = " ".join(payload["filter_chips"])
    assert "Only committed calls" in chips and "gap ≥30" in chips and "Never fades NO_TRADE" in chips
    assert "60m time exit" in chips and "Stop 40bp" in chips


def test_runtime_tile_view_refuses_uncommitted_and_inverts_committed():
    for raw, expected in ((_ai(62, 38, raw_direction="NO_TRADE"), "NO_TRADE"), (_ai(80, 20), "SHORT")):
        shared = dict(copy.deepcopy(raw), direction="LONG", decision="APPROVE")
        snapshot = copy.deepcopy(shared)
        ai, direction, spread, _ = bot._tile_view_of_shared_call(
            policy.LANE, raw, shared, _admission(raw), "LONG", 4,
        )
        assert direction == expected and shared == snapshot
        assert (spread == 0) == (expected == "NO_TRADE")
