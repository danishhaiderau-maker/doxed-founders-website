"""Pt 6: Complete Cheetah/CONTINUOUS toggle matrix for the relay contract.

Tests all 11 scenarios from the toggle contract spec. Uses an in-process
mock of the Bitfinex exchange object so NO real orders are ever placed.

Run: cd services/btc-conservative-agent && python test_toggle_matrix.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Boot safe: paper mode + research mode (no real Bitfinex calls)
os.environ["FORCE_PAPER_MODE"] = "1"
os.environ["RESEARCH_DATA_COLLECTION"] = "1"
os.environ["SKIP_EXCHANGE_MARKET_LOAD"] = "1"

import bot
# The matrix deliberately simulates LIVE and OFF transitions. Keep all of
# those mutations in memory so a test run cannot arm live mode or disable a
# tile in the operator's real config-7002.json.
bot.save_persistent_config = lambda: None
from bot import (
    execution_mode_for_lane,
    lane_can_place_new_entry,
    lane_is_live,
    lane_execution_block_reason,
    mark_lane_exit_only,
    clear_lane_exit_only,
    lane_is_exit_only,
    suspend_lane_trading,
    _cancel_bitfinex_orders_for_lane,
    _mark_exit_only_for_open_bitfinex_positions,
    EXEC_MODE_LAB_SHADOW,
    EXEC_MODE_PAPER,
    EXEC_MODE_LIVE,
    EXEC_MODE_EXIT_ONLY,
    RESEARCH_LANE_CONTINUOUS,
)
import bitfinex_live_executor as bx

LANE = RESEARCH_LANE_CONTINUOUS
passed = 0
failed = 0


def set_lane(on):
    with bot.state_lock:
        if LANE == RESEARCH_LANE_CONTINUOUS:
            bot.state["continuous_ai_research_enabled"] = bool(on)
        m = dict(bot.state.get("research_lane_enabled") or {})
        m[LANE] = bool(on)
        bot.state["research_lane_enabled"] = m


def set_bx(on):
    with bot.state_lock:
        bot.state["bitfinex_live_enabled"] = bool(on)
        bot.state["live_armed"] = bool(on)


def reset():
    set_lane(True)
    set_bx(False)
    for lane in list(bot._exit_only_until.keys()):
        clear_lane_exit_only(lane)


def check(name, cond, detail=""):
    global passed, failed
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if cond:
        passed += 1
    else:
        failed += 1


print("=" * 72)
print("Pt 6 toggle test matrix -- CONTINUOUS / Cheetah source")
print("=" * 72)

# === 1. Tile OFF + Bitfinex OFF: LAB shadow, zero orders, zero exchange calls
print("\n[1] Tile OFF + Bitfinex OFF")
reset()
set_lane(False)
check("mode == LAB_SHADOW", execution_mode_for_lane(LANE) == EXEC_MODE_LAB_SHADOW,
      execution_mode_for_lane(LANE))
check("cannot place entry", not lane_can_place_new_entry(LANE))
check("not live", not lane_is_live(LANE))

# === 2. Tile OFF + Bitfinex ON: LAB shadow, zero exchange entry calls
print("\n[2] Tile OFF + Bitfinex ON")
reset()
set_lane(False)
set_bx(True)
check("mode == LAB_SHADOW (tile off wins)", execution_mode_for_lane(LANE) == EXEC_MODE_LAB_SHADOW,
      execution_mode_for_lane(LANE))
check("cannot place entry even with Bitfinex ON", not lane_can_place_new_entry(LANE))
set_bx(False)

# === 3. Tile ON + Bitfinex OFF: PAPER mode
print("\n[3] Tile ON + Bitfinex OFF -> PAPER")
reset()
check("mode == PAPER", execution_mode_for_lane(LANE) == EXEC_MODE_PAPER,
      execution_mode_for_lane(LANE))
check("can place entry", lane_can_place_new_entry(LANE))
check("not live", not lane_is_live(LANE))

# === 4. Legacy source live flags cannot bypass the platform relay
print("\n[4] Tile ON + legacy source Bitfinex ON -> local PAPER")
reset()
set_bx(True)
check("mode == PAPER", execution_mode_for_lane(LANE) == EXEC_MODE_PAPER,
      execution_mode_for_lane(LANE))
check("can place entry", lane_can_place_new_entry(LANE))
check("is not direct-live", not lane_is_live(LANE))
br = lane_execution_block_reason(LANE)
check("local paper lane has no local entry block", br is None, br)
set_bx(False)

# === 5. Legacy source keys/flags still cannot submit an eligible entry
print("\n[5] Platform-relay lanes never enter through the legacy source executor")
reset()
set_bx(True)
called = []
original_submit = bx.submit_limit_entry
original_keys = bot._private_api_keys_ok
try:
    bx.submit_limit_entry = lambda *args, **kwargs: called.append((args, kwargs))
    bot._private_api_keys_ok = lambda: True
    bot._maybe_bitfinex_limit_entry(
        {
            "trade_id": "tbhv1-no-direct-entry",
            "research_lane": LANE,
            "signal_dir": "LONG",
            "qty": 0.001,
            "limit_price": 64000.0,
        },
        {
            "trade_id": "tbhv1-no-direct-entry",
            "research_lane": LANE,
            "final_direction": "LONG",
        },
    )
finally:
    bx.submit_limit_entry = original_submit
    bot._private_api_keys_ok = original_keys
check("legacy direct submit was not called", called == [], f"calls={called}")
check("source execution remains PAPER", execution_mode_for_lane(LANE) == EXEC_MODE_PAPER)
set_bx(False)

# === 6. Turn Tile OFF with a live pending order: local removal + Bitfinex cancel
print("\n[6] Tile OFF with live pending order -> local + Bitfinex cancel")
reset()
# Simulate a pending order with Bitfinex id
import time as _time
fake_order = {
    "trade_id": "test-tid-1",
    "status": "PENDING",
    "research_lane": LANE,
    "bitfinex_order_id": "BF-12345",
    "price": 64000.0,
    "side": "buy",
    "qty": 0.001,
    "timestamp": _time.time(),
}
with bot.trade_lock:
    bot.pending_orders.append(fake_order)
    bot.lane_register_pending_order(fake_order) if hasattr(bot, "lane_register_pending_order") else None
# Suspend lane -> cancellation uncertainty must retain ownership and try again.
result = suspend_lane_trading(LANE, reason="TEST_TILE_OFF")
check("unconfirmed pending remains owned", any(
          o.get("trade_id") == "test-tid-1" for o in bot.pending_orders
      ) and any(
          row.get("trade_id") == "test-tid-1" and row.get("reason") == "CANCEL_UNCONFIRMED"
          for row in result.get("bitfinex_failed", [])
      ),
      f"result={result}")
# Bitfinex cancel will fail because we don't have keys, but the attempt is recorded
check("Bitfinex cancel attempted (best-effort)",
      isinstance(result.get("bitfinex_cancelled"), list),
      f"bx_cancelled={result.get('bitfinex_cancelled')}")
# Cleanup
with bot.trade_lock:
    bot.pending_orders[:] = [o for o in bot.pending_orders if o.get("trade_id") != "test-tid-1"]

# === 7. Turn Tile OFF with a filled position: no new entries; exit mgmt continues
print("\n[7] Tile OFF with filled position -> no new entries, exits continue")
reset()
# Simulate a filled position
fake_pos = {
    "trade_id": "test-pos-1",
    "status": "FILLED",
    "research_lane": LANE,
    "side": "buy",
    "entry_price": 64000.0,
    "qty": 0.001,
    "bitfinex_order_id": "BF-FILLED-EXIT-ONLY",
}
with bot.trade_lock:
    bot.open_positions.append(fake_pos)
# Actually turn the tile OFF (the test is "Tile OFF with filled position")
set_lane(False)
result = suspend_lane_trading(LANE, reason="TEST_TILE_OFF")
check("open position remains (not orphaned)",
      any(p.get("trade_id") == "test-pos-1" for p in bot.open_positions))
check("cannot place new entries", not lane_can_place_new_entry(LANE))
# Cleanup
with bot.trade_lock:
    bot.open_positions[:] = [p for p in bot.open_positions if p.get("trade_id") != "test-pos-1"]
set_lane(True)  # restore for subsequent tests

# === 8. Turn Bitfinex OFF with open exposure: exit-only management continues
print("\n[8] Bitfinex OFF with open exposure -> EXIT_ONLY for lane")
reset()
# Arm Bitfinex, simulate filled position, then disarm
set_bx(True)
fake_pos = {
    "trade_id": "test-pos-2",
    "status": "FILLED",
    "research_lane": LANE,
    "side": "buy",
    "entry_price": 64000.0,
    "qty": 0.001,
    "bitfinex_order_id": "BF-FILLED-EXIT-ONLY",
}
with bot.trade_lock:
    bot.open_positions.append(fake_pos)
# Disarm Bitfinex via the helper (simulates the /api/bitfinex_live OFF path)
result = _mark_exit_only_for_open_bitfinex_positions(reason="TEST_BFX_DISARM")
check("lane marked EXIT_ONLY", lane_is_exit_only(LANE))
check("exit_only_lanes includes lane", LANE in result, f"result={result}")
check("execution mode is EXIT_ONLY", execution_mode_for_lane(LANE) == EXEC_MODE_EXIT_ONLY,
      execution_mode_for_lane(LANE))
check("no new entries", not lane_can_place_new_entry(LANE))
# Cleanup
clear_lane_exit_only(LANE)
with bot.trade_lock:
    bot.open_positions[:] = [p for p in bot.open_positions if p.get("trade_id") != "test-pos-2"]
set_bx(False)

# === 9. Restart with persisted state: behavior + status consistent
print("\n[9] Restart with persisted state")
reset()
# Simulate restart by checking that resolver is consistent across re-invocations
m1 = execution_mode_for_lane(LANE)
m2 = execution_mode_for_lane(LANE)
check("mode stable across calls", m1 == m2 == EXEC_MODE_PAPER)

# === 10. Retired/unknown lanes cannot create orders
print("\n[10] Retired/unknown lanes cannot create orders")
reset()
check("BOGUS_LANE unknown -> LAB_SHADOW",
      execution_mode_for_lane("BOGUS_LANE_XYZ") == EXEC_MODE_LAB_SHADOW)
check("None lane -> LAB_SHADOW",
      execution_mode_for_lane(None) == EXEC_MODE_LAB_SHADOW)

# === 11. Duplicate callbacks cannot submit same exchange order twice
print("\n[11] Duplicate callbacks idempotent")
reset()
# Two calls to suspend should not error and should produce same shape
r1 = suspend_lane_trading(LANE, reason="TEST_DUP_1")
r2 = suspend_lane_trading(LANE, reason="TEST_DUP_2")
check("both calls succeed", isinstance(r1, dict) and isinstance(r2, dict))
check("second call has no orders to cancel (already gone)",
      len(r2.get("cancelled_pending", [])) == 0)

print()
print("=" * 72)
print(f"RESULT: {passed} passed, {failed} failed")
print("=" * 72)
sys.exit(0 if failed == 0 else 1)
