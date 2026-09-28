import json
import subprocess
from types import SimpleNamespace

import pytest

import data_sync_bundle_runtime as runtime
from test_data_sync_bundle_worker import _fixture, _row


class Stop:
    def __init__(self): self.waits = []
    def is_set(self): return False
    def wait(self, seconds): self.waits.append(seconds)


def test_real_child_builds_generation_and_small_receipt(tmp_path):
    source = tmp_path / "source"
    rows = [_row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"sample")]
    metadata = _fixture(tmp_path, rows)
    result = runtime.run_slice(metadata, source, tmp_path / "out")
    assert result["status"] == "COMPLETE", result
    assert result["package_index_count"] == 1
    assert "package" not in result and "source_root" not in result
    assert len(json.dumps(result)) < runtime.MAX_RESULT


def test_real_child_in_excluded_volume_namespace(tmp_path):
    source = tmp_path / "source"
    rows = [_row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"sample")]
    result = runtime.run_slice(_fixture(tmp_path, rows), source,
                               source / ".data-sync-snapshots" / "transport-bundles")
    assert result["status"] == "COMPLETE", result


def test_child_rejects_nested_output_without_exposing_paths(tmp_path):
    source = tmp_path / "source"
    rows = [_row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"sample")]
    result = runtime.run_slice(_fixture(tmp_path, rows), source, source / "unsafe")
    assert result["status"] == "FAILED"
    assert str(tmp_path) not in json.dumps(result)


def test_runner_deadline_nonce_and_secret_isolation(monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "must-not-leak")
    monkeypatch.setenv("DATABASE_URL", "must-not-leak")
    def runner(command, **kwargs):
        assert kwargs["timeout"] == 12
        assert "BOT_ADMIN_TOKEN" not in kwargs["env"] and "DATABASE_URL" not in kwargs["env"]
        payload = json.loads(kwargs["input"])
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "schema": runtime.SCHEMA, "nonce": "wrong", "status": "COMPLETE",
            "identity": runtime._identity(payload["generation"])}).encode())
    assert runtime.run_slice({}, "source", "out", runner=runner)["error"] == "BUNDLE_SLICE_RECEIPT_INVALID"
    def timeout(*args, **kwargs): raise subprocess.TimeoutExpired("child", 12)
    assert runtime.run_slice({}, "source", "out", runner=timeout)["error"] == "BUNDLE_SLICE_TIMEOUT"


def test_coordinator_pressure_then_two_advancing_slices(tmp_path):
    stop = Stop()
    samples = iter([{"pressure": True, "emergency": False},
                    {"pressure": False, "emergency": False},
                    {"pressure": False, "emergency": False}])
    results = iter([{"status": "BUILDING", "cursor": {"page_index": 1}}, {"status": "COMPLETE"}])
    receipts = []
    result = runtime.run_managed_generation({}, tmp_path, tmp_path / "out",
        pressure_probe=lambda: next(samples), generation_available=lambda _: True,
        stop_event=stop, publish=receipts.append, slice_runner=lambda *args: next(results))
    assert result["status"] == "COMPLETE"
    assert stop.waits == [3, 1]
    assert receipts[0]["error"] == "RESOURCE_PRESSURE"


def test_coordinator_opens_after_two_failures(tmp_path):
    calls = []
    def failed(*args): calls.append(1); return {"status": "FAILED", "error": "TEST"}
    result = runtime.run_managed_generation({}, tmp_path, tmp_path / "out",
        pressure_probe=lambda: {"pressure": False, "emergency": False},
        generation_available=lambda _: True, stop_event=Stop(), slice_runner=failed)
    assert result["error"] == "BUNDLE_CIRCUIT_OPEN" and len(calls) == 2


@pytest.mark.parametrize("available,pressure", [(False, {}), (True, {}),
    (True, {"pressure": None, "emergency": False})])
def test_admission_fails_closed(tmp_path, available, pressure):
    def forbidden(*args): raise AssertionError("child admitted without authority")
    result = runtime.run_managed_generation({}, tmp_path, tmp_path / "out",
        pressure_probe=lambda: pressure, generation_available=lambda _: available,
        stop_event=Stop(), slice_runner=forbidden)
    assert result["status"] == "DEFERRED"


