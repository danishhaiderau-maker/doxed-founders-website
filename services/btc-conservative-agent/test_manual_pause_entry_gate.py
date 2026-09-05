"""Fail-closed tests for the operator's manual execution pause.

The manual pause must cancel unfilled paper orders and block every route that
can create a new global order/position. Existing positions must keep receiving
normal exit management so the bot can reach a natural flat boundary.
"""

import json
import os
import sys
import tempfile
import threading

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
        enabled[bot.COMBO_EXECUTION_LANES[0]] = True
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


print("\n[3] Paused research may collect, while every execution route fails closed")
reset_state()
with bot.state_lock:
    bot.state["manual_admin_pause"] = True
    bot.state["execution_paused"] = True
    bot.state["execution_reason"] = "BLOCKED"

signal_event = {
    "trade_id": "pause-signal-1",
    "research_lane": bot.COMBO_EXECUTION_LANES[0],
    "event_trigger": True,
}
original_is_buffer_ready = bot.is_buffer_ready
original_log_no_signal = bot.log_no_signal_with_context
research_progress = []
bot.is_buffer_ready = lambda: False
bot.log_no_signal_with_context = lambda reason=None, **kwargs: research_progress.append(reason)
bot.process_signal(signal_event)
bot.is_buffer_ready = original_is_buffer_ready
bot.log_no_signal_with_context = original_log_no_signal
check(
    "paused paper entry stops before feature work",
    research_progress == [],
)
check("paused research creates no global order", not bot.pending_orders)
check("paused research creates no global position", not bot.open_positions)

with bot.state_lock:
    bot.state["strategy_mode"] = "LIVE"
    bot.state["live_armed"] = True
live_pause_reached_features = []
bot.is_buffer_ready = lambda: live_pause_reached_features.append(True) or False
bot.process_signal(signal_event)
bot.is_buffer_ready = original_is_buffer_ready
check("paused live runtime stops before feature work", not live_pause_reached_features)
with bot.state_lock:
    bot.state["strategy_mode"] = "RESEARCH"
    bot.state["live_armed"] = False

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

print("\n[4] A fill racing with the pause is cancelled before exposure opens")
reset_state()
with bot.state_lock:
    bot.state["manual_admin_pause"] = True
    bot.state["execution_paused"] = True
    bot.state["execution_reason"] = "ADMIN_MANUAL"
raced = {
    "trade_id": "pause-raced-fill-1",
    "research_lane": bot.RESEARCH_LANE_CONTINUOUS,
    # The real touch detector retains PENDING until durable OPEN commit.
    "status": "PENDING",
    "fill_handoff_in_progress": True,
    "side": "sell",
    "signal_dir": "SHORT",
    "limit_price": 64_000.0,
    "fill_price": 64_000.0,
    "qty": 0.01,
}
bot.lane_register_pending_order(raced)
bot.fill_handoff_trade_ids.add(raced["trade_id"])
bot.fill_order(raced)
check("raced order removed", raced not in bot.pending_orders)
check("raced order cancelled", raced.get("status") == "CANCELLED")
check("raced fill creates no position", not bot.open_positions)
check("raced cancellation recorded once", expired == [("pause-raced-fill-1", "ADMIN_MANUAL_PAUSE")])
check("raced handoff released", raced["trade_id"] not in bot.fill_handoff_trade_ids and "fill_handoff_in_progress" not in raced)


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
original_mark = bot.get_mark_price
original_exits = bot._apply_position_exits
bot.refresh_bbo_state = lambda: None
bot.refresh_order_book_state = lambda: None
bot.process_funding_accrual = lambda: None
bot.get_mark_price = lambda direction, fallback=None: float(fallback or 64_000.0)
bot._apply_position_exits = lambda pos, mark, now: managed.append((pos["trade_id"], mark))
bot.process_positions()
bot.refresh_bbo_state = original_refresh_bbo
bot.refresh_order_book_state = original_refresh_book
bot.process_funding_accrual = original_funding
bot.get_mark_price = original_mark
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


