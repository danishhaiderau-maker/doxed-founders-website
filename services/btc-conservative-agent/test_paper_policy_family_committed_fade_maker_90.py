"""Dedicated contract for Tile 4: committed-call fade, maker 0.10% offset entry, 90-min hold."""
import copy

import pytest

import bot
import paper_policy_family_committed_fade_maker_90 as policy
from adaptive_regime_entry import ACTION_MAKER, ACTION_STAND_ASIDE
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    COMMITTED_FADE_MAKER_ADMISSION_POLICY_ID,
    RETIRED_POLICY_IDENTITIES,
    RETIRED_TILE_LANES,
    resolve_score_led_paper_admission,
    validate_tile_registry,
)
from maker_time_exit_binding import passive_offset_limit

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
    assert ACTIVE_TILE_ORDER[-1] == policy.LANE
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["relay_capability"] == "BLOCKED_UNQUALIFIED"
    assert spec["default_enabled"] is False
    assert spec["id_prefix"] == "cfm" and spec["max_active_signals"] == 3
    assert spec["requested_margin_usd"] == 0.25 and spec["entry_ttl_sec"] == 1800
    assert spec["path_end_sec"] == 5400
    assert spec["implementation_modules"] == ("paper_policy_family_committed_fade_maker_90.py",)
    assert spec["dedicated_test_modules"] == ("test_paper_policy_family_committed_fade_maker_90.py",)
    assert spec["admission_treatment"] == COMMITTED_FADE_MAKER_ADMISSION_POLICY_ID
    assert policy.POLICY_ID == spec["raw_policy_id"] not in RETIRED_POLICY_IDENTITIES
    assert policy.LANE not in RETIRED_TILE_LANES
    others = {COMBO_LANE_SPECS[lane]["policy_signature"] for lane in ACTIVE_TILE_ORDER if lane != policy.LANE}
    assert policy.POLICY_SIGNATURE == spec["policy_signature"] not in others
    entry = spec["entry_policy"]
    assert entry["chase_windows"] == () and entry["marketable_fallback"] is False
    assert entry["trades_raw_ai_no_trade"] is False and entry["min_score_gap"] is None
    assert entry["fill_model"] == "REALISTIC_V1"


def test_pre_registration_has_keep_and_kill_gates():
    pre = COMBO_LANE_SPECS[policy.LANE]["pre_registration"]
    assert pre["evidence_world"] == "REALISTIC_V1" and pre["ci_method"] == "1H_CLUSTER_BOOTSTRAP_95"
    assert pre["promotion"]["min_fills"] == 150 and pre["promotion"]["min_utc_days"] == 7
    assert pre["promotion"]["per_fill_ev_lower_ci95_gt_bp"] == 0.0
    assert pre["promotion"]["meaning"] == "ELIGIBLE_FOR_OWNER_REVIEW_NEVER_RELAY"
    assert pre["kill"]["k4_max_drawdown_usd"] == 1.0
    assert pre["kill"]["k5_max_days_without_promotion"] == 21
    assert pre["variants_tried"] >= 3416
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert spec["promotion_criteria"] == pre["promotion_summary"]
    assert spec["kill_criteria"] == pre["kill_summary"]


@pytest.mark.parametrize("long_score,short_score,ours", [(70, 30, "SHORT"), (30, 70, "LONG"), (55, 45, "SHORT")])
def test_committed_call_is_faded_at_any_gap(long_score, short_score, ours):
    raw = _ai(long_score, short_score)
    original = copy.deepcopy(raw)
    view = policy.lane_admission(raw, _admission(raw))
    assert raw == original
    assert (view["accepted"], view["direction"]) == (True, ours)
    assert view["lane_ai"]["effective_research_admission_policy_id"] == COMMITTED_FADE_MAKER_ADMISSION_POLICY_ID


@pytest.mark.parametrize("raw,reason", [
    (_ai(62, 38, raw_direction="NO_TRADE", direction="NO_TRADE", decision="REJECT"), "RAW_AI_NO_TRADE"),
    (_ai(80, 10, raw_direction="NO_TRADE", explicit_abstain=True), "RAW_AI_NO_TRADE"),
    (_ai(70, 30, raw_direction="SHORT"), "SCORE_DIRECTION_MISMATCH"),
    (_ai(70, 30, score_direction_mismatch=True), "SCORE_DIRECTION_MISMATCH"),
    (_ai(50, 50), "SCORE_LED_TRUE_TIE"),
    (_ai(70, 30, ai_error=True), "AI_ERROR"),
])
def test_uncommitted_calls_are_never_faded(raw, reason):
    view = policy.lane_admission(raw, _admission(raw))
    assert view["accepted"] is False and view["direction"] == "NO_TRADE"
    assert view["reason"].endswith(reason)