def test_child_budget_preserves_prior_state(tmp_path, monkeypatch):
    source = tmp_path / "source"
    rows = [_row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"sample")]
    metadata = _fixture(tmp_path, rows)
    out = tmp_path / "out"
    assert runtime.run_slice(metadata, source, out)["status"] == "COMPLETE"
    state = out / f"g-{metadata['generation_id'][:16]}" / "bundle-worker-state.json"
    parsed = json.loads(state.read_text())
    parsed["package_index"][0]["payload_bytes"] = runtime.MAX_GENERATION_BYTES
    state.write_text(json.dumps(parsed))
    before = state.read_bytes()
    result = runtime.run_slice(metadata, source, out)
    assert result["error"] == "BUNDLE_GENERATION_STORAGE_BUDGET"
    assert state.read_bytes() == before


def test_observed_tiny_receipt_backlog_fits_reserved_generation_budget():
    from data_sync_bundle_transport import MAX_PACKAGE_BYTES
    files = 34433
    packages = (files + 127) // 128
    estimate = files * (732 + 4096) + packages * 32 * 1024
    assert estimate + MAX_PACKAGE_BYTES < runtime.MAX_GENERATION_BYTES
    assert runtime.MIN_FREE_BYTES >= 2 * runtime.MAX_GENERATION_BYTES


def _status_fixture(tmp_path):
    source = tmp_path / "source"
    metadata = _fixture(tmp_path, [_row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"sample")])
    output = tmp_path / "out"
    assert runtime.run_slice(metadata, source, output)["status"] == "COMPLETE"
    directory = output / f"g-{metadata['generation_id'][:16]}"
    return metadata, source, output, directory


@pytest.mark.parametrize("mode,error", [
    ("complete", None), ("deferred", "GENERATION_AUTHORITY_UNAVAILABLE"),
    ("failure", "BUNDLE_CIRCUIT_OPEN"), ("exception", "BUNDLE_COORDINATOR_EXCEPTION"),
])
def test_coordinator_persists_generation_bound_terminal_status(tmp_path, mode, error):
    metadata, source, output, directory = _status_fixture(tmp_path)
    before = (directory / "bundle-worker-state.json").read_bytes()
    def slice_runner(*args):
        if mode == "exception":
            raise RuntimeError("SECRET_DO_NOT_PERSIST")
        return {"status": "COMPLETE" if mode == "complete" else "FAILED",
                "error": "SECRET_DO_NOT_PERSIST", "token": "SECRET_DO_NOT_PERSIST",
                "cursor": {"page_index": 1, "secret": "SECRET_DO_NOT_PERSIST"}}
    kwargs = dict(pressure_probe=lambda: {"pressure": False, "emergency": False},
                  generation_available=lambda _: mode != "deferred", stop_event=Stop(),
                  slice_runner=slice_runner)
    if mode == "exception":
        with pytest.raises(RuntimeError):
            runtime.run_managed_generation(metadata, source, output, **kwargs)
    else:
        runtime.run_managed_generation(metadata, source, output, **kwargs)
    raw = (directory / runtime.COORDINATOR_STATUS_FILE).read_text()
    receipt = json.loads(raw)
    assert receipt["terminal"] is True
    assert receipt["identity"] == runtime._identity(metadata)
    assert receipt["started_at"] <= receipt["updated_at"]
    assert receipt["authority"] == "DIAGNOSTIC_ONLY_NO_ACK_OR_LIVENESS_AUTHORITY"
    if error:
        assert receipt["error"] == error
    assert "SECRET_DO_NOT_PERSIST" not in raw
    assert (directory / "bundle-worker-state.json").read_bytes() == before


def test_status_atomic_failure_keeps_prior_receipt_and_cleans_temp(tmp_path, monkeypatch):
    metadata, source, output, directory = _status_fixture(tmp_path)
    args = (metadata, source, output, {"status": "BUILDING", "cursor": {"page_index": 2}})
    assert runtime._persist_coordinator_status(*args, started_at="fixed")
    target = directory / runtime.COORDINATOR_STATUS_FILE
    before = target.read_bytes()
    def fail(*args): raise OSError("interrupted replace")
    monkeypatch.setattr(runtime.os, "replace", fail)
    assert not runtime._persist_coordinator_status(metadata, source, output,
        {"status": "FAILED"}, started_at="fixed", terminal=True)
    assert target.read_bytes() == before
    assert not list(directory.glob(".bundle-coordinator-status-*.tmp"))


def test_status_rejects_wrong_generation_and_records_pre_state_failure(tmp_path):
    metadata, source, output, directory = _status_fixture(tmp_path)
    wrong = {**metadata, "generation_id": metadata["generation_id"][:16] + "f" * 48}
    assert not runtime._persist_coordinator_status(wrong, source, output,
        {"status": "FAILED"}, started_at="fixed")
    assert runtime._persist_coordinator_status(metadata, source, tmp_path / "missing",
        {"status": "FAILED"}, started_at="fixed")
    assert (tmp_path / "missing" / runtime.COORDINATOR_EARLY_STATUS_FILE).is_file()
    assert not (tmp_path / "missing" / directory.name).exists()
    assert not (directory / runtime.COORDINATOR_STATUS_FILE).exists()


