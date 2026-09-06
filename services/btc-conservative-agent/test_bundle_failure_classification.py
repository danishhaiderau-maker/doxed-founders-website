import json

import pytest

import data_sync_bundle_runtime as runtime
from data_sync_bundle_transport import BundleTransportError
from test_data_sync_bundle_worker import _fixture, _row


@pytest.mark.parametrize("message,code", runtime.TRANSPORT_FAILURE_CODES.items())
def test_known_transport_failures_survive_diagnostic_allowlist(message, code):
    assert runtime._slice_failure_code(BundleTransportError(message)) == code
    assert code in runtime.COORDINATOR_ERRORS


def test_unknown_messages_do_not_leak():
    assert runtime._slice_failure_code(ValueError("secret /private/path")) == "BUNDLE_SLICE_FAILED"
    assert runtime._slice_failure_code(ValueError("source generation differs from inventory")) == "BUNDLE_SLICE_FAILED"


def test_real_child_preserves_changed_source_failure(tmp_path):
    source = tmp_path / "source"
    rel = "v3/market_segments/11/" + "1" * 64 + ".json"
    row = _row(source, rel, b"original")
    metadata = _fixture(tmp_path, [row])
    (source / rel).write_bytes(b"different length payload")
    result = runtime.run_slice(metadata, source, tmp_path / "out")
    assert result["status"] == "FAILED"
    assert result["error"] == "BUNDLE_SOURCE_GENERATION_MISMATCH", result
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize("code", sorted(runtime.DERIVATIVE_ADMISSION_CODES) + [
    "BUNDLE_DERIVATIVE_SECRET_UNKNOWN", "private /secret/path"])
def test_early_persist_preserves_only_static_derivative_codes(tmp_path, code):
    source = tmp_path / "source"
    source.mkdir()
    metadata = _fixture(tmp_path, [])
    output = tmp_path / "output"
    assert runtime._persist_coordinator_status(metadata, source, output,
        {"status": "FAILED", "error": "BUNDLE_CIRCUIT_OPEN", "last_error": code},
        started_at="2026-09-06T00:00:00Z", terminal=True)
    saved = json.loads((output / runtime.COORDINATOR_EARLY_STATUS_FILE).read_text())
    assert saved["last_error"] == (code if code in runtime.DERIVATIVE_ADMISSION_CODES else "BUNDLE_WORKER_FAILURE")
    assert saved["worker_state_present"] is False
    assert saved["authority"] == "DIAGNOSTIC_ONLY_NO_ACK_OR_LIVENESS_AUTHORITY"
    assert not (output / ("g-" + metadata["generation_id"][:16])).exists()
