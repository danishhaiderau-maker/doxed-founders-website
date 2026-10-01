"""Dedicated contract for Tile 2: Dynamic Adaptive + Scenario-C ladder, no break-even rung."""
import time

import pytest

import bot
import paper_policy_family_adaptive_regime as base
import paper_policy_family_adaptive_regime_ladder as policy
import paper_policy_family_adaptive_regime_ladder_be as be
from combo_pathway_config import ACTIVE_TILE_ORDER, COMBO_LANE_SPECS, RETIRED_TILE_LANES
from scenario_c_config import TRAIL_LADDER_SCENARIO_C
from test_paper_policy_family_adaptive_regime import CALM, EXTREME, FAST_UP, NORMAL, _ai, _signal_ts

ENTRY = 65000.0
TILES = (base, policy, be)


def _exit(direction, price, peak, *, atr=60.0, module=policy):
    return module.exit_action(
        entry=ENTRY, direction=direction, price=price, atr_abs=atr,
        leverage=100.0, peak_price=peak,
    )


def _px(margin_pct, direction="LONG"):
    sign = 1 if direction == "LONG" else -1
    return ENTRY * (1.0 + sign * margin_pct / 10_000.0)


def test_registry_owns_this_paper_only_relay_ineligible_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert ACTIVE_TILE_ORDER[:3] == (base.LANE, policy.LANE, be.LANE)
    assert spec["paper_only"] is True
    assert spec["platform_relay_eligible"] is False
    assert spec["default_enabled"] is False
    assert spec["id_prefix"] == "fal"
    assert spec["max_active_signals"] == 1
    assert spec["exit_policy"]["max_open_positions"] == 1
    assert spec["exit_policy"]["hard_stop_margin_pct"] == 30.0
    assert spec["exit_policy"]["max_duration_sec"] == 7200
    assert spec["requested_margin_usd"] == 0.25
    assert spec["implementation_modules"] == ("paper_policy_family_adaptive_regime_ladder.py",)
    assert "breakeven_trigger_margin_pct" not in spec["exit_policy"]
    assert spec["ladder"] == tuple(tuple(row) for row in TRAIL_LADDER_SCENARIO_C)
    assert policy.LANE not in RETIRED_TILE_LANES
    signatures = {COMBO_LANE_SPECS[m.LANE]["policy_signature"] for m in TILES}
    prefixes = {COMBO_LANE_SPECS[m.LANE]["id_prefix"] for m in TILES}
    assert len(signatures) == len(prefixes) == 3
    for module in TILES:
        assert COMBO_LANE_SPECS[module.LANE]["entry_policy"] == COMBO_LANE_SPECS[base.LANE]["entry_policy"]


def test_pre_registration_is_frozen_in_the_registry():
    for module, hypothesis in ((policy, "H2_SCENC_NO_BE_20261001"), (be, "H3_SCENC_BE4_1_20261001")):
        pre = COMBO_LANE_SPECS[module.LANE]["pre_registration"]
        assert pre["hypothesis_id"] == hypothesis
        assert pre["control_lane"] == base.LANE
        assert pre["evidence_world"] == "CONSERVATIVE_BBO"
        assert pre["promotion"] == {
            "meaning": "ELIGIBLE_FOR_OWNER_REVIEW_NEVER_RELAY", "min_fills": 400, "min_days": 14,
            "per_fill_ev_lower_ci95_gt_bp": 0.0, "both_halves_positive": True,
            "deflated_sharpe_min": 0.95, "deflated_sharpe_trials": "LIVE_PRE_REGISTERED_HYPOTHESES",
            "paired_vs_control_lower_ci95_gt_bp": 0.0,
        }
        assert pre["kill"] == {
            "k1_min_fills": 150, "k1_per_fill_ev_upper_ci95_lt_bp": 0.0,
            "k2_min_paired_signals": 300, "k2_paired_vs_control_upper_ci95_lt_bp": 0.0,
            "k3_hard_stops_per_rolling_50_kill_at": 3, "k3_max_lock_or_stop_overshoot_bp": 10.0,
            "k4_max_drawdown_usd": 1.5, "k5_max_days_without_promotion": 21,
        }
        assert "never relay" in COMBO_LANE_SPECS[module.LANE]["promotion_criteria"]


