"""Focused behavioral contract for WS/readiness and direct-live fail-closed safety."""

import ast
import copy
import math
import os
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


class WsLiveReadinessBehaviorTest(unittest.TestCase):
    def setUp(self):
        self.now = 10_000.0
        self.state = {
            "price": 64_000.0,
            "price_ts": self.now,
            "rest_price": 64_010.0,
            "rest_price_ts": self.now,
            "rest_last_tick": self.now,
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
        self.namespace = {
            "copy": copy,
            "math": math,
            "os": os,
            "time": time,
            "state": self.state,
            "state_lock": threading.RLock(),
            "WS_ENTRY_FRESH_SEC": 15.0,
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
        self.state["ws_last_tick"] = self.now - 16.0
        runtime = self.namespace["_runtime_readiness_components"](self.now)
        self.assertFalse(runtime["ws_transport_ready"])
        self.assertIn("WS_NOT_READY", runtime["readiness_reasons"])

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
        self.assertIn("snapshot_seed=True", function_source("safe_ws_handler"))
        self.assertIn("if snapshot_seed:", function_source("_process_ws_trade_tick"))

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
            "heartbeat_loop",
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
        touch = pending.index("if not _pending_limit_touched")
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


if __name__ == "__main__":
    unittest.main()