print("\n[7] Direct local bridge control is safe during secret rotation")
reset_state()
original_admin_token = bot._BOT_ADMIN_TOKEN
original_bootstrap_complete = bot._DASHBOARD_BOOTSTRAP_COMPLETE
original_recompute_system_readiness = bot._recompute_system_readiness
bot._BOT_ADMIN_TOKEN = "required-test-token"
bot._DASHBOARD_BOOTSTRAP_COMPLETE = True
bot._recompute_system_readiness = lambda: {
    "system_ready": False,
    "readiness_reasons": ["TEST_NOT_READY"],
}
with bot.app.test_client() as client:
    with bot._api_state_cache_lock:
        bot._api_state_cache["payload"] = {
            "execution_paused": False,
            "execution_reason": "",
            "manual_admin_pause": False,
            "orders": [{"trade_id": "stale-cache-order"}],
            "positions": [],
        }
    direct_pause = client.post("/api/pause", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    check("direct loopback pause is accepted without a token", direct_pause.status_code == 200)
    check("direct loopback pause changes state", bot.state.get("execution_paused") is True)
    direct_state = client.get(
        "/api/state",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ).get_json()
    check("pause immediately patches cached execution state", direct_state.get("execution_paused") is True)
    check("pause immediately patches cached manual flag", direct_state.get("manual_admin_pause") is True)
    check("pause preserves ADMIN_MANUAL as cached reason", direct_state.get("execution_reason") == "ADMIN_MANUAL")
    check("pause immediately removes cancelled paper order from cache", direct_state.get("orders") == [])
    forwarded_resume = client.post(
        "/api/resume",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        headers={"X-Forwarded-For": "203.0.113.10"},
    )
    check("proxied loopback resume remains token-protected", forwarded_resume.status_code == 401)
    check("rejected proxied resume leaves pause active", bot.state.get("execution_paused") is True)
    unready_resume = client.post("/api/resume", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    check("unready direct loopback resume fails closed", unready_resume.status_code == 409)
    check("unready direct loopback resume leaves pause active", bot.state.get("execution_paused") is True)
    bot._recompute_system_readiness = lambda: {
        "system_ready": True,
        "readiness_reasons": [],
    }
    ready_resume = client.post("/api/resume", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    check("ready direct loopback resume is accepted without a token", ready_resume.status_code == 200)
    check("ready direct loopback resume changes state", bot.state.get("execution_paused") is False)
    resumed_state = client.get(
        "/api/state",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ).get_json()
    check("resume immediately patches cached execution state", resumed_state.get("execution_paused") is False)
    check("resume immediately patches cached manual flag", resumed_state.get("manual_admin_pause") is False)
bot._BOT_ADMIN_TOKEN = original_admin_token
bot._DASHBOARD_BOOTSTRAP_COMPLETE = original_bootstrap_complete
bot._recompute_system_readiness = original_recompute_system_readiness


print("\n[7b] Manual pause reason cannot be overwritten by generic execution status")
reset_state()
with bot.state_lock:
    bot.state["manual_admin_pause"] = True
    bot.state["execution_paused"] = True
    bot.state["execution_reason"] = "ADMIN_MANUAL"
    bot.state["_pause_priority"] = bot.PAUSE_PRIORITIES["ADMIN_MANUAL"]
check("execution remains blocked", bot.execution_allowed(bot.RESEARCH_LANE_CONTINUOUS) is False)
check("manual pause reason remains authoritative", bot.state.get("execution_reason") == "ADMIN_MANUAL")
check("manual pause priority remains authoritative", bot.state.get("_pause_priority") == bot.PAUSE_PRIORITIES["ADMIN_MANUAL"])


print("\n[8] Paused shadow replay is visible without entering global books")
reset_state()
baseline_paused_open = bot.paused_shadow_dashboard_stats().get("open", 0)
paused_shadow_start = bot.time.time()
paused_shadow_call_ts = bot.utc_iso()
with bot.replay_lock:
    bot.replay_buffers["pause-shadow-visible-1"] = {
        "closed": False,
        "start_ts": paused_shadow_start,
        "research_lane": bot.COMBO_EXECUTION_LANES[0],
        "direction": "SHORT",
        "paused_shadow": True,
        "collection_mode": "ADMIN_PAUSED_SHADOW",
        "source_trade_id": "scan-pause-shadow-1",
        "shared_ai_call_id": "scan-pause-shadow-1",
        "shared_ai_call_ts": paused_shadow_call_ts,
        "adx_at_signal": 27.0,
        "prompt_id": bot.SHARED_DIRECTION_PROMPT_ID,
    }
paused_stats = bot.paused_shadow_dashboard_stats()
check(
    "paused shadow replay appears in dedicated stats",
    paused_stats.get("open") == baseline_paused_open + 1,
)
check("paused shadow safety is explicit", paused_stats.get("safety") == "NEVER_RELAY_ELIGIBLE")
visible_row = next(
    row for row in paused_stats.get("recent", [])
    if row.get("trade_id") == "pause-shadow-visible-1"
)
check("paused shadow preserves one shared paid-call ID", visible_row.get("shared_ai_call_id") == "scan-pause-shadow-1")
check(
    "paused shadow separates AI-call time from lane time",
    visible_row.get("shared_ai_call_ts") == paused_shadow_call_ts
    and visible_row.get("lane_recorded_ts"),
)
check("paused shadow remains outside pending orders", not bot.pending_orders)
check("paused shadow remains outside positions", not bot.open_positions)
with open(bot.__file__, "r", encoding="utf-8") as dashboard_file:
    dashboard_source = dashboard_file.read()
check(
    "paused shadow dashboard limits the visible table to five rows",
    "const pausedRecent = (ps.recent || []).slice(0, 5);" in dashboard_source
    and "pausedRecent.length ? pausedRecent.map" in dashboard_source,
)
with bot.replay_lock:
    bot.replay_buffers.pop("pause-shadow-visible-1", None)


print("\n[15] Admin controls never hold state_lock across slow persistence/cancellation")
reset_state()
lock_observations = []


def observe_state_lock(label):
    acquired = threading.Event()

    def probe():
        if bot.state_lock.acquire(timeout=0.25):
            acquired.set()
            bot.state_lock.release()

    worker = threading.Thread(target=probe)
    worker.start()
    worker.join(timeout=0.5)
    lock_observations.append((label, acquired.is_set()))


bot.save_persistent_config = lambda: observe_state_lock("persist")
original_cancel = bot.circuit_breaker_cancel_pending
bot.circuit_breaker_cancel_pending = lambda reason: (
    observe_state_lock("cancel"),
    0,
)[1]
original_recompute_system_readiness = bot._recompute_system_readiness
bot._recompute_system_readiness = lambda: {
    "system_ready": True,
    "readiness_reasons": [],
}
with bot.app.test_request_context("/api/pause", method="POST"):
    pause_response = bot.api_pause()
with bot.app.test_request_context("/api/resume", method="POST"):
    resume_response = bot.api_resume()
check("pause endpoint succeeds", pause_response.status_code == 200)
check("resume endpoint succeeds", resume_response.status_code == 200)
check(
    "state lock released before persistence and cancellation",
    lock_observations == [
        ("persist", True),
        ("cancel", True),
        ("persist", True),
    ],
    detail=str(lock_observations),
)
bot.circuit_breaker_cancel_pending = original_cancel
bot.save_persistent_config = lambda: None
bot._recompute_system_readiness = original_recompute_system_readiness


print("\n" + "=" * 72)
print(f"RESULT: {passed} passed, {failed} failed")
print("=" * 72)
if failed:
    raise SystemExit(1)
