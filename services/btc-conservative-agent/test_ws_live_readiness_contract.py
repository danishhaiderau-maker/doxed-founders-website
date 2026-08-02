"""Focused behavioral contract for WS/readiness and direct-live fail-closed safety."""

import ast
import copy
import json
import math
import os
import re
import sys
import threading
import time
import unittest
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


BOT_PATH = Path(__file__).with_name("bot.py")
SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(BOT_PATH))
FUNCTIONS = {
    node.name: node
    for node in TREE.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}


def function_source(name: str) -> str:
    return ast.get_source_segment(SOURCE, FUNCTIONS[name]) or ""


def compile_functions(names, namespace):
    module = ast.Module(body=[FUNCTIONS[name] for name in names], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace


def _module_level_float_default(var_name: str) -> float:
    """Read the literal default of a module-level
    `VAR = ...float(os.getenv(NAME, "<v>"))...` definition (handles both the
    direct `float(...)` form and the `max(..., float(...))` wrapper form)."""
    pattern = re.compile(
        rf'{re.escape(var_name)}\s*=\s.*?os\.getenv\([^)]+,\s*"([^"]+)"\)',
        re.DOTALL,
    )
    match = pattern.search(SOURCE)
    assert match, f"could not locate module-level definition of {var_name}"
    return float(match.group(1))


class WsLiveReadinessBehaviorTest(unittest.TestCase):
    def setUp(self):
        self.now = 10_000.0
        self.state = {
            "price": 64_000.0,
            "price_ts": self.now,
            "bid": 64_000.0,
            "ask": 64_020.0,
            "rest_price": 64_010.0,
            "rest_price_ts": self.now,
            "rest_last_tick": self.now,
            "bbo_ts": self.now,
            "ws_last_tick": self.now - 500.0,
            "ws_transport_connected": False,
            "ws_ready": False,
            "ohlcv_ready": True,
            "ema_status": {"ema9": 1.0, "ema21": 1.0, "ema200": 1.0},
            "pathway_safety_block": False,
            "last_ready_ts": 0.0,
            "execution_paused": False,
            "execution_reason": "",
            "_pause_priority": 0,
            "manual_admin_pause": False,
            "live_armed": False,
            "bitfinex_live_enabled": False,
            "exchange_sync_audit": {
                "checked_ts": self.now,
                "authoritative": True,
                "fresh": True,
                "flat": True,
                "orders_synced": True,
                "positions_synced": True,
                "trades_synced": True,
                "orphan_order_ids": [],
                "orphan_position_ids": [],
            },
        }
        self.saved = []
        self.pause_reasons = []

        def set_execution_paused(reason):
            self.pause_reasons.append(reason)
            self.state["execution_paused"] = True
            self.state["execution_reason"] = reason

        self.namespace = {
            "copy": copy,
            "math": math,
            "os": os,
            "time": time,
            "state": self.state,
            "state_lock": threading.RLock(),
            "WS_ENTRY_FRESH_SEC": 60.0,
            "REST_ENTRY_FRESH_SEC": 10.0,
            "STALE_HARD_SEC": 180.0,
            "CANDLE_STALE_SEC": 180.0,
            "OHLCV_FETCH_INTERVAL": 60.0,
            "READY_STABLE_SEC": 5.0,
            "MIN_CANDLES": 3,
            "WINDOW_SIZE": 3,
            "last_ohlcv_fetch": self.now,
            "latest_candles": [1, 2, 3],
            "volume_buffer": deque([1, 2, 3], maxlen=10),
            "price_buffer": deque([1, 2, 3], maxlen=10),
            "delta_buffer": deque([1, 2, 3], maxlen=10),
            "LIVE_EXPOSURE_AUDIT_MAX_AGE_SEC": 60.0,
            "_WS_RECOVERABLE_PAUSE_REASONS": frozenset(
                {"WS_STALE", "STALE_DATA_HARD_STOP", "PRICE_STALE_OR_MISSING"}
            ),
            "_private_api_keys_ok": lambda: True,
            "save_persistent_config": lambda: self.saved.append(True),
            "set_execution_paused": set_execution_paused,
            "fmt": lambda value: f"{float(value):.1f}",
            "logger": SimpleNamespace(
                debug=lambda *args, **kwargs: None,
                info=lambda *args, **kwargs: None,
                warning=lambda *args, **kwargs: None,
                error=lambda *args, **kwargs: None,
                critical=lambda *args, **kwargs: None,
            ),
        }
        compile_functions(
            (
                "_force_paper_mode_active",
                "_genuine_ws_transport_ready",
                "_market_data_health_snapshot",
                "_fresh_rest_entry_quote_ready",
                "_runtime_readiness_components",
                "_recompute_system_readiness",
                "can_progress_new_entry",
                "_exchange_exposure_audit_snapshot",
                "can_open_live_entry",
                "_strict_json_boolean",
                "_apply_env_live_gating",
                "_clear_execution_pause_if_reason",
                "validate_market_data",
                "_require_fly_runtime_for_direct_start",
                "system_health_check",
            ),
            self.namespace,
        )

    def _make_ws_ready(self):
        self.state.update(
            {
                "ws_transport_connected": True,
                "ws_ready": True,
                "ws_last_tick": self.now,
                "last_ready_ts": self.now - 10.0,
            }
        )

    def test_rest_price_cannot_impersonate_websocket_or_ready_system(self):
        runtime = self.namespace["_recompute_system_readiness"](self.now)
        self.assertFalse(runtime["ws_transport_ready"])
        self.assertFalse(runtime["prerequisites_ready"])
        self.assertFalse(runtime["system_ready"])
        self.assertFalse(self.state["system_ready"])
        self.assertTrue(self.state["allow_rest_price"])
        self.assertFalse(self.namespace["validate_market_data"]())

    def test_genuine_websocket_requires_stability_epoch(self):
        self.state.update(
            {
                "ws_transport_connected": True,
                "ws_ready": True,
                "ws_last_tick": self.now,
            }
        )
        first = self.namespace["_recompute_system_readiness"](self.now)
        self.assertTrue(first["ws_transport_ready"])
        self.assertFalse(first["system_ready"])
        stable = self.namespace["_recompute_system_readiness"](
            self.now + self.namespace["READY_STABLE_SEC"]
        )
        self.assertTrue(stable["system_ready"])
        self.assertTrue(stable["signal_generation_ready"])

    def test_ws_entry_freshness_is_stricter_than_old_hard_stale_window(self):
        self._make_ws_ready()
        self.state["ws_last_tick"] = self.now - 61.0
        runtime = self.namespace["_runtime_readiness_components"](self.now)
        self.assertFalse(runtime["ws_transport_ready"])
        self.assertIn("WS_NOT_READY", runtime["readiness_reasons"])

    def test_quiet_ws_tape_with_fresh_rest_does_not_hard_stop(self):
        self.namespace["time"] = SimpleNamespace(time=lambda: self.now)
        healthy = self.namespace["system_health_check"]()
        self.assertFalse(healthy)
        self.assertFalse(self.state["execution_paused"])
        self.assertEqual(self.pause_reasons, [])
        self.assertFalse(
            self.namespace["_runtime_readiness_components"](self.now)[
                "signal_generation_ready"
            ]
        )

    def test_all_market_data_stale_triggers_hard_stop(self):
        self.namespace["time"] = SimpleNamespace(time=lambda: self.now)
        stale_ts = self.now - 200.0
        self.state.update(
            {
                "price_ts": stale_ts,
                "rest_price_ts": stale_ts,
                "rest_last_tick": stale_ts,
                "bbo_ts": stale_ts,
            }
        )
        healthy = self.namespace["system_health_check"]()
        self.assertFalse(healthy)
        self.assertTrue(self.state["execution_paused"])
        self.assertEqual(self.state["execution_reason"], "STALE_DATA_HARD_STOP")
        self.assertEqual(self.pause_reasons, ["STALE_DATA_HARD_STOP"])

    def test_fresh_rest_clears_stale_hard_stop_but_not_entry_gate(self):
        self.namespace["time"] = SimpleNamespace(time=lambda: self.now)
        self.state["execution_paused"] = True
        self.state["execution_reason"] = "STALE_DATA_HARD_STOP"
        healthy = self.namespace["system_health_check"]()
        self.assertFalse(healthy)
        self.assertFalse(self.state["execution_paused"])
        self.assertEqual(self.state["execution_reason"], "")
        self.assertFalse(
            self.namespace["_runtime_readiness_components"](self.now)[
                "signal_generation_ready"
            ]
        )
        self.assertEqual(self.pause_reasons, [])

    def test_fresh_ws_cannot_enter_without_fresh_rest_bid_ask(self):
        self._make_ws_ready()
        self.state["rest_price_ts"] = self.now - 11.0
        self.state["rest_last_tick"] = self.now - 11.0
        self.state["bbo_ts"] = self.now - 11.0
        runtime = self.namespace["_runtime_readiness_components"](self.now)
        self.assertTrue(runtime["ws_transport_ready"])
        self.assertFalse(runtime["rest_entry_quote_ready"])
        self.assertFalse(runtime["prerequisites_ready"])
        self.assertIn("REST_ENTRY_QUOTE_NOT_READY", runtime["readiness_reasons"])

    def test_stale_ohlcv_blocks_new_entry_even_with_fresh_ws(self):
        self._make_ws_ready()
        self.namespace["last_ohlcv_fetch"] = self.now - 181.0
        allowed, reason, runtime = self.namespace["can_progress_new_entry"](self.now)
        self.assertFalse(allowed)
        self.assertEqual(reason, "OHLCV_NOT_READY")
        self.assertFalse(runtime["ohlcv_ready"])

    def test_any_execution_pause_freezes_pending_and_chase_progression(self):
        self._make_ws_ready()
        self.state["execution_paused"] = True
        self.state["execution_reason"] = "THREAD_CRASH"
        allowed, reason, _ = self.namespace["can_progress_new_entry"](self.now)
        self.assertFalse(allowed)
        self.assertEqual(reason, "EXECUTION_PAUSED")

    def test_live_guard_requires_fresh_flat_audit_and_explicit_arm(self):
        self._make_ws_ready()
        # This unit contract exercises the live arm/audit gates themselves;
        # isolate it from an operator's shell-level paper-mode setting.
        self.namespace["_force_paper_mode_active"] = lambda: False
        armable, reason, _ = self.namespace["can_open_live_entry"](
            require_armed=False, now=self.now
        )
        self.assertTrue(armable, reason)
        active, reason, _ = self.namespace["can_open_live_entry"](
            require_armed=True, now=self.now
        )
        self.assertFalse(active)
        self.assertEqual(reason, "LIVE_NOT_ARMED")
        self.state["live_armed"] = True
        self.state["bitfinex_live_enabled"] = True
        active, reason, _ = self.namespace["can_open_live_entry"](
            require_armed=True, now=self.now
        )
        self.assertTrue(active, reason)

        self.state["exchange_sync_audit"]["checked_ts"] = self.now - 61.0
        active, reason, _ = self.namespace["can_open_live_entry"](
            require_armed=True, now=self.now
        )
        self.assertFalse(active)
        self.assertEqual(reason, "EXCHANGE_AUDIT_NOT_FRESH")

        self.state["exchange_sync_audit"].update(
            {"checked_ts": self.now, "orphan_order_ids": ["foreign-1"]}
        )
        active, reason, _ = self.namespace["can_open_live_entry"](
            require_armed=True, now=self.now
        )
        self.assertFalse(active)
        self.assertEqual(reason, "EXCHANGE_ORPHAN_EXPOSURE")

        self.state["exchange_sync_audit"].update(
            {"orphan_order_ids": [], "flat": False}
        )
        armable, reason, _ = self.namespace["can_open_live_entry"](
            require_armed=False, now=self.now
        )
        self.assertFalse(armable)
        self.assertEqual(reason, "EXCHANGE_NOT_FLAT")

    def test_startup_discards_env_and_persisted_live_arm(self):
        with mock.patch.dict(
            os.environ,
            {"BITFINEX_LIVE_ENABLED": "true"},
            clear=False,
        ):
            os.environ.pop("FORCE_PAPER_MODE", None)
            self.state["live_armed"] = True
            self.state["bitfinex_live_enabled"] = True
            self.namespace["_apply_env_live_gating"]()
        self.assertFalse(self.state["live_armed"])
        self.assertFalse(self.state["bitfinex_live_enabled"])
        self.assertTrue(self.state["live_startup_requested"])
        self.assertTrue(self.saved)

    def test_json_boolean_is_strict(self):
        strict = self.namespace["_strict_json_boolean"]
        self.assertIs(strict({"enabled": True}, "enabled"), True)
        self.assertIs(strict({"enabled": False}, "enabled"), False)
        for value in ("true", "false", 1, 0, None, [], {}):
            self.assertIsNone(strict({"enabled": value}, "enabled"))
        self.assertIsNone(strict({}, "enabled"))

    def test_ws_recovery_cannot_clear_thread_crash_or_newer_pause(self):
        clear = self.namespace["_clear_execution_pause_if_reason"]
        self.state.update(
            {
                "execution_paused": True,
                "execution_reason": "THREAD_CRASH",
                "_pause_priority": 1,
            }
        )
        self.assertFalse(clear("THREAD_CRASH"))
        self.assertTrue(self.state["execution_paused"])

        self.state["execution_reason"] = "ADMIN_MANUAL"
        self.assertFalse(clear("WS_STALE"))
        self.assertEqual(self.state["execution_reason"], "ADMIN_MANUAL")

        self.state.update(
            {
                "manual_admin_pause": False,
                "execution_paused": True,
                "execution_reason": "WS_STALE",
            }
        )
        self.assertTrue(clear("WS_STALE"))
        self.assertFalse(self.state["execution_paused"])

    def test_direct_execution_requires_real_fly_runtime_markers(self):
        guard = self.namespace["_require_fly_runtime_for_direct_start"]
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as raised:
                guard()
            self.assertEqual(raised.exception.code, 78)
        with mock.patch.dict(
            os.environ,
            {
                "FLY_APP_NAME": "doxed-btc-bot",
                "FLY_MACHINE_ID": "machine-1",
                "FLY_REGION": "syd",
            },
            clear=True,
        ):
            guard()
        with mock.patch.dict(
            os.environ,
            {
                "FLY_APP_NAME": "wrong-app",
                "FLY_MACHINE_ID": "machine-1",
                "FLY_REGION": "syd",
            },
            clear=True,
        ):
            with self.assertRaises(SystemExit) as raised:
                guard()
            self.assertEqual(raised.exception.code, 78)


class ExchangeAuditBehaviorTest(unittest.TestCase):
    def _namespace(self, exchange):
        state = {}
        pending = []
        positions = []
        namespace = {
            "copy": copy,
            "time": time,
            "state": state,
            "state_lock": threading.RLock(),
            "trade_lock": threading.RLock(),
            "pending_orders": pending,
            "open_positions": positions,
            "bitfinex_private": exchange,
            "SYMBOL_CCXT": "BTC/USDT:USDT",
            "_direct_private_exchange_owner": lambda: True,
            "_exchange_call_with_retry": lambda fn, label=None: fn(),
            "_normalize_order_side_to_dir": lambda value: (
                "LONG"
                if str(value).upper() in ("BUY", "LONG")
                else "SHORT"
                if str(value).upper() in ("SELL", "SHORT")
                else "UNKNOWN"
            ),
        }
        compile_functions(
            (
                "_exchange_position_size",
                "_exchange_row_value",
                "_normalize_exchange_order_for_rebuild",
                "_normalize_exchange_position_for_rebuild",
                "_normalize_exchange_trade_for_rebuild",
                "_managed_exchange_identity_snapshot",
                "_refresh_bitfinex_exposure_audit",
            ),
            namespace,
        )
        return namespace, state, pending, positions

    def test_failed_private_read_is_never_authoritative_or_flat(self):
        class Exchange:
            def fetch_open_orders(self, _symbol):
                return []

            def fetch_positions(self, _symbols):
                raise RuntimeError("positions unavailable")

            def fetch_my_trades(self, _symbol, limit=50):
                return []

        ns, state, _, _ = self._namespace(Exchange())
        with mock.patch.dict(
            sys.modules,
            {"bitfinex_live_executor": SimpleNamespace(_STATE={"open_order_ids": []})},
        ):
            result = ns["_refresh_bitfinex_exposure_audit"]()
        self.assertFalse(result["authoritative"])
        self.assertFalse(result["flat"])
        self.assertNotIn("_rebuild_payload", result)
        self.assertFalse(state["exchange_sync_audit"]["authoritative"])

    def test_successful_strict_reads_return_ephemeral_payload_only(self):
        class Exchange:
            def fetch_open_orders(self, _symbol):
                return [
                    {
                        "id": "owned-1",
                        "clientOrderId": "cont-1",
                        "side": "buy",
                        "amount": 0.1,
                        "price": 64_000,
                    }
                ]

            def fetch_positions(self, _symbols):
                return []

            def fetch_my_trades(self, _symbol, limit=50):
                return []

        ns, state, pending, _ = self._namespace(Exchange())
        pending.append(
            {
                "trade_id": "cont-1",
                "bitfinex_order_id": "owned-1",
                "bitfinex_live_entry": True,
                "status": "PENDING",
                "side": "buy",
                "qty": 0.1,
            }
        )
        with mock.patch.dict(
            sys.modules,
            {"bitfinex_live_executor": SimpleNamespace(_STATE={"open_order_ids": ["owned-1"]})},
        ):
            result = ns["_refresh_bitfinex_exposure_audit"]()
        self.assertTrue(result["authoritative"])
        self.assertFalse(result["flat"])
        self.assertEqual(result["orphan_order_ids"], [])
        self.assertIn("_rebuild_payload", result)
        self.assertNotIn("_rebuild_payload", state["exchange_sync_audit"])
        self.assertEqual(
            result["_rebuild_payload"]["open_orders"][0]["client_order_id"],
            "cont-1",
        )


class LateFillAdoptionBehaviorTest(unittest.TestCase):
    def _namespace(self):
        state = {"regime": "RANGE", "orphan_position_ids": []}
        pending = []
        positions = []
        lane_pending = defaultdict(list)
        lane_positions = defaultdict(list)
        audit = []

        def lane_key(row):
            if isinstance(row, dict):
                return str(row.get("research_lane") or "CONTINUOUS").upper()
            return str(row or "CONTINUOUS").upper()

        def unregister(order):
            if order in pending:
                pending.remove(order)
            bucket = lane_pending[lane_key(order)]
            if order in bucket:
                bucket.remove(order)

        def register(pos):
            if pos not in positions:
                positions.append(pos)
            if pos not in lane_positions[lane_key(pos)]:
                lane_positions[lane_key(pos)].append(pos)

        namespace = {
            "copy": copy,
            "time": time,
            "state": state,
            "state_lock": threading.RLock(),
            "trade_lock": threading.RLock(),
            "pending_orders": pending,
            "open_positions": positions,
            "lane_pending_orders": lane_pending,
            "lane_open_positions": lane_positions,
            "trades_map": {},
            "RESEARCH_LANE_CONTINUOUS": "CONTINUOUS",
            "TRAIL_LADDER": [(12, 10)],
            "TP_TARGET_PCT": 10.0,
            "EXEC_MODE_LIVE": "LIVE",
            "_state_leverage": lambda: 20,
            "sl_price_pct": lambda leverage: 0.01,
            "compute_tp": lambda entry, direction, target, leverage: (
                entry * 1.01 if direction == "LONG" else entry * 0.99
            ),
            "get_exit_config_for_lane": lambda lane: {
                "trail_ladder": [(12, 10)]
            },
            "_normalize_order_side_to_dir": lambda value: (
                "LONG"
                if str(value).upper() in ("BUY", "LONG")
                else "SHORT"
                if str(value).upper() in ("SELL", "SHORT")
                else "UNKNOWN"
            ),
            "_normalize_lane_key": lane_key,
            "_ensure_lane_bucket": lane_key,
            "lane_unregister_pending_order": unregister,
            "lane_register_open_position": register,
            "pipeline_state_sync": lambda: None,
            "_reconcile_adopt_audit": lambda *args: audit.append(args),
            "logger": SimpleNamespace(
                warning=lambda *args, **kwargs: None,
                info=lambda *args, **kwargs: None,
            ),
        }
        compile_functions(
            (
                "_managed_exchange_identity_snapshot",
                "_adopted_open_position_view",
                "_adopt_position_from_rebuild",
            ),
            namespace,
        )
        return namespace, state, pending, positions, lane_pending, audit

    def test_disappeared_owned_limit_becomes_exit_only_manageable_position(self):
        ns, _, pending, positions, lane_pending, _ = self._namespace()
        order = {
            "trade_id": "cont-owned",
            "bitfinex_order_id": "oid-1",
            "bitfinex_live_entry": True,
            "status": "CANCEL_PENDING_LIVE",
            "signal_dir": "LONG",
            "side": "buy",
            "qty": 0.1,
            "research_lane": "CONTINUOUS",
        }
        pending.append(order)
        lane_pending["CONTINUOUS"].append(order)
        rebuilt = {
            "open_orders": [],
            "positions": [
                {
                    "id": "pos-1",
                    "side": "long",
                    "contracts": 0.1,
                    "entry": 63_900.0,
                    "leverage": 20,
                    "info": {},
                }
            ],
            "recent_trades": [],
        }
        with mock.patch.dict(
            sys.modules,
            {"bitfinex_live_executor": SimpleNamespace(_STATE={"open_order_ids": ["oid-1"]})},
        ):
            result = ns["_adopt_position_from_rebuild"](rebuilt)
        self.assertEqual(result["adopted"], 1)
        self.assertEqual(pending, [])
        self.assertEqual(order["status"], "FILLED_ON_EXCHANGE")
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["bitfinex_position_id"], "pos-1")
        self.assertEqual(positions[0]["bitfinex_order_id"], "oid-1")
        self.assertTrue(positions[0]["bitfinex_live_entry"])

    def test_foreign_position_is_surfaced_but_never_adopted(self):
        ns, state, _, positions, _, _ = self._namespace()
        rebuilt = {
            "open_orders": [],
            "positions": [
                {
                    "id": "foreign-pos",
                    "side": "long",
                    "contracts": 0.1,
                    "entry": 63_900.0,
                    "info": {},
                }
            ],
            "recent_trades": [],
        }
        with mock.patch.dict(
            sys.modules,
            {"bitfinex_live_executor": SimpleNamespace(_STATE={"open_order_ids": []})},
        ):
            result = ns["_adopt_position_from_rebuild"](rebuilt)
        self.assertEqual(result["adopted"], 0)
        self.assertEqual(result["orphaned"], 1)
        self.assertEqual(positions, [])
        self.assertIn("foreign-pos", state["orphan_position_ids"])