def test_passive_limit_is_offset_away_from_market_and_never_crosses():
    assert passive_offset_limit("LONG", 100_000.0, 0.10, 99_999.0, 100_001.0, 1.0) == 99_900.0
    assert passive_offset_limit("SHORT", 100_000.0, 0.10, 99_999.0, 100_001.0, 1.0) == 100_100.0
    # A market that already moved through the offset clamps to the touch, never past it.
    assert passive_offset_limit("LONG", 100_000.0, 0.10, 99_800.0, 99_801.0, 1.0) == 99_800.0
    assert passive_offset_limit("SHORT", 100_000.0, 0.10, 100_200.0, 100_201.0, 1.0) == 100_201.0
    assert passive_offset_limit("LONG", 0.0, 0.10, 1.0, 2.0, 1.0) is None


@pytest.mark.parametrize("direction,limit", [("LONG", 99_900.0), ("SHORT", 100_100.0)])
def test_decide_entry_rests_one_maker_limit_without_chase(direction, limit):
    decision = policy.decide_entry(direction=direction, signal_ts=NOW, bid=99_999.0, ask=100_001.0,
                                   bbo_ts=NOW - 1.0, reference_price=100_000.0)
    assert decision["action"] == ACTION_MAKER and decision["liquidity_intent"] == "MAKER"
    assert decision["limit_price"] == limit and decision["entry_ttl_sec"] == 1800
    assert policy.decision_is_executable(decision, direction)
    assert policy.chase_due(created_ts=NOW, last_chase_ts=None, now=NOW + 1700) is False


@pytest.mark.parametrize("kwargs,reason", [
    ({"direction": "NO_TRADE"}, "NO_DIRECTION"),
    ({"bid": 0.0}, "BBO_UNAVAILABLE"),
    ({"bbo_ts": NOW - 6.0}, "BBO_STALE"),
    ({"bbo_ts": None}, "BBO_STALE"),
])
def test_decide_entry_stands_aside_without_a_fresh_book(kwargs, reason):
    args = {"direction": "LONG", "signal_ts": NOW, "bid": 99_999.0, "ask": 100_001.0,
            "bbo_ts": NOW - 1.0, "reference_price": 100_000.0, **kwargs}
    decision = policy.decide_entry(**args)
    assert decision["action"] == ACTION_STAND_ASIDE and decision["reason"] == reason
    assert not policy.decision_is_executable(decision, args["direction"])


def test_exit_is_time_plus_catastrophic_stop():
    assert policy.EXIT["max_duration_sec"] == 5400 and policy.EXIT["hard_stop_bps"] == 40.0
    config = policy.exit_config("sync-1")
    assert config["hard_stop_bps"] == 40.0
    assert policy.SPEC.entry_offset_pct == 0.10 and policy.SPEC.chase_windows == ()


def test_dashboard_discloses_maker_entry_and_commit_rule():
    payload = policy.dashboard_policy()
    chips = " ".join(payload["filter_chips"])
    assert "PAPER ONLY" in chips and "HINT" in chips
    assert "Only committed calls" in chips and "Never fades NO_TRADE" in chips
    assert "Maker limit 0.1%" in chips and "no chase" in chips
    assert "90m time exit" in chips and "Stop 40bp" in chips and "Max 3 open positions" in chips
    assert payload["entry"]["liquidity_intent"] == "MAKER"


def test_runtime_tile_view_refuses_uncommitted_and_inverts_committed():
    for raw, expected in ((_ai(62, 38, raw_direction="NO_TRADE"), "NO_TRADE"), (_ai(80, 20), "SHORT")):
        shared = dict(copy.deepcopy(raw), direction="LONG", decision="APPROVE")
        snapshot = copy.deepcopy(shared)
        ai, direction, spread, _ = bot._tile_view_of_shared_call(
            policy.LANE, raw, shared, _admission(raw), "LONG", 4,
        )
        assert direction == expected and shared == snapshot
        assert (spread == 0) == (expected == "NO_TRADE")
