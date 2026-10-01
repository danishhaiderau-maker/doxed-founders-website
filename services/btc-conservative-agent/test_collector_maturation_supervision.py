"""Collector maturation worker supervision, truthful stall alarms and late-backfill labels.

Fly 2026-10-01: the full-history V2->V3 terminal reconcile (1.17 GB of V2
rows) ran inline on the maturation worker every 900 s and took ~900 s, so
the worker matured ~1 row per pass, the in-memory tape index went stale and
both MULTIVERSE_TAPE_SOURCE_UNAVAILABLE and COLLECTOR_MATURATION_WORKER_STALLED
fired while the 1s tape itself was healthy.
"""
from __future__ import annotations

import ast
import collections
import sys
import threading
import time
from pathlib import Path

import pytest

BOT = Path(__file__).resolve().parent / "bot.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fly_monitor_alerts as alerts  # noqa: E402
import fly_monitor_rules as rules  # noqa: E402


def _bot_tree():
    return ast.parse(BOT.read_text(encoding="utf-8"))


def _bot_function(name: str):
    return next(node for node in _bot_tree().body if isinstance(node, ast.FunctionDef) and node.name == name)


def _load(names, namespace):
    """Compile selected top-level bot.py functions into ``namespace``."""
    tree = _bot_tree()
    wanted = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in wanted} == set(names)
    module = ast.Module(body=wanted, type_ignores=[])
    exec(compile(module, str(BOT), "exec"), namespace)
    return namespace


class _Log:
    def __init__(self):
        self.lines = []

    def error(self, message):
        self.lines.append(("error", message))

    warning = info = error


# ------------------------------------------------------------ supervision
def _supervisor_namespace():
    ns = {
        "threading": threading, "time": time, "logger": _Log(),
        "shutdown_event": threading.Event(),
        "COLLECTOR_WORKER_RESTART_MIN_INTERVAL_SEC": 30.0,
        "_collector_worker_threads": {}, "_collector_worker_threads_lock": threading.Lock(),
    }
    runs = collections.Counter()

    def dies():
        runs["maturation"] += 1
        raise RuntimeError("boom")

    def stays():
        runs["reconcile"] += 1
        ns["shutdown_event"].wait(5)

    ns["maturation_status"] = {"alive": False, "restarts": 0, "exit_error": "RuntimeError: boom"}
    ns["reconcile_status"] = {"alive": False, "restarts": 0}
    ns["_COLLECTOR_WORKERS"] = {
        "collector-maturation": (dies, ns["maturation_status"]),
        "collector-v3-reconcile": (stays, ns["reconcile_status"]),
    }
    _load(["_start_collector_worker", "supervise_collector_workers"], ns)
    return ns, runs


def test_supervisor_restarts_dead_worker_with_backoff_and_leaves_live_one(monkeypatch):
    ns, runs = _supervisor_namespace()
    monkeypatch.setattr(threading, "excepthook", lambda _args: None)
    ns["_start_collector_worker"]("collector-maturation")
    ns["_start_collector_worker"]("collector-v3-reconcile")
    ns["_collector_worker_threads"]["collector-maturation"].join(2)
    try:
        restarted = ns["supervise_collector_workers"](now=1000.0)
        assert restarted == ["collector-maturation"]
        status = ns["maturation_status"]
        assert status["restarts"] == 1
        assert status["last_restart_ts"] == 1000.0
        assert status["last_restart_reason"] == "RuntimeError: boom"
        assert any("restarting" in line for _level, line in ns["logger"].lines)
        ns["_collector_worker_threads"]["collector-maturation"].join(2)
        assert runs["maturation"] == 2
        # Backoff: a worker that dies again is not hot-looped.
        assert ns["supervise_collector_workers"](now=1010.0) == []
        assert ns["supervise_collector_workers"](now=1031.0) == ["collector-maturation"]
        assert runs["reconcile"] == 1
    finally:
        ns["shutdown_event"].set()
    assert ns["supervise_collector_workers"](now=2000.0) == []


def test_bot_starts_both_workers_and_supervises_from_main_loop():
    """Collector workers moved out of main()'s literal Thread manifest
    (cleanup_characterization_contract.fixture) into the supervised starter:
    unwrapped by safe_thread (exceptions contained, a death is restarted,
    never a trading-thread crash) and daemon."""
    main = ast.unparse(_bot_function("main"))
    assert "_start_collector_worker('collector-maturation')" in main
    assert "_start_collector_worker('collector-v3-reconcile')" in main
    assert "supervise_collector_workers()" in main
    starter = _bot_function("_start_collector_worker")
    thread_call = next(
        node for node in ast.walk(starter)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "threading.Thread"
    )
    keywords = {kw.arg: ast.unparse(kw.value) for kw in thread_call.keywords}
    assert keywords == {"target": "target", "name": "name", "daemon": "True"}
    workers = next(
        node for node in _bot_tree().body
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_COLLECTOR_WORKERS"
    )
    text = ast.unparse(workers)
    assert "collector_maturation_worker_loop" in text and "collector_v3_reconcile_loop" in text


