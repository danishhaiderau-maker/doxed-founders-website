"""Dedicated contract for Trend Fade 60 + Profit Lock: Trend Fade 60 entry, Scenario-C ladder, up to 5 open."""
import copy
import time

import pytest

import bot
import paper_policy_family_trend_fade_60 as fade
import paper_policy_family_trend_fade_60_ladder as policy
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    INVERTED_SCORE_LED_ADMISSION_POLICY_ID,
    RETIRED_TILE_LANES,
    combo_toggle_defaults,
    resolve_score_led_paper_admission,
    validate_tile_registry,
)
from scenario_c_config import SCENARIO_C_PROFILE_ID, TRAIL_LADDER_SCENARIO_C

ENTRY = 65000.0
NOW = 1_790_000_000.0
SCENARIO_C = ((8, 5), (12, 10), (19, 17), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120))


def _admission(ai):
    return resolve_score_led_paper_admission(
        ai, score_led_enabled=True, research_mode=True, forced_paper=True,
        live_armed=False, bitfinex_live_enabled=False,
    )


def _ai(long_score=70, short_score=30, **extra):
    return {"raw_direction": "LONG", "raw_decision": "APPROVE", "direction": "LONG",
            "decision": "APPROVE", "long_score": long_score, "short_score": short_score, **extra}


def _decide(module, direction="SHORT", bid=64999.0, ask=65000.0, bbo_age=1.0):
    return module.decide_entry(
        direction=direction, signal_ts=NOW, bid=bid, ask=ask, bbo_ts=NOW - bbo_age,
        reference_price=(bid + ask) / 2.0,
    )


def _price_at_margin(direction, margin_pct):
    sign = 1 if direction == "LONG" else -1
    return ENTRY * (1 + sign * margin_pct / 10_000.0)


def test_registry_owns_this_paper_only_relay_ineligible_default_on_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER.index(policy.LANE) == ACTIVE_TILE_ORDER.index(fade.LANE) + 1
    assert spec["paper_only"] is True and spec["execution_scope"] == "PAPER_ONLY"
    assert spec["platform_relay_eligible"] is False and spec["live_copy_eligible"] is False
    assert spec["relay_capability"] == "BLOCKED_UNQUALIFIED"
    assert spec["default_enabled"] is True and combo_toggle_defaults()[policy.LANE] is True
    assert spec["id_prefix"] == "ftl" and spec["toggle_key"] == "research_lane_enabled"
    assert spec["max_active_signals"] == 5 == spec["exit_policy"]["max_open_positions"]
    assert spec["requested_margin_usd"] == 0.25
    assert spec["implementation_modules"] == ("paper_policy_family_trend_fade_60_ladder.py",)
    assert spec["admission_treatment"] == INVERTED_SCORE_LED_ADMISSION_POLICY_ID
    assert policy.POLICY_ID == spec["raw_policy_id"] != fade.POLICY_ID
    assert policy.POLICY_SIGNATURE == spec["policy_signature"] != fade.POLICY_SIGNATURE
    assert len({COMBO_LANE_SPECS[lane]["policy_signature"] for lane in ACTIVE_TILE_ORDER}) == len(ACTIVE_TILE_ORDER)
    assert len({COMBO_LANE_SPECS[lane]["id_prefix"] for lane in ACTIVE_TILE_ORDER}) == len(ACTIVE_TILE_ORDER)
    assert policy.LANE not in RETIRED_TILE_LANES


def test_trend_fade_keeps_its_own_cap_and_default():
    spec = COMBO_LANE_SPECS[fade.LANE]
    assert spec["max_active_signals"] == 1 and spec["exit_policy"]["max_open_positions"] == 1
    assert spec["default_enabled"] is False
    assert not spec.get("ladder")


def test_entry_is_identical_to_trend_fade():
    assert COMBO_LANE_SPECS[policy.LANE]["entry_policy"] == COMBO_LANE_SPECS[fade.LANE]["entry_policy"]
    assert policy.ENTRY["max_spread_bps"] == 1.68
    assert policy.ENTRY["taker_protection_bps"] == 5.0 and policy.ENTRY["taker_ttl_sec"] == 15
    assert policy.ENTRY["direction_source"] == "INVERTED_SCORE_LED_SIDE"


