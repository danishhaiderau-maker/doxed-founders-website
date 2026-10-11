"""FP #36: account_balance books forced closes; session_pnl_usd does not.

The residual "drift" was exactly the forced (deploy-flatten / admin) close
PnL. balance_reconcile must explain the wallet to the cent.
"""
import bot



def _rows():
    return [
        {"net_pnl_usd": -0.25, "exit_reason": "STOP_LOSS", "research_lane": ""},
        {"net_pnl_usd": 0.10, "exit_reason": "TAKE_PROFIT", "research_lane": ""},
        {"net_pnl_usd": -0.40, "exit_reason": "ADMIN_MANUAL_CLOSE", "research_lane": ""},
    ]


def test_forced_close_pnl_explains_balance_drift(monkeypatch):
    rows = _rows()
    monkeypatch.setattr(bot, "trades", rows)
    monkeypatch.setattr(bot, "_showcase_trade_session_start", lambda: 0.0)
    monkeypatch.setitem(bot.state, "strategy_mode", "RESEARCH")
    monkeypatch.setitem(bot.state, "live_armed", False)
    bot._recompute_research_balance_from_trades()
    balance = bot.get_display_balance()
    assert balance == round(bot.STARTING_BALANCE - 0.55, 4)
    with bot.trade_lock:
        _, session_pnl, _ = bot._session_trade_accounting_locked(0.0)
        forced = bot._session_forced_close_pnl_usd_locked(0.0)
    assert session_pnl == -0.15
    assert forced == -0.40
    rec = bot._balance_reconcile_block(balance, session_pnl, forced)
    assert rec["residual_usd"] == 0.0
    assert rec["expected_balance_usd"] == balance


def test_public_state_keeps_reconcile_fields():
    assert "balance_reconcile" in bot._PUBLIC_STATE_SAFE_TOP_KEYS
    assert "forced_close_pnl_usd" in bot._PUBLIC_STATE_SAFE_TOP_KEYS