def test_pressure_receipt_marks_waiting_then_terminal_budget(tmp_path):
    metadata, source, output, directory = _status_fixture(tmp_path)
    observed = []
    runtime.run_managed_generation(metadata, source, output,
        pressure_probe=lambda: {"pressure": True, "emergency": False},
        generation_available=lambda _: True, stop_event=Stop(), max_slices=1,
        publish=lambda _: observed.append(json.loads((directory / runtime.COORDINATOR_STATUS_FILE).read_text())))
    assert observed[0]["status"] == "DEFERRED"
    assert observed[0]["terminal"] is False
    assert observed[0]["retry_seconds"] == 3
    terminal = json.loads((directory / runtime.COORDINATOR_STATUS_FILE).read_text())
    assert terminal["terminal"] is True
    assert terminal["error"] == "BUNDLE_COORDINATOR_SLICE_LIMIT"


def test_status_rejects_link_target(tmp_path):
    metadata, source, output, directory = _status_fixture(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("preserve")
    try:
        (directory / runtime.COORDINATOR_STATUS_FILE).symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    assert not runtime._persist_coordinator_status(metadata, source, output,
        {"status": "FAILED"}, started_at="fixed")
    assert outside.read_text() == "preserve"


@pytest.mark.parametrize("mode,error", [
    ("admission", "BUNDLE_ADMISSION_UNAVAILABLE"),
    ("authority", "GENERATION_AUTHORITY_UNAVAILABLE"),
    ("pressure", "BUNDLE_COORDINATOR_SLICE_LIMIT"),
    ("failure", "BUNDLE_CIRCUIT_OPEN"),
])
def test_early_status_without_worker_state(tmp_path, mode, error):
    source = tmp_path / "source"
    metadata = _fixture(tmp_path, [_row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"x")])
    output = tmp_path / "out"
    observed = []
    def pressure():
        if mode == "admission":
            raise RuntimeError("private error")
        return {"pressure": mode == "pressure", "emergency": False}
    runtime.run_managed_generation(metadata, source, output,
        pressure_probe=pressure, generation_available=lambda _: mode != "authority",
        stop_event=Stop(), max_slices=2,
        slice_runner=lambda *args: {"status": "FAILED", "error": "BUNDLE_SLICE_TIMEOUT"},
        publish=lambda _: observed.append(json.loads((output / runtime.COORDINATOR_EARLY_STATUS_FILE).read_text())))
    receipt = json.loads((output / runtime.COORDINATOR_EARLY_STATUS_FILE).read_text())
    assert receipt["error"] == error and receipt["terminal"] is True
    assert receipt["worker_state_present"] is False
    assert receipt["identity"] == runtime._identity(metadata)
    assert receipt["authority"] == "DIAGNOSTIC_ONLY_NO_ACK_OR_LIVENESS_AUTHORITY"
    assert not list(output.glob("g-*"))
    if mode == "pressure":
        assert observed[0]["error"] == "RESOURCE_PRESSURE"


def test_early_status_rejects_unsafe_output_and_identity(tmp_path):
    source = tmp_path / "source"
    metadata = _fixture(tmp_path, [_row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"x")])
    assert not runtime._persist_coordinator_status(metadata, source, source / "unsafe",
        {"status": "FAILED"}, started_at="fixed")
    assert not (source / "unsafe").exists()
    assert not runtime._persist_coordinator_status({**metadata, "generation_id": "../escape"}, source,
        tmp_path / "out", {"status": "FAILED"}, started_at="fixed")
    assert not (tmp_path / "out").exists()


def test_early_status_rejects_link_before_root_write(tmp_path, monkeypatch):
    source = tmp_path / "source"
    metadata = _fixture(tmp_path, [_row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"x")])
    output = tmp_path / "out"
    output.mkdir()
    target = output / runtime.COORDINATOR_EARLY_STATUS_FILE
    target.write_text("preserve")
    original = runtime._reject_links
    def reject(path):
        if Path(path) == target:
            raise ValueError("BUNDLE_LINK_REJECTED")
        original(path)
    monkeypatch.setattr(runtime, "_reject_links", reject)
    assert not runtime._persist_coordinator_status(metadata, source, output,
        {"status": "FAILED"}, started_at="fixed")
    assert target.read_text() == "preserve"