@pytest.mark.parametrize("raw", [
    _ai(70, 30), _ai(30, 70), _ai(51, 49),
    _ai(62, 38, raw_direction="NO_TRADE", raw_decision="NO_TRADE", direction="NO_TRADE", decision="REJECT"),
    _ai(50, 50), _ai(None, None), _ai(70, 30, ai_error=True),
])
def test_lane_admission_matches_trend_fade_on_the_same_call(raw):
    original = copy.deepcopy(raw)
    ours = policy.lane_admission(raw, _admission(raw))
    theirs = fade.lane_admission(raw, _admission(raw))
    assert raw == original
    assert (ours["accepted"], ours["direction"], ours["reason"]) == (
        theirs["accepted"], theirs["direction"], theirs["reason"],
    )


@pytest.mark.parametrize("kwargs", [
    {"direction": "LONG"}, {"direction": "SHORT"},
    {"bid": 64988.0, "ask": 65000.0}, {"bbo_age": 6.0}, {"bid": 0.0}, {"direction": "NO_TRADE"},
    {"direction": "LONG", "bid": 64989.1, "ask": 65000.0},
])
def test_entry_decision_matches_trend_fade_except_identity(kwargs):
    ours, theirs = _decide(policy, **kwargs), _decide(fade, **kwargs)
    for key in ("lane", "policy_id", "policy_signature"):
        assert ours.pop(key) != theirs.pop(key)
    assert ours == theirs


def test_ladder_is_the_scenario_c_ladder():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert policy.LADDER == SCENARIO_C == tuple(tuple(row) for row in TRAIL_LADDER_SCENARIO_C)
    assert spec["ladder"] == SCENARIO_C
    assert spec["ladder_profile_id"] == SCENARIO_C_PROFILE_ID
    exit_policy = spec["exit_policy"]
    assert exit_policy["profit_lock"] == "SCENARIO_C_LADDER"
    assert exit_policy["hard_stop_bps"] == 40 and exit_policy["hard_stop_margin_pct"] == 40
    assert exit_policy["max_duration_sec"] == 3600
    for absent in ("breakeven_trigger_margin_pct", "trail_atr_k", "initial_stop_atr_k", "take_profit", "breakeven", "trail"):
        assert exit_policy.get(absent) is None
    config = policy.exit_config("sync")
    assert config["trail_ladder"] == [list(row) for row in SCENARIO_C]
    assert config["ladder_profile_id"] == SCENARIO_C_PROFILE_ID
    assert config["hard_stop_bps"] == 40 and config["path_end_sec"] == 3600
    assert "breakeven_trigger_margin_pct" not in config


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("trigger,lock", SCENARIO_C)
def test_each_rung_locks_its_floor_once_peak_reaches_the_trigger(direction, trigger, lock):
    kwargs = dict(entry=ENTRY, direction=direction, atr_abs=60.0, leverage=100.0, age_sec=600)
    peak = _price_at_margin(direction, trigger + 0.01)
    # Just below the trigger this rung is not armed (only lower floors), so the same give-back holds.
    assert policy.exit_action(price=_price_at_margin(direction, lock - 0.5),
                              peak_price=_price_at_margin(direction, trigger - 0.01), **kwargs) is None
    # At the floor plus a hair the position holds; a tick through the floor exits at the lock.
    assert policy.exit_action(price=_price_at_margin(direction, lock + 0.5), peak_price=peak, **kwargs) is None
    action = policy.exit_action(price=_price_at_margin(direction, lock - 0.5), peak_price=peak, **kwargs)
    assert action is not None and action.reason == "PROFIT_LOCK_LADDER"
    assert action.trigger_price == pytest.approx(_price_at_margin(direction, lock))
    assert action.close_fraction == 1.0 and action.remaining_fraction == 0.0


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_no_lock_below_the_first_rung_and_highest_armed_rung_wins(direction):
    kwargs = dict(entry=ENTRY, direction=direction, atr_abs=60.0, leverage=100.0, age_sec=600)
    # Peak 7.9% never armed a lock: a round-trip to entry holds (no break-even rung).
    assert policy.exit_action(price=ENTRY, peak_price=_price_at_margin(direction, 7.9), **kwargs) is None
    # Peak 45% arms 40->28, not 19->17: falling to 27.5% exits at the 28% lock.
    action = policy.exit_action(price=_price_at_margin(direction, 27.5),
                                peak_price=_price_at_margin(direction, 45.0), **kwargs)
    assert action.reason == "PROFIT_LOCK_LADDER"
    assert action.trigger_price == pytest.approx(_price_at_margin(direction, 28.0))


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_forty_bp_catastrophic_stop_and_sixty_minute_backstop(direction):
    sign = 1 if direction == "LONG" else -1
    kwargs = dict(entry=ENTRY, direction=direction, atr_abs=60.0, leverage=100.0)
    assert policy.exit_action(price=ENTRY * (1 - sign * 0.0039), age_sec=600, **kwargs) is None
    stop = policy.exit_action(price=ENTRY * (1 - sign * 0.0045), age_sec=600, **kwargs)
    assert stop.reason == "PHYSICAL_HARD_STOP_40PCT"
    timed = policy.exit_action(price=_price_at_margin(direction, 6.0), age_sec=3600,
                               peak_price=_price_at_margin(direction, 7.0), **kwargs)
    assert timed.reason == "PATH_END_60M"
    # An unarmed favourable move with no fixed target keeps running until the backstop.
    assert policy.exit_action(price=_price_at_margin(direction, 200.0), age_sec=1800,
                              peak_price=_price_at_margin(direction, 200.0), **kwargs) is None


