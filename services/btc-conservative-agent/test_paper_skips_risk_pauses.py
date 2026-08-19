"""Paper Showcase must not latch DAILY_DRAWDOWN or LOSS_STREAK."""
import os
import sys

os.environ["FORCE_PAPER_MODE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot


def check(name, cond):
    if not cond:
        raise SystemExit(f"FAIL {name}")
    print("ok", name)


bot.state["execution_paused"] = True
bot.state["execution_reason"] = "DAILY_DRAWDOWN"
bot.state["daily_pnl_usd"] = -(bot.DAILY_DRAWDOWN_PAUSE_USD + 5)
bot.state["consecutive_losses"] = 9
bot.state["loss_pause_until"] = 10**12
check("paper skip helper", bot._paper_skips_entry_risk_pauses() is True)
check("risk_trading_allowed True on paper", bot.risk_trading_allowed() is True)
check("drawdown latch cleared", bot.state.get("execution_reason") != "DAILY_DRAWDOWN")

bot.apply_trade_pnl({"net_pnl_usd": -50.0})
check("apply_trade_pnl does not pause paper", bot.state.get("execution_reason") not in ("DAILY_DRAWDOWN", "LOSS_STREAK"))
print("PASS")