class ConfirmedCancelBehaviorTest(unittest.TestCase):
    def setUp(self):
        self.trade_lock = threading.RLock()
        self.pending = []
        self.lane_pending = defaultdict(list)
        self.expired = []
        self.signal_expired = []
        self.cancel_calls = []
        self.cancel_ok = False

        def lane_key(order):
            if isinstance(order, dict):
                return str(order.get("research_lane") or "CONTINUOUS").upper()
            return str(order or "CONTINUOUS").upper()

        def unregister(order):
            with self.trade_lock:
                if order in self.pending:
                    self.pending.remove(order)
                bucket = self.lane_pending[lane_key(order)]
                if order in bucket:
                    bucket.remove(order)

        def cancel(order):
            # The private exchange request must never inherit trade_lock from
            # the caller or from the helper's local reservation section.
            self.assertFalse(self.trade_lock._is_owned())
            self.cancel_calls.append(str(order.get("bitfinex_order_id") or ""))
            return self.cancel_ok

        def record(order, reason):
            self.assertFalse(order.get("bitfinex_order_id"))
            row = {"trade_id": order.get("trade_id"), "reason": reason}
            self.expired.append(row)
            return row

        def expire(order, reason):
            self.signal_expired.append((order.get("trade_id"), reason))
            return True

        self.namespace = {
            "time": time,
            "trade_lock": self.trade_lock,
            "pending_orders": self.pending,
            "lane_pending_orders": self.lane_pending,
            "open_positions": [],
            "trades_map": {},
            "_ensure_lane_bucket": lane_key,
            "_normalize_lane_key": lane_key,
            "lane_unregister_pending_order": unregister,
            "_maybe_bitfinex_cancel": cancel,
            "_record_expired_order": record,
            "expire_signal_for_order": expire,
            "_emit_genome_execution_event": lambda *args, **kwargs: None,
            "CIRCUIT_BREAKER_CANCEL_REASONS": frozenset({"ADMIN_MANUAL"}),
            "LIMIT_ORDER_MAX_AGE_SEC": 30.0,
            "_agent_dbg": lambda *args, **kwargs: None,
            "pipeline_state_sync": lambda: None,
            "logger": SimpleNamespace(
                debug=lambda *args, **kwargs: None,
                info=lambda *args, **kwargs: None,
                warning=lambda *args, **kwargs: None,
                error=lambda *args, **kwargs: None,
                critical=lambda *args, **kwargs: None,
            ),
        }
        compile_functions(
            (
                "_cancel_pending_order_confirmed",
                "circuit_breaker_cancel_pending",
                "cleanup_expired_orders",
                "_cancel_bitfinex_orders_for_lane",
                "_cancel_managed_live_pending_entries",
                "_cancel_pending_for_chase_gate",
            ),
            self.namespace,
        )

    def _live_order(self, trade_id="cont-live"):
        order = {
            "trade_id": trade_id,
            "research_lane": "CONTINUOUS",
            "status": "PENDING",
            "bitfinex_order_id": f"oid-{trade_id}",
            "created_ts": time.time() - 120.0,
            "entry_expires_ts": time.time() - 60.0,
        }
        self.pending.append(order)
        self.lane_pending["CONTINUOUS"].append(order)
        return order

    def test_failed_circuit_cancel_stays_tracked_and_count_is_zero(self):
        order = self._live_order()
        self.cancel_ok = False
        cancelled = self.namespace["circuit_breaker_cancel_pending"]("ADMIN_MANUAL")
        self.assertEqual(cancelled, 0)
        self.assertEqual(self.cancel_calls, ["oid-cont-live"])
        self.assertIn(order, self.pending)
        self.assertIn(order, self.lane_pending["CONTINUOUS"])
        self.assertEqual(order["status"], "CANCEL_PENDING_LIVE")
        self.assertEqual(order["bitfinex_order_id"], "oid-cont-live")
        self.assertEqual(self.expired, [])
        self.assertEqual(self.signal_expired, [])

    def test_confirmed_circuit_cancel_finalizes_once_and_count_is_accurate(self):
        order = self._live_order()
        self.cancel_ok = True
        cancelled = self.namespace["circuit_breaker_cancel_pending"]("ADMIN_MANUAL")
        self.assertEqual(cancelled, 1)
        self.assertNotIn(order, self.pending)
        self.assertNotIn(order, self.lane_pending["CONTINUOUS"])
        self.assertEqual(order["status"], "CANCELLED")
        self.assertIsNone(order["bitfinex_order_id"])
        self.assertEqual(len(self.expired), 1)
        self.assertEqual(len(self.signal_expired), 1)
        self.assertEqual(
            self.namespace["circuit_breaker_cancel_pending"]("ADMIN_MANUAL"),
            0,
        )
        self.assertEqual(len(self.expired), 1)

    def test_expiry_does_not_count_or_finalize_unconfirmed_live_cancel(self):
        order = self._live_order("expiry-live")
        self.cancel_ok = False
        expired = self.namespace["cleanup_expired_orders"]()
        self.assertEqual(expired, 0)
        self.assertIn(order, self.pending)
        self.assertEqual(order["status"], "CANCEL_PENDING_LIVE")
        self.assertEqual(order["bitfinex_order_id"], "oid-expiry-live")
        self.assertEqual(self.expired, [])

    def test_outer_trade_lock_defers_private_io_and_retains_handle(self):
        order = self._live_order("outer-lock")
        self.cancel_ok = True
        with self.trade_lock:
            result = self.namespace["_cancel_pending_order_confirmed"](
                order,
                "TEST_OUTER_LOCK",
            )
        self.assertFalse(result["finalized"])
        self.assertEqual(result["failure_reason"], "TRADE_LOCK_HELD")
        self.assertEqual(self.cancel_calls, [])
        self.assertIn(order, self.pending)
        self.assertEqual(order["status"], "CANCEL_PENDING_LIVE")
        self.assertEqual(order["bitfinex_order_id"], "oid-outer-lock")

    def test_lane_disarm_and_chase_report_unconfirmed_cancel_as_failure(self):
        order = self._live_order("path-live")
        self.cancel_ok = False

        lane = self.namespace["_cancel_bitfinex_orders_for_lane"](
            "CONTINUOUS",
            "LANE_TOGGLE_OFF",
        )
        self.assertEqual(lane["cancelled"], [])
        self.assertEqual(len(lane["failed"]), 1)
        self.assertIn(order, self.pending)

        disarm = self.namespace["_cancel_managed_live_pending_entries"](
            "TEST_DISARM"
        )
        self.assertEqual(disarm["cancelled"], [])
        self.assertEqual(len(disarm["failed"]), 1)
        self.assertIn(order, self.pending)

        self.assertFalse(
            self.namespace["_cancel_pending_for_chase_gate"](
                order,
                "CHASE_BUCKET_3",
            )
        )
        self.assertIn(order, self.pending)
        self.assertEqual(order["status"], "CANCEL_PENDING_LIVE")
        self.assertEqual(order["bitfinex_order_id"], "oid-path-live")

        self.cancel_ok = True
        lane = self.namespace["_cancel_bitfinex_orders_for_lane"](
            "CONTINUOUS",
            "LANE_TOGGLE_OFF",
        )
        self.assertEqual(len(lane["cancelled"]), 1)
        self.assertEqual(lane["failed"], [])
        self.assertNotIn(order, self.pending)


