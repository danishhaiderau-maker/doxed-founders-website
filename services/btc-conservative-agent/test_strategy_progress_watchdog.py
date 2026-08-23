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
RECOVERY_GATE_FUNCTION = next(
    node
    for node in TREE.body
    if isinstance(node, ast.FunctionDef)
    and node.name == "can_run_ai_recovery_observation"
)


def compile_snapshot(namespace):
    # The production watchdog now includes bounded replay-lock and post-AI
    # worker diagnostics. Source-extracted tests supply lightweight defaults
    # so each case can continue to override only the dependency it exercises.
    namespace.setdefault("replay_lock", threading.RLock())
    namespace.setdefault("post_ai_evidence_health_snapshot", lambda: {})
    namespace.setdefault("sys", __import__("sys"))
    namespace.setdefault("traceback", __import__("traceback"))
    namespace.setdefault("Path", Path)
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


def test_all_process_startup_grace_uses_non_persisted_boot_clock():
    assert "time.time() - bot_start_time < STARTUP_GRACE_PERIOD" not in SOURCE
    assert "time.time() - process_boot_time < STARTUP_GRACE_PERIOD" in SOURCE


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
            "process_boot_time": self.now - 1_000,
            "open_positions": [],
            "pending_orders": [],
            "_strategy_progress_incident_lock": threading.Lock(),
            "_strategy_progress_incident": {
                "active": False, "reasons": [],
            },
            "scheduled_ai_cycle_state": {},
            "copy": __import__("copy"),
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
            "process_boot_time": self.now - 1_000,
            "open_positions": [],
            "pending_orders": [],
            "_strategy_progress_incident_lock": threading.Lock(),
            "_strategy_progress_incident": {
                "active": False, "reasons": [],
            },
            "scheduled_ai_cycle_state": {},
            "copy": __import__("copy"),
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

    def test_fresh_process_gets_bounded_ai_startup_grace_even_for_old_session(self):
        self.snapshot.__globals__["process_boot_time"] = self.now - 10
        self.state["last_ai_call_ts"] = 0
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "present"}):
            result = self.snapshot(self.now)
        self.assertFalse(result["ai_expected"])
        self.assertTrue(result["ai_progressing"])
        self.assertTrue(result["ok"])
        self.assertEqual(10.0, result["process_startup_age_sec"])

    def test_ai_startup_grace_expires_if_no_call_completes(self):
        self.snapshot.__globals__["process_boot_time"] = self.now - 501
        self.state["last_ai_call_ts"] = 0
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "present"}):
            result = self.snapshot(self.now)
        self.assertTrue(result["ai_expected"])
        self.assertFalse(result["ai_progressing"])
        self.assertFalse(result["ok"])
        self.assertIn("AI_CADENCE_STALLED", result["reasons"])

    def test_manual_pause_cannot_hide_an_already_latched_ai_stall(self):
        self.state["execution_paused"] = True
        self.state["manual_admin_pause"] = True
        self.snapshot.__globals__["_strategy_progress_incident"].update({
            "active": True,
            "reasons": ["AI_CADENCE_STALLED"],
        })
        result = self.snapshot(self.now)
        self.assertFalse(result["ok"])
        self.assertFalse(result["ai_progressing"])
        self.assertIn("AI_CADENCE_STALLED", result["reasons"])

    def test_latched_ai_stall_exposes_independent_recovery_probe(self):
        self.snapshot.__globals__["_strategy_progress_incident"].update({
            "active": True,
            "reasons": ["AI_CADENCE_STALLED"],
        })
        result = self.snapshot(self.now)
        self.assertFalse(result["ok"])
        self.assertFalse(result["ai_progressing"])
        self.assertTrue(result["recovery_probe_ok"])

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

    def test_ai_only_latch_clears_after_real_recovery_probes(self):
        failed = {"ok": False, "reasons": ["AI_CADENCE_STALLED"]}
        for offset in range(3):
            self.update(failed, self.now + offset)
        self.assertTrue(self.incident["active"])

        recovery = {
            "ok": False,
            "reasons": ["AI_CADENCE_STALLED"],
            "recovery_probe_ok": True,
        }
        first = self.update(recovery, self.now + 5)
        self.assertTrue(first["active"])
        cleared = self.update(recovery, self.now + 10)
        self.assertFalse(cleared["active"])

    def test_non_ai_latch_cannot_clear_from_recovery_probe(self):
        failed = {"ok": False, "reasons": ["TRADE_LOCK_UNAVAILABLE"]}
        for offset in range(3):
            self.update(failed, self.now + offset)
        result = self.update({
            "ok": False,
            "reasons": ["TRADE_LOCK_UNAVAILABLE"],
            "recovery_probe_ok": True,
        }, self.now + 5)
        self.assertTrue(result["active"])
        self.assertEqual(0, result["consecutive_successes"])

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

    def _compile_recovery_gate(self, reasons, **state_overrides):
        state = {
            "execution_paused": False,
            "manual_admin_pause": False,
            "live_armed": False,
            **state_overrides,
        }
        incident = {"active": True, "reasons": list(reasons)}
        namespace = {
            "_recompute_system_readiness": lambda _now=None: {
                "system_ready": True,
            },
            "_strategy_progress_incident_lock": threading.Lock(),
            "_strategy_progress_incident": incident,
            "state_lock": threading.RLock(),
            "state": state,
            "_force_paper_mode_active": lambda: True,
            "_sole_ai_research_mode": lambda: True,
        }
        module = ast.Module(body=[RECOVERY_GATE_FUNCTION], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(BOT_PATH), "exec"), namespace)
        return namespace["can_run_ai_recovery_observation"]

    def test_ai_only_incident_allows_data_only_recovery_observation(self):
        gate = self._compile_recovery_gate(["AI_CADENCE_STALLED"])
        allowed, reason, _ = gate(self.now)
        self.assertTrue(allowed)
        self.assertEqual("AI_RECOVERY_OBSERVATION_ONLY", reason)

    def test_mixed_or_transport_incident_cannot_bypass_entry_latch(self):
        for reasons in (
            ["WS_TRADE_STREAM_STALLED"],
            ["AI_CADENCE_STALLED", "TRADE_LOCK_UNAVAILABLE"],
        ):
            gate = self._compile_recovery_gate(reasons)
            self.assertFalse(gate(self.now)[0], reasons)

    def test_recovery_observation_is_refused_when_live_armed(self):
        gate = self._compile_recovery_gate(
            ["AI_CADENCE_STALLED"], live_armed=True,
        )
        self.assertFalse(gate(self.now)[0])

    def test_recovery_path_returns_before_any_child_lane_fanout(self):
        process_source = ast.get_source_segment(
            SOURCE,
            next(
                node for node in TREE.body
                if isinstance(node, ast.FunctionDef) and node.name == "process_signal"
            ),
        )
        recovery_return = process_source.index(
            '"exact_reason": "AI_RECOVERY_OBSERVATION_ONLY"'
        )
        self.assertLess(
            recovery_return,
            process_source.index("spawn_combo_lanes_from_ai_scan("),
        )
        self.assertLess(
            recovery_return,
            process_source.index("spawn_continuous_lane_from_ai_scan("),
        )

    def test_stale_reconciliation_never_runs_slow_expiry_io_under_trade_lock(self):
        reconcile_source = ast.get_source_segment(
            SOURCE,
            next(
                node for node in TREE.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "reconcile_stale_signals"
            ),
        )
        outside_boundary = reconcile_source.index(
            "Expiry persistence, relay publication, collector writes"
        )
        self.assertNotIn(
            "_record_expired_order(", reconcile_source[:outside_boundary]
        )
        self.assertNotIn(
            "_cancel_pending_order_confirmed(",
            reconcile_source[:outside_boundary],
        )
        self.assertGreater(
            reconcile_source.index("_record_expired_order("), outside_boundary
        )
        self.assertGreater(
            reconcile_source.index("_cancel_pending_order_confirmed("),
            outside_boundary,
        )

    def test_periodic_ai_poll_cannot_stretch_research_cooldown(self):
        """The scheduler poll must stay well below the configured AI cadence."""
        heartbeat_assign = next(
            node for node in TREE.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "HEARTBEAT_INTERVAL"
                for target in node.targets
            )
        )
        namespace = {"max": max, "min": min, "float": float, "os": __import__("os")}
        interval = eval(
            compile(ast.Expression(heartbeat_assign.value), str(BOT_PATH), "eval"),
            namespace,
        )
        self.assertLessEqual(interval, 30.0)
        self.assertLess(interval, 180.0)


if __name__ == "__main__":
    unittest.main()
