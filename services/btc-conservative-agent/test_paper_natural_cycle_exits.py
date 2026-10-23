"""Paper Showcase natural-cycle exits: thesis or Scenario C ladder only.

Instant STOP_LOSS on a 13bps last-trade tick is the bug. There is no
post-fill grace hold — paper simply never closes until ladder or thesis
conditions actually hit on the side-correct executable mark.
"""

from __future__ import annotations

import os
import time

import pytest

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
        "research_lane": bot.LEGACY_ADOPTION_LANE,
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


def _book(monkeypatch, bid: float, ask: float):
    """RK step 2: exits read the 1 s order-book rows (paper_fill.book10l_at), never the WS ticker."""
    import math

    import paper_fill

    monkeypatch.setattr(paper_fill, "book10l_at", lambda t: {
        "s": float(math.floor(float(t)) - 1), "b": [[float(bid), 5.0]], "a": [[float(ask), 5.0]]})


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
        bot.state["price"] = through_sl  # a ticker last trade through the SL is ignored: only the book row counts
    _book(monkeypatch, ENTRY - 8.0, ENTRY + 8.0)
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
    _book(monkeypatch, thesis_px - 1.0, thesis_px)  # SHORT exit side = ask
    fired = bot._apply_position_exits(pos, 1.0, now=1_700_000_010.0)  # a caller price is never trusted
    assert fired is True
    assert closed.calls[0][0] == "THESIS_FAST_CUT"
    assert abs(closed.calls[0][1] - thesis_px) < 0.02
    monkeypatch.setattr(bot, "refresh_bbo_state", lambda *a, **k: None)
    with bot.state_lock:
        bot.state["bid"], bot.state["ask"] = thesis_px - 30, thesis_px + 30  # ticker: must be ignored
    _book(monkeypatch, thesis_px - 3, thesis_px + 3)
    booked, sim = bot.resolve_sim_exit_price(pos, False, "THESIS_FAST_CUT")
    assert abs(sim["trigger_price"] - thesis_px) < 0.02
    assert abs(booked - (thesis_px + 3)) < 0.02  # SHORT covers at the book ask of the fresh row at fill
    assert sim["source"] == "exit_book_row_at_fill"
    assert sim["exit_pricing_basis"] == "BOOK_ROW_AT_FILL"


def test_scenario_c_ladder_closes_and_books_trigger(monkeypatch):
    closed = _CloseCapture()
    monkeypatch.setattr(bot, "close_position", closed)
    monkeypatch.setattr(bot, "append_replay_tick", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_emit_genome_execution_event", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_log_ladder_exit_audit", lambda *a, **k: None)
    first_rung, lock_floor = bot.TRAIL_LADDER_SCENARIO_C[0]
    pos = _short_pos(max_pnl_pct=float(first_rung) + 0.5)
    lock_px = _margin_price(float(lock_floor))
    _book(monkeypatch, lock_px - 1.0, lock_px)
    fired = bot._apply_position_exits(pos, lock_px, now=1_700_000_020.0)
    assert fired is True
    assert closed.calls[0][0] == "PROFIT_LOCK_LADDER"
    monkeypatch.setattr(bot, "refresh_bbo_state", lambda *a, **k: None)
    _book(monkeypatch, lock_px - 2, lock_px + 2)
    booked, sim = bot.resolve_sim_exit_price(pos, False, "PROFIT_LOCK_LADDER")
    assert abs(sim["trigger_price"] - lock_px) < 0.02
    assert abs(booked - (lock_px + 2)) < 0.02
    assert sim["source"] == "exit_book_row_at_fill"


def test_non_combo_lane_stale_book_fires_no_price_exit_and_refuses_ticker(monkeypatch):
    """HM B3: the CONTINUOUS adoption owner on a stale book: no price-level exit (even with the ticker through the
    stop), STALE_BOOK stamped; the exit fires on the first fresh row."""
    import paper_fill

    closed = _CloseCapture()
    monkeypatch.setattr(bot, "close_position", closed)
    monkeypatch.setattr(bot, "append_replay_tick", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_emit_genome_execution_event", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_log_ladder_exit_audit", lambda *a, **k: None)
    monkeypatch.setattr(paper_fill, "book10l_at", lambda t: None)
    pos = _short_pos()
    thesis_px = _margin_price(bot.THESIS_FAST_EXIT_UNREAL_PCT)
    with bot.state_lock:
        bot.state["bid"], bot.state["ask"], bot.state["price"] = thesis_px - 1, thesis_px, thesis_px
    assert bot.get_mark_price("SHORT", fallback=thesis_px) == 0.0  # the ticker fallback is refused
    assert bot._apply_position_exits(pos, thesis_px, now=1_700_000_010.0) is False
    assert closed.calls == []
    assert pos["_trigger_meta"]["basis"] == "CLOCK" and pos["_trigger_meta"]["clock_only"] is True
    _book(monkeypatch, thesis_px - 1.0, thesis_px)
    assert bot._apply_position_exits(pos, 0.0, now=1_700_000_012.0) is True
    assert closed.calls[0][0] == "THESIS_FAST_CUT"
    assert pos["exit_stale_book_wait_s"] == 2.0
    assert pos["_trigger_meta"]["basis"] == "BOOK_TOP"


