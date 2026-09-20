import json
from pathlib import Path

import pytest

import research_reset_receipt_state as receipt_state


RESET_ID = "a" * 24


def _write(root: Path, operation: bytes) -> Path:
    directory = root / "research_reset_receipts" / RESET_ID
    directory.mkdir(parents=True)
    (directory.parent / "ACTIVE_RESET.json").write_text(
        json.dumps({"reset_id": RESET_ID, "binding_sha256": "b" * 64}),
        encoding="utf-8",
    )
    path = directory / "operation.json"
    path.write_bytes(operation)
    return path


def test_large_complete_operation_is_inactive_and_cached(tmp_path, monkeypatch):
    target_size = 36_081_612
    prefix = b'{"padding":"'
    suffix = b'","stage":"COMPLETE"}'
    _write(tmp_path, prefix + (b"x" * (target_size - len(prefix) - len(suffix))) + suffix)
    calls = 0
    original = receipt_state._strict_json

    def counted(payload):
        nonlocal calls
        calls += 1
        return original(payload)

    monkeypatch.setattr(receipt_state, "_strict_json", counted)
    assert receipt_state.active_reset_receipt_exists(tmp_path) is False
    assert receipt_state.active_reset_receipt_exists(tmp_path) is False
    # Both files are keyed by stable identity; the 36 MiB operation is parsed once.
    assert calls == 2


@pytest.mark.parametrize("payload", [
    b'{"stage":"COMPLETE","stage":"FAILED"}',
    b'{"stage":42}',
    b'{"stage":"COMPLETE"',
])
def test_ambiguous_operation_fails_closed(tmp_path, payload):
    _write(tmp_path, payload)
    with pytest.raises(ValueError):
        receipt_state.active_reset_receipt_exists(tmp_path)


def test_nonterminal_is_active_and_cache_invalidates_on_replacement(tmp_path):
    operation = _write(tmp_path, b'{"stage":"FAILED"}')
    assert receipt_state.active_reset_receipt_exists(tmp_path) is True
    operation.write_bytes(b'{"stage":"COMPLETE"}')
    assert receipt_state.active_reset_receipt_exists(tmp_path) is False


def test_operation_over_64_mib_fails_closed_without_reading(tmp_path):
    operation = _write(tmp_path, b'{"stage":"COMPLETE"}')
    with operation.open("r+b") as handle:
        handle.truncate(receipt_state.MAX_OPERATION_BYTES + 1)
    with pytest.raises(ValueError, match="SIZE_OR_TYPE"):
        receipt_state.active_reset_receipt_exists(tmp_path)
