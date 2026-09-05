import hashlib
import json
import pytest
from research.canonical_data_store import append_manifest, current_analyzer_dataset_identity, CanonicalStoreError
from test_canonical_data_store import _fields


def marker(root):
    path = root / "canonical_dataset_current.json"
    (root / "canonical_generation_retired.json").write_text(json.dumps({
        "schema": "canonical_generation_retirement_v1", "retired_epoch_id": "epoch-1",
        "new_epoch_id": "epoch-2", "generation_current": False,
        "metadata_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()}}))


def test_marker_blocks_crash_before_pointer_unlink(tmp_path):
    append_manifest(tmp_path, _fields())
    marker(tmp_path)
    with pytest.raises(CanonicalStoreError, match="RETIRED"):
        current_analyzer_dataset_identity(tmp_path)


def test_new_valid_promotion_survives_historical_marker(tmp_path):
    append_manifest(tmp_path, _fields())
    marker(tmp_path)
    (tmp_path / "canonical_dataset_current.json").unlink()
    (tmp_path / "canonical_dataset_manifest.jsonl").unlink()
    append_manifest(tmp_path, _fields(dataset_epoch="epoch-2"))
    assert current_analyzer_dataset_identity(tmp_path)["dataset_epoch"] == "epoch-2"


@pytest.mark.parametrize("content", ["bad json", "{}", "x" * 65537], ids=["malformed", "empty", "oversized"])
def test_invalid_marker_cannot_restore_old_current(tmp_path, content):
    append_manifest(tmp_path, _fields())
    (tmp_path / "canonical_generation_retired.json").write_text(content)
    with pytest.raises(CanonicalStoreError, match="MARKER_INVALID"):
        current_analyzer_dataset_identity(tmp_path)
