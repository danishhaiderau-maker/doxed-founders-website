"""Dedicated contract for Tile 4: trend fade (opposite of the score-led side), 60 m time exit."""
import copy
import time

import pytest

import bot
import paper_policy_family_adaptive_regime as base
import paper_policy_family_trend_fade_60 as policy
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    INVERTED_SCORE_LED_ADMISSION_POLICY_ID,
    RETIRED_TILE_LANES,
    resolve_score_led_paper_admission,
)

ENTRY = 65000.0
NOW = 1_790_000_000.0


def _admission(ai):
    return resolve_score_led_paper_admission(
        ai, score_led_enabled=True, research_mode=True, forced_paper=True,
        live_armed=False, bitfinex_live_enabled=False,
    )


def _ai(long_score=70, short_score=30, **extra):
    return {"raw_direction": "LONG", "raw_decision": "APPROVE", "direction": "LONG",
            "decision": "APPROVE", "long_score": long_score, "short_score": short_score, **extra}


def _decide(direction="SHORT", bid=64999.0, ask=65000.0, bbo_age=1.0):
    return policy.decide_entry(
        direction=direction, signal_ts=NOW, bid=bid, ask=ask, bbo_ts=NOW - bbo_age,
        reference_price=(bid + ask) / 2.0,
    )


def test_registry_owns_this_paper_only_relay_ineligible_beta_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert ACTIVE_TILE_ORDER[-1] == policy.LANE and len(ACTIVE_TILE_ORDER) == 4
    assert spec["paper_only"] is True
    assert spec["platform_relay_eligible"] is False
    assert spec["default_enabled"] is False
    assert spec["id_prefix"] == "ftf"
    assert spec["max_active_signals"] == 1
    assert spec["requested_margin_usd"] == 0.25
    assert spec["implementation_modules"] == ("paper_policy_family_trend_fade_60.py",)
    assert spec["admission_treatment"] == INVERTED_SCORE_LED_ADMISSION_POLICY_ID
    assert policy.POLICY_ID == spec["raw_policy_id"] != base.POLICY_ID
    assert policy.POLICY_SIGNATURE == spec["policy_signature"]
    assert len({COMBO_LANE_SPECS[lane]["policy_signature"] for lane in ACTIVE_TILE_ORDER}) == 4
    assert policy.LANE not in RETIRED_TILE_LANES
    exit_policy = spec["exit_policy"]
    assert exit_policy["max_duration_sec"] == 3600
    assert exit_policy["hard_stop_bps"] == 40
    for absent in ("ladder", "breakeven_trigger_margin_pct", "trail_atr_k", "take_profit"):
        assert exit_policy.get(absent) is None
    assert spec["path_end_sec"] == 3600 and spec["risk_limits"]["hard_stop_margin_pct"] == 40.0
    pre = spec["pre_registration"]
    assert pre["schema"] == "tile_pre_registration_trade_count_v1"
    assert pre["honest_label"] == "in-sample +$1.30 / 47 trades; expected heavy decay; beta test"
    assert pre["promotion"]["min_fills"] == 150
    assert pre["kill"]["k1_after_fills"] == 40 and pre["kill"]["k1_net_usd_at_or_below"] == -0.40
    assert pre["kill"]["k3_worst_trade_bp_below"] == -60.0
    assert pre["kill"]["k5_max_days_without_promotion"] == 14


@pytest.mark.parametrize("long_score,short_score,score_led,ours", [
    (70, 30, "LONG", "SHORT"),
    (30, 70, "SHORT", "LONG"),
    (51, 49, "LONG", "SHORT"),
])
def test_tile_trades_the_opposite_of_the_score_led_side(long_score, short_score, score_led, ours):
    raw = _ai(long_score, short_score)
    original = copy.deepcopy(raw)
    view = policy.lane_admission(raw, _admission(raw))
    assert raw == original
    assert view["accepted"] is True
    assert view["direction"] == ours
    assert view["lane_ai"]["score_led_direction"] == score_led
    assert view["lane_ai"]["decision"] == "APPROVE"
    assert view["lane_ai"]["effective_research_admission_policy_id"] == INVERTED_SCORE_LED_ADMISSION_POLICY_ID


