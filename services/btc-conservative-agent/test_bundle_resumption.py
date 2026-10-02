import ast
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

import data_sync_bundle_resumption as resume
import data_sync_bundle_runtime as runtime
from data_sync_bundle_worker import run_bundle_worker
from test_data_sync_bundle_worker import _fixture, _row


class Stop:
    def __init__(self, interrupt=False): self.waits = []; self.stopped = False; self.interrupt = interrupt
    def is_set(self): return self.stopped
    def wait(self, seconds):
        self.waits.append(seconds)
        self.stopped = self.interrupt
        return self.stopped


@pytest.mark.parametrize("error", sorted(resume.RETRYABLE))
def test_only_budget_deferral_retries_frozen_generation_with_unchanged_limits(error):
    calls = []; stop = Stop(); metadata = {"generation_id": "a", "nested": {"identity": "original"}}
    def runner(meta, source, output, **kwargs):
        assert meta == metadata
        assert kwargs["max_slices"] == 7 and kwargs["max_seconds"] == 11
        assert kwargs["stop_event"] is stop
        meta["nested"]["identity"] = "modified"
        calls.append(1)
        return {"status": "DEFERRED", "error": error} if len(calls) == 1 else {"status": "COMPLETE"}
    result = resume.run_resumable_generation(metadata, "src", "out", pressure_probe=lambda: {},
        generation_available=lambda _: True, stop_event=stop, max_slices=7, max_seconds=11, attempt_runner=runner)
    assert result["status"] == "COMPLETE" and len(calls) == 2 and stop.waits == [3]
    assert metadata["nested"]["identity"] == "original"


@pytest.mark.parametrize("status,error", [("FAILED", "BUNDLE_COORDINATOR_BUDGET"),
    ("DEFERRED", "BUNDLE_NO_CURSOR_PROGRESS"), ("DEFERRED", "GENERATION_AUTHORITY_UNAVAILABLE"),
    ("DEFERRED", "BUNDLE_ADMISSION_UNAVAILABLE"), ("DEFERRED", "RESOURCE_PRESSURE"),
    ("FAILED", "BUNDLE_CIRCUIT_OPEN"), ("STOPPED", None), ("COMPLETE", None)])
def test_other_terminal_results_never_retry(status, error):
    calls = []; stop = Stop(); receipt = {"status": status, "error": error}
    def runner(*args, **kwargs): calls.append(1); return receipt
    assert resume.run_resumable_generation({}, "s", "o", pressure_probe=lambda: {},
        generation_available=lambda _: True, stop_event=stop, attempt_runner=runner) == receipt
    assert calls == [1] and stop.waits == []


def test_attempt_cap_and_backoff_cap():
    calls = []; stop = Stop()
    def runner(*args, **kwargs): calls.append(1); return {"status": "DEFERRED", "error": "BUNDLE_COORDINATOR_BUDGET"}
    result = resume.run_resumable_generation({}, "s", "o", pressure_probe=lambda: {},
        generation_available=lambda _: True, stop_event=stop, max_attempts=8, attempt_runner=runner)
    assert result["status"] == "DEFERRED" and len(calls) == 8
    assert stop.waits == [3, 6, 12, 24, 30, 30, 30]


def test_stop_interrupts_backoff_and_prevents_next_attempt():
    stop = Stop(interrupt=True); calls = []
    def runner(*args, **kwargs): calls.append(1); return {"status": "DEFERRED", "error": "BUNDLE_COORDINATOR_BUDGET"}
    args = dict(pressure_probe=lambda: {}, generation_available=lambda _: True, stop_event=stop, attempt_runner=runner)
    assert resume.run_resumable_generation({}, "s", "o", **args) == {"status": "STOPPED"}
    assert resume.run_resumable_generation({}, "s", "o", **args) == {"status": "STOPPED"}
    assert calls == [1] and stop.waits == [3]


@pytest.mark.parametrize("limits", [{"max_attempts": 0}, {"max_attempts": 9}, {"max_attempts": True},
    {"max_seconds": 1801}, {"max_slices": 513}])
def test_invalid_limits_fail_before_attempt(limits):
    with pytest.raises(ValueError, match="INVALID_RESUMPTION_LIMIT"):
        resume.run_resumable_generation({}, "s", "o", pressure_probe=lambda: {},
            generation_available=lambda _: True, **limits)


