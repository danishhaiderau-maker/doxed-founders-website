"""Test the centralized execution_mode_for_lane resolver (Pt 1 of toggle contract).

This is the foundation for the entire toggle contract. If this resolver is
wrong, every downstream path is wrong. Hence its own test file.

Run: cd services/btc-conservative-agent && python test_execution_mode.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Boot minimal state required by the resolver without starting the bot.
os.environ.setdefault("FORCE_PAPER_MODE", "1")  # ensure Bitfinex stays OFF during tests
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot
# Unit tests must never persist their simulated live/toggle state into the
# operator's real config-7002.json.
bot.save_persistent_config = lambda: None
from bot import (
    execution_mode_for_lane,
    lane_can_place_new_entry,
    lane_is_live,
    lane_execution_block_reason,
    mark_lane_exit_only,
    clear_lane_exit_only,
    lane_is_exit_only,
    EXEC_MODE_LAB_SHADOW,
    EXEC_MODE_PAPER,
    EXEC_MODE_LIVE,
    EXEC_MODE_EXIT_ONLY,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
    RESEARCH_LANE_CONTINUOUS,
    RESEARCH_LANE_AI_SCAN,
)
import combo_pathway_config as cfg


def set_research_lane(lane, on):
    with bot.state_lock:
        m = dict(bot.state.get("research_lane_enabled") or {})
        m[lane] = bool(on)
        bot.state["research_lane_enabled"] = m


def set_bitfinex(on):
    with bot.state_lock:
        bot.state["bitfinex_live_enabled"] = bool(on)


def reset_state():
    """Reset to known-good baseline: TYPE_B ON, Bitfinex OFF."""
    set_research_lane(RESEARCH_LANE_TYPE_B_HUNTER_V1, True)
    set_bitfinex(False)
    # Clear any EXIT_ONLY markers
    for lane in list(bot._exit_only_until.keys()):
        clear_lane_exit_only(lane)


def check(name, got, expected):
    ok = got == expected
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}: expected={expected} got={got}")
    return ok


print("=" * 70)
print("execution_mode_for_lane resolver tests")
print("=" * 70)

passed = 0
failed = 0

# === Group 1: Tile OFF -> LAB_SHADOW ===
print("\n[Group 1] Tile OFF -> LAB_SHADOW")
reset_state()
set_research_lane(RESEARCH_LANE_TYPE_B_HUNTER_V1, False)
if check("Tile OFF mode", execution_mode_for_lane(RESEARCH_LANE_TYPE_B_HUNTER_V1), EXEC_MODE_LAB_SHADOW):
    passed += 1
else:
    failed += 1
if check("Tile OFF cannot place entry", lane_can_place_new_entry(RESEARCH_LANE_TYPE_B_HUNTER_V1), False):
    passed += 1
else:
    failed += 1
br = lane_execution_block_reason(RESEARCH_LANE_TYPE_B_HUNTER_V1)
if check("Tile OFF block reason", br, "TILE_OFF"):
    passed += 1
else:
    failed += 1

# === Group 2: Tile ON + Bitfinex OFF -> PAPER ===
print("\n[Group 2] Tile ON + Bitfinex OFF -> PAPER")
reset_state()
if check("Tile ON + BFX OFF mode", execution_mode_for_lane(RESEARCH_LANE_TYPE_B_HUNTER_V1), EXEC_MODE_PAPER):
    passed += 1
else:
    failed += 1
if check("Tile ON + BFX OFF can place entry", lane_can_place_new_entry(RESEARCH_LANE_TYPE_B_HUNTER_V1), True):
    passed += 1
else:
    failed += 1
if check("Tile ON + BFX OFF not live", lane_is_live(RESEARCH_LANE_TYPE_B_HUNTER_V1), False):
    passed += 1
else:
    failed += 1

# === Group 3: legacy source Bitfinex flags do not bypass platform relay ===
print("\n[Group 3] Tile ON + legacy source Bitfinex ON -> local PAPER")
reset_state()
set_bitfinex(True)
mode = execution_mode_for_lane(RESEARCH_LANE_TYPE_B_HUNTER_V1)
if check("Tile ON remains local PAPER", mode, EXEC_MODE_PAPER):
    passed += 1
else:
    failed += 1
if check("Tile ON + BFX ON can place entry", lane_can_place_new_entry(RESEARCH_LANE_TYPE_B_HUNTER_V1), True):
    passed += 1
else:
    failed += 1
if check(
    "legacy source flags cannot make lane direct-live",
    lane_is_live(RESEARCH_LANE_TYPE_B_HUNTER_V1),
    False,
):
    passed += 1
else:
    failed += 1
br = lane_execution_block_reason(RESEARCH_LANE_TYPE_B_HUNTER_V1)
if check("local paper lane has no local entry block", br, None):
    passed += 1
else:
    failed += 1
set_bitfinex(False)  # reset

# === Group 4: EXIT_ONLY precedence ===
print("\n[Group 4] EXIT_ONLY precedence")
reset_state()
# Tile is ON, but lane marked EXIT_ONLY (e.g. Bitfinex disarmed w/ open exposure)
mark_lane_exit_only(RESEARCH_LANE_TYPE_B_HUNTER_V1, reason="TEST")
if check("EXIT_ONLY overrides Tile ON", execution_mode_for_lane(RESEARCH_LANE_TYPE_B_HUNTER_V1), EXEC_MODE_EXIT_ONLY):
    passed += 1
else:
    failed += 1
if check("EXIT_ONLY cannot place entry", lane_can_place_new_entry(RESEARCH_LANE_TYPE_B_HUNTER_V1), False):
    passed += 1
else:
    failed += 1
br = lane_execution_block_reason(RESEARCH_LANE_TYPE_B_HUNTER_V1)
if check("EXIT_ONLY block reason", br, "EXIT_ONLY (bitfinex disarmed with open exposure)"):
    passed += 1
else:
    failed += 1
# Clearing restores PAPER
clear_lane_exit_only(RESEARCH_LANE_TYPE_B_HUNTER_V1)
if check("Clear EXIT_ONLY -> PAPER", execution_mode_for_lane(RESEARCH_LANE_TYPE_B_HUNTER_V1), EXEC_MODE_PAPER):
    passed += 1
else:
    failed += 1

# === Group 5: Retired / unknown lane -> LAB_SHADOW (fail closed) ===
print("\n[Group 5] Retired / unknown lanes -> LAB_SHADOW (fail-closed)")
reset_state()
if check("Retired lane mode", execution_mode_for_lane("SR_MICRO_TILE_V1"), EXEC_MODE_LAB_SHADOW):
    passed += 1
else:
    failed += 1
if check("Unknown lane mode", execution_mode_for_lane("BOGUS_LANE_XYZ"), EXEC_MODE_LAB_SHADOW):
    passed += 1
else:
    failed += 1
if check("None lane mode", execution_mode_for_lane(None), EXEC_MODE_LAB_SHADOW):
    passed += 1
else:
    failed += 1

# === Group 6: CONTINUOUS benchmark ===
print("\n[Group 6] CONTINUOUS benchmark respects its own toggle")
reset_state()
# CONTINUOUS is special -- it uses continuous_ai_research_enabled() not the
# per-lane map. With FORCE_PAPER_MODE on, it should be PAPER.
mode = execution_mode_for_lane(RESEARCH_LANE_CONTINUOUS)
if check("CONTINUOUS ON + BFX OFF mode", mode, EXEC_MODE_PAPER):
    passed += 1
else:
    failed += 1
if check("CONTINUOUS can place paper entry", lane_can_place_new_entry(RESEARCH_LANE_CONTINUOUS), True):
    passed += 1
else:
    failed += 1
if check("CONTINUOUS has no retired block", lane_execution_block_reason(RESEARCH_LANE_CONTINUOUS), None):
    passed += 1
else:
    failed += 1

# Prove the active benchmark reaches the paper pending-order lifecycle rather
# than only returning the right resolver label.
paper_trade_id = "cont-test-paper-route"
paper_signal = {
    "trade_id": paper_trade_id,
    "research_lane": RESEARCH_LANE_CONTINUOUS,
    "final_direction": "LONG",
    "direction": "LONG",
}
original_create_limit_order = bot.create_limit_order
try:
    def _record_test_limit(signal):
        order = {
            "trade_id": signal["trade_id"],
            "research_lane": signal["research_lane"],
            "status": "PENDING",
        }
        bot.pending_orders.append(order)
        return order

    bot.create_limit_order = _record_test_limit
    with bot.state_lock:
        bot.state["strategy_mode"] = "RESEARCH"
        bot.state["live_armed"] = False
        bot.state["bitfinex_live_enabled"] = False
        bot.state["pullback_threshold"] = 0.001
    routed = bot.execute_order(paper_signal, {"direction": "LONG", "win_prob": 0})
    if check("CONTINUOUS paper order reaches pending lifecycle", routed, True):
        passed += 1
    else:
        failed += 1
finally:
    bot.create_limit_order = original_create_limit_order
    bot.pending_orders[:] = [
        order for order in bot.pending_orders
        if order.get("trade_id") != paper_trade_id
    ]

# Prove the candidate tile reaches the real global pending-order table and
# preserves its lane ownership. This is the operator-visible contract: ON
# means the lane can create a global order; relay state is a separate gate.
type_b_trade_id = "tbhv1-test-global-pending"
type_b_signal = {
    "trade_id": type_b_trade_id,
    "research_lane": RESEARCH_LANE_TYPE_B_HUNTER_V1,
    "final_direction": "LONG",
    "direction": "LONG",
    "signal_price": 64000.0,
    "pullback_pct": 0.001,
}
original_maybe_bitfinex_limit_entry = bot._maybe_bitfinex_limit_entry
original_relay_mirror = bot._relay_mirror
readiness_missing = object()
readiness_state_keys = (
    "ws_transport_connected",
    "ws_ready",
    "ws_last_tick",
    "ohlcv_ready",
    "ema_status",
    "pathway_safety_block",
    "last_ready_ts",
    "execution_paused",
    "manual_admin_pause",
)
with bot.state_lock:
    original_readiness_state = {
        key: bot.state.get(key, readiness_missing)
        for key in readiness_state_keys
    }
original_last_ohlcv_fetch = bot.last_ohlcv_fetch
original_latest_candles = list(bot.latest_candles)
original_volume_buffer = list(bot.volume_buffer)
original_price_buffer = list(bot.price_buffer)
original_delta_buffer = list(bot.delta_buffer)
try:
    bot._maybe_bitfinex_limit_entry = lambda *args, **kwargs: None
    bot._relay_mirror = lambda *args, **kwargs: None
    ready_now = bot.time.time()
    with bot.state_lock:
        bot.state["strategy_mode"] = "RESEARCH"
        bot.state["live_armed"] = False
        bot.state["bitfinex_live_enabled"] = False
        bot.state["price"] = 64000.0
        bot.state["account_balance"] = 500.0
        bot.state["pullback_threshold"] = 0.001
        bot.state["allow_compression"] = True
        # Paper entries obey the same genuine-fresh-WS safety boundary as live
        # entries. Seed an explicit current-session tick for this positive
        # pending-order lifecycle fixture.
        bot.state["ws_transport_connected"] = True
        bot.state["ws_ready"] = True
        bot.state["ws_last_tick"] = ready_now
        bot.state["ohlcv_ready"] = True
        bot.state["ema_status"] = {
            "ema9": 64_000.0,
            "ema21": 64_000.0,
            "ema200": 64_000.0,
        }
        bot.state["pathway_safety_block"] = False
        bot.state["last_ready_ts"] = ready_now - bot.READY_STABLE_SEC - 1.0
        bot.state["execution_paused"] = False
        bot.state["manual_admin_pause"] = False
    bot.last_ohlcv_fetch = ready_now
    bot.latest_candles[:] = [
        [ready_now, 64_000.0, 64_010.0, 63_990.0, 64_000.0, 1.0]
        for _ in range(bot.MIN_CANDLES)
    ]
    bot.volume_buffer.clear()
    bot.volume_buffer.extend([1.0] * bot.WINDOW_SIZE)
    bot.price_buffer.clear()
    bot.price_buffer.extend([64_000.0] * bot.WINDOW_SIZE)
    bot.delta_buffer.clear()
    bot.delta_buffer.extend([0.0] * bot.WINDOW_SIZE)
    routed = bot.execute_order(
        type_b_signal,
        {"direction": "LONG", "decision": "APPROVE", "win_prob": 0},
    )
    matching = [
        order for order in bot.pending_orders
        if order.get("trade_id") == type_b_trade_id
    ]
    if check("Type B ON reaches global pending lifecycle", routed, True):
        passed += 1
    else:
        failed += 1
    if check("Type B global order is lane-owned", len(matching), 1):
        passed += 1
    else:
        failed += 1
    if check(
        "Type B global order keeps TYPE_B provenance",
        (matching[0].get("research_lane") if matching else None),
        RESEARCH_LANE_TYPE_B_HUNTER_V1,
    ):
        passed += 1
    else:
        failed += 1
finally:
    bot._maybe_bitfinex_limit_entry = original_maybe_bitfinex_limit_entry
    bot._relay_mirror = original_relay_mirror
    bot.last_ohlcv_fetch = original_last_ohlcv_fetch
    bot.latest_candles[:] = original_latest_candles
    bot.volume_buffer.clear()
    bot.volume_buffer.extend(original_volume_buffer)
    bot.price_buffer.clear()
    bot.price_buffer.extend(original_price_buffer)
    bot.delta_buffer.clear()
    bot.delta_buffer.extend(original_delta_buffer)
    with bot.state_lock:
        for key, value in original_readiness_state.items():
            if value is readiness_missing:
                bot.state.pop(key, None)
            else:
                bot.state[key] = value
    with bot.trade_lock:
        for order in list(bot.pending_orders):
            if order.get("trade_id") == type_b_trade_id:
                bot.lane_unregister_pending_order(order)

print()
print("=" * 70)
print(f"RESULT: {passed} passed, {failed} failed")
print("=" * 70)
sys.exit(0 if failed == 0 else 1)