def test_raw_ai_no_trade_still_trades_against_the_score_led_side():
    raw = _ai(62, 38, raw_direction="NO_TRADE", raw_decision="NO_TRADE", direction="NO_TRADE", decision="REJECT")
    view = policy.lane_admission(raw, _admission(raw))
    assert (view["accepted"], view["direction"]) == (True, "SHORT")
    assert view["lane_ai"]["raw_decision"] == "NO_TRADE"


@pytest.mark.parametrize("raw,reason", [
    (_ai(50, 50), "SCORE_LED_TRUE_TIE"),
    (_ai(None, None), "SCORE_LED_INVALID_OR_MISSING_SCORES"),
    (_ai(70, 30, ai_error=True), "AI_ERROR"),
])
def test_ties_invalid_scores_and_ai_errors_refuse(raw, reason):
    view = policy.lane_admission(raw, _admission(raw))
    assert view["accepted"] is False
    assert view["direction"] == "NO_TRADE"
    assert view["lane_ai"]["decision"] == "REJECT"
    assert view["reason"].endswith(reason)


def test_inactive_score_led_treatment_fails_closed():
    raw = _ai()
    inactive = resolve_score_led_paper_admission(
        raw, score_led_enabled=True, research_mode=True, forced_paper=True,
        live_armed=True, bitfinex_live_enabled=False,
    )
    view = policy.lane_admission(raw, inactive)
    assert view["accepted"] is False and view["direction"] == "NO_TRADE"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_taker_limit_is_capped_at_five_bp_and_rounded_toward_the_cap(direction):
    decision = _decide(direction)
    assert decision["action"] == policy.ACTION_TAKER
    assert decision["liquidity_intent"] == "TAKER"
    assert decision["entry_ttl_sec"] == 15
    if direction == "LONG":
        assert 65000.0 < decision["limit_price"] <= 65000.0 * 1.0005
    else:
        assert 64999.0 * 0.9995 <= decision["limit_price"] < 64999.0
    assert policy.decision_is_executable(decision, direction)
    assert not base.decision_is_executable(decision, direction)
    fields = policy.adaptive_entry_fields(direction, 64999.5, decision)
    assert fields["entry_path"] == policy.LANE


@pytest.mark.parametrize("kwargs,reason", [
    ({"bid": 64988.0, "ask": 65000.0}, "SPREAD_ABOVE_MAX"),
    ({"bbo_age": 6.0}, "BBO_STALE"),
    ({"bid": 0.0}, "BBO_UNAVAILABLE"),
    ({"direction": "NO_TRADE"}, "NO_DIRECTION"),
])
def test_stands_aside_on_wide_spread_stale_or_missing_quote(kwargs, reason):
    decision = _decide(**kwargs)
    assert decision["action"] == policy.ACTION_STAND_ASIDE
    assert decision["reason"] == reason
    assert not policy.decision_is_executable(decision, "SHORT")


def test_spread_exactly_at_the_limit_still_trades():
    decision = _decide("LONG", bid=64989.1, ask=65000.0)
    assert 1.67 < decision["spread_bps"] <= 1.68
    assert decision["action"] == policy.ACTION_TAKER


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_forty_bp_catastrophic_stop_and_sixty_minute_exit(direction):
    sign = 1 if direction == "LONG" else -1
    kwargs = dict(entry=ENTRY, direction=direction, atr_abs=60.0, leverage=100.0)
    # No ATR stop, trail, target or lock: a 39 bp adverse move or a 60 bp favourable move holds.
    assert policy.exit_action(price=ENTRY * (1 - sign * 0.0039), age_sec=600, **kwargs) is None
    assert policy.exit_action(price=ENTRY * (1 + sign * 0.0060), age_sec=600,
                              peak_price=ENTRY * (1 + sign * 0.0090), **kwargs) is None
    through = ENTRY * (1 - sign * 0.0045)
    stop = policy.exit_action(price=through, age_sec=600, **kwargs)
    assert stop.reason == "PHYSICAL_HARD_STOP_40PCT"
    assert stop.trigger_price == pytest.approx(through)
    timed = policy.exit_action(price=ENTRY, age_sec=3600, **kwargs)
    assert timed.reason == "PATH_END_60M"