def test_real_checkpoint_continues_after_slice_budget_without_duplicate_packages(tmp_path, monkeypatch):
    source = tmp_path / "source"
    rows = [_row(source, f"v3/market_segments/{c*2}/{c*64}.json", c.encode()) for c in ("1", "2")]
    metadata = _fixture(tmp_path, rows); output = tmp_path / "out"; stop = Stop(); snapshots = []
    monkeypatch.setattr(runtime.shutil, "disk_usage", lambda _: SimpleNamespace(free=10**10))
    def attempt(meta, src, out, **kwargs):
        result = runtime.run_managed_generation(meta, src, out, **kwargs,
            slice_runner=lambda m, s, o: run_bundle_worker(m, s, o, max_members=1))
        snapshots.append(json.loads((output / ("g-" + metadata["generation_id"][:16]) / "bundle-worker-state.json").read_text()))
        return result
    result = resume.run_resumable_generation(metadata, source, output,
        pressure_probe=lambda: {"pressure": False, "emergency": False}, generation_available=lambda _: True,
        stop_event=stop, max_slices=1, attempt_runner=attempt)
    assert result["status"] == "COMPLETE" and len(snapshots) == 2
    assert len(snapshots[0]["package_index"]) == 1 and len(snapshots[1]["package_index"]) == 2
    assert snapshots[1]["package_index"][0] == snapshots[0]["package_index"][0]
    assert snapshots[1]["completed"] is True


def test_each_resumed_attempt_rechecks_authority_before_any_slice(tmp_path, monkeypatch):
    availability = iter([True, False]); slices = []; stop = Stop()
    monkeypatch.setattr(runtime.shutil, "disk_usage", lambda _: SimpleNamespace(free=10**10))
    def slice_runner(*args):
        slices.append(1)
        return {"status": "BUILDING", "cursor": {"page_index": 1}}
    def attempt(*args, **kwargs):
        return runtime.run_managed_generation(*args, **kwargs, slice_runner=slice_runner)
    result = resume.run_resumable_generation({}, tmp_path, tmp_path / "out",
        pressure_probe=lambda: {"pressure": False, "emergency": False},
        generation_available=lambda _: next(availability), stop_event=stop,
        max_slices=1, attempt_runner=attempt)
    assert result == {"status": "DEFERRED", "error": "GENERATION_AUTHORITY_UNAVAILABLE"}
    assert slices == [1] and stop.waits == [1, 3]


def test_actual_bot_owner_retains_singleton_through_all_attempts(tmp_path, monkeypatch):
    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)
                and n.name == "_start_data_sync_bundle_generation")
    assert "run_resumable_generation" in ast.unparse(node)
    lock = threading.Lock(); targets = []; attempts = []; metadata = {"generation_id": "a" * 64}
    class Thread:
        def __init__(self, target, **kwargs): self.target = target
        def start(self): targets.append(self.target)
    actual_helper = resume.run_resumable_generation
    def wrapper(*args, **kwargs):
        def attempt(*args, **kwargs):
            assert lock.locked()
            assert start(metadata["generation_id"]) is False
            attempts.append(1)
            return {"status": "DEFERRED", "error": "BUNDLE_COORDINATOR_BUDGET"} if len(attempts) == 1 else {"status": "COMPLETE"}
        return actual_helper(*args, **kwargs, stop_event=Stop(), attempt_runner=attempt)
    monkeypatch.setattr(resume, "run_resumable_generation", wrapper)
    ns = {"os": SimpleNamespace(getenv=lambda *args: "1"),
          "_data_sync_bundle_generation": lambda _: metadata,
          "_DATA_SYNC_BUNDLE_COORDINATOR_LOCK": lock,
          "threading": SimpleNamespace(Thread=Thread), "utc_iso": lambda: "now",
          "_data_sync_inventory_work_root": lambda: tmp_path,
          "_data_sync_runtime_root": lambda: tmp_path,
          "_data_sync_bundle_maintain_capacity": lambda _: {"status": "ADMITTED"},
          "_lifecycle_pipeline_pressure_probe": lambda: {"pressure": False, "emergency": False},
          "_lifecycle_pipeline_overlap_probe": lambda: False}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), ns)
    start = ns[node.name]
    assert start(metadata["generation_id"]) is True and lock.locked()
    assert start(metadata["generation_id"]) is False and len(targets) == 1
    targets[0]()
    assert attempts == [1, 1] and not lock.locked()
    assert ns["_DATA_SYNC_BUNDLE_LAST_STATUS"]["status"] == "COMPLETE"


def test_docker_copies_and_import_checks_resumption_helper():
    docker = Path(__file__).with_name("Dockerfile").read_text(encoding="utf-8")
    ignore = Path(__file__).with_name(".dockerignore").read_text(encoding="utf-8")
    assert "!*.py" in ignore.splitlines()
    assert "data_sync_bundle_resumption.py" not in ignore.splitlines()
    assert "COPY . /app" in docker
    assert any(line.startswith("RUN python -c") and "import " in line
               and "data_sync_bundle_resumption" in line for line in docker.splitlines())