def test_sizing_never_exceeds_quarter_margin_at_100x():
    sized = policy.account_risk_quantity(equity_usd=1_000_000.0, entry_price=ENTRY, atr_abs=60.0)
    assert sized["quantity"] * ENTRY / 100.0 <= 0.25 + 1e-12
    assert sized["capped_by"] == "MARGIN"


def test_pre_registration_matches_trend_fade_rules_with_trend_fade_as_control():
    ours = COMBO_LANE_SPECS[policy.LANE]["pre_registration"]
    theirs = COMBO_LANE_SPECS[fade.LANE]["pre_registration"]
    assert ours["schema"] == theirs["schema"] == "tile_pre_registration_trade_count_v1"
    assert ours["promotion"] == theirs["promotion"] and ours["promotion"]["min_fills"] == 150
    assert ours["kill"] == theirs["kill"]
    kill = ours["kill"]
    assert (kill["k1_after_fills"], kill["k1_net_usd_at_or_below"]) == (40, -0.40)
    assert (kill["k2_after_fills"], kill["k2_net_usd_at_or_below"]) == (80, 0.0)
    assert kill["k3_worst_trade_bp_below"] == -60.0
    assert kill["k4_max_drawdown_usd"] == 1.0 and kill["k5_max_days_without_promotion"] == 14
    assert ours["control_lane"] == fade.LANE
    assert ours["hypothesis_id"] != theirs["hypothesis_id"]


def test_dashboard_discloses_ladder_cap_and_paper_only():
    payload = policy.dashboard_policy()
    chips = " ".join(payload["filter_chips"])
    assert "opposite of score-led" in chips
    assert "Ladder 8→5, 12→10, 19→17, 40→28, 60→45, 80→60, 100→75, 150→120" in chips
    assert "No break-even / trail / target beyond the ladder" in chips
    assert "No ladder / break-even / trail / target" not in chips
    assert "Stop 40bp" in chips and "60m time exit" in chips and "Max 5 open positions" in chips
    assert payload["exit"]["profit_lock"] == "SCENARIO_C_LADDER"
    assert payload["pre_registration"]["control_lane"] == fade.LANE
    assert "PAPER ONLY" in COMBO_LANE_SPECS[policy.LANE]["subtitle"]
    fade_chips = " ".join(fade.dashboard_policy()["filter_chips"])
    assert "No ladder / break-even / trail / target" in fade_chips and "Max 1 open position" in fade_chips


def test_runtime_tile_view_inverts_this_tile_like_trend_fade():
    raw = _ai(70, 30, raw_direction="NO_TRADE", raw_decision="NO_TRADE", direction="NO_TRADE", decision="REJECT")
    admission = _admission(raw)
    shared = dict(copy.deepcopy(raw), direction="LONG", decision="APPROVE")
    snapshot = copy.deepcopy(shared)
    ours = bot._tile_view_of_shared_call(policy.LANE, raw, shared, admission, "LONG", 4)
    theirs = bot._tile_view_of_shared_call(fade.LANE, raw, shared, admission, "LONG", 4)
    assert ours[1:] == theirs[1:] and ours[1] == "SHORT"
    assert shared == snapshot