class WsLiveReadinessSourceContractTest(unittest.TestCase):
    def test_only_ws_trade_handler_writes_ws_tick_and_ready_true(self):
        tick_writers = set()
        ready_true_writers = set()
        for fn_name, fn in FUNCTIONS.items():
            for node in ast.walk(fn):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if not isinstance(target, ast.Subscript):
                        continue
                    key = target.slice
                    if isinstance(key, ast.Constant) and key.value == "ws_last_tick":
                        tick_writers.add(fn_name)
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "ws_ready"
                        and isinstance(node.value, ast.Constant)
                        and node.value.value is True
                    ):
                        ready_true_writers.add(fn_name)
        self.assertEqual(tick_writers, {"_process_ws_trade_tick"})
        self.assertEqual(ready_true_writers, {"_process_ws_trade_tick"})
        self.assertNotIn("update_price", FUNCTIONS)

    def test_watchdog_and_validation_never_use_rest_as_ws(self):
        watchdog = function_source("ws_watchdog")
        validator = function_source("validate_market_data")
        monitor = function_source("state_monitor_loop")
        self.assertNotIn("price_ts", watchdog)
        self.assertNotIn("rest_last_tick", watchdog)
        self.assertIn("_close_ws_app()", watchdog)
        self.assertIn("_recompute_system_readiness", validator)
        self.assertNotIn("price_ts", validator)
        self.assertNotIn("max(price_ts, ws_tick)", monitor)

    def test_websocket_lifecycle_is_session_bound_and_torn_down(self):
        starter = function_source("start_websocket")
        opened = function_source("on_open")
        for name in ("on_message", "on_open", "on_error", "on_close"):
            self.assertIn("_is_current_ws_app", function_source(name), name)
        self.assertIn("finally:", starter)
        self.assertIn("if ws_app is app:", starter)
        self.assertIn("_mark_ws_transport_disconnected()", starter)
        self.assertIn('"ws_ready"] = False', opened)
        self.assertIn("_seed_ws_trade_buffers(trades)", function_source("safe_ws_handler"))
        self.assertIn("if snapshot_seed:", function_source("_process_ws_trade_tick"))
        self.assertIn("_seed_ws_trade_buffers([trade])", function_source("_process_ws_trade_tick"))
    def test_snapshot_warms_features_without_authorizing_ws_or_execution(self):
        state = {
            "ws_ready": False,
            "ws_last_tick": None,
            "ws_transport_connected": True,
        }
        price_buffer = deque(maxlen=200)
        volume_buffer = deque(maxlen=200)
        delta_buffer = deque(maxlen=200)
        delta_change_buffer = deque(maxlen=200)
        imbalance_buffer = deque(maxlen=200)
        velocity_buffer = deque(maxlen=200)
        orderflow = {"delta": 0.0, "prev_delta": 0.0, "imbalance": 0.0}
        feature_refreshes = []

        def update_orderflow(trade):
            orderflow["prev_delta"] = orderflow["delta"]
            orderflow["delta"] += float(trade["v"])
            orderflow["imbalance"] = orderflow["delta"] / 100.0

        namespace = {
            "WINDOW_SIZE": 10,
            "state": state,
            "price_buffer": price_buffer,
            "volume_buffer": volume_buffer,
            "delta_buffer": delta_buffer,
            "delta_change_buffer": delta_change_buffer,
            "imbalance_buffer": imbalance_buffer,
            "velocity_buffer": velocity_buffer,
            "orderflow": orderflow,
            "update_orderflow": update_orderflow,
            "update_feature_snapshot": lambda: feature_refreshes.append(True),
            "_ws_trade_timestamp_sec": lambda trade: float(trade["T"]),
            "logger": SimpleNamespace(info=lambda *a, **k: None),
        }
        compile_functions(("_seed_ws_trade_buffers",), namespace)
        trades = [
            {"T": 1_000 + index, "p": 63_000 + index, "v": 0.01, "S": 1}
            for index in range(12)
        ]

        seeded = namespace["_seed_ws_trade_buffers"](list(reversed(trades)))

        self.assertEqual(seeded, 12)
        self.assertEqual(len(price_buffer), 12)
        self.assertEqual(len(volume_buffer), 12)
        self.assertEqual(len(delta_buffer), 12)
        self.assertEqual(price_buffer[0], 63_000)
        self.assertEqual(price_buffer[-1], 63_011)
        self.assertEqual(feature_refreshes, [True])
        self.assertFalse(state["ws_ready"])
        self.assertIsNone(state["ws_last_tick"])

    def test_feature_builder_treats_pre_tick_price_as_zero_not_an_exception(self):
        builder = function_source("build_full_feature_snapshot")
        self.assertIn('price = nz(state.get("price"), 0.0)', builder)
        self.assertLess(
            builder.index('price = nz(state.get("price"), 0.0)'),
            builder.index("if price <= 0:"),
        )

    def test_all_new_entry_progression_uses_central_guard(self):
        for name in (
            "process_awaiting_dashboard_virtual_chase_entries",
            "_submit_tile2_paper_resting_limit",
            "_place_simulated_limit_order",
            "_apply_limit_chase",
            "process_limit_chase",
            "process_pending_orders",
            "execute_market_order",
            "create_limit_order",
            "process_signal",
            "periodic_pipeline_loop",
        ):
            self.assertIn("can_progress_new_entry", function_source(name), name)
        for name in (
            "_maybe_bitfinex_limit_entry_locked",
            "_maybe_bitfinex_market_entry_locked",
        ):
            self.assertIn("can_open_live_entry", function_source(name), name)

    def test_direct_live_limits_never_simulate_fill_or_local_only_chase(self):
        pending = function_source("process_pending_orders")
        live_check = pending.index('order.get("bitfinex_order_id")')
        touch = pending.index("if not _pending_limit_ready_for_fill")
        self.assertLess(live_check, touch)
        chase = function_source("_apply_limit_chase")
        self.assertIn('order.get("bitfinex_order_id")', chase)
        self.assertLess(
            chase.index('order.get("bitfinex_order_id")'),
            chase.index('order["limit_price"] = new_limit'),
        )

    def test_private_submit_precedes_local_exposure_registration(self):
        market = function_source("execute_market_order")
        self.assertLess(
            market.index("_maybe_bitfinex_market_entry"),
            market.index("_build_open_position"),
        )
        self.assertIn("no local position opened", market)
        limit = function_source("create_limit_order")
        self.assertLess(
            limit.index("_maybe_bitfinex_limit_entry"),
            limit.index("lane_register_pending_order"),
        )
        self.assertIn("no local pending order", limit)

    def test_all_pending_cancel_paths_use_confirmed_finalize_helper(self):
        for name in (
            "circuit_breaker_cancel_pending",
            "_cancel_pending_for_chase_gate",
            "_pull_order_to_virtual_chase",
            "_cancel_bitfinex_orders_for_lane",
            "cleanup_expired_orders",
            "_cancel_managed_live_pending_entries",
        ):
            source = function_source(name)
            self.assertIn("_cancel_pending_order_confirmed", source, name)
            self.assertNotIn("_maybe_bitfinex_cancel(", source, name)
        recorder = function_source("_record_expired_order")
        self.assertNotIn("_maybe_bitfinex_cancel(", recorder)
        purge = function_source("purge_dead_pending_orders")
        self.assertIn('"CANCEL_PENDING_LIVE"', purge)

    def test_private_close_precedes_local_close_and_failure_disarms(self):
        close = function_source("close_position")
        self.assertLess(
            close.index("should_skip_unprofitable_profit_exit"),
            close.index("_maybe_bitfinex_close"),
        )
        self.assertLess(
            close.index("_maybe_bitfinex_close"),
            close.index('pos["status"] = "CLOSED"'),
        )
        self.assertIn('_disarm_live_control("BITFINEX_CLOSE_FAILED")', close)
        priorities = SOURCE[
            SOURCE.index("PAUSE_PRIORITIES = {"):
            SOURCE.index("def get_edge_threshold", SOURCE.index("PAUSE_PRIORITIES = {"))
        ]
        self.assertIn('"BITFINEX_CLOSE_FAILED": 190', priorities)

    def test_strict_audit_payload_drives_startup_and_reconcile(self):
        startup = function_source("main")
        reconcile = function_source("bitfinex_live_reconcile_loop")
        self.assertNotIn("rebuild_state_from_exchange", startup)
        self.assertIn('startup_audit.pop("_rebuild_payload"', startup)
        self.assertIn('strict_result.pop("_rebuild_payload"', reconcile)
        self.assertIn("_adopt_position_from_rebuild", reconcile)
        self.assertIn("_cancel_managed_live_pending_entries", reconcile)
        refresh = function_source("_refresh_bitfinex_exposure_audit")
        self.assertIn('result["_rebuild_payload"] = rebuild_payload', refresh)
        self.assertNotIn('state["_rebuild_payload"]', refresh)

    def test_live_flags_are_transient_and_endpoints_use_one_arm_helper(self):
        persistence = function_source("_persistent_config_keys")
        self.assertNotIn('"live_armed"', persistence)
        self.assertNotIn('"bitfinex_live_enabled"', persistence)
        reset = function_source("reset_transient_runtime_state")
        self.assertIn('"live_armed": False', reset)
        self.assertIn('"bitfinex_live_enabled": False', reset)
        self.assertIn("can_open_live_entry", function_source("_arm_live_control"))
        for name in ("live_arm", "api_bitfinex_live"):
            endpoint = function_source(name)
            self.assertIn("_strict_json_boolean", endpoint)
            self.assertIn("_arm_live_control", endpoint)

    def test_force_paper_blocks_every_direct_private_exchange_path(self):
        owner = function_source("_direct_private_exchange_owner")
        self.assertIn("_force_paper_mode_active", owner)
        for name in (
            "_refresh_bitfinex_exposure_audit",
            "_maybe_bitfinex_close_locked",
            "_maybe_bitfinex_cancel_locked",
        ):
            self.assertIn("_direct_private_exchange_owner", function_source(name), name)

    def test_health_and_ready_layers_are_truthful(self):
        for name in ("health", "ready"):
            route = function_source(name)
            self.assertIn('"signal_generation_ready"', route)
            self.assertIn('"live_entry_armable"', route)
            self.assertIn('"trading_ready"', route)
            self.assertIn('"trading_block_reason"', route)
        ready = function_source("ready")
        self.assertIn("ready_ok", ready)
        self.assertIn('runtime["system_ready"]', ready)
        self.assertNotIn('ready_ok = bool(process_ready and runtime["signal_generation_ready"])', ready)
        self.assertIn("(200 if ready_ok else 503)", ready)
        resume = function_source("api_resume")
        self.assertIn("_recompute_system_readiness", resume)
        self.assertIn("resume_blocked", resume)
        self.assertIn("response.status_code = 409", resume)
        integrity = function_source("build_state_integrity")
        self.assertNotIn('"orders_synced": True', integrity)
        self.assertNotIn('"positions_synced": True', integrity)
        self.assertIn("_exchange_exposure_audit_snapshot", integrity)
        for name in ("api_analyzer_summary", "api_analyzer_genome"):
            route = function_source(name)
            self.assertLess(
                route.index("FLY_APP_NAME"),
                route.index("_analyzer_proxy_fetch"),
                name,
            )

    def test_position_hard_stop_keeps_exit_management_running(self):
        manager = function_source("position_manager")
        halted = manager.split("if is_engine_halted():", 1)[1].split(
            "time.sleep(5)", 1
        )[0]
        self.assertIn("process_positions()", halted)
        self.assertNotIn("process_pending_orders()", halted)

    def test_direct_module_entry_calls_fly_guard_before_main(self):
        main_guard = SOURCE.rsplit('if __name__ == "__main__":', 1)[1]
        self.assertLess(
            main_guard.index("_require_fly_runtime_for_direct_start()"),
            main_guard.index("main()"),
        )
        guard = function_source("_require_fly_runtime_for_direct_start")
        self.assertIn("FLY_APP_NAME", guard)
        self.assertIn("FLY_MACHINE_ID", guard)
        self.assertIn("FLY_REGION", guard)
        self.assertIn('fly_app == "doxed-btc-bot"', guard)
        self.assertIn("SystemExit(78)", guard)


