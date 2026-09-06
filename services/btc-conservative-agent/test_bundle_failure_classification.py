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
