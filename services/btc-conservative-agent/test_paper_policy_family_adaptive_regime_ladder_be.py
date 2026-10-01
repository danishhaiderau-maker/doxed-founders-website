"""Dedicated contract for Tile 3: Dynamic Adaptive + Scenario-C ladder + break-even rung."""
import time

import pytest

import bitfinex_cost_profile
import bot
import paper_policy_family_adaptive_regime as base
import paper_policy_family_adaptive_regime_ladder as ladder
import paper_policy_family_adaptive_regime_ladder_be as policy
from combo_pathway_config import ACTIVE_TILE_ORDER, COMBO_LANE_SPECS, RETIRED_TILE_LANES
from scenario_c_config import TRAIL_LADDER_SCENARIO_C
from test_paper_policy_family_adaptive_regime import CALM, EXTREME, FAST_UP, NORMAL, _ai, _signal_ts

ENTRY = 65000.0


def _exit(direction, price, peak, *, entry=ENTRY, atr=60.0):
    return policy.exit_action(
        entry=entry, direction=direction, price=price, atr_abs=atr,
        leverage=100.0, peak_price=peak,
    )


def _px(margin_pct, direction="LONG", entry=ENTRY):
    sign = 1 if direction == "LONG" else -1
    return entry * (1.0 + sign * margin_pct / 10_000.0)


def test_registry_owns_this_paper_only_relay_ineligible_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert ACTIVE_TILE_ORDER[:3] == (base.LANE, ladder.LANE, policy.LANE)
    assert spec["max_active_signals"] == 1
    assert spec["exit_policy"] == {**COMBO_LANE_SPECS[ladder.LANE]["exit_policy"], **{
        k: spec["exit_policy"][k] for k in ("profit_lock", "breakeven_trigger_margin_pct",
                                             "breakeven_lock_margin_pct", "breakeven_cost_basis")}}
    assert spec["pre_registration"]["hypothesis_id"] == "H3_SCENC_BE4_1_20261001"
    assert spec["paper_only"] is True
    assert spec["platform_relay_eligible"] is False
    assert spec["default_enabled"] is False
    assert spec["id_prefix"] == "flb"
    assert spec["implementation_modules"] == ("paper_policy_family_adaptive_regime_ladder_be.py",)
    assert policy.POLICY_ID == spec["raw_policy_id"] != base.POLICY_ID
    assert policy.POLICY_SIGNATURE == spec["policy_signature"] != base.POLICY_SIGNATURE
    assert policy.LANE not in RETIRED_TILE_LANES
    assert spec["entry_policy"] == COMBO_LANE_SPECS[base.LANE]["entry_policy"]
    assert spec["ladder"] == tuple(tuple(row) for row in TRAIL_LADDER_SCENARIO_C)
    assert spec["exit_policy"]["family"] != COMBO_LANE_SPECS[base.LANE]["exit_policy"]["family"]


@pytest.mark.parametrize("candles", [CALM, NORMAL, EXTREME, FAST_UP])
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_entry_decision_is_identical_to_dynamic_adaptive_except_identity(candles, direction):
    ts = _signal_ts(candles)
    kwargs = dict(
        direction=direction, signal_ts=ts, candles_1m=candles, bid=64990.0, ask=65000.0,
        bbo_ts=ts - 1.0, atr_abs=100.0, reference_price=64995.0, ai_feature=_ai(direction),
    )
    ours, theirs = policy.decide_entry(**kwargs), base.decide_entry(**kwargs)
    identity = ("lane", "policy_id", "policy_signature")
    assert {k: v for k, v in ours.items() if k not in identity} == {
        k: v for k, v in theirs.items() if k not in identity
    }
    assert (ours["lane"], ours["policy_id"]) == (policy.LANE, policy.POLICY_ID)
    if ours["action"] != policy.ACTION_STAND_ASIDE:
        assert policy.decision_is_executable(ours, direction)
        # A decision belongs to exactly one tile; it can never place the other's order.
        assert not base.decision_is_executable(ours, direction)
        assert not policy.decision_is_executable(theirs, direction)
        assert policy.adaptive_entry_fields(direction, 64995.0, ours)["entry_path"] == policy.LANE