class WsHeartbeatTransportLivenessTest(unittest.TestCase):
    """Bitfinex `[chanId, "hb"]` heartbeats keep the transport alive without
    authorizing entries. Regression guard for the sin-region reconnect loop."""

    def setUp(self):
        self.now = 10_000.0
        self.trades_seen = []
        self.diag_calls = []
        self.state = {
            "ws_last_tick": None,
            "ws_last_hb_ts": None,
            "ws_transport_connected": False,
            "ws_ready": False,
            "ws_connected_ts": None,
            "data_source": None,
            "diag": {},
        }
        self.namespace = {
            "json": json,
            "time": time,
            "state": self.state,
            "state_lock": threading.RLock(),
            "last_ws_message_time": 0.0,
            # Trade-handling path must never run for an hb-only message. The
            # handler returns before reaching _bitfinex_ws_trades_from_message
            # for an hb frame, so these helpers should never be called.
            "_bitfinex_ws_trades_from_message": lambda data: self.trades_seen.append(data) or [],
            "_process_ws_trade_tick": lambda *a, **k: self.trades_seen.append((a, k)),
            "_ws_trade_timestamp_sec": lambda t: 0.0,
            "_agent_dbg": lambda *a, **k: self.diag_calls.append((a, k)),
            "_mark_ws_transport_disconnected": lambda *a, **k: None,
            "_close_ws_app": lambda *a, **k: None,
            "logger": SimpleNamespace(
                debug=lambda *a, **k: None,
                info=lambda *a, **k: None,
                warning=lambda *a, **k: None,
                error=lambda *a, **k: None,
                critical=lambda *a, **k: None,
                exception=lambda *a, **k: None,
            ),
        }
        compile_functions(("safe_ws_handler",), self.namespace)

    def _send_hb(self, chan_id: int = 7):
        # Bitfinex on-wire frame for an idle trades channel heartbeat.
        self.namespace["safe_ws_handler"](json.dumps([chan_id, "hb"]))

    def test_ws_heartbeat_keeps_transport_alive_without_authorizing_entry(self):
        self.state["ws_last_tick"] = None
        self.state["ws_ready"] = False
        before = time.time()
        self._send_hb(chan_id=7)
        # hb stamp recorded.
        self.assertIsNotNone(self.state["ws_last_hb_ts"])
        self.assertGreaterEqual(self.state["ws_last_hb_ts"], before)
        # Transport stays connected.
        self.assertTrue(self.state["ws_transport_connected"])
        # Entry authorization state is NOT touched by a heartbeat.
        self.assertIsNone(self.state["ws_last_tick"])
        self.assertFalse(self.state["ws_ready"])
        # Trade path never invoked.
        self.assertEqual(self.trades_seen, [])
        # Diagnostic tag still emitted for observability.
        self.assertTrue(self.diag_calls)

    def test_ws_pong_keeps_transport_alive_without_authorizing_entry(self):
        self.state["ws_last_tick"] = None
        self.state["ws_ready"] = False
        before = time.time()
        self.namespace["safe_ws_handler"](json.dumps({"event": "pong", "cid": 1}))
        self.assertGreaterEqual(self.state["ws_last_hb_ts"], before)
        self.assertTrue(self.state["ws_transport_connected"])
        self.assertIsNone(self.state["ws_last_tick"])
        self.assertFalse(self.state["ws_ready"])
        self.assertEqual(self.trades_seen, [])

    def test_watchdog_accepts_recent_heartbeat_as_liveness(self):
        """ws_watchdog must NOT trip a reconnect when only a heartbeat is
        recent (no recent trade tick). This is the direct sin-region fix."""
        ns, calls = _build_watchdog_namespace(self_now=10_000.0)
        # Transport up, recent heartbeat, NO trade tick at all.
        ns["state"].update(
            {
                "ws_transport_connected": True,
                "ws_last_hb_ts": 10_000.0 - 5.0,  # 5s ago — well within 30s
                "ws_last_tick": None,
                "ws_ready": False,
            }
        )
        # Replace real side effects with spies.
        ns["_close_ws_app"] = lambda *a, **k: calls.append("close")
        ns["refresh_dashboard_market_snapshot"] = lambda *a, **k: calls.append("refresh")
        ns["_recompute_system_readiness"] = lambda now: {"system_ready": False}
        ns["_clear_execution_pause_if_reason"] = lambda reason: calls.append(("clear", reason))

        # Run one iteration of the watchdog with a fake clock.
        ran = _run_watchdog_once(ns, now=10_000.0)
        self.assertTrue(ran)
        # No reconnect was triggered.
        self.assertNotIn("close", calls)
        # And no stale-nudge refresh was triggered.
        self.assertNotIn("refresh", calls)

    def test_watchdog_allows_first_heartbeat_window_after_subscribe(self):
        """A fresh subscription must survive long enough to receive the
        first ~15s Bitfinex heartbeat when no trade tick arrives immediately."""
        ns, calls = _build_watchdog_namespace(self_now=10_000.0)
        ns["state"].update(
            {
                "ws_transport_connected": True,
                "ws_connected_ts": 10_000.0 - 9.0,
                "ws_last_hb_ts": None,
                "ws_last_tick": None,
                "ws_ready": False,
            }
        )
        ns["_close_ws_app"] = lambda *a, **k: calls.append("close")
        ns["refresh_dashboard_market_snapshot"] = lambda *a, **k: calls.append("refresh")

        self.assertTrue(_run_watchdog_once(ns, now=10_000.0))
        self.assertNotIn("close", calls)
        self.assertNotIn("refresh", calls)

    def test_watchdog_reconnects_if_first_frame_never_arrives_after_grace(self):
        ns, calls = _build_watchdog_namespace(self_now=10_000.0)
        ns["state"].update(
            {
                "ws_transport_connected": True,
                "ws_connected_ts": 10_000.0 - 31.0,
                "ws_last_hb_ts": None,
                "ws_last_tick": None,
                "ws_ready": False,
            }
        )
        ns["_close_ws_app"] = lambda *a, **k: calls.append("close")
        ns["refresh_dashboard_market_snapshot"] = lambda *a, **k: calls.append("refresh")

        self.assertTrue(_run_watchdog_once(ns, now=10_000.0))
        self.assertIn("close", calls)

    def test_watchdog_triggers_reconnect_when_both_tick_and_hb_stale(self):
        """Safety check: heartbeat tolerance does not silence genuinely dead
        transports. If both tick and hb are older than the threshold, the
        watchdog must still nudge a reconnect."""
        ns, calls = _build_watchdog_namespace(self_now=10_000.0)
        ns["state"].update(
            {
                "ws_transport_connected": True,
                "ws_last_hb_ts": 10_000.0 - 120.0,  # 120s ago — stale
                "ws_last_tick": 10_000.0 - 120.0,  # also stale
                "ws_ready": True,
            }
        )
        ns["_close_ws_app"] = lambda *a, **k: calls.append("close")
        ns["refresh_dashboard_market_snapshot"] = lambda *a, **k: calls.append("refresh")
        ns["_recompute_system_readiness"] = lambda now: {"system_ready": False}
        ns["_clear_execution_pause_if_reason"] = lambda reason: calls.append(("clear", reason))

        ran = _run_watchdog_once(ns, now=10_000.0)
        self.assertTrue(ran)
        self.assertIn("close", calls)

    def test_watchdog_recycles_heartbeat_only_partial_trade_stream(self):
        """A fresh heartbeat must not mask a trades subscription that has
        delivered no trade tick for the bounded 90-second recovery window."""
        ns, calls = _build_watchdog_namespace(self_now=10_000.0)
        ns["state"].update(
            {
                "ws_transport_connected": True,
                "ws_connected_ts": 10_000.0 - 300.0,
                "ws_last_hb_ts": 10_000.0 - 5.0,
                "ws_last_tick": 10_000.0 - 91.0,
                "ws_ready": True,
            }
        )
        ns["_close_ws_app"] = lambda *a, **k: calls.append("close")
        ns["refresh_dashboard_market_snapshot"] = lambda *a, **k: calls.append("refresh")

        self.assertTrue(_run_watchdog_once(ns, now=10_000.0))
        self.assertIn("close", calls)
        self.assertIn("refresh", calls)

    def test_watchdog_threshold_is_30s(self):
        """WATCHDOG_WS_STALE_SEC default must be 30 (2x Bitfinex's 15s hb
        cadence), not the old 15s that caused the flap."""
        threshold = _module_level_float_default("WATCHDOG_WS_STALE_SEC")
        self.assertEqual(threshold, 30.0)

    def test_entry_fresh_sec_is_60s_with_separate_fresh_rest_gate(self):
        """The quiet-tape tolerance is 60s, while submit-time REST BBO has
        its own stricter freshness requirement."""
        fresh = _module_level_float_default("WS_ENTRY_FRESH_SEC")
        self.assertEqual(fresh, 60.0)
        self.assertEqual(
            _module_level_float_default("WATCHDOG_WS_TRADE_STALE_SEC"),
            90.0,
        )
        self.assertEqual(_module_level_float_default("REST_ENTRY_FRESH_SEC"), 10.0)

    def test_reconnect_resets_heartbeat_timestamp(self):
        """Both _mark_ws_transport_disconnected AND on_open must reset
        ws_last_hb_ts so a stale hb from a prior session can never be
        mistaken for fresh transport liveness."""
        for fn_name in ("_mark_ws_transport_disconnected", "on_open"):
            self.assertIn(fn_name, FUNCTIONS, fn_name)
            src = function_source(fn_name)
            self.assertIn('"ws_last_hb_ts"] = None', src, fn_name)

    def test_sin_region_quiet_book_does_not_trip_reconnect(self):
        """Regression for the 9-hour Fly sin flap: 25s of ONLY heartbeats (no
        trade ticks) must keep the transport alive and avoid WS_NOT_READY.
        The old 15s watchdog would have killed this connection."""
        ns, calls = _build_watchdog_namespace(self_now=10_000.0)
        ns["state"].update(
            {
                "ws_transport_connected": True,
                "ws_last_tick": None,
                "ws_ready": False,
            }
        )
        ns["_close_ws_app"] = lambda *a, **k: calls.append("close")
        ns["refresh_dashboard_market_snapshot"] = lambda *a, **k: calls.append("refresh")
        ns["_recompute_system_readiness"] = lambda now: {"system_ready": False}
        ns["_clear_execution_pause_if_reason"] = lambda reason: calls.append(("clear", reason))

        # Simulate Bitfinex's ~15s heartbeat cadence across 25s of quiet book.
        for elapsed in (0, 15, 25):
            ns["state"]["ws_last_hb_ts"] = 10_000.0 - (25 - elapsed)
            ok = _run_watchdog_once(ns, now=10_000.0 + elapsed)
            self.assertTrue(ok, f"watchdog iteration at t={elapsed}")
        # No reconnect should have been triggered.
        self.assertNotIn("close", calls)
        self.assertNotIn("refresh", calls)
        # And transport should still be considered alive by the source-level
        # readiness recomputation — system_ready stays gated on ws_ready /
        # ws_last_tick freshness, which only real ticks satisfy.
        self.assertTrue(ns["state"]["ws_transport_connected"])


