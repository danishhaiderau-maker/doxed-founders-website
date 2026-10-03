"""Replay capacity eviction priority, HTTP overload rejection counters, PIPELINE_ERROR crash site."""
import ast
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict

BOT = Path(__file__).with_name("bot.py")
TREE = ast.parse(BOT.read_text(encoding="utf-8"))


def load(names, ns):
    nodes = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot.py", "exec"), ns)
    return ns


def replay_ns(buffers=None, cap=3):
    return load(["_replay_eviction_class", "replay_capacity_evictions", "replay_buffer_status"], {
        "Dict": Dict, "replay_lock": threading.Lock(), "replay_buffers": buffers or {},
        "MAX_REPLAY_BUFFERS": cap, "_REPLAY_CAPACITY_EVICTIONS": {},
        "_REPLAY_EVICTION_PRIORITY": {"shadow": 0, "executed": 1, "executed_post_exit": 2},
    })


def test_capacity_eviction_drops_shadows_before_executed_post_exit_replays():
    buffers = {
        "exec_post_old": {"lane": "executed", "post_exit": True, "start_ts": 1},
        "exec_live": {"lane": "executed", "start_ts": 2},
        "shadow_new": {"lane": "R2_SHADOW", "start_ts": 9},
        "shadow_old": {"lane": "R2_SHADOW", "start_ts": 3},
        "exec_post_new": {"lane": "executed", "post_exit": True, "start_ts": 8},
    }
    evict = replay_ns()["replay_capacity_evictions"]
    assert evict(buffers, [], 3) == ["shadow_old", "shadow_new"]
    assert evict(buffers, [], 1) == ["shadow_old", "shadow_new", "exec_live", "exec_post_old"]


def test_already_expired_buffers_count_toward_the_excess():
    buffers = {k: {"lane": "R2_SHADOW", "start_ts": i} for i, k in enumerate("abcd")}
    evict = replay_ns()["replay_capacity_evictions"]
    assert evict(buffers, ["a"], 3) == []
    assert evict(buffers, ["a"], 2) == ["b"]


def test_replay_buffer_status_reports_active_classes_and_executed_evictions():
    buffers = {
        "x": {"lane": "executed", "post_exit": True},
        "y": {"lane": "R2_SHADOW"},
    }
    ns = replay_ns(buffers, cap=100)
    ns["_REPLAY_CAPACITY_EVICTIONS"].update({"shadow": 5, "executed_post_exit": 2})
    status = ns["replay_buffer_status"]()
    assert status["active"] == 2 and status["cap"] == 100
    assert status["active_by_class"] == {"executed_post_exit": 1, "shadow": 1}
    assert status["executed_capacity_evictions"] == 2


def test_overload_rejections_are_counted_by_cap_and_reason():
    ns = load(["_record_overload_rejection", "_dashboard_handler_snapshot"], {
        "time": time, "_dashboard_handler_lock": threading.Lock(),
        "_dashboard_active_handlers": {}, "_dashboard_overload_rejections": {},
        "_DASHBOARD_TELEMETRY_STATIC_ROUTES": set(),
    })
    ns["_record_overload_rejection"]("dispatch", "cap_full", now=10)
    ns["_record_overload_rejection"]("dispatch", "cap_full", now=11)
    ns["_record_overload_rejection"]("heavy", "admission_timeout", now=12)
    snap = ns["_dashboard_handler_snapshot"](now=0)
    assert snap["rejected_total"] == 3
    assert snap["rejected_by_cap"]["dispatch"] == {"total": 2, "by_reason": {"cap_full": 2}, "last_ts": 11.0}
    assert snap["rejected_by_cap"]["heavy"]["by_reason"] == {"admission_timeout": 1}


def test_pipeline_error_records_innermost_crash_site():
    ns = load(["_exception_site", "_record_pipeline_error", "pipeline_error_status"], {
        "os": os, "time": time, "traceback": traceback, "Dict": Dict, "Any": Any,
        "_PIPELINE_ERRORS_LOCK": threading.Lock(),
        "_PIPELINE_ERRORS": {"total": 0, "by_site": {}, "last": None},
    })

    def mutate_while_iterating():
        d = {"a": 1}
        for k in d:
            d["b"] = 2

    try:
        mutate_while_iterating()
    except RuntimeError as exc:
        site = ns["_exception_site"](exc)
        ns["_record_pipeline_error"]("R2", exc, site, now=5)
    assert site.startswith("test_replay_eviction_overload_pipeline_site.py:")
    assert site.endswith(":mutate_while_iterating")
    status = ns["pipeline_error_status"]()
    assert status["total"] == 1 and status["by_site"] == {site: 1}
    assert status["last"]["error_type"] == "RuntimeError" and status["last"]["lane"] == "R2"
    assert ns["_exception_site"](ValueError("no tb")) == "unknown"


def test_runtime_status_exposes_replay_and_pipeline_telemetry():
    fn = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_runtime_blindspot_status_fields")
    src = ast.get_source_segment(BOT.read_text(encoding="utf-8"), fn)
    assert '"replay_buffers": replay_buffer_status' in src
    assert '"pipeline_errors": pipeline_error_status' in src