def test_forced_no_book_legacy_exit_is_priced_unverified_not_dropped(monkeypatch):
    import paper_fill

    monkeypatch.setattr(bot, "refresh_bbo_state", lambda *a, **k: None)
    monkeypatch.setattr(bot, "LEGACY_EXIT_BOOK_WAIT_S", 0.0)
    old = {"s": 1_600_000_000.0, "b": [[99_990.0, 1.0]], "a": [[100_010.0, 1.0]]}
    monkeypatch.setattr(paper_fill, "book10l_at", lambda t: old if t <= 1_600_000_001.5 else old)
    pos = _short_pos(_exit_eval_price=100_000.0)
    booked, sim = bot.resolve_sim_exit_price(pos, False, "THESIS_FAST_CUT")
    assert booked == 100_010.0
    assert sim["exit_pricing_basis"] == "FORCED_NO_BOOK_FIRST_ROW"
    assert sim["exit_evidence_status"] == "UNVERIFIED"


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
    _book(monkeypatch, ENTRY - 5, ENTRY + 5)
    booked, sim = bot.resolve_sim_exit_price(pos, False, "THESIS_FAST_CUT")
    walk, _ = bot.resolve_sim_exit_price(pos, False, "STOP_LOSS")
    # A stalled close books the market at fill time, never the stale trigger.
    assert abs(booked - (ENTRY + 5)) < 0.02
    assert abs(sim["trigger_price"] - trigger) < 0.02
    assert sim["trigger_slippage_bp"] is not None
    assert pos["exit_trigger_price"] == sim["trigger_price"] and pos["exit_fill_price"] == booked
    assert abs(walk - (ENTRY + 5)) < 1.0
    assert sim["source"] == "exit_book_row_at_fill"


@pytest.mark.parametrize(
    "reason",
    [
        "PHYSICAL_HARD_STOP_30PCT",
        "INITIAL_ATR_STOP",
        "PROFIT_PROTECTION_STOP",
        "PATH_END_120M",
    ],
)
def test_family_policy_exits_book_the_tick_that_triggered_them(monkeypatch, reason):
    pos = _short_pos()
    trigger = _margin_price(-30.0)
    pos["_exit_eval_price"] = trigger
    with bot.state_lock:
        bot.state["bid"] = ENTRY - 5
        bot.state["ask"] = ENTRY + 500
        bot.state["price"] = ENTRY
        bot.state["order_book"] = {
            "bids": [[ENTRY - 5, 1, 2.0]],
            "asks": [[ENTRY + 500, 1, 2.0]],
        }
    monkeypatch.setattr(bot, "refresh_bbo_state", lambda *a, **k: None)
    monkeypatch.setattr(bot, "refresh_order_book_state", lambda *a, **k: None)
    _book(monkeypatch, ENTRY - 5, ENTRY + 500)
    booked, sim = bot.resolve_sim_exit_price(pos, False, reason)
    assert abs(booked - (ENTRY + 500)) < 0.02  # side-correct book ask, not the trigger
    assert abs(sim["trigger_price"] - trigger) < 0.02
    assert sim["source"] == "exit_book_row_at_fill"


def test_maker_take_profit_books_its_resting_limit(monkeypatch):
    pos = _short_pos()
    pos["_exit_eval_price"] = ENTRY - 100
    monkeypatch.setattr(bot, "refresh_bbo_state", lambda *a, **k: None)
    booked, sim = bot.resolve_sim_exit_price(pos, True, "GS_ATR_TAKE_PROFIT")
    assert abs(booked - (ENTRY - 100)) < 0.02 and sim["source"] == "exit_maker_limit_fill"


def test_paper_helper_is_force_paper_mode():
    assert bot._force_paper_mode_active() is True
    assert bot._paper_natural_cycle_exits() is True
    assert bot.THESIS_FAST_EXIT_UNREAL_PCT == -12.0
    assert bot.MAX_SL_MARGIN_PCT == 30.0
    assert bot.TRAIL_LADDER_SCENARIO_C[0] == (8, 5)


def test_thesis_fast_cut_requires_mfe_at_most_two(monkeypatch):
    closed = _CloseCapture()
    monkeypatch.setattr(bot, "close_position", closed)
    pos = _short_pos(max_pnl_pct=2.5)
    assert bot._check_phase_margin_stop(pos, -13.0, 60.0) is False
    assert closed.calls == []
    pos = _short_pos(max_pnl_pct=1.9)
    assert bot._check_phase_margin_stop(pos, -13.0, 60.0) is True
    assert closed.calls[0][0] == "THESIS_FAST_CUT"


def test_gs01_and_b1_cuts_carry_the_mfe_cap_and_window():
    import combo_pathway_config as c
    import gs_regime_exit_stack as g
    for policy in (c._GS01_EXIT, c._GSB1_EXIT):
        for prof in policy["profiles"].values():
            assert prof["cut_max_peak_bp"] == 2.0 and prof["cut_win_sec"] == 300
    assert "cut_max_peak_bp" in open(g.__file__).read()