def _build_watchdog_namespace(self_now: float):
    """Build a minimal namespace capable of exec-ing ws_watchdog in isolation.

    ws_watchdog uses time, state, state_lock, and module-level constants. We
    inject spies for the side-effects it can trigger and patch the global
    counters it mutates so behavior is observable without real threads. The
    `now` parameter sets the value returned by `time.time()` so the watchdog's
    age arithmetic is deterministic."""
    state = {
        "ws_last_tick": None,
        "ws_last_hb_ts": None,
        "ws_connected_ts": None,
        "ws_transport_connected": False,
        "ws_ready": False,
        "system_ready": False,
        "execution_reason": "",
        "diag": {},
    }
    calls = []
    clock = {"t": self_now}

    class _Clock:
        """Stand-in for the `time` module with a controllable wall clock."""

        def __init__(self, real):
            self._real = real
            self._sleep_handler = None

        def time(self):
            return clock["t"]

        def set(self, value):
            clock["t"] = value

        def sleep(self, seconds):
            if self._sleep_handler is not None:
                self._sleep_handler(seconds)
            else:
                self._real.sleep(seconds)

        def __getattr__(self, name):
            return getattr(self._real, name)

    fake_time = _Clock(time)
    namespace = {
        "time": fake_time,
        "math": math,
        "state": state,
        "state_lock": threading.RLock(),
        "WATCHDOG_WS_STALE_SEC": 30.0,
        "WATCHDOG_WS_TRADE_STALE_SEC": 90.0,
        "fmt": lambda x: f"{x:.1f}",
        # Module-level ws_* globals the watchdog mutates.
        "ws_app": object(),
        "ws_reconnecting": False,
        "last_ws_reconnect": 0.0,
        "ws_alive": True,
        "ws_stale_count": 0,
        "ws_retry": 0,
        "shutdown_event": threading.Event(),
        # Spy-able side effects.
        "refresh_dashboard_market_snapshot": lambda *a, **k: None,
        "_close_ws_app": lambda *a, **k: calls.append("close"),
        "_recompute_system_readiness": lambda now: {"system_ready": False},
        "_clear_execution_pause_if_reason": lambda reason: False,
        "_WS_RECOVERABLE_PAUSE_REASONS": frozenset(
            {"WS_STALE", "STALE_DATA_HARD_STOP", "PRICE_STALE_OR_MISSING"}
        ),
        "logger": SimpleNamespace(
            debug=lambda *a, **k: None,
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
            critical=lambda *a, **k: None,
            exception=lambda *a, **k: None,
        ),
    }
    compile_functions(("ws_watchdog",), namespace)
    return namespace, calls


