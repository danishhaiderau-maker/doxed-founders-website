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
