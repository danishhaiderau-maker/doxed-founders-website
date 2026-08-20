"""Paper Showcase natural-cycle exits: thesis or Scenario C ladder only.

Instant STOP_LOSS on a 13bps last-trade tick is the bug. There is no
post-fill grace hold — paper simply never closes until ladder or thesis
conditions actually hit on the side-correct executable mark.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


ENTRY = 100_000.0
LEV = 100


def _short_pos(**overrides):
    sl = ENTRY * (1 + bot.sl_price_pct(LEV))
    pos = {
        "trade_id": "paper-sim-short",
        "dir": "SHORT",
        "entry": ENTRY,
        "qty": 0.02,
        "leverage": LEV,
        "entry_ts": time.time(),
        "sl": sl,
        "tp": 0,
        "status": "OPEN",
        "max_pnl_pct": 0.0,
        "max_drawdown": 0.0,
        "research_lane": bot.RESEARCH_LANE_CONTINUOUS,
        "exit_config": {
            "trail_ladder": list(bot.TRAIL_LADDER_SCENARIO_C),
            "peak_never_loser_min_peak": bot.PEAK_NEVER_LOSER_MIN_PEAK,
            "peak_never_loser_floor": bot.PEAK_NEVER_LOSER_FLOOR,
        },
        "entry_thesis": {
            "bull_score": 2,
            "bear_score": 8,
            "mtf_agreement": "BEAR_ALIGNED",
            "structure_score": -2,
        },
    }
    pos.update(overrides)
    return pos


def _margin_price(unreal_pct: float) -> float:
    """SHORT mark that produces the given margin pct at 100x."""
    return ENTRY * (1 - (unreal_pct / (LEV * 100.0)))


class _CloseCapture:
    def __init__(self):
        self.calls = []

    def __call__(self, pos, reason):
        self.calls.append((reason, float(pos.get("_exit_eval_price") or 0), dict(pos)))
        pos["status"] = "CLOSED"
        pos["_close_reason"] = reason


def test_no_grace_hold_is_not_the_paper_sl_mechanism():
    src = open(bot.__file__, encoding="utf-8").read()
    apply_src = src[
        src.index("def _apply_position_exits"):
        src.index("CIRCUIT_BREAKER_REASONS")
    ]
    helper_src = src[
        src.index("def _paper_natural_cycle_exits"):
        src.index("def _genuine_ws_transport_ready")
    ]
    assert "return _force_paper_mode_active()" in helper_src
    assert apply_src.index("PROFIT_LOCK_LADDER") < apply_src.index("if _paper_natural_cycle_exits()")
    assert apply_src.index("check_thesis_invalidation") < apply_src.index("if _paper_natural_cycle_exits()")
    assert apply_src.index("if _paper_natural_cycle_exits()") < apply_src.index('close_position(pos, "STOP_LOSS")')


def test_adverse_last_trade_wick_does_not_close(monkeypatch):
    closed = _CloseCapture()
    monkeypatch.setattr(bot, "close_position", closed)
    monkeypatch.setattr(bot, "append_replay_tick", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_emit_genome_execution_event", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_log_ladder_exit_audit", lambda *a, **k: None)
    pos = _short_pos()
    with bot.trade_lock:
        bot.open_positions.clear()
        bot.open_positions.append(pos)
    sl_px = float(pos["sl"])
    through_sl = sl_px + 20.0
    with bot.state_lock:
        bot.state["bid"] = ENTRY - 8.0
        bot.state["ask"] = ENTRY + 8.0
        bot.state["price"] = through_sl
    bot._tick_driven_position_exits(through_sl)
    assert closed.calls == []
    assert pos["status"] == "OPEN"
    assert pos["max_drawdown"] <= 0


def test_thesis_fast_cut_closes_and_books_trigger(monkeypatch):
    closed = _CloseCapture()
    monkeypatch.setattr(bot, "close_position", closed)
    monkeypatch.setattr(bot, "append_replay_tick", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_emit_genome_execution_event", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_log_ladder_exit_audit", lambda *a, **k: None)
    pos = _short_pos()
    thesis_px = _margin_price(bot.THESIS_FAST_EXIT_UNREAL_PCT)
    fired = bot._apply_position_exits(pos, thesis_px, now=1_700_000_010.0)
    assert fired is True
    assert closed.calls[0][0] == "THESIS_FAST_CUT"
    assert abs(closed.calls[0][1] - thesis_px) < 0.02
    booked, sim = bot.resolve_sim_exit_price(pos, False, "THESIS_FAST_CUT")
    assert abs(booked - thesis_px) < 0.02
    assert sim["source"] == "exit_trigger_side_correct"


def test_scenario_c_ladder_closes_and_books_trigger(monkeypatch):
    closed = _CloseCapture()
    monkeypatch.setattr(bot, "close_position", closed)
    monkeypatch.setattr(bot, "append_replay_tick", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_emit_genome_execution_event", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_log_ladder_exit_audit", lambda *a, **k: None)
    first_rung, lock_floor = bot.TRAIL_LADDER_SCENARIO_C[0]
    pos = _short_pos(max_pnl_pct=float(first_rung) + 0.5)
    lock_px = _margin_price(float(lock_floor))
    fired = bot._apply_position_exits(pos, lock_px, now=1_700_000_020.0)
    assert fired is True
    assert closed.calls[0][0] == "PROFIT_LOCK_LADDER"
    booked, sim = bot.resolve_sim_exit_price(pos, False, "PROFIT_LOCK_LADDER")
    assert abs(booked - lock_px) < 0.02
    assert sim["source"] == "exit_trigger_side_correct"


def test_booked_exit_is_not_a_better_book_walk(monkeypatch):
    pos = _short_pos()
    trigger = _margin_price(-12.0)
    pos["_exit_eval_price"] = trigger
    with bot.state_lock:
        bot.state["bid"] = ENTRY - 5
        bot.state["ask"] = ENTRY + 5
        bot.state["price"] = ENTRY
        bot.state["order_book"] = {
            "bids": [[ENTRY - 5, 1, 2.0]],
            "asks": [[ENTRY + 5, 1, 2.0]],
        }
    monkeypatch.setattr(bot, "refresh_bbo_state", lambda *a, **k: None)
    monkeypatch.setattr(bot, "refresh_order_book_state", lambda *a, **k: None)
    booked, sim = bot.resolve_sim_exit_price(pos, False, "THESIS_FAST_CUT")
    walk, _ = bot.resolve_sim_exit_price(pos, False, "STOP_LOSS")
    assert abs(booked - trigger) < 0.02
    assert abs(walk - (ENTRY + 5)) < 1.0
    assert abs(booked - walk) > 50
    assert sim["source"] == "exit_trigger_side_correct"


def test_paper_helper_is_force_paper_mode():
    assert bot._force_paper_mode_active() is True
    assert bot._paper_natural_cycle_exits() is True
    assert bot.THESIS_FAST_EXIT_UNREAL_PCT == -12.0
    assert bot.MAX_SL_MARGIN_PCT == 30.0
    assert bot.TRAIL_LADDER_SCENARIO_C[0] == (8, 5)
