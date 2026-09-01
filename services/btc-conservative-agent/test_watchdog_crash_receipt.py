"""Cause-bound receipts for safe strategy-progress watchdog recovery."""

import ast
import math
import os
import shutil
import threading
import time
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(BOT_PATH))
CONTEXT_FUNCTION = next(
    node
    for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name == "_watchdog_crash_context"
)
PRESSURE_FUNCTION = next(
    node
    for node in TREE.body
    if isinstance(node, ast.FunctionDef)
    and node.name == "_bounded_process_pressure_snapshot"
)
HANDLER_SNAPSHOT_FUNCTION = next(
    node
    for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name == "_dashboard_handler_snapshot"
)
WATCHDOG_FUNCTION = next(
    node
    for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name == "watchdog_loop"
)


def compile_context():
    state_lock = type(
        "StateLock",
        (),
        {"diagnostics": lambda self, _now=None: {
            "owner_thread": "state-owner",
            "owner_active": True,
            "held_seconds": 12.5,
            "depth": 2,
            "stack_tail": [r"C:\private\operator\bot.py:4:mutate_state"],
        }},
    )()
    namespace = {
        "Path": Path,
        "math": math,
        "os": os,
        "shutil": shutil,
        "sys": __import__("sys"),
        "time": time,
        "state_lock": state_lock,
        "SOURCE_GIT_REV": "revision-under-test",
        "BOT_INSTANCE_ID": "paper-instance",
        "_force_paper_mode_active": lambda: True,
        "_dashboard_handler_snapshot": lambda: {"active_total": 0, "by_cap": {}},
    }
    namespace["_bounded_process_pressure_snapshot"] = lambda: {
        "schema": "process_pressure_v1",
        "rss_bytes": 1234,
        "disk_used_pct": 25.0,
    }
    module = ast.Module(body=[CONTEXT_FUNCTION], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace["_watchdog_crash_context"]


def test_watchdog_receipt_preserves_exact_progress_cause_and_lock_owner():
    receipt = compile_context()(
        {
            "ok": False,
            "reasons": ["TRADE_LOCK_UNAVAILABLE"],
            "recovery_probe_ok": False,
            "live_armed": False,
            "open_positions": 0,
            "pending_orders": 0,
            "ai_expected": True,
            "ai_age_sec": 346.0,
            "evaluation_age_sec": 309.0,
            "evaluation_progressing": False,
            "scheduled_ai_cycle": {
                "stage": "PROCESS_SIGNAL",
                "stage_age_sec": 309.0,
                "owner": "Thread-7",
                "owner_active": True,
                "stack_tail": [r"C:\private\operator\bot.py:1:process_signal"],
            },
            "trade_lock_diagnostics": {
                "owner_thread": "Thread-8",
                "owner_active": True,
                "held_seconds": 309.0,
                "timeout_count": 12,
                "stack_tail": ["bot.py:2:publish_snapshot"],
            },
        },
        {
            "active": True,
            "age_sec": 309.0,
            "reasons": ["TRADE_LOCK_UNAVAILABLE"],
            "consecutive_failures": 62,
            "consecutive_successes": 0,
        },
        trigger="STRATEGY_PROGRESS_EXIT_75",
        restart_allowed=True,
    )

    assert receipt["schema"] == "watchdog_crash_context_v1"
    assert receipt["source_revision"] == "revision-under-test"
    assert receipt["trigger"] == "STRATEGY_PROGRESS_EXIT_75"
    assert receipt["restart_allowed"] is True
    assert receipt["exit_code"] == 75
    assert receipt["incident"]["reasons"] == ["TRADE_LOCK_UNAVAILABLE"]
    assert receipt["progress"]["pending_orders"] == 0
    assert receipt["scheduled_ai_cycle"]["stage"] == "PROCESS_SIGNAL"
    assert receipt["scheduled_ai_cycle"]["stack_tail"] == ["bot.py:1:process_signal"]
    assert receipt["trade_lock"]["owner_thread"] == "Thread-8"
    assert receipt["trade_lock"]["stack_tail"]
    assert receipt["state_lock"]["owner_thread"] == "state-owner"
    assert receipt["state_lock"]["held_seconds"] == 12.5
    assert receipt["state_lock"]["stack_tail"] == ["bot.py:4:mutate_state"]
    assert receipt["pressure"]["rss_bytes"] == 1234


def test_watchdog_receipt_is_bounded_and_secret_free():
    receipt = compile_context()(
        {
            "reasons": ["AI_CADENCE_STALLED"] * 40,
            "query_string": "token=secret",
            "headers": {"Authorization": "secret"},
            "trade_lock_diagnostics": {"exception": "secret"},
        },
        {"reasons": ["AI_CADENCE_STALLED"]},
        trigger="STRATEGY_PROGRESS_INCIDENT",
    )
    rendered = repr(receipt)
    assert "token=secret" not in rendered
    assert "Authorization" not in rendered
    assert "exception" not in rendered
    assert receipt["restart_allowed"] is False
    assert receipt["exit_code"] is None
    assert len(receipt["progress"]["reasons"]) == 16


def test_pressure_receipt_is_fixed_shape_numeric_and_secret_free():
    namespace = {
        "Path": Path,
        "math": math,
        "os": os,
        "shutil": shutil,
        "sys": __import__("sys"),
    }
    module = ast.Module(body=[PRESSURE_FUNCTION], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    receipt = namespace["_bounded_process_pressure_snapshot"]()
    assert set(receipt) == {
        "schema", "cpu_count", "process_cpu_sec", "load_1m", "load_5m",
        "load_15m", "load_1m_per_cpu", "rss_bytes", "disk_total_bytes",
        "disk_used_bytes", "disk_free_bytes", "disk_used_pct",
    }
    assert receipt["schema"] == "process_pressure_v1"
    for key, value in receipt.items():
        if key != "schema" and value is not None:
            assert isinstance(value, (int, float))
    assert "cwd" not in receipt
    assert "path" not in repr(receipt).lower()


def test_state_lock_uses_the_same_tracked_rlock_contract():
    assignment = next(
        node for node in TREE.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "state_lock"
                for target in node.targets)
    )
    assert isinstance(assignment.value, ast.Call)
    assert isinstance(assignment.value.func, ast.Name)
    assert assignment.value.func.id == "_TrackedRLock"
    assert assignment.value.args[0].value == "state_lock"


def test_handler_snapshot_is_bounded_and_uses_only_fixed_labels():
    active = {
        index: {
            "started": 90.0,
            "cap_name": "data_sync",
            "pre_label": "/api/data-sync/file" if index == 0 else "secret?token=bad",
            "routed_label": None,
        }
        for index in range(40)
    }
    namespace = {
        "time": type("Clock", (), {"monotonic": staticmethod(lambda: 100.0)}),
        "_dashboard_handler_lock": threading.Lock(),
        "_dashboard_active_handlers": active,
        "_DASHBOARD_TELEMETRY_STATIC_ROUTES": frozenset({"/api/data-sync/file"}),
    }
    module = ast.Module(body=[HANDLER_SNAPSHOT_FUNCTION], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    receipt = namespace["_dashboard_handler_snapshot"](100.0)
    assert receipt["active_total"] == 32
    assert receipt["by_cap"]["data_sync"]["active"] == 32
    assert receipt["by_cap"]["data_sync"]["oldest_ms"] == 10_000
    assert receipt["by_cap"]["data_sync"]["routes"]["/api/data-sync/file"] == 1
    assert receipt["by_cap"]["data_sync"]["routes"]["UNCLASSIFIED"] == 31
    assert "token=bad" not in repr(receipt)


def test_watchdog_writes_incident_and_pre_exit_receipts():
    assert 'trigger="STRATEGY_PROGRESS_INCIDENT"' in SOURCE
    assert 'trigger="STRATEGY_PROGRESS_EXIT_75"' in SOURCE
    assert "restart_allowed=True" in SOURCE


class ExitCalled(Exception):
    def __init__(self, code):
        self.code = code


def compile_watchdog(*, pending, iterations=1, advances=()):
    class Shutdown:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > iterations

    class Clock:
        now = 1000.0
        advances = []

        @classmethod
        def time(cls):
            return cls.now

        @classmethod
        def sleep(cls, _seconds):
            if cls.advances:
                cls.now += cls.advances.pop(0)

    Clock.advances = list(advances)

    dumps = []
    exits = []

    def exit_(code):
        exits.append(code)
        raise ExitCalled(code)

    progress = {
        "ok": False,
        "reasons": ["TRADE_LOCK_UNAVAILABLE"],
        "trade_lock_available": False,
        "ws_age_sec": 1.0,
        "ai_age_sec": 10.0,
        "ai_expected": True,
        "open_positions": 0,
        "pending_orders": pending,
        "live_armed": False,
    }
    namespace = {
        "shutdown_event": Shutdown(),
        "state_lock": threading.RLock(),
        "state": {"last_heartbeat": 1000.0, "ws_last_tick": 1000.0},
        "last_heartbeat": 1000.0,
        "time": Clock,
        "WATCHDOG_HEARTBEAT_STALE_SEC": 10_000.0,
        "WATCHDOG_WS_STALE_SEC": 10_000.0,
        "WATCHDOG_PROGRESS_FAILURES_BEFORE_RECOVERY": 3,
        "_strategy_progress_health_snapshot": lambda: dict(progress),
        "_update_strategy_progress_incident": lambda _progress: {
            "active": True,
            "consecutive_failures": 3,
            "reasons": ["TRADE_LOCK_UNAVAILABLE"],
        },
        "dump_system_state": lambda **kwargs: dumps.append(kwargs),
        "dump_threads": lambda: None,
        "_force_paper_mode_active": lambda: True,
        "logger": type("Logger", (), {"critical": staticmethod(lambda *a, **k: None), "error": staticmethod(lambda *a, **k: None)}),
        "os": type("OS", (), {"_exit": staticmethod(exit_)}),
    }
    module = ast.Module(body=[WATCHDOG_FUNCTION], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace["watchdog_loop"], dumps, exits


def test_pending_paper_order_blocks_watchdog_exit_and_dump_cadence_is_300_seconds():
    watchdog, dumps, exits = compile_watchdog(pending=1, iterations=3, advances=(299.0, 1.0))
    watchdog()
    assert exits == []
    assert [item["trigger"] for item in dumps] == [
        "STRATEGY_PROGRESS_INCIDENT",
        "STRATEGY_PROGRESS_INCIDENT",
    ]


def test_flat_paper_process_emits_receipt_then_exits_exactly_75():
    watchdog, dumps, exits = compile_watchdog(pending=0)
    try:
        watchdog()
    except ExitCalled as exc:
        assert exc.code == 75
    else:
        raise AssertionError("flat stalled paper watchdog did not exit")
    assert exits == [75]
    assert dumps[-1]["trigger"] == "STRATEGY_PROGRESS_EXIT_75"
    assert dumps[-1]["restart_allowed"] is True
