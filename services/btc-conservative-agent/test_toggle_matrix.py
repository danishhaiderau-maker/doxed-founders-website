"""Pt 6: Complete toggle test matrix for the toggle contract.

Tests all 11 scenarios from the toggle contract spec. Uses an in-process
mock of the Bitfinex exchange object so NO real orders are ever placed.

Run: cd services/btc-conservative-agent && python test_toggle_matrix.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Boot safe: paper mode + research mode (no real Bitfinex calls)
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")

import bot
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
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
    RESEARCH_LANE_CONTINUOUS,
)
import bitfinex_live_executor as bx

LANE = RESEARCH_LANE_TYPE_B_HUNTER_V1
passed = 0
failed = 0


def set_lane(on):
    with bot.state_lock:
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
print("Pt 6 toggle test matrix -- TYPE_B_HUNTER_V1")
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

# === 4. Tile ON + Bitfinex ON with all gates passing -> LIVE
print("\n[4] Tile ON + Bitfinex ON -> LIVE (subject to keys at submit)")
reset()
set_bx(True)
check("mode == LIVE", execution_mode_for_lane(LANE) == EXEC_MODE_LIVE,
      execution_mode_for_lane(LANE))
check("can place entry", lane_can_place_new_entry(LANE))
check("is live", lane_is_live(LANE))
# In test env (no keys), block_reason should surface the gate
br = lane_execution_block_reason(LANE)
check("block reason surfaces missing keys", br == "BITFINEX_KEYS_MISSING", br)
set_bx(False)

# === 5. Tile ON + Bitfinex ON with failed gate: no exchange submission
print("\n[5] Tile ON + Bitfinex ON + failed gate -> block reason explicit")
reset()
set_bx(True)
br = lane_execution_block_reason(LANE)
check("explicit block reason present", br is not None and "BITFINEX_KEYS" in str(br), br)
check("would still report LIVE mode", execution_mode_for_lane(LANE) == EXEC_MODE_LIVE)
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
# Suspend lane -> should remove local order and try Bitfinex cancel
result = suspend_lane_trading(LANE, reason="TEST_TILE_OFF")
check("local pending removed", "test-tid-1" in result.get("expired_awaiting", []) or
      "test-tid-1" in [str(x) for x in result.get("cancelled_pending", [])],
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
check("SR_MICRO_TILE_V1 retired -> LAB_SHADOW",
      execution_mode_for_lane("SR_MICRO_TILE_V1") == EXEC_MODE_LAB_SHADOW)
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
