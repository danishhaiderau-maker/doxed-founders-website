"""Fail-closed tests for the operator's manual execution pause.

The manual pause must cancel unfilled paper orders and block every route that
can create a new global order/position. Existing positions must keep receiving
normal exit management so the bot can reach a natural flat boundary.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


passed = 0
failed = 0
expired = []


def check(name, condition, detail=""):
    global passed, failed
    ok = bool(condition)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" ({detail})" if detail and not ok else ""))
    if ok:
        passed += 1
    else:
        failed += 1


def reset_state():
    expired.clear()
    with bot.trade_lock:
        bot.pending_orders.clear()
        bot.open_positions.clear()
        bot.trades_map.clear()
        for values in bot.lane_pending_orders.values():
            values.clear()
        for values in bot.lane_open_positions.values():
            values.clear()
    with bot.state_lock:
        for key in bot._persistent_config_keys() + ["_threshold_locked", "bootstrap_done"]:
            bot.state.setdefault(key, None)
        bot.state["manual_admin_pause"] = False
        bot.state["execution_paused"] = False
        bot.state["execution_reason"] = ""
        bot.state["_pause_priority"] = 0
        bot.state["strategy_mode"] = "RESEARCH"
        bot.state["account_balance"] = 100.0
        bot.state["price"] = 64_000.0
        bot.state["bid"] = 63_999.0
        bot.state["ask"] = 64_001.0
        bot.state["leverage"] = 100
        enabled = dict(bot.state.get("research_lane_enabled") or {})
        enabled[bot.RESEARCH_LANE_CONTINUOUS] = True
        enabled[bot.RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC] = True
        bot.state["research_lane_enabled"] = enabled


# Keep this unit test isolated from runtime CSV/config persistence.
_original_save_persistent_config = bot.save_persistent_config
bot.save_persistent_config = lambda: None
bot._record_expired_order = lambda order, reason: expired.append(
    (order.get("trade_id"), reason)
)
bot.expire_signal_for_order = lambda order, reason="TTL_EXPIRED": None
bot.pipeline_state_sync = lambda: None


print("=" * 72)
print("Manual admin pause entry-gate tests")
print("=" * 72)


print("\n[1] Pause cancels every unfilled global paper order")
reset_state()
pending = {
    "trade_id": "pause-pending-1",
    "research_lane": bot.RESEARCH_LANE_CONTINUOUS,
    "status": "PENDING",
}
bot.lane_register_pending_order(pending)
with bot.state_lock:
    bot.state["manual_admin_pause"] = True
bot.set_execution_paused("ADMIN_MANUAL")
check("pending order removed", pending not in bot.pending_orders)
check("pending order cancelled", pending.get("status") == "CANCELLED")
check("cancellation recorded", expired == [("pause-pending-1", "CIRCUIT_BREAKER_ADMIN_MANUAL")])


print("\n[2] Persisted manual flag stays authoritative if pause reason changes")
reset_state()
pending = {
    "trade_id": "pause-pending-2",
    "research_lane": bot.RESEARCH_LANE_CONTINUOUS,
    "status": "PENDING",
    "side": "sell",
    "signal_dir": "SHORT",
    "limit_price": 63_900.0,
}
bot.lane_register_pending_order(pending)
with bot.state_lock:
    bot.state["manual_admin_pause"] = True
    bot.state["execution_paused"] = True
    bot.state["execution_reason"] = "BLOCKED"
bot.process_pending_orders()
check("manual flag blocks fills after reason replacement", not bot.open_positions)
check("reason replacement order cancelled", pending.get("status") == "CANCELLED")


print("\n[3] Signal, market, limit, and Tile 2 entry routes fail closed")
reset_state()
with bot.state_lock:
    bot.state["manual_admin_pause"] = True
    bot.state["execution_paused"] = True
    bot.state["execution_reason"] = "BLOCKED"

signal_event = {
    "trade_id": "pause-signal-1",
    "research_lane": bot.RESEARCH_LANE_CONTINUOUS,
    "event_trigger": True,
}
original_is_buffer_ready = bot.is_buffer_ready
bot.is_buffer_ready = lambda: (_ for _ in ()).throw(
    AssertionError("paused signal reached feature pipeline")
)
bot.process_signal(signal_event)
bot.is_buffer_ready = original_is_buffer_ready
check("signal pipeline blocked before feature work", signal_event.get("status") == "BLOCKED")

market_signal = {
    "trade_id": "pause-market-1",
    "research_lane": bot.RESEARCH_LANE_CONTINUOUS,
    "final_direction": "LONG",
    "signal_price": 64_000.0,
}
market_result = bot.execute_market_order(market_signal)
check("market entry refused", market_result is False)
check("market route creates no position", not bot.open_positions)

limit_signal = {
    "trade_id": "pause-limit-1",
    "research_lane": bot.RESEARCH_LANE_CONTINUOUS,
    "final_direction": "LONG",
    "signal_price": 64_000.0,
}
limit_result = bot.create_limit_order(limit_signal)
check("limit entry refused", limit_result is None)
check("limit route creates no pending order", not bot.pending_orders)

tile2_ctx = {
    "trade_id": "pause-tile2-1",
    "research_lane": bot.RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC,
}
tile2_result = bot._submit_tile2_paper_resting_limit(
    tile2_ctx,
    "LONG",
    63_900.0,
    3.0,
    {},
    {},
    "pause-episode",
)
check("Tile 2 resting limit refused", tile2_result == "REFUSED_ADMIN_PAUSE")
check("Tile 2 route creates no pending order", not bot.pending_orders)


print("\n[4] A fill racing with the pause is cancelled before exposure opens")
reset_state()
with bot.state_lock:
    bot.state["manual_admin_pause"] = True
    bot.state["execution_paused"] = True
    bot.state["execution_reason"] = "ADMIN_MANUAL"
raced = {
    "trade_id": "pause-raced-fill-1",
    "research_lane": bot.RESEARCH_LANE_CONTINUOUS,
    "status": "FILLED",
    "side": "sell",
    "signal_dir": "SHORT",
    "limit_price": 64_000.0,
    "fill_price": 64_000.0,
    "qty": 0.01,
}
bot.lane_register_pending_order(raced)
bot.fill_order(raced)
check("raced order removed", raced not in bot.pending_orders)
check("raced order cancelled", raced.get("status") == "CANCELLED")
check("raced fill creates no position", not bot.open_positions)


print("\n[5] Existing positions continue through normal exit management")
reset_state()
with bot.state_lock:
    bot.state["manual_admin_pause"] = True
    bot.state["execution_paused"] = True
    bot.state["execution_reason"] = "ADMIN_MANUAL"
position = {
    "trade_id": "pause-existing-position-1",
    "research_lane": bot.RESEARCH_LANE_CONTINUOUS,
    "status": "OPEN",
    "dir": "SHORT",
}
bot.lane_register_open_position(position)
managed = []
original_refresh_bbo = bot.refresh_bbo_state
original_refresh_book = bot.refresh_order_book_state
original_funding = bot.process_funding_accrual
original_mark = bot.get_executable_mark_price
original_exits = bot._apply_position_exits
bot.refresh_bbo_state = lambda: None
bot.refresh_order_book_state = lambda: None
bot.process_funding_accrual = lambda: None
bot.get_executable_mark_price = lambda pos, fallback=None: float(fallback or 64_000.0)
bot._apply_position_exits = lambda pos, mark, now: managed.append((pos["trade_id"], mark))
bot.process_positions()
bot.refresh_bbo_state = original_refresh_bbo
bot.refresh_order_book_state = original_refresh_book
bot.process_funding_accrual = original_funding
bot.get_executable_mark_price = original_mark
bot._apply_position_exits = original_exits
check("existing position still managed", managed == [("pause-existing-position-1", 64_000.0)])


print("\n[6] Manual pause survives a process restart")
reset_state()
config_dir = tempfile.mkdtemp(prefix="manual-pause-config-")
config_path = os.path.join(config_dir, "config-7002.json")
original_get_config_file = bot.get_config_file
original_resolve_config_file = bot._resolve_config_file_for_load
bot.get_config_file = lambda: config_path
bot._resolve_config_file_for_load = lambda: config_path
with bot.state_lock:
    bot.state["manual_admin_pause"] = True
    bot.state["execution_paused"] = True
    bot.state["execution_reason"] = "ADMIN_MANUAL"
    bot.state["_pause_priority"] = bot.PAUSE_PRIORITIES["ADMIN_MANUAL"]
_original_save_persistent_config()
with open(config_path, "r", encoding="utf-8") as handle:
    saved_config = json.load(handle)
check("manual pause written to config", saved_config.get("manual_admin_pause") is True)
with bot.state_lock:
    bot.state["manual_admin_pause"] = False
    bot.state["execution_paused"] = False
    bot.state["execution_reason"] = ""
    bot.state["_pause_priority"] = 0
bot.load_persistent_config()
check("manual pause restored", bot.state.get("manual_admin_pause") is True)
check("restored process is execution-paused", bot.state.get("execution_paused") is True)
check("restored reason is ADMIN_MANUAL", bot.state.get("execution_reason") == "ADMIN_MANUAL")
bot.reset_transient_runtime_state()
check("startup reset preserves manual pause flag", bot.state.get("manual_admin_pause") is True)
check("startup reset preserves execution pause", bot.state.get("execution_paused") is True)
check("startup reset preserves ADMIN_MANUAL reason", bot.state.get("execution_reason") == "ADMIN_MANUAL")
check(
    "startup reset preserves ADMIN_MANUAL priority",
    bot.state.get("_pause_priority") == bot.PAUSE_PRIORITIES["ADMIN_MANUAL"],
)
bot.reset_session_risk_state()
check("session reset preserves manual pause flag", bot.state.get("manual_admin_pause") is True)
check("session reset preserves execution pause", bot.state.get("execution_paused") is True)
check("session reset preserves ADMIN_MANUAL reason", bot.state.get("execution_reason") == "ADMIN_MANUAL")
check(
    "session reset preserves ADMIN_MANUAL priority",
    bot.state.get("_pause_priority") == bot.PAUSE_PRIORITIES["ADMIN_MANUAL"],
)
bot.get_config_file = original_get_config_file
bot._resolve_config_file_for_load = original_resolve_config_file


print("\n" + "=" * 72)
print(f"RESULT: {passed} passed, {failed} failed")
print("=" * 72)
if failed:
    raise SystemExit(1)
