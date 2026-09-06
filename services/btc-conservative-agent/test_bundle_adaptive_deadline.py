import json
from types import SimpleNamespace

import pytest

import data_sync_bundle_worker as worker
import data_sync_bundle_transport as transport
import data_sync_bundle_runtime as runtime
from test_data_sync_bundle_worker import _row, _fixture, GEN


def test_actual_worker_aborts_then_smaller_same_prefix_commits_and_restart_retains_limit(tmp_path, monkeypatch):
    source = tmp_path / "source"
    rows = [_row(source, f"v3/market_segments/00/{i:064x}.json", b"{}") for i in range(200)]
    meta = _fixture(tmp_path, rows)
    output = tmp_path / "out"
    ticks = [0.0]
    real_read = transport._read_fenced
    reads = []
    def costly(path, expected):
        reads.append(path.name)
        value = real_read(path, expected)
        ticks[0] += 0.06
        return value
    monkeypatch.setattr(worker.time, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(transport, "_read_fenced", costly)
    with pytest.raises(worker.BundleWorkerError, match="BUNDLE_BUILD_DEADLINE"):
        worker.run_bundle_worker(meta, source, output)
    statepath = output / f"g-{GEN[:16]}" / "bundle-worker-state.json"
    assert not statepath.exists()
    assert not list(output.rglob("*.tar"))
    reads.clear()
    first = worker.run_bundle_worker(meta, source, output, max_members=64)
    assert first["cursor"]["page_row_index"] == 64
    assert reads == [f"{i:064x}.json" for i in range(64)]
    saved = statepath.read_bytes()
    assert json.loads(saved)["adaptive_member_limit"] == 64
    # A fresh default invocation respects the successfully persisted smaller batch.
    reads.clear()
    second = worker.run_bundle_worker(meta, source, output)
    assert second["cursor"]["page_row_index"] == 128
    assert reads == [f"{i:064x}.json" for i in range(64, 128)]
    before = statepath.read_bytes()
    def too_slow(path, expected):
        value = real_read(path, expected); ticks[0] += 6; return value
    monkeypatch.setattr(transport, "_read_fenced", too_slow)
    with pytest.raises(worker.BundleWorkerError, match="BUNDLE_BUILD_DEADLINE"):
        worker.run_bundle_worker(meta, source, output, max_members=1)
    assert statepath.read_bytes() == before


@pytest.mark.parametrize("error,expected", [("BUNDLE_BUILD_DEADLINE", [128,64,32,16,8,4,2,1]),
                                         ("BUNDLE_SLICE_TIMEOUT", [128,128]),
                                         ("BUNDLE_WORKER_FAILURE", [128,128])])
def test_coordinator_only_adapts_explicit_deadline_and_is_bounded(tmp_path, monkeypatch, error, expected):
    monkeypatch.setattr(runtime.shutil, "disk_usage", lambda p: SimpleNamespace(free=10**10))
    calls = []
    def slice_run(*args, max_members=128):
        calls.append(max_members)
        return {"status":"FAILED", "error":error}
    stop = SimpleNamespace(is_set=lambda:False, wait=lambda n:None)
    result = runtime._run_managed_generation({}, tmp_path, tmp_path,
        pressure_probe=lambda:{"pressure":False,"emergency":False},
        generation_available=lambda m:True, stop_event=stop, slice_runner=slice_run)
    assert calls == expected
    assert result["error"] == (error if error == "BUNDLE_BUILD_DEADLINE" else "BUNDLE_CIRCUIT_OPEN")


def test_coordinator_keeps_smaller_successful_batch_until_budget_defer(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.shutil, "disk_usage", lambda p: SimpleNamespace(free=10**10))
    calls = []
    def slice_run(*args, max_members=128):
        calls.append(max_members)
        if max_members == 128: return {"status":"FAILED", "error":"BUNDLE_BUILD_DEADLINE"}
        return {"status":"BUILDING", "cursor":{"page_row_index": len(calls)*64}}
    result = runtime._run_managed_generation({}, tmp_path, tmp_path,
        pressure_probe=lambda:{"pressure":False,"emergency":False}, generation_available=lambda m:True,
        stop_event=SimpleNamespace(is_set=lambda:False, wait=lambda n:None),
        slice_runner=slice_run, max_slices=4)
    assert calls == [128,64,64,64]
    assert result == {"status":"DEFERRED", "error":"BUNDLE_COORDINATOR_SLICE_LIMIT"}


@pytest.mark.parametrize("limit", [0, 3, 256, True, "64"])
def test_request_rejects_invalid_adaptive_limits_before_child(limit):
    with pytest.raises(ValueError, match="INVALID_SLICE_MEMBER_LIMIT"):
        runtime.run_slice({}, "source", "output", max_members=limit,
                          runner=lambda *a, **k: pytest.fail("invalid request ran"))