# ------------------------------------------------- reconcile off the worker
def test_v3_reconcile_never_runs_on_the_maturation_worker_while_owner_alive():
    drain = ast.unparse(_bot_function("_maybe_complete_pending_order_multiverse"))
    assert "reconcile_terminal_v22_into_v3" not in drain
    assert "if not _collector_v3_reconcile_status.get('alive'):" in drain
    assert "_run_v3_terminal_reconcile(now)" in drain
    worker = ast.unparse(_bot_function("collector_maturation_worker_loop"))
    assert "_run_v3_terminal_reconcile" not in worker
    owner = ast.unparse(_bot_function("collector_v3_reconcile_loop"))
    assert "_run_v3_terminal_reconcile(" in owner
    run = ast.unparse(_bot_function("_run_v3_terminal_reconcile"))
    for needle in ("cursor=_collector_v3_reconcile_cursor", "row_lock=_collector_epoch_lock",
                   "still_current=", "COLLECTOR_V3_TERMINAL_RECONCILE_INTERVAL_SEC"):
        assert needle in run


def test_reconcile_poll_is_incremental_and_records_status(monkeypatch, tmp_path):
    calls = []

    def fake_reconcile(**kwargs):
        calls.append(kwargs)
        kwargs["cursor"]["seen"] = True
        return {"passed": True, "scanned": 3, "skipped_bytes": 10, "backfilled": 0}

    fake_bridge = type(sys)("research_v3_bridge")
    fake_bridge.reconcile_terminal_v22_into_v3 = fake_reconcile
    monkeypatch.setitem(sys.modules, "research_v3_bridge", fake_bridge)
    monkeypatch.chdir(tmp_path)
    signatures = iter([("gen", 1), ("gen", 1), ("gen", 2)])
    ns = {
        "time": time, "os": __import__("os"), "logger": _Log(),
        "COLLECTOR_V3_TERMINAL_RECONCILE_INTERVAL_SEC": 900.0,
        "_v3_terminal_reconcile_last_ts": 0.0, "_v3_terminal_reconcile_last_v22_size": -1,
        "_collector_v3_reconcile_status": {"runs": 0}, "_collector_v3_reconcile_cursor": {},
        "_collector_epoch_lock": threading.Lock(),
        "_collector_v22_epoch_id": lambda: "epoch-x",
        "research_event_generation_stat_signature": lambda _root: next(signatures),
    }
    _load(["_collector_worker_phase", "_run_v3_terminal_reconcile"], ns)
    ns["_run_v3_terminal_reconcile"](now=1000.0)
    assert len(calls) == 1 and calls[0]["epoch_id"] == "epoch-x"
    assert calls[0]["cursor"] is ns["_collector_v3_reconcile_cursor"]
    assert calls[0]["still_current"]() is True
    status = ns["_collector_v3_reconcile_status"]
    assert status["runs"] == 1 and status["last_passed"] is True and status["phase"] == "IDLE"
    ns["_run_v3_terminal_reconcile"](now=1500.0)  # inside interval
    ns["_run_v3_terminal_reconcile"](now=2000.0)  # unchanged V2 signature
    assert len(calls) == 1
    ns["_run_v3_terminal_reconcile"](now=3000.0)
    assert len(calls) == 2


# --------------------------------------------------------- health alarms
class _Store:
    def __init__(self, status):
        self._status = status

    def status(self, _now):
        return dict(self._status)