@pytest.mark.parametrize("candles", [CALM, NORMAL, EXTREME, FAST_UP])
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_all_three_tiles_make_the_identical_entry_decision(candles, direction):
    ts = _signal_ts(candles)
    kwargs = dict(
        direction=direction, signal_ts=ts, candles_1m=candles, bid=64990.0, ask=65000.0,
        bbo_ts=ts - 1.0, atr_abs=100.0, reference_price=64995.0, ai_feature=_ai(direction),
    )
    identity = ("lane", "policy_id", "policy_signature")
    decisions = [m.decide_entry(**kwargs) for m in TILES]
    stripped = [{k: v for k, v in d.items() if k not in identity} for d in decisions]
    assert stripped[0] == stripped[1] == stripped[2]
    for module, decision in zip(TILES, decisions):
        assert decision["lane"] == module.LANE
        if decision["action"] != module.ACTION_STAND_ASIDE:
            assert module.decision_is_executable(decision, direction)
            for other in TILES:
                if other is not module:
                    assert not other.decision_is_executable(decision, direction)


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


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("peak_margin,lock_margin", [
    (8, 5), (12, 10), (19, 17), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120),
])
def test_scenario_c_rungs_lock_their_floor(direction, peak_margin, lock_margin):
    sign = 1 if direction == "LONG" else -1
    peak = _px(peak_margin + 0.01, direction)
    lock = _px(lock_margin, direction)
    assert _exit(direction, lock + sign * 0.5, peak, atr=10_000.0) is None
    action = _exit(direction, lock - sign * 0.5, peak, atr=10_000.0)
    assert action.reason == "PROFIT_LOCK_LADDER"
    assert action.stop_price == pytest.approx(lock)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_no_break_even_rung_below_the_first_ladder_rung(direction):
    sign = 1 if direction == "LONG" else -1
    peak = _px(7.9, direction)
    assert _exit(direction, ENTRY - sign * 5.0, peak) is None
    assert _exit(direction, ENTRY - sign * 5.0, peak, module=be).reason == "BREAKEVEN_LOCK"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_effective_stop_is_the_tighter_of_atr_trail_and_lock(direction):
    sign = 1 if direction == "LONG" else -1
    peak = _px(50, direction)
    trail = peak - sign * 60.0
    action = _exit(direction, trail - sign * 0.5, peak, atr=60.0)
    assert action.reason == "PROFIT_PROTECTION_STOP"
    assert action.stop_price == pytest.approx(trail)
    action = _exit(direction, _px(27.9, direction), peak, atr=400.0)
    assert action.reason == "PROFIT_LOCK_LADDER"
    assert action.stop_price == pytest.approx(_px(28, direction))


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_hard_stop_and_initial_atr_stop_still_protect_losers(direction):
    sign = 1 if direction == "LONG" else -1
    stop = ENTRY - sign * 1.5 * 60.0
    assert _exit(direction, stop - sign * 1.0, ENTRY).reason == "INITIAL_ATR_STOP"
    assert _exit(direction, _px(-30.5, direction), ENTRY, atr=10_000.0).reason == "PHYSICAL_HARD_STOP_30PCT"


def test_dashboard_discloses_ladder_and_capacity_without_break_even():
    payload = policy.dashboard_policy()
    chips = " ".join(payload["filter_chips"])
    assert "Ladder" in chips and "Max 1 open position" in chips
    assert "Break-even" not in chips
    assert payload["exit"]["lock_fill"] == "SIDE_CORRECT_BBO_TICK_THAT_CROSSED_THE_STOP"
    assert payload["pre_registration"]["hypothesis_id"] == "H2_SCENC_NO_BE_20261001"
    assert "breakeven_trigger_margin_pct" not in policy.exit_config("sync")


def _position(direction):
    return {
        "trade_id": f"fal-test-{direction}", "research_lane": policy.LANE,
        "entry": ENTRY, "dir": direction, "atr14_3m": 60.0,
        "atr14_pct_3m": 60.0 / ENTRY * 100.0, "entry_ts": time.time() - 60,
        "leverage": 100.0, "qty": 0.0003, "policy_remaining_fraction": 1.0,
    }


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_runtime_ladder_lock_books_the_crossing_quote(monkeypatch, direction):
    sign = 1 if direction == "LONG" else -1
    up = [ENTRY + sign * step for step in range(0, 54)]
    down = [ENTRY + sign * step for step in range(53, -91, -1)]
    closed = []
    monkeypatch.setattr(bot, "close_position", lambda row, reason: closed.append((reason, dict(row))))
    pos = _position(direction)
    for price in up + down:
        pos["_exit_eval_price"] = float(price)
        if bot._apply_family_tile_exit(pos, float(price), time.time()):
            break
    assert [reason for reason, _ in closed] == ["PROFIT_LOCK_LADDER"]
    row = closed[0][1]
    assert row["policy_stop_price"] == pytest.approx(_px(5.0, direction))
    booked, sim = bot.resolve_sim_exit_price(row, False, "PROFIT_LOCK_LADDER")
    assert sim["source"] == "exit_trigger_side_correct"
    assert booked == row["_exit_eval_price"]
    assert sign * (booked - ENTRY) > 0
