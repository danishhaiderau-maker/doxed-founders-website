import json
from pathlib import Path

import pytest

from research.legacy_archive_verifier import (
    LegacyArchiveVerificationError,
    verify_legacy_archive,
)


def _session(root: Path, name: str, files: dict[str, bytes], *, declared=True) -> Path:
    session = root / name
    (session / "payload").mkdir(parents=True)
    inventory = []
    import hashlib
    for index, (filename, content) in enumerate(files.items()):
        path = session / "payload" / filename
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        inventory.append({"preserved_path": f"payload/{filename}",
                          "preserved_sha256": digest, "preserved_bytes": len(content)})
    (session / "archive_meta.json").write_text(json.dumps({
        "schema": "research_archive_receipt_v2",
        "source_inventory": inventory,
        "integrity": {"verified": declared, "file_count": len(inventory)},
    }), encoding="utf-8")
    return session


def test_bounded_run_resumes_without_rehashing_verified_files(tmp_path):
    archive = tmp_path / "research_archive"
    canonical = tmp_path / "canonical-research-data"
    _session(archive, "session_001", {"a.json": b"a", "b.json": b"b"})
    first = verify_legacy_archive(archive, canonical, max_files=1)
    assert first["files_hashed_this_run"] == 1
    assert first["pending_file_count"] == 1
    assert not first["complete"]
    second = verify_legacy_archive(archive, canonical, max_files=1)
    assert second["files_hashed_this_run"] == 1
    assert second["file_receipts_reused_this_run"] == 1
    assert second["verified_session_count"] == 1
    assert second["complete"]
    third = verify_legacy_archive(archive, canonical, max_files=0)
    assert third["files_hashed_this_run"] == 0
    assert third["file_receipts_reused_this_run"] == 2


def test_changed_payload_is_rehashed_and_invalidated(tmp_path):
    archive = tmp_path / "research_archive"
    canonical = tmp_path / "canonical-research-data"
    session = _session(archive, "session_001", {"a.json": b"original"})
    assert verify_legacy_archive(archive, canonical, max_files=1)["verified_session_count"] == 1
    (session / "payload" / "a.json").write_bytes(b"tampered")
    result = verify_legacy_archive(archive, canonical, max_files=1)
    assert result["invalid_session_count"] == 1
    assert result["sessions"][0]["error_codes"] == ["ARCHIVE_CHECKSUM_MISMATCH"]


def test_archive_path_escape_is_invalid_and_never_read(tmp_path):
    archive = tmp_path / "research_archive"
    canonical = tmp_path / "canonical-research-data"
    session = _session(archive, "session_001", {})
    (tmp_path / "outside.bin").write_bytes(b"secret")
    (session / "archive_meta.json").write_text(json.dumps({
        "source_inventory": [{"preserved_path": "../../outside.bin",
                              "preserved_sha256": "0" * 64}],
        "integrity": {"verified": True},
    }), encoding="utf-8")
    result = verify_legacy_archive(archive, canonical, max_files=1)
    assert result["invalid_session_count"] == 1
    assert result["sessions"][0]["error_codes"] == ["ARCHIVE_PATH_OUTSIDE_SESSION"]


def test_output_must_be_named_canonical_store(tmp_path):
    archive = tmp_path / "research_archive"
    _session(archive, "session_001", {"a": b"a"})
    with pytest.raises(LegacyArchiveVerificationError, match="CANONICAL_ROOT_NAME_INVALID"):
        verify_legacy_archive(archive, tmp_path / "not-canonical", max_files=1)


def test_empty_inventory_is_truthfully_unverifiable(tmp_path):
    archive = tmp_path / "research_archive"
    canonical = tmp_path / "canonical-research-data"
    _session(archive, "session_001", {}, declared=False)
    result = verify_legacy_archive(archive, canonical, max_files=1)
    assert result["unverifiable_session_count"] == 1
    assert result["sessions"][0]["error_codes"] == ["ARCHIVE_INVENTORY_EMPTY"]
