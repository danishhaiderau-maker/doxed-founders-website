"""Regression tests for the UTC-midnight rollover of daily_pnl_usd.

Background: ``daily_pnl_usd`` was only reset on process restart, so once the
DAILY_DRAWDOWN guard tripped it stayed tripped indefinitely. The fix
(``_rollover_daily_pnl_if_new_utc_day``) resets the bucket when the UTC date
advances and clears a stale DAILY_DRAWDOWN pause carried over from the prior
day. These tests pin both behaviours so the deadlock Danish hit on 2026-08-04
cannot silently come back.

These tests run WITHOUT pytest (matches the rest of this repo's test style):
``python test_daily_pnl_utc_rollover.py`` or ``python -m pytest`` (the
module-level print/check harness still executes via import).
"""

import os
import sys
import datetime as _dt
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("FORCE_PAPER_MODE", "1")
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
    """Reset only the risk/pause fields the rollover + drawdown gate touch."""
    with bot.state_lock:
        bot.state["daily_pnl_usd"] = 0.0
        bot.state["consecutive_losses"] = 0
        bot.state["loss_pause_until"] = 0.0
        bot.state["execution_paused"] = False
        bot.state["execution_reason"] = ""
        bot.state["_pause_priority"] = 0
        bot.state["manual_admin_pause"] = False
        bot.state["current_trading_day"] = _dt.datetime.now(_dt.timezone.utc).date()


# Keep the test isolated from persistent config writes and order cancellation
# side-effects of set_execution_paused.
bot.save_persistent_config = lambda: None
bot._disarm_live_control = lambda reason="TEST": {"cancel": {"failed": [], "ok": []}, "exit_only": {}}
bot.pipeline_state_sync = lambda: None


def _force_pause_active(reason="DAILY_DRAWDOWN"):
    """Drive the bot into a paused state via the canonical entrypoint."""
    bot.set_execution_paused(reason)


print("=" * 72)
print("Daily PnL UTC-midnight rollover regression tests")
print("=" * 72)


# ---------------------------------------------------------------------------
# [1] Same UTC day, drawdown exceeded -> DAILY_DRAWDOWN pause fires
# ---------------------------------------------------------------------------
print("\n[1] Same UTC day, drawdown exceeded -> DAILY_DRAWDOWN pause fires")
reset_risk_state()
# Push daily PnL past the drawdown threshold on the SAME UTC day.
with bot.state_lock:
    bot.state["daily_pnl_usd"] = -(bot.DAILY_DRAWDOWN_PAUSE_USD + 0.01)
    bot.state["current_trading_day"] = _dt.datetime.now(_dt.timezone.utc).date()
allowed = bot.risk_trading_allowed()
check("risk_trading_allowed returns False when drawdown exceeded", allowed is False)
check("execution_paused is set", bot.state.get("execution_paused") is True)
check(
    "execution_reason is DAILY_DRAWDOWN",
    bot.state.get("execution_reason") == "DAILY_DRAWDOWN",
    detail=f"reason={bot.state.get('execution_reason')!r}",
)


# ---------------------------------------------------------------------------
# [2] New UTC day -> daily_pnl_usd resets to 0 and DAILY_DRAWDOWN pause clears
# ---------------------------------------------------------------------------
print("\n[2] New UTC day -> daily_pnl_usd resets and pause auto-clears")
reset_risk_state()
# Plant yesterday's drawdown state.
yesterday = _dt.datetime.now(_dt.timezone.utc).date() - _dt.timedelta(days=1)
with bot.state_lock:
    bot.state["daily_pnl_usd"] = -bot.DAILY_DRAWDOWN_PAUSE_USD - 5.0
    bot.state["current_trading_day"] = yesterday
_force_pause_active("DAILY_DRAWDOWN")
check("precondition: bot is paused with DAILY_DRAWDOWN", bot.state.get("execution_reason") == "DAILY_DRAWDOWN")
check("precondition: stale daily_pnl_usd below threshold", bot.state.get("daily_pnl_usd") <= -bot.DAILY_DRAWDOWN_PAUSE_USD)
check("precondition: stored day is yesterday", bot.state.get("current_trading_day") == yesterday)

allowed = bot.risk_trading_allowed()
today = _dt.datetime.now(_dt.timezone.utc).date()
check("current_trading_day advanced to today", bot.state.get("current_trading_day") == today)
check("daily_pnl_usd reset to 0.0", bot.state.get("daily_pnl_usd") == 0.0)
check(
    "stale DAILY_DRAWDOWN pause auto-clears on rollover",
    bot.state.get("execution_paused") is False,
    detail=f"reason={bot.state.get('execution_reason')!r}",
)
check(
    "execution_reason cleared",
    bot.state.get("execution_reason") == "",
)
check(
    "risk_trading_allowed returns True after rollover (no other gates tripped)",
    allowed is True,
)


# ---------------------------------------------------------------------------
# [3] New UTC day with the same losses re-applied -> pause fires again
#     (rollover must not break the protection)
# ---------------------------------------------------------------------------
print("\n[3] New UTC day with same losses re-applied -> pause fires again")
reset_risk_state()
# Simulate the rollover happening cleanly first.
with bot.state_lock:
    bot.state["daily_pnl_usd"] = 0.0
    bot.state["current_trading_day"] = _dt.datetime.now(_dt.timezone.utc).date()
