"""RK PASS (6): per-exit current-epoch scan stamps + native_id -> thread-name runtime telemetry."""
from __future__ import annotations

import ast
import pathlib
import threading
import time
from types import SimpleNamespace

import paper_fill as pf
import runtime_telemetry as rt

SOURCE_PATH = pathlib.Path(__file__).with_name("bot.py")
SOURCE = SOURCE_PATH.read_text()
TREE = ast.parse(SOURCE)
NODES = {n.name: n for n in TREE.body if isinstance(n, (ast.FunctionDef, ast.Assign))
         and getattr(n, "name", None)}


def _ns():
    ns = {"threading": threading, "time": time, "_RUNTIME_TELEMETRY": SimpleNamespace(lag=SimpleNamespace(last_ms=12.5))}
    body = [n for n in TREE.body if (isinstance(n, ast.FunctionDef) and n.name in (
        "_exit_scan_begin", "_exit_scan_end", "_exit_scan_stamp")) or (isinstance(n, ast.Assign) and any(
        getattr(t, "id", None) in ("_EXIT_SCAN_CTX", "EXIT_STAMP_SCHEMA", "EXIT_SCAN_STAMP_KEYS") for t in n.targets))]
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE_PATH), "exec"), ns)
    return ns


def test_stamp_inside_scan_has_every_field():
    ns = _ns()
    ns["_exit_scan_begin"]("PROTECTIVE_EXIT_WORKER", 0.25)
    ns["_exit_scan_end"]()
    time.sleep(0.3)
    ns["_exit_scan_begin"]("PROTECTIVE_EXIT_WORKER", 0.25)
    time.sleep(0.01)
    st = ns["_exit_scan_stamp"]("PROTECTIVE_EXIT_WORKER")
    ns["_exit_scan_end"]()
    import exit_lag_regression as _elr  # receive keys are stamped by _stamp_trigger_basis, persisted via the same list
    assert set(st) == set(ns["EXIT_SCAN_STAMP_KEYS"]) - set(_elr.RECEIVE_KEYS)
    assert st["exit_source"] == "PROTECTIVE_EXIT_WORKER" and st["exit_scan_source"] == "PROTECTIVE_EXIT_WORKER"
    assert st["exit_scan_duration_ms"] >= 10.0
    assert 0.0 <= st["exit_worker_loop_lag_ms"] < 250.0  # slept 0.30 s vs a 0.25 s period -> ~50 ms late
    assert st["exit_scheduler_lag_ms"] == 12.5
    assert st["exit_book_refreshes_in_scan"] == 0 and st["exit_book_refresh_ms"] == 0.0
    assert st["exit_trigger_wall_ts"] != int(st["exit_trigger_wall_ts"])  # sub-second
    assert st["exit_thread_native_id"] == threading.current_thread().native_id


def test_stamp_outside_scan_is_none_not_guessed():
    ns = _ns()
    st = ns["_exit_scan_stamp"]("ADMIN_CLOSE")
    assert st["exit_source"] == "ADMIN_CLOSE"
    assert st["exit_scan_started_ts"] is None and st["exit_scan_duration_ms"] is None
    assert st["exit_book_refreshes_in_scan"] is None


def test_book_refresh_counted_in_scan(tmp_path, monkeypatch):
    p = tmp_path / "b.jsonl"
    p.write_text('{"s": 1, "b": [[1, 1]], "a": [[2, 1]]}\n')
    monkeypatch.setattr(pf, "_BOOK_TAIL", pf.BookTail(str(p)))
    ns = _ns()
    ns["_exit_scan_begin"]("POSITION_MANAGER", 1.0)
    pf._BOOK_TAIL.at(5.0)
    st = ns["_exit_scan_stamp"]("POSITION_MANAGER")
    assert st["exit_book_refreshes_in_scan"] == 1 and st["exit_book_refresh_ms"] >= 0.0


def test_stamps_wired_into_trigger_sites_event_and_event_timestamps():
    assert SOURCE.count("pos.update(_ess(") >= 3
    assert 'for k in globals().get("EXIT_SCAN_STAMP_KEYS", ())}' in SOURCE
    i = SOURCE.index('_emit_genome_execution_event("EXIT_TRIGGERED"')
    assert "EXIT_SCAN_STAMP_KEYS" in SOURCE[i:i + 800]


def test_runtime_telemetry_thread_names_and_top_cpu():
    ev = threading.Event()
    t = threading.Thread(target=ev.wait, name="probe-thread-xyz", daemon=True)
    t.start()
    try:
        names = rt.thread_names()
        assert names.get(str(t.native_id)) == "probe-thread-xyz"
        prev = {"1": 10, "2": 5}
        rows = rt.top_threads(prev, {"1": 110, "2": 5, "3": 1}, {"1": "protective-exit"}, 1.0)
        assert rows[0]["tid"] == 1 and rows[0]["name"] == "protective-exit" and rows[0]["cpu_pct"] > 0
    finally:
        ev.set()


def test_lag_probe_keeps_last():
    lp = rt.LagProbe()
    lp.record(3.0)
    lp.record(7.25)
    assert lp.last_ms == 7.25
