"""Paper Showcase must not latch DAILY_DRAWDOWN or LOSS_STREAK.

Danish: there is no point in a daily loss cap on paper — we need data.
Live copy keeps both gates when FORCE_PAPER_MODE is off.
"""

import datetime as _dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["FORCE_PAPER_MODE"] = "1"
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    ok = bool(condition)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" ({detail})" if detail and not ok else ""))
    if ok:
        passed += 1
    else:
        failed += 1


def reset_risk_state():
    with bot.state_lock:
        bot.state["daily_pnl_usd"] = 0.0
        bot.state["consecutive_losses"] = 0
        bot.state["loss_pause_until"] = 0.0
        bot.state["execution_paused"] = False
        bot.state["execution_reason"] = ""
        bot.state["_pause_priority"] = 0
        bot.state["manual_admin_pause"] = False
        bot.state["live_armed"] = False
        bot.state["current_trading_day"] = _dt.datetime.now(_dt.timezone.utc).date()


bot.save_persistent_config = lambda: None
bot._disarm_live_control = lambda reason="TEST": {"cancel": {"failed": [], "ok": []}, "exit_only": {}}
bot.pipeline_state_sync = lambda: None


print("=" * 72)
print("Paper Showcase skips DAILY_DRAWDOWN / LOSS_STREAK pauses")
print("=" * 72)

print("\n[1] Paper daily PnL -$23 does not set execution_paused")
reset_risk_state()
check("paper skip helper is active", bot._paper_skips_entry_risk_pauses() is True)
with bot.state_lock:
    bot.state["daily_pnl_usd"] = -23.0
allowed = bot.risk_trading_allowed()
check("risk_trading_allowed stays True at -$23", allowed is True)
check("execution_paused is False", bot.state.get("execution_paused") is False)
check(
    "execution_reason is not DAILY_DRAWDOWN",
    bot.state.get("execution_reason") not in ("DAILY_DRAWDOWN", "LOSS_STREAK"),
    detail=f"reason={bot.state.get('execution_reason')!r}",
)

print("\n[2] Paper apply_trade_pnl past -$20 does not latch")
reset_risk_state()
bot.apply_trade_pnl({"trade_id": "paper-loss", "net_pnl_usd": -23.0})
check("daily_pnl_usd recorded", bot.state.get("daily_pnl_usd") == -23.0)
check("execution_paused stays False after -$23 close", bot.state.get("execution_paused") is False)
check(
    "execution_reason not DAILY_DRAWDOWN after -$23 close",
    bot.state.get("execution_reason") not in ("DAILY_DRAWDOWN", "LOSS_STREAK"),
    detail=f"reason={bot.state.get('execution_reason')!r}",
)

print("\n[3] Paper LOSS_STREAK / 2h timer does not pause collection")
reset_risk_state()
with bot.state_lock:
    bot.state["execution_paused"] = True
    bot.state["execution_reason"] = "LOSS_STREAK"
    bot.state["consecutive_losses"] = 9
    bot.state["loss_pause_until"] = time.time() + 7200.0
allowed = bot.risk_trading_allowed()
check("stale LOSS_STREAK latch is cleared on paper", bot.state.get("execution_paused") is False)
check("LOSS_STREAK reason is cleared on paper", bot.state.get("execution_reason") != "LOSS_STREAK")
check("trading allowed despite 2h streak timer", allowed is True)

print("\n[4] Paper consecutive-loss closes do not latch LOSS_STREAK")
reset_risk_state()
for i in range(bot.CONSECUTIVE_LOSS_PAUSE + 1):
    bot.apply_trade_pnl({"trade_id": f"streak-{i}", "net_pnl_usd": -1.0})
check("streak counter still recorded", bot.state.get("consecutive_losses") >= bot.CONSECUTIVE_LOSS_PAUSE)
check("LOSS_STREAK not latched on paper", bot.state.get("execution_reason") != "LOSS_STREAK")
check("execution_paused stays False after streak", bot.state.get("execution_paused") is False)

print("\n[5] Live copy still pauses at -$23 when paper skip is off")
reset_risk_state()
orig = bot._paper_skips_entry_risk_pauses
bot._paper_skips_entry_risk_pauses = lambda: False
try:
    with bot.state_lock:
        bot.state["daily_pnl_usd"] = -23.0
    allowed = bot.risk_trading_allowed()
    check("live copy risk_trading_allowed is False at -$23", allowed is False)
    check("live copy sets execution_paused", bot.state.get("execution_paused") is True)
    check(
        "live copy reason is DAILY_DRAWDOWN",
        bot.state.get("execution_reason") == "DAILY_DRAWDOWN",
        detail=f"reason={bot.state.get('execution_reason')!r}",
    )
finally:
    bot._paper_skips_entry_risk_pauses = orig

print("\n" + "=" * 72)
print(f"PASS={passed} FAIL={failed}")
print("=" * 72)
if failed:
    sys.exit(1)
