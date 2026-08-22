"""Regression tests for half-alive strategy progress detection."""

import ast
import os
import threading
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace


BOT_PATH = Path(__file__).with_name("bot.py")
SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(BOT_PATH))
TRACKED_LOCK_CLASS = next(
    node
    for node in TREE.body
    if isinstance(node, ast.ClassDef) and node.name == "_TrackedRLock"
)
FUNCTION = next(
    node
    for node in TREE.body
    if isinstance(node, ast.FunctionDef)
    and node.name == "_strategy_progress_health_snapshot"
)
INCIDENT_FUNCTIONS = [
    node
    for node in TREE.body
    if isinstance(node, ast.FunctionDef)
    and node.name in {
        "_strategy_progress_incident_snapshot",
        "_update_strategy_progress_incident",
    }
]
ENTRY_GATE_FUNCTION = next(
    node
    for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name == "can_progress_new_entry"
)


def compile_snapshot(namespace):
    module = ast.Module(body=[FUNCTION], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace["_strategy_progress_health_snapshot"]


def compile_tracked_lock():
    namespace = {
        "threading": threading,
        "time": __import__("time"),
        "sys": __import__("sys"),
        "traceback": __import__("traceback"),
        "Path": Path,
    }
    module = ast.Module(body=[TRACKED_LOCK_CLASS], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace["_TrackedRLock"]


def test_tracked_rlock_reports_and_clears_owner_diagnostics():
    lock = compile_tracked_lock()("test-lock")
    with lock:
        detail = lock.diagnostics()
        assert detail["owner_thread"] == threading.current_thread().name
        assert detail["depth"] == 1
        assert detail["stack_tail"]
    detail = lock.diagnostics()
    assert detail["owner_thread"] is None
    assert detail["depth"] == 0


class StrategyProgressHealthTest(unittest.TestCase):
    def setUp(self):
        self.now = 10_000.0
        self.state = {
            "ws_last_tick": self.now - 1,
            "ws_last_hb_ts": self.now - 1,
            "ws_transport_connected": True,
            "last_ai_call_ts": self.now - 10,
            "execution_paused": False,
            "manual_admin_pause": False,
            "live_armed": False,
        }
        self.lock = threading.Lock()
        self.snapshot = compile_snapshot({
            "time": SimpleNamespace(time=lambda: self.now),
            "os": os,
            "state": self.state,
            "state_lock": threading.RLock(),
            "trade_lock": self.lock,
            "WATCHDOG_TRADE_LOCK_TIMEOUT_SEC": 0.01,
            "WATCHDOG_WS_STALE_SEC": 30.0,
            "WATCHDOG_WS_TRADE_STALE_SEC": 90.0,
            "get_effective_ai_cooldown_sec": lambda: 180,
            "bot_start_time": self.now - 1_000,
            "open_positions": [],
            "pending_orders": [],
        })

    def test_healthy_requires_real_progress_not_only_process_heartbeat(self):
        self.assertTrue(self.snapshot(self.now)["ok"])

    def test_wedged_trade_lock_is_unhealthy(self):
        self.lock.acquire()
        try:
            result = self.snapshot(self.now)
        finally:
            self.lock.release()
        self.assertFalse(result["ok"])
        self.assertFalse(result["trade_lock_available"])
        self.assertEqual(result["trade_lock_diagnostics"], {})

    def test_tracked_lock_reports_owner_and_stack_without_releasing_it(self):
        class DiagnosticLock:
            def acquire(self, **_kwargs):
                return False

            def diagnostics(self, now=None):
                return {"owner_thread": "paper-engine", "held_seconds": 42.0}

        snapshot = compile_snapshot({
            "time": SimpleNamespace(time=lambda: self.now),
            "os": os,
            "state": self.state,
            "state_lock": threading.RLock(),
            "trade_lock": DiagnosticLock(),
            "WATCHDOG_TRADE_LOCK_TIMEOUT_SEC": 0.01,
            "WATCHDOG_WS_STALE_SEC": 30.0,
            "WATCHDOG_WS_TRADE_STALE_SEC": 90.0,
            "get_effective_ai_cooldown_sec": lambda: 180,
            "bot_start_time": self.now - 1_000,
            "open_positions": [],
            "pending_orders": [],
        })
        result = snapshot(self.now)
        self.assertEqual(result["trade_lock_diagnostics"]["owner_thread"], "paper-engine")
        self.assertEqual(result["trade_lock_diagnostics"]["held_seconds"], 42.0)

    def test_server_heartbeat_cannot_hide_stale_trade_stream(self):
        self.state["ws_last_tick"] = self.now - 91
        self.assertFalse(self.snapshot(self.now)["ok"])

    def test_stale_ai_call_is_unhealthy_when_collection_expected(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "present"}):
            self.state["last_ai_call_ts"] = self.now - 501
            result = self.snapshot(self.now)
        self.assertTrue(result["ai_expected"])
        self.assertFalse(result["ai_progressing"])
        self.assertFalse(result["ok"])

    def test_failure_reasons_are_explicit(self):
        self.lock.acquire()
        try:
            self.state["ws_last_tick"] = self.now - 91
            result = self.snapshot(self.now)
        finally:
            self.lock.release()
        self.assertIn("TRADE_LOCK_UNAVAILABLE", result["reasons"])
        self.assertIn("WS_TRADE_STREAM_STALLED", result["reasons"])


class StrategyProgressIncidentTest(unittest.TestCase):
    def setUp(self):
        self.now = 20_000.0
        self.incident = {
            "active": False,
            "started_ts": 0.0,
            "last_failure_ts": 0.0,
            "reasons": [],
            "consecutive_failures": 0,
            "consecutive_successes": 0,
        }
        namespace = {
            "time": SimpleNamespace(time=lambda: self.now),
            "_strategy_progress_incident_lock": threading.Lock(),
            "_strategy_progress_incident": self.incident,
            "WATCHDOG_PROGRESS_FAILURES_BEFORE_RECOVERY": 3,
            "WATCHDOG_PROGRESS_SUCCESSES_BEFORE_CLEAR": 2,
        }
        module = ast.Module(body=INCIDENT_FUNCTIONS, type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(BOT_PATH), "exec"), namespace)
        self.update = namespace["_update_strategy_progress_incident"]
        self.snapshot = namespace["_strategy_progress_incident_snapshot"]

    def test_incident_latches_and_suppresses_entries_until_proven_recovery(self):
        failed = {"ok": False, "reasons": ["AI_CADENCE_STALLED"]}
        self.update(failed, self.now)
        self.update(failed, self.now + 5)
        latched = self.update(failed, self.now + 10)
        self.assertTrue(latched["active"])
        self.assertTrue(latched["new_entries_suppressed"])
        self.assertEqual(["AI_CADENCE_STALLED"], latched["reasons"])
        self.assertEqual(10.0, self.snapshot(self.now + 20)["age_sec"])

        first_recovery = self.update({"ok": True}, self.now + 25)
        self.assertTrue(first_recovery["active"])
        cleared = self.update({"ok": True}, self.now + 30)
        self.assertFalse(cleared["active"])
        self.assertFalse(cleared["new_entries_suppressed"])

    def test_failures_do_not_reset_while_incident_remains_unhealthy(self):
        failed = {"ok": False, "reasons": ["TRADE_LOCK_UNAVAILABLE"]}
        for offset in range(6):
            current = self.update(failed, self.now + offset)
        self.assertTrue(current["active"])
        self.assertEqual(6, current["consecutive_failures"])

    def test_latched_incident_blocks_only_new_entry_gate(self):
        self.incident.update({
            "active": True,
            "started_ts": self.now - 10,
            "reasons": ["TRADE_LOCK_UNAVAILABLE"],
        })
        namespace = {
            "_strategy_progress_incident_lock": threading.Lock(),
            "_strategy_progress_incident": self.incident,
            "_strategy_progress_incident_snapshot": self.snapshot,
            "_recompute_system_readiness": lambda _now=None: {
                "signal_generation_ready": True,
                "readiness_reasons": [],
            },
        }
        module = ast.Module(body=[ENTRY_GATE_FUNCTION], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(BOT_PATH), "exec"), namespace)
        allowed, reason, runtime = namespace["can_progress_new_entry"](self.now)
        self.assertFalse(allowed)
        self.assertEqual("TRADE_LOCK_UNAVAILABLE", reason)
        self.assertTrue(runtime["strategy_progress_incident"]["active"])


if __name__ == "__main__":
    unittest.main()