def _run_watchdog_once(ns, now: float) -> bool:
    """Drive exactly one body iteration of ws_watchdog.

    The real ws_watchdog loops on `time.sleep(3)` until `shutdown_event` is
    set. We rewrite the injected `time` module so its first `sleep` call sets
    the shutdown event, then invoke the watchdog. This exercises the full
    body — including the transport_alive decision branch — exactly once."""
    sleep_calls = []
    original_shutdown = ns["shutdown_event"]

    class _OneShotEvent:
        def __init__(self, real):
            self._real = real
            self._fired = False

        def is_set(self):
            return self._fired

        def set(self):
            self._fired = True
            self._real.set()

        def clear(self):
            self._fired = False
            self._real.clear()

        def wait(self, timeout=None):
            return self._real.wait(timeout)

    event = _OneShotEvent(original_shutdown)
    ns["shutdown_event"] = event

    def _fake_sleep(seconds):
        sleep_calls.append(seconds)
        event.set()

    # Set the fake clock BEFORE invoking so time.time() returns `now`.
    ns["time"].set(now)
    ns["time"]._sleep_handler = _fake_sleep
    try:
        ns["ws_watchdog"]()
    finally:
        ns["time"]._sleep_handler = None
        ns["shutdown_event"] = original_shutdown
    # The watchdog should have done at least one sleep(3) before exiting.
    return len(sleep_calls) >= 1


if __name__ == "__main__":
    unittest.main()