def _health_namespace(*, tape_status, worker, reconcile=None, pending=3):
    ns = {
        "time": time, "threading": threading,
        "COLLECTION_HEALTH_WINDOW_SEC": 3600.0, "COLLECTION_EMPTY_PATH_ALARM_RATE": 0.2,
        "COLLECTION_EMPTY_PATH_ALARM_MIN_ROWS": 5, "COLLECTION_TOUCH_GRID_ALARM_COVERAGE": 0.9,
        "COLLECTION_TOUCH_GRID_ALARM_MIN_CALLS": 3, "COLLECTOR_TAPE_REFRESH_FRESH_SEC": 120.0,
        "COLLECTOR_WORKER_RESTART_ALARM_SEC": 3600.0, "COLLECTOR_LATE_MATURATION_SEC": 3600.0,
        "_collection_stats_lock": threading.Lock(), "_collection_counters": collections.Counter(),
        "_collection_multiverse_recent": collections.deque(), "_collection_touch_grid_recent": collections.deque(),
        "_collector_tape_store_obj": _Store(tape_status),
        "_order_multiverse_pending_src": {f"id-{i}": {} for i in range(pending)},
        "_collector_maturation_worker_status": worker,
        "_collector_v3_reconcile_status": reconcile or {},
    }
    return _load(["research_collection_health"], ns)


NOW = 1_790_892_500.0


def test_stale_index_from_blocked_worker_is_a_worker_stall_not_a_tape_outage():
    """Fly 22:08Z: index refreshed 400 s ago, tape file healthy, worker mid-pass."""
    ns = _health_namespace(
        tape_status={"initial_scan_complete": True, "latest_bucket_age_sec": 441.4,
                     "last_refresh_ts": NOW - 400},
        worker={"alive": True, "last_pass_ts": NOW - 400, "phase": "MATURATION"},
    )
    health = ns["research_collection_health"](NOW)
    assert health["alarms"] == ["COLLECTOR_MATURATION_WORKER_STALLED"]
    assert health["tape_source"]["last_refresh_age_sec"] == 400.0
    finding = rules.collection_findings({"research_collection": health})
    assert "phase='MATURATION'" in finding["multiverse_worker_stalled"]


def test_freshly_refreshed_but_old_tape_is_a_real_source_outage():
    ns = _health_namespace(
        tape_status={"initial_scan_complete": True, "latest_bucket_age_sec": 900.0,
                     "last_refresh_ts": NOW - 10},
        worker={"alive": True, "last_pass_ts": NOW - 10},
    )
    assert ns["research_collection_health"](NOW)["alarms"] == ["MULTIVERSE_TAPE_SOURCE_UNAVAILABLE"]


def test_healthy_worker_and_tape_report_ok_with_reconcile_status():
    ns = _health_namespace(
        tape_status={"initial_scan_complete": True, "latest_bucket_age_sec": 3.0, "last_refresh_ts": NOW - 4},
        worker={"alive": True, "last_pass_ts": NOW - 4},
        reconcile={"alive": True, "phase": "RECONCILING", "runs": 0},
    )
    health = ns["research_collection_health"](NOW)
    assert health["status"] == "OK" and health["alarms"] == []
    assert health["multiverse"]["v3_reconcile_worker"]["phase"] == "RECONCILING"


def test_recent_restart_raises_restart_alarm_for_an_hour():
    tape = {"initial_scan_complete": True, "latest_bucket_age_sec": 3.0, "last_refresh_ts": NOW - 4}
    worker = {"alive": True, "last_pass_ts": NOW - 4, "restarts": 1, "last_restart_ts": NOW - 120,
              "last_restart_reason": "RuntimeError: boom"}
    health = _health_namespace(tape_status=tape, worker=worker)["research_collection_health"](NOW)
    assert health["alarms"] == ["COLLECTOR_MATURATION_WORKER_RESTARTED"]
    finding = rules.collection_findings({"research_collection": health})
    assert "restarts=1" in finding["multiverse_worker_restarted"]
    assert "multiverse_worker_restarted" in alerts.POLICIES
    worker["last_restart_ts"] = NOW - 4000
    assert _health_namespace(tape_status=tape, worker=worker)["research_collection_health"](NOW)["alarms"] == []


# ------------------------------------------------------- late backfill label
@pytest.mark.parametrize("lag,late", [(0, False), (3500, False), (3700, True), (6 * 3600, True)])
def test_maturation_lag_fields_label_late_backfill(lag, late):
    ns = _load(["collector_maturation_lag_fields"], {
        "COLLECTOR_TAPE_LIVE_LAG_SEC": 60.0, "COLLECTOR_LATE_MATURATION_SEC": 3600.0,
    })
    fields = ns["collector_maturation_lag_fields"](10_000.0, now=10_060.0 + lag)
    assert fields["maturation_lag_sec"] == float(lag)
    assert fields["late_backfill"] is late
    assert fields["late_backfill_reason"] == ("MATURED_LATE_FROM_RETAINED_TAPE" if late else None)


def test_path_source_carries_maturation_lag_fields():
    source = ast.unparse(_bot_function("_collector_path_candles_1m"))
    assert "**collector_maturation_lag_fields(end, now=now)" in source