bot.risk_trading_allowed()  # no-op rollover (same day)
check("same-day rollover is a no-op", bot.state.get("daily_pnl_usd") == 0.0)

# Now apply today's losing trades until drawdown trips.
loss_per_trade = -((bot.DAILY_DRAWDOWN_PAUSE_USD / 2.0) + 0.5)  # 2 trades trips it
bot.apply_trade_pnl({"trade_id": "loss-1", "net_pnl_usd": loss_per_trade})
check("first loss does not yet trip drawdown", bot.state.get("execution_reason") != "DAILY_DRAWDOWN")
bot.apply_trade_pnl({"trade_id": "loss-2", "net_pnl_usd": loss_per_trade})
check(
    "second loss trips DAILY_DRAWDOWN again on the new day",
    bot.state.get("execution_reason") == "DAILY_DRAWDOWN",
    detail=f"reason={bot.state.get('execution_reason')!r}",
)
check("bot is paused again", bot.state.get("execution_paused") is True)
check("daily_pnl_usd reflects only today's losses", bot.state.get("daily_pnl_usd") < 0)


# ---------------------------------------------------------------------------
# [4] Rollover does NOT clear ADMIN_MANUAL pause (only DAILY_DRAWDOWN)
# ---------------------------------------------------------------------------
print("\n[4] Rollover preserves ADMIN_MANUAL pause")
reset_risk_state()
yesterday = _dt.datetime.now(_dt.timezone.utc).date() - _dt.timedelta(days=1)
with bot.state_lock:
    bot.state["daily_pnl_usd"] = -100.0
    bot.state["current_trading_day"] = yesterday
    bot.state["manual_admin_pause"] = True
bot.set_execution_paused("ADMIN_MANUAL")
check("precondition: paused with ADMIN_MANUAL", bot.state.get("execution_reason") == "ADMIN_MANUAL")

rolled, prev_reason = bot._rollover_daily_pnl_if_new_utc_day()
check("rollover fires (new day)", rolled is True)
check("captured prior reason was ADMIN_MANUAL", prev_reason == "ADMIN_MANUAL")
# risk_trading_allowed() must NOT auto-clear ADMIN_MANUAL even though it sees a
# rollover. (Note: the manual-admin pause is enforced separately by
# can_progress_new_entry / api_pause; risk_trading_allowed() only owns the
# risk-specific gates. What matters here is that this function does not stomp
# the ADMIN_MANUAL pause via set_execution_paused("") on rollover.)
bot.risk_trading_allowed()
check(
    "ADMIN_MANUAL pause survives rollover (priority respected)",
    bot.state.get("execution_reason") == "ADMIN_MANUAL",
    detail=f"reason={bot.state.get('execution_reason')!r}",
)
check("manual_admin_pause flag survives rollover", bot.state.get("manual_admin_pause") is True)


# ---------------------------------------------------------------------------
# [5] Helper is a no-op when day has not advanced
# ---------------------------------------------------------------------------
print("\n[5] Helper is a no-op when UTC day has not advanced")
reset_risk_state()
with bot.state_lock:
    bot.state["daily_pnl_usd"] = 12.34
rolled, _ = bot._rollover_daily_pnl_if_new_utc_day()
check("rollover returns False on same day", rolled is False)
check("daily_pnl_usd untouched on same day", bot.state.get("daily_pnl_usd") == 12.34)


# ---------------------------------------------------------------------------
# [6] Cleared loss counters cannot leave a stale LOSS_STREAK pause latched
# ---------------------------------------------------------------------------
print("\n[6] Cleared loss counters release a stale LOSS_STREAK pause")
reset_risk_state()
with bot.state_lock:
    bot.state["execution_paused"] = True
    bot.state["execution_reason"] = "LOSS_STREAK"
    bot.state["consecutive_losses"] = 0
    bot.state["loss_pause_until"] = 0.0
allowed = bot.risk_trading_allowed()
check("stale LOSS_STREAK pause is cleared", bot.state.get("execution_paused") is False)
check("stale LOSS_STREAK reason is cleared", bot.state.get("execution_reason") == "")
check("trading is allowed after stale latch clears", allowed is True)


# ---------------------------------------------------------------------------
# [7] A real, unexpired loss pause remains enforced even if counters reset
# ---------------------------------------------------------------------------
print("\n[7] Active LOSS_STREAK timer remains enforced")
reset_risk_state()
active_until = time.time() + 300.0
with bot.state_lock:
    bot.state["execution_paused"] = True
    bot.state["execution_reason"] = "LOSS_STREAK"
    bot.state["consecutive_losses"] = 0
    bot.state["loss_pause_until"] = active_until
allowed = bot.risk_trading_allowed()
check("active LOSS_STREAK pause remains set", bot.state.get("execution_paused") is True)
check("active LOSS_STREAK reason remains set", bot.state.get("execution_reason") == "LOSS_STREAK")
check("active loss timer remains intact", bot.state.get("loss_pause_until") == active_until)
check("trading remains blocked during active timer", allowed is False)


print("\n" + "=" * 72)
print(f"PASS={passed} FAIL={failed}")
print("=" * 72)
if failed:
    sys.exit(1)