def test_sizing_never_exceeds_quarter_margin_at_100x():
    sized = policy.account_risk_quantity(equity_usd=1_000_000.0, entry_price=ENTRY, atr_abs=60.0)
    assert sized["quantity"] * ENTRY / 100.0 <= 0.25 + 1e-12
    assert sized["capped_by"] == "MARGIN"


def test_dashboard_discloses_beta_label_and_absence_of_ladder():
    payload = policy.dashboard_policy()
    chips = " ".join(payload["filter_chips"])
    assert "opposite of score-led" in chips
    assert "No ladder / break-even / trail / target" in chips
    assert "Stop 40bp" in chips and "60m time exit" in chips
    assert payload["pre_registration"]["honest_label"].startswith("in-sample +$1.30 / 47 trades")
    assert "BETA TEST" in COMBO_LANE_SPECS[policy.LANE]["subtitle"]
    config = policy.exit_config("sync")
    assert config["hard_stop_bps"] == 40 and config["ladder_profile_id"] is None


def test_existing_tiles_keep_their_reason_strings():
    action = base.exit_action(entry=ENTRY, direction="LONG", price=ENTRY, atr_abs=60.0,
                              leverage=100.0, age_sec=7200)
    assert action.reason == "PATH_END_120M"
    assert bot._is_trigger_consistent_exit_reason("PATH_END_60M")
    assert bot._is_trigger_consistent_exit_reason("PHYSICAL_HARD_STOP_40PCT")
    assert bot._is_trigger_consistent_exit_reason("PATH_END_120M")
    assert not bot._is_trigger_consistent_exit_reason("PATH_END_SOON")


def test_runtime_tile_view_inverts_only_this_tile_and_never_mutates_the_shared_call():
    raw = _ai(70, 30, raw_direction="NO_TRADE", raw_decision="NO_TRADE", direction="NO_TRADE", decision="REJECT")
    admission = _admission(raw)
    shared = dict(copy.deepcopy(raw), direction="LONG", decision="APPROVE")
    snapshot = copy.deepcopy(shared)
    ai, direction, spread, reason = bot._tile_view_of_shared_call(
        policy.LANE, raw, shared, admission, "LONG", 4,
    )
    assert (direction, ai["direction"], ai["decision"]) == ("SHORT", "SHORT", "APPROVE")
    assert spread == bot.compute_directional_spread("SHORT", ai) < 0
    assert reason == "LANE_ADMISSION_INVERTED_SCORE_LED_SIDE"
    assert shared == snapshot
    same = bot._tile_view_of_shared_call(base.LANE, raw, shared, admission, "LONG", 4)
    assert same == (shared, "LONG", 4, None)


def test_runtime_stop_books_the_crossing_tick(monkeypatch):
    pos = {
        "trade_id": "ftf-test", "research_lane": policy.LANE, "entry": ENTRY, "dir": "SHORT",
        "atr14_3m": 60.0, "atr14_pct_3m": 60.0 / ENTRY * 100.0, "entry_ts": time.time() - 60,
        "leverage": 100.0, "qty": 0.0003, "policy_remaining_fraction": 1.0,
    }
    closed = []
    monkeypatch.setattr(bot, "close_position", lambda row, reason: closed.append((reason, dict(row))))
    for price in (ENTRY + step * 13.0 for step in range(0, 40)):
        pos["_exit_eval_price"] = float(price)
        if bot._apply_family_tile_exit(pos, float(price), time.time()):
            break
    assert [reason for reason, _ in closed] == ["PHYSICAL_HARD_STOP_40PCT"]
    row = closed[0][1]
    booked, sim = bot.resolve_sim_exit_price(row, False, "PHYSICAL_HARD_STOP_40PCT")
    assert sim["source"] == "exit_trigger_side_correct"
    assert booked == row["_exit_eval_price"] >= ENTRY * 1.004
