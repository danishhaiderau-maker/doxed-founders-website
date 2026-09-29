"""Regression for the observed four-old-generations admission incident."""
import hashlib

import pytest

from data_sync_bundle_retirement import retire_derivative_generation
from data_sync_bundle_storage import check_derivative_admission
from test_data_sync_bundle_runtime import _status_fixture
from test_data_sync_bundle_storage import generation


def test_exact_old_derivative_retirement_reopens_slot_without_raising_cap(tmp_path):
    metadata, source, output, target = _status_fixture(tmp_path)
    for digit in ("1", "2", "3"):
        generation(output, digit * 64)
    current = "b" * 64
    original = {path: path.read_bytes() for path in source.rglob("*") if path.is_file()}
    state_hash = hashlib.sha256((target / "bundle-worker-state.json").read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="BUNDLE_DERIVATIVE_GENERATION_LIMIT"):
        check_derivative_admission(output, current, 1024)
    result = retire_derivative_generation(
        source, output, metadata["generation_id"], current_generation=current,
        expected_state_sha256=state_hash, protected_generations=lambda: {current},
        receipt_path=tmp_path / "retired-generation.json",
    )
    assert result["status"] == "COMPLETE" and result["raw_source_deleted"] is False
    admission = check_derivative_admission(output, current, 1024)
    assert admission["existing_generations"] == 3
    assert admission["projected_generations"] == 4
    assert admission["status"] == "ADMITTED"
    assert not target.exists()
    assert all(path.read_bytes() == data for path, data in original.items())
    assert all((output / ("g-" + digit * 16)).exists() for digit in ("1", "2", "3"))
