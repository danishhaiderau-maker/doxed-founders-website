import json
import subprocess
import threading
import time
from pathlib import Path

import lifecycle_pipeline_runtime as runtime_module
from lifecycle_pipeline_runtime import LifecyclePipelineRuntime

REVISION = "a" * 40


def _runtime(tmp_path, **kwargs):
    return LifecyclePipelineRuntime(
        tmp_path, source_revision=REVISION, interval_sec=999,
        wall_timeout_sec=2, **kwargs,
    )


class _Process:
    def __init__(self, *, return_code=0, timeout=False):
        self.return_code = return_code
        self.timeout = timeout
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        if self.timeout and not self.terminated and not self.killed:
            raise subprocess.TimeoutExpired("worker", timeout)
        return self.return_code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def _install_launch(monkeypatch, runtime, process, *, corrupt=False, pipeline=None, emergency_wal=None):
    def launch(command):
        request = Path(command[command.index("--request") + 1])
        result = Path(command[command.index("--result") + 1])
        nonce = command[command.index("--nonce") + 1]
        if corrupt:
            result.write_text("not-json", encoding="utf-8")
        elif process.return_code == 0 and not process.timeout:
            request_payload = json.loads(request.read_text(encoding="utf-8"))
            payload = {
                "schema": "lifecycle_pipeline_worker_result_v1", "nonce": nonce,
                "source_revision": REVISION, "launched_unix": request_payload["launched_unix"],
                "started_unix": 1, "generated_unix": 2, "generated_at": "x",
                "request_sha256": runtime_module.hashlib.sha256(request.read_bytes()).hexdigest(),
                "pipeline": pipeline or {
                    "candidate_count": 1, "bundle_count": 1,
                    "pressure_mode": request_payload["pressure_mode"],
                    "emergency_closure_mode": request_payload.get("emergency_closure_mode", False),
                },
                "hard_runtime_result_deadline_enforced": True,
                "source_cleanup_authorized": False,
                "emergency_wal": emergency_wal,
            }
            payload["result_sha256"] = runtime_module.hashlib.sha256(
                json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest()
            result.write_text(json.dumps(payload), encoding="utf-8")
        return process
    monkeypatch.setattr(runtime, "_launch", launch)


def test_success_resets_backoff_and_pressure_clamps_to_one(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, pressure_probe=lambda: {"pressure": True})
    runtime._status.update({"failure_count": 4, "backoff_sec": 1440})
    _install_launch(monkeypatch, runtime, _Process())
    assert runtime._run_once() is True
    status = runtime.status()
    assert status["source_revision"] == REVISION
    assert status["failure_count"] == 0
    assert status["backoff_sec"] == 0
    assert status["last_result"]["pressure_mode"] is True
    assert status["source_cleanup_authorized"] is False


def test_success_retains_only_bounded_emergency_wal_observability(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    generation = "12345678-1234-1234-1234-123456789abc"
    emergency_wal = {
        "action": {"replayed": True, "generation": generation, "state": "REPLAYED"},
        "status": {
            "observed_unix": 123.5,
            "identity": {
                "epoch_id": "epoch-test", "source_revision": REVISION,
                "deployed_revision": REVISION, "tile_config_signature": "b" * 64,
            },
            "identity_sha256": "c" * 64, "capacity_extents": 4,
            "free_extents": 3, "retained_count": 1, "retained_bytes": 100,
            "state_counts": {"PREPARED": 0, "DEFERRED": 0, "REPLAYED": 1},
            "oldest_generation": generation, "oldest_state": "REPLAYED",
            "alarms": [], "records": [{"payload": "must-not-survive"}],
        },
    }
    _install_launch(monkeypatch, runtime, _Process(), emergency_wal=emergency_wal)
    assert runtime._run_once() is True
    retained = runtime.status()["last_result"]["emergency_wal"]
    assert retained["capacity_extents"] == 4
    assert retained["last_action"] == {
        "replayed": True, "generation": generation, "state": "REPLAYED",
    }
    assert retained["event_counts"] == {"replayed": 1, "released": 0}
    assert "records" not in retained
    assert "payload" not in repr(retained)

    emergency_wal["action"] = {"released": True, "generation": generation}
    emergency_wal["status"].update({
        "free_extents": 4, "retained_count": 0, "retained_bytes": 0,
        "state_counts": {"PREPARED": 0, "DEFERRED": 0, "REPLAYED": 0},
        "oldest_generation": None, "oldest_state": None,
    })
    _install_launch(monkeypatch, runtime, _Process(), emergency_wal=emergency_wal)
    assert runtime._run_once() is True
    assert runtime.status()["last_result"]["emergency_wal"]["event_counts"] == {
        "replayed": 1, "released": 1,
    }


def test_success_promptly_continues_until_dirty_and_cursor_backlog_are_drained(
    tmp_path, monkeypatch,
):
    clock = lambda: 1000.0
    runtime = LifecyclePipelineRuntime(
        tmp_path, source_revision=REVISION, interval_sec=999,
        wall_timeout_sec=2, clock=clock,
    )
    pipeline = {
        "candidate_count": 5,
        "bundle_count": 0,
        "pressure_mode": False,
        "scan": {
            "pending_dirty_lifecycles": 4045,
            "caught_up": False,
            "ledgers": {"lifecycle": {"caught_up": False}},
        },
    }
    _install_launch(monkeypatch, runtime, _Process(), pipeline=pipeline)

    assert runtime._run_once() is True
    status = runtime.status()
    assert status["next_run_unix"] == 1000.0 + runtime_module.BACKLOG_INTERVAL_SEC
    assert status["last_result"]["pending_dirty_lifecycles"] == 4045
    assert status["last_result"]["backlog_pending"] is True


def test_success_returns_to_normal_cadence_only_after_backlog_is_drained(
    tmp_path, monkeypatch,
):
    clock = lambda: 2000.0
    runtime = LifecyclePipelineRuntime(
        tmp_path, source_revision=REVISION, interval_sec=999,
        wall_timeout_sec=2, clock=clock,
    )
    pipeline = {
        "candidate_count": 0,
        "bundle_count": 0,
        "pressure_mode": False,
        "scan": {
            "pending_dirty_lifecycles": 0,
            "caught_up": True,
            "ledgers": {"lifecycle": {"caught_up": True}},
        },
    }
    _install_launch(monkeypatch, runtime, _Process(), pipeline=pipeline)

    assert runtime._run_once() is True
    status = runtime.status()
    assert status["next_run_unix"] == 2999.0
    assert status["last_result"]["backlog_pending"] is False


def test_timeout_terminates_child_and_backs_off(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    process = _Process(timeout=True)
    _install_launch(monkeypatch, runtime, process)
    assert runtime._run_once() is False
    assert process.terminated is True
    assert runtime.status()["last_outcome"] == "TIMEOUT"
    assert runtime.status()["backoff_sec"] == 180


def test_cleanup_lease_excludes_worker_cycle_without_calling_overlap_probe(tmp_path, monkeypatch):
    overlap_calls = []
    runtime = _runtime(tmp_path, overlap_probe=lambda: overlap_calls.append(True) or False)
    _install_launch(monkeypatch, runtime, _Process())
    assert runtime.acquire_cleanup_lease(timeout=0.0) is True
    result = []
    contender = threading.Thread(target=lambda: result.append(runtime.acquire_cleanup_lease(timeout=0.0)))
    contender.start(); contender.join(timeout=1)
    assert result == [False]
    assert overlap_calls == []
    runtime.release_cleanup_lease()
    assert runtime._run_once() is True
    assert overlap_calls == [True]


def test_corrupt_result_fails_closed_and_backoff_is_bounded(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    for expected in (180, 360, 720, 1440, 1800, 1800):
        _install_launch(monkeypatch, runtime, _Process(), corrupt=True)
        assert runtime._run_once() is False
        assert runtime.status()["backoff_sec"] == expected
        assert runtime.status()["source_cleanup_authorized"] is False
        assert not list(runtime.work_root.glob("pipeline-request-*.json"))
        assert not list(runtime.work_root.glob("pipeline-result-*.json"))


def test_classified_worker_failure_retains_only_canonical_diagnostics(tmp_path):
    runtime = _runtime(tmp_path)
    runtime._record_failure("WORKER_FAILED", "sanitized", worker_failure={
        "error_class": "ValueError", "error_code": "INVALID_JSONL_ROW",
        "ledger": "lifecycle", "byte_offset": 1406,
    })
    assert runtime.status()["last_worker_failure"] == {
        "error_class": "ValueError", "error_code": "INVALID_JSONL_ROW",
        "ledger": "lifecycle", "byte_offset": 1406,
    }


def test_runtime_drops_noncanonical_ledger_and_invalid_offset(tmp_path):
    runtime = _runtime(tmp_path)
    runtime._record_failure("WORKER_FAILED", "sanitized", worker_failure={
        "error_class": "ValueError", "error_code": "WORKER_PIPELINE_FAILED",
        "ledger": "../../secret", "byte_offset": -1,
    })
    assert runtime.status()["last_worker_failure"] == {
        "error_class": "ValueError", "error_code": "WORKER_PIPELINE_FAILED",
    }


def test_worker_failure_receipt_is_retained_as_bounded_status_then_cleaned(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    process = _Process(return_code=1)

    def launch(command):
        request = Path(command[command.index("--request") + 1])
        result = Path(command[command.index("--result") + 1])
        nonce = command[command.index("--nonce") + 1]
        request_payload = json.loads(request.read_text(encoding="utf-8"))
        payload = {
            "schema": "lifecycle_pipeline_worker_result_v1",
            "status": "FAILED",
            "nonce": nonce,
            "source_revision": REVISION,
            "launched_unix": request_payload["launched_unix"],
            "started_unix": 1,
            "generated_unix": 2,
            "generated_at": "x",
            "request_sha256": runtime_module.hashlib.sha256(request.read_bytes()).hexdigest(),
            "failure": {
                "error_class": "RuntimeError",
                "error_code": "WORKER_PIPELINE_FAILED",
            },
            "source_cleanup_authorized": False,
        }
        unsigned = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        payload["result_sha256"] = runtime_module.hashlib.sha256(unsigned).hexdigest()
        result.write_text(json.dumps(payload), encoding="utf-8")
        return process

    monkeypatch.setattr(runtime, "_launch", launch)
    assert runtime._run_once() is False
    status = runtime.status()
    assert status["last_outcome"] == "WORKER_FAILED"
    assert status["last_worker_failure"] == {
        "error_class": "RuntimeError",
        "error_code": "WORKER_PIPELINE_FAILED",
    }
    assert status["last_worker_failure_unix"] is not None
    assert "RuntimeError" not in status["last_error"]
    assert not list(runtime.work_root.glob("pipeline-request-*.json"))
    assert not list(runtime.work_root.glob("pipeline-result-*.json"))


def test_worker_failure_receipt_survives_later_overlap_skip(tmp_path, monkeypatch, caplog):
    runtime = _runtime(tmp_path)
    process = _Process(return_code=7)

    def launch(command):
        request = Path(command[command.index("--request") + 1])
        result = Path(command[command.index("--result") + 1])
        nonce = command[command.index("--nonce") + 1]
        request_payload = json.loads(request.read_text(encoding="utf-8"))
        payload = {
            "schema": "lifecycle_pipeline_worker_result_v1", "status": "FAILED",
            "nonce": nonce, "source_revision": REVISION,
            "launched_unix": request_payload["launched_unix"],
            "started_unix": 1, "generated_unix": 2, "generated_at": "x",
            "request_sha256": runtime_module.hashlib.sha256(request.read_bytes()).hexdigest(),
            "failure": {"error_class": "RuntimeError", "error_code": "WORKER_PIPELINE_FAILED"},
            "source_cleanup_authorized": False,
        }
        unsigned = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        payload["result_sha256"] = runtime_module.hashlib.sha256(unsigned).hexdigest()
        result.write_text(json.dumps(payload), encoding="utf-8")
        return process

    monkeypatch.setattr(runtime, "_launch", launch)
    with caplog.at_level("ERROR"):
        assert runtime._run_once() is False
    retained = runtime.status()["last_worker_failure_unix"]
    runtime.overlap_probe = lambda: "SYNC_ACTIVE"
    assert runtime._run_once() is False
    status = runtime.status()
    assert status["last_outcome"] == "OVERLAP_SKIPPED"
    assert status["last_worker_failure"] == {
        "error_class": "RuntimeError", "error_code": "WORKER_PIPELINE_FAILED",
    }
    assert status["last_worker_failure_unix"] == retained
    assert "class=RuntimeError code=WORKER_PIPELINE_FAILED" in caplog.text


def test_overlap_skips_but_emergency_launches_bounded_closure_worker(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, overlap_probe=lambda: "SYNC_ACTIVE")
    monkeypatch.setattr(runtime, "_launch", lambda _command: (_ for _ in ()).throw(AssertionError()))
    assert runtime._run_once() is False
    assert runtime.status()["last_outcome"] == "OVERLAP_SKIPPED"

    runtime.overlap_probe = lambda: False
    runtime.pressure_probe = lambda: {"pressure": True, "emergency": True}
    _install_launch(monkeypatch, runtime, _Process())
    assert runtime._run_once() is True
    assert runtime.status()["last_outcome"] == "SUCCESS"
    assert runtime.status()["emergency"] is True
    assert runtime.status()["last_result"]["pressure_mode"] is True
    assert runtime.status()["last_result"]["emergency_closure_mode"] is True


def test_duplicate_owner_rejected_and_stop_releases_owner(tmp_path):
    first = _runtime(tmp_path, overlap_probe=lambda: "HOLD")
    second = _runtime(tmp_path, overlap_probe=lambda: "HOLD")
    assert first.start() is True
    try:
        assert second.start() is False
        assert second.status()["last_outcome"] == "DUPLICATE_OWNER_REJECTED"
    finally:
        assert first.stop() is True
    assert not first.owner_path.exists()


def test_pid_alive_probe_never_terminates_current_windows_process():
    assert runtime_module._pid_alive(runtime_module.os.getpid()) is True
    if runtime_module.os.name == "nt":
        assert runtime_module._pid_alive(2_147_483_647) is False


def test_stop_interrupts_active_child_without_propagating(tmp_path):
    runtime = _runtime(tmp_path)
    process = _Process(timeout=True)
    with runtime._lock:
        runtime._process = process
    assert runtime.stop() is True
    assert process.terminated is True


def test_minimal_environment_excludes_credentials(monkeypatch):
    monkeypatch.setenv("BITFINEX_API_SECRET", "forbidden")
    monkeypatch.setenv("DATABASE_URL", "forbidden")
    environment = runtime_module._minimal_worker_environment("a" * 40)
    assert "BITFINEX_API_SECRET" not in environment
    assert "DATABASE_URL" not in environment
    assert environment["PYTHONIOENCODING"] == "utf-8"
    assert environment["SOURCE_GIT_REV"] == "a" * 40
    assert "SOURCE_GIT_REV" not in runtime_module._minimal_worker_environment("short")


def test_revision_mismatch_fails_closed_and_capability_is_truthful(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    _install_launch(monkeypatch, runtime, _Process())
    original = runtime_module.verify_result
    monkeypatch.setattr(
        runtime_module, "verify_result",
        lambda *args: {**original(*args), "source_revision": "b" * 40},
    )
    assert runtime._run_once() is False
    assert "SOURCE_REVISION_MISMATCH" in runtime.status()["last_error"]
    limits = runtime.status()["resource_limits"]
    assert limits["parent_wall_timeout_enforced"] is True
    assert limits["cpu_rlimit_enforced"] is (runtime_module.os.name == "posix")
    assert limits["rss_rlimit_enforced"] is (runtime_module.os.name == "posix")


def test_requires_full_revision_and_rejects_symlink_root(tmp_path):
    try:
        _runtime(tmp_path, source_revision="short")
    except TypeError:
        # _runtime supplies its own revision; directly test the public class.
        pass
    try:
        LifecyclePipelineRuntime(tmp_path, source_revision="short")
    except ValueError as exc:
        assert str(exc) == "SOURCE_REVISION_NOT_FULL_HEX"
    else:
        raise AssertionError("short revision accepted")
    link = tmp_path.parent / f"{tmp_path.name}-link"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        return
    try:
        LifecyclePipelineRuntime(link, source_revision=REVISION)
    except ValueError as exc:
        assert str(exc) == "DATA_ROOT_LINKED_OR_INVALID"
    else:
        raise AssertionError("symlink root accepted")