@pytest.fixture
def isolated_books(monkeypatch):
    books = {"open": [], "pending": [], "trades": {}}
    monkeypatch.setattr(bot, "open_positions", books["open"])
    monkeypatch.setattr(bot, "pending_orders", books["pending"])
    monkeypatch.setattr(bot, "trades_map", books["trades"])
    monkeypatch.setattr(bot, "purge_dead_pending_orders", lambda: None)
    monkeypatch.setattr(bot, "reconcile_stale_signals", lambda: None)
    monkeypatch.setattr(bot, "_refresh_order_and_signal_ttl", lambda: None)
    return books


def test_tile_holds_up_to_five_concurrent_positions_in_its_own_pool(isolated_books):
    assert bot.tile_max_active_signals(policy.LANE) == 5
    assert bot.get_lane_max_active_signals(policy.LANE) == 5
    # Other tiles' exposure never consumes this tile's capacity.
    for i in range(3):
        isolated_books["open"].append({"trade_id": f"ftf-{i}", "research_lane": fade.LANE})
    for i in range(5):
        assert bot.ensure_lane_signal_capacity(policy.LANE) is True, f"refused slot {i + 1}"
        isolated_books["open"].append({"trade_id": f"ftl-{i}", "research_lane": policy.LANE})
    assert bot.get_active_signal_count(policy.LANE) == 5
    assert bot.ensure_lane_signal_capacity(policy.LANE) is False
    # A resting 15 s taker order counts toward the same five slots.
    isolated_books["open"].pop()
    isolated_books["pending"].append({"trade_id": "ftl-p", "research_lane": policy.LANE, "status": "PENDING"})
    assert bot.ensure_lane_signal_capacity(policy.LANE) is False
    isolated_books["pending"].clear()
    assert bot.ensure_lane_signal_capacity(policy.LANE) is True
    # Trend Fade 60 keeps its own single-slot cap regardless.
    assert bot.ensure_lane_signal_capacity(fade.LANE) is False


def _runtime_position(direction="LONG"):
    return {
        "trade_id": "ftl-test", "research_lane": policy.LANE, "entry": ENTRY, "dir": direction,
        "atr14_3m": 60.0, "atr14_pct_3m": 60.0 / ENTRY * 100.0, "entry_ts": time.time() - 60,
        "leverage": 100.0, "qty": 0.0003, "policy_remaining_fraction": 1.0,
    }


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_runtime_winner_locks_profit_on_the_ladder(monkeypatch, direction):
    pos = _runtime_position(direction)
    closed = []
    monkeypatch.setattr(bot, "close_position", lambda row, reason: closed.append((reason, dict(row))))
    # Run up to +45% margin (arms 40->28), then give back until the lock fires.
    path = [_price_at_margin(direction, m) for m in (*range(0, 46, 3), 45, *range(44, 0, -1))]
    for price in path:
        pos["_exit_eval_price"] = float(price)
        if bot._apply_family_tile_exit(pos, float(price), time.time()):
            break
    assert [reason for reason, _ in closed] == ["PROFIT_LOCK_LADDER"]
    row = closed[0][1]
    assert row["exit_policy_id"] == policy.POLICY_ID
    assert row["policy_stop_price"] == pytest.approx(_price_at_margin(direction, 28.0))
    booked, sim = bot.resolve_sim_exit_price(row, False, "PROFIT_LOCK_LADDER")
    assert sim["source"] == "exit_trigger_side_correct"
    assert booked == pytest.approx(row["_exit_eval_price"], abs=0.01)
    margin = (booked - ENTRY) / ENTRY * 10_000.0 * (1 if direction == "LONG" else -1)
    assert 26.0 <= margin <= 28.0
    assert bot._is_trigger_consistent_exit_reason("PROFIT_LOCK_LADDER")


def test_runtime_loser_still_hits_the_catastrophic_stop(monkeypatch):
    pos = _runtime_position("SHORT")
    closed = []
    monkeypatch.setattr(bot, "close_position", lambda row, reason: closed.append((reason, dict(row))))
    for price in (ENTRY + step * 13.0 for step in range(0, 40)):
        pos["_exit_eval_price"] = float(price)
        if bot._apply_family_tile_exit(pos, float(price), time.time()):
            break
    assert [reason for reason, _ in closed] == ["PHYSICAL_HARD_STOP_40PCT"]
