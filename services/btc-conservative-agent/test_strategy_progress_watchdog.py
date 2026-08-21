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
FUNCTION = next(
    node
    for node in TREE.body
    if isinstance(node, ast.FunctionDef)
    and node.name == "_strategy_progress_health_snapshot"
)


def compile_snapshot(namespace):
    module = ast.Module(body=[FUNCTION], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace["_strategy_progress_health_snapshot"]


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


if __name__ == "__main__":
    unittest.main()