@pytest.mark.parametrize("ai", [
    {"raw_direction": "NO_TRADE", "raw_decision": "NO_TRADE", "long_score": 70, "short_score": 30},
    {"raw_direction": "LONG", "raw_decision": "APPROVE", "long_score": 52, "short_score": 48},
])
def test_ai_no_trade_and_score_gap_below_five_never_trade(ai):
    ts = _signal_ts(CALM)
    decision = policy.decide_entry(
        direction="LONG", signal_ts=ts, candles_1m=CALM, bid=64990.0, ask=65000.0,
        bbo_ts=ts - 1.0, atr_abs=100.0, reference_price=64995.0, ai_feature=ai,
    )
    assert decision["action"] == policy.ACTION_STAND_ASIDE
    assert not policy.decision_is_executable(decision, "LONG")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_breakeven_rung_arms_at_four_percent_and_locks_entry_plus_cost(direction):
    sign = 1 if direction == "LONG" else -1
    below_trigger = _px(3.9, direction)
    assert _exit(direction, ENTRY + sign * 1.0, below_trigger) is None
    peak = _px(4.0, direction)
    lock = _px(1.0, direction)
    assert _exit(direction, lock + sign * 0.5, peak) is None
    action = _exit(direction, lock - sign * 0.5, peak)
    assert action.reason == "BREAKEVEN_LOCK"
    assert action.stop_price == pytest.approx(lock)
    assert sign * (action.stop_price - ENTRY) > 0


def test_breakeven_lock_covers_the_round_trip_fee():
    maker, taker = bitfinex_cost_profile.fee_rates()
    round_trip_margin_pct = 2 * max(maker, taker) * 100.0 * 100.0
    assert policy.SPEC.breakeven_lock_margin_pct >= round_trip_margin_pct
    assert policy.SPEC.breakeven_trigger_margin_pct > policy.SPEC.breakeven_lock_margin_pct


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("peak_margin,lock_margin", [
    (8, 5), (12, 10), (19, 17), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120),
])
def test_scenario_c_rungs_lock_their_floor(direction, peak_margin, lock_margin):
    sign = 1 if direction == "LONG" else -1
    peak = _px(peak_margin + 0.01, direction)
    lock = _px(lock_margin, direction)
    # Wide ATR keeps the ATR trail far below every rung so the ladder owns the stop.
    assert _exit(direction, lock + sign * 0.5, peak, atr=10_000.0) is None
    action = _exit(direction, lock - sign * 0.5, peak, atr=10_000.0)
    assert action.reason == "PROFIT_LOCK_LADDER"
    assert action.stop_price == pytest.approx(lock)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_effective_stop_is_the_tighter_of_atr_trail_and_lock(direction):
    sign = 1 if direction == "LONG" else -1
    # Peak +50% margin (325 USD): ladder 40->28 locks +28%; an armed ATR 60
    # trail sits at peak - 60 = +40.8% margin, so the ATR trail is tighter.
    peak = _px(50, direction)
    trail = peak - sign * 60.0
    action = _exit(direction, trail - sign * 0.5, peak, atr=60.0)
    assert action.reason == "PROFIT_PROTECTION_STOP"
    assert action.stop_price == pytest.approx(trail)
    # ATR 400 arms at 0.81 ATR but trails to peak - 400 (below entry); the
    # ladder's +28% lock is tighter and owns the stop.
    action = _exit(direction, _px(27.9, direction), peak, atr=400.0)
    assert action.reason == "PROFIT_LOCK_LADDER"
    assert action.stop_price == pytest.approx(_px(28, direction))


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_initial_atr_stop_still_protects_losers(direction):
    sign = 1 if direction == "LONG" else -1
    stop = ENTRY - sign * 1.5 * 60.0
    action = _exit(direction, stop - sign * 1.0, ENTRY)
    assert action.reason == "INITIAL_ATR_STOP"
    assert action.stop_price == pytest.approx(stop)


