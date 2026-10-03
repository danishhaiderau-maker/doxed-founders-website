"""trade_lock telemetry: probe misses vs real timeouts, contended-wait latency."""

import ast
import os
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BOT_PATH = Path(__file__).with_name("bot.py")
SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(BOT_PATH))

# Every bounded/zero-wait trade_lock acquire must live in a read, diagnostic or
# reset-guard path. Money paths (fill, exit, cancel, reprice, lifecycle commit)
# use the blocking ``with trade_lock:`` and therefore can be delayed by
# contention but never skipped by a lock timeout.
TIMED_ACQUIRE_ALLOWLIST = {
    "_perform_fresh_collection_reset_locked",
    "_fresh_research_reset_assert_quiesced",
    "_strategy_progress_health_snapshot",
    "_dashboard_http_restart_allowed",
    "_build_relay_execution_state_snapshot",
    "api_relay_state",
    "_build_dashboard_truth",
    "_build_api_state_snapshot",
    "_monitor_lane_rows",
}


def _function(name):
    return next(
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _tracked_lock_cls(wait_budget_ms=20.0):
    namespace = {
        "threading": threading, "time": time, "sys": sys,
        "traceback": __import__("traceback"), "Path": Path,
        "TRADE_LOCK_HOLD_BUDGET_MS": 50.0, "TRADE_LOCK_WAIT_BUDGET_MS": wait_budget_ms,
        "_TRACKED_LOCK_SITE_MAX": 8,
    }
    node = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "_TrackedRLock")
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    cls = namespace["_TrackedRLock"]
    namespace["_TRACKED_LOCK_INTERNAL_CODES"] = frozenset(
        {cls.acquire.__code__, cls.__enter__.__code__}
    )
    return cls


def _hold_in_thread(lock, seconds):
    held = threading.Event()

    def holder():
        with lock:
            held.set()
            time.sleep(seconds)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert held.wait(2)
    return thread


def test_zero_wait_probe_miss_is_not_a_timed_timeout():
    lock = _tracked_lock_cls()("trade_lock")
    thread = _hold_in_thread(lock, 0.2)

    def ready_probe():
        return lock.acquire(timeout=0.0)

    assert ready_probe() is False
    thread.join(2)
    counters = lock.telemetry_counters()
    assert counters["timeout_count"] == 1
    assert counters["probe_busy_count"] == 1
    assert counters["timed_timeout_count"] == 0
    assert counters["waits"] == 0
    assert counters["top_timeout_sites"][0]["site"].startswith("probe:ready_probe:")
    diag = lock.diagnostics()
    assert diag["probe_busy_count"] == 1 and diag["timed_timeout_count"] == 0


def test_bounded_wait_timeout_is_classified_with_its_site():
    lock = _tracked_lock_cls()("trade_lock")
    thread = _hold_in_thread(lock, 0.3)

    def relay_snapshot_builder():
        return lock.acquire(timeout=0.05)

    assert relay_snapshot_builder() is False
    thread.join(2)
    counters = lock.telemetry_counters()
    assert counters["timed_timeout_count"] == 1
    assert counters["probe_busy_count"] == 0
    assert counters["waits"] == 1 and counters["interval_wait_max_ms"] >= 40.0
    assert counters["top_timeout_sites"][0]["site"].startswith("timed:relay_snapshot_builder:")


def test_contended_blocking_acquire_records_wait_latency_and_site():
    lock = _tracked_lock_cls(wait_budget_ms=20.0)("trade_lock")
    assert lock._wait_budget_ms == 20.0
    thread = _hold_in_thread(lock, 0.08)

    def paper_exit_commit():
        with lock:
            pass

    paper_exit_commit()
    thread.join(2)
    counters = lock.telemetry_counters()
    assert counters["waits"] == 1
    assert counters["wait_over_budget"] == 1
    assert counters["interval_wait_max_ms"] >= 40.0
    assert counters["interval_wait_max_site"].startswith("paper_exit_commit:")
    assert counters["top_wait_sites"][0]["site"].startswith("paper_exit_commit:")
    assert counters["timeout_count"] == 0


def test_uncontended_and_reentrant_acquires_record_no_wait():
    lock = _tracked_lock_cls()("trade_lock")
    for _ in range(50):
        with lock:
            with lock:
                pass
    counters = lock.telemetry_counters()
    assert counters["waits"] == 0 and counters["timeout_count"] == 0
    assert counters["holds"] == 50


def test_interval_maxima_reset_only_when_requested():
    lock = _tracked_lock_cls()("trade_lock")

    def slow():
        with lock:
            time.sleep(0.03)

    slow()
    first = lock.telemetry_counters()
    assert first["interval_hold_max_ms"] >= 20.0
    again = lock.telemetry_counters(reset_interval=True)
    assert again["interval_hold_max_ms"] == first["interval_hold_max_ms"]
    after = lock.telemetry_counters()
    assert after["interval_hold_max_ms"] == 0.0 and after["interval_hold_max_site"] is None
    assert after["holds"] == 1


def test_no_money_path_uses_a_timed_trade_lock_acquire():
    lines = [
        number for number, text in enumerate(SOURCE.splitlines(), start=1)
        if re.search(r"\btrade_lock\.acquire\(", text)
    ]
    functions = [
        (node.lineno, node.end_lineno, node.name) for node in ast.walk(TREE)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    owners = set()
    for number in lines:
        enclosing = [f for f in functions if f[0] <= number <= f[1]]
        assert enclosing, f"module-level trade_lock.acquire at line {number}"
        owners.add(max(enclosing, key=lambda f: f[0])[2])
    assert owners, "expected timed trade_lock acquires to exist"
    assert owners <= TIMED_ACQUIRE_ALLOWLIST, sorted(owners - TIMED_ACQUIRE_ALLOWLIST)


def test_runtime_telemetry_is_started_and_exposed():
    starter = ast.get_source_segment(SOURCE, _function("_start_api_state_cache_refresher"))
    assert "_start_runtime_telemetry()" in starter
    sampler = ast.get_source_segment(SOURCE, _function("_start_runtime_telemetry"))
    assert "reset_interval=True" in sampler
    assert "stop_event=shutdown_event" in sampler
    status = ast.get_source_segment(SOURCE, _function("status"))
    assert '"runtime_telemetry": _runtime_telemetry_status(now)' in status
