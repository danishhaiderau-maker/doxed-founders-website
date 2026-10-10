"""HM full audit 10 Oct 2026: parallel position-manager closes, balance restore."""
import threading
import time

import bot


def test_position_manager_and_ws_tick_dispatch_closes_async(monkeypatch):
    calls = []

    def fake_apply(pos, mark, now, exit_source="POSITION_MANAGER", close_async=False):
        calls.append((exit_source, close_async))
        return False

    monkeypatch.setattr(bot, "_apply_position_exits", fake_apply)
    monkeypatch.setattr(bot, "_position_exit_claims", set())
    pos = {"trade_id": "t-par", "status": "OPEN", "dir": "LONG"}
    monkeypatch.setattr(bot, "open_positions", [pos])
    monkeypatch.setattr(bot, "refresh_bbo_state", lambda: None)
    monkeypatch.setattr(bot, "refresh_order_book_state", lambda: None)
    monkeypatch.setattr(bot, "process_funding_accrual", lambda: None)
    monkeypatch.setattr(bot, "_observable_exit_price", lambda: 60000.0)
    monkeypatch.setattr(bot, "get_mark_price", lambda *a, **k: 60000.0)
    bot.process_positions()
    assert calls == [("POSITION_MANAGER", True)]


def test_parallel_dispatch_does_not_serialize_slow_closes(monkeypatch):
    monkeypatch.setattr(bot, "_exit_close_threads", {})
    started = []

    def slow_close(pos, reason):
        started.append(time.monotonic())
        time.sleep(0.5)

    monkeypatch.setattr(bot, "close_position", slow_close)
    t0 = time.monotonic()
    for i in range(3):
        bot._dispatch_position_close({"trade_id": f"p{i}"}, "PROFIT_PROTECTION_STOP")
    for t in list(bot._exit_close_threads.values()):
        t.join(2)
    assert len(started) == 3 and max(started) - t0 < 0.3


def test_balance_restore_treats_zero_net_as_zero_not_percent(monkeypatch):
    rows = [{"net_pnl_usd": 0.0, "pnl": 3.3}, {"net_pnl_usd": -0.25, "pnl": -100.0}]
    monkeypatch.setattr(bot, "trades", rows)
    monkeypatch.setattr(bot, "_showcase_trade_session_start", lambda: 0.0)
    monkeypatch.setitem(bot.state, "strategy_mode", "RESEARCH")
    monkeypatch.setitem(bot.state, "live_armed", False)
    monkeypatch.setitem(bot.state, "account_balance", 0.0)
    bot._recompute_research_balance_from_trades()
    assert bot.state["account_balance"] == round(bot.STARTING_BALANCE - 0.25, 4)


def test_open_position_carries_decision_ts_and_entry_lock_wait():
    order = {"trade_id": "t-dec", "limit_price": 60000.0, "qty": 0.001, "created_ts": 100.0,
             "decision_ts": 99.5, "entry_lock_wait_ms": 12.5, "signal_dir": "LONG"}
    signal = {"trade_id": "t-dec", "final_direction": "LONG", "signal_price": 60000.0,
              "order_created_ts": 100.0}
    pos = bot._build_open_position(order, signal)
    assert pos["decision_ts"] == 99.5 and pos["entry_lock_wait_ms"] == 12.5