def test_dynamic_adaptive_exit_is_unchanged_by_the_lock_primitive():
    peak = _px(4.0)
    assert base.exit_action(
        entry=ENTRY, direction="LONG", price=_px(0.5), atr_abs=60.0, leverage=100.0, peak_price=peak,
    ) is None
    config = base.exit_config("sync")
    assert "breakeven_trigger_margin_pct" not in config
    assert "effective_stop" not in config
    assert config["ladder_profile_id"] is None


def test_exit_config_and_dashboard_disclose_the_lock():
    config = policy.exit_config("sync")
    assert config["breakeven_trigger_margin_pct"] == 4.0
    assert config["breakeven_lock_margin_pct"] == 1.0
    assert config["effective_stop"] == "MOST_PROTECTIVE_OF_ATR_STOP_AND_PROFIT_LOCK"
    assert config["ladder_profile_id"] == COMBO_LANE_SPECS[policy.LANE]["ladder_profile_id"]
    payload = policy.dashboard_policy()
    chips = " ".join(payload["filter_chips"])
    assert "Break-even at +4%" in chips and "tighter of ATR trail and lock" in chips
    assert payload["exit"]["profit_lock"] == "BREAKEVEN_PLUS_SCENARIO_C_LADDER"


def _position(direction):
    return {
        "trade_id": f"flb-test-{direction}",
        "research_lane": policy.LANE,
        "entry": ENTRY,
        "dir": direction,
        "atr14_3m": 60.0,
        "atr14_pct_3m": 60.0 / ENTRY * 100.0,
        "entry_ts": time.time() - 60,
        "leverage": 100.0,
        "qty": 0.0003,
        "policy_remaining_fraction": 1.0,
    }


def _walk(monkeypatch, pos, path):
    closed = []
    monkeypatch.setattr(bot, "close_position", lambda row, reason: closed.append((reason, dict(row))))
    for price in path:
        # _apply_position_exits stamps the side-correct tick before the policy runs.
        pos["_exit_eval_price"] = float(price)
        if bot._apply_family_tile_exit(pos, float(price), time.time()):
            break
    return closed


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_runtime_winner_reaching_a_rung_cannot_book_below_entry_plus_cost(monkeypatch, direction):
    sign = 1 if direction == "LONG" else -1
    # One-dollar ticks: up to the +4% margin rung, then a full give-back.
    up = [ENTRY + sign * step for step in range(0, 27)]
    down = [ENTRY + sign * step for step in range(26, -91, -1)]
    pos = _position(direction)
    closed = _walk(monkeypatch, pos, up + down)
    assert [reason for reason, _ in closed] == ["BREAKEVEN_LOCK"]
    row = closed[0][1]
    lock = _px(1.0, direction)
    assert sign * (row["policy_stop_price"] - lock) == pytest.approx(0.0, abs=1e-9)
    booked, sim = bot.resolve_sim_exit_price(row, False, "BREAKEVEN_LOCK")
    assert sim["source"] == "exit_trigger_side_correct"
    assert booked == row["_exit_eval_price"]
    maker, taker = bitfinex_cost_profile.fee_rates()
    entry_plus_fees = ENTRY * (1.0 + sign * 2 * max(maker, taker))
    assert sign * (booked - entry_plus_fees) >= 0
    # The booked tick is the first observed tick through the lock; any gap is slip.
    assert abs(booked - lock) <= 1.0


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("lane", [base.LANE, ladder.LANE])
def test_runtime_tiles_without_the_rung_do_not_lock_the_same_winner(monkeypatch, direction, lane):
    sign = 1 if direction == "LONG" else -1
    up = [ENTRY + sign * step for step in range(0, 27)]
    down = [ENTRY + sign * step for step in range(26, -2, -1)]
    pos = dict(_position(direction), research_lane=lane, trade_id=f"{lane}-test")
    assert _walk(monkeypatch, pos, up + down) == []
