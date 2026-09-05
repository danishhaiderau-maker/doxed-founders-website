import json
import pytest
from research.canonical_generation_retirement import METADATA, metadata_snapshot, retire_canonical_generation, RETIRED_MARKER
from research.mirror_generation_lease import MirrorGenerationLease, MirrorGenerationLeaseTimeout
from research_exact_deletion import ResearchDeletionRejected


def args(root):
    return dict(root=root, expected_snapshot=metadata_snapshot(root), retired_epoch_id="epoch-old",
        new_epoch_id="epoch-new", receipt_path=root / "retirement-receipt.json", quiescent=True,
        recovery_states={"downloader": "RECONCILED", "analyzer": "RECONCILED"})


def test_exact_metadata_only_and_tombstone(tmp_path):
    for name in METADATA: (tmp_path / name).write_text('{"old":true}')
    for name in ("canonical_store.json", ".fly-data-sync-loop.heartbeat.json", "research.db", "paper_state.json"):
        (tmp_path / name).write_text("preserved")
    result = retire_canonical_generation(**args(tmp_path))
    assert not any((tmp_path / n).exists() for n in METADATA)
    assert (tmp_path / "research.db").read_text() == "preserved"
    assert (tmp_path / "paper_state.json").read_text() == "preserved"
    assert (tmp_path / ".fly-data-sync-loop.heartbeat.json").read_text() == "preserved"
    assert json.loads((tmp_path / RETIRED_MARKER).read_text())["generation_current"] is False
    assert result["deletion_receipt"]["deleted_bytes"] > 0


def test_changed_snapshot_refuses(tmp_path):
    params = args(tmp_path)
    (tmp_path / METADATA[0]).write_text("appeared")
    with pytest.raises(ResearchDeletionRejected, match="SNAPSHOT_CHANGED"):
        retire_canonical_generation(**params)
    assert (tmp_path / METADATA[0]).exists()


def test_owner_lease_exclusive(tmp_path):
    lease = MirrorGenerationLease(tmp_path).acquire(timeout_seconds=0)
    try:
        with pytest.raises(MirrorGenerationLeaseTimeout): retire_canonical_generation(**args(tmp_path))
    finally: lease.release()


def test_supplied_unheld_lease_refuses(tmp_path):
    with pytest.raises(ResearchDeletionRejected, match="LEASE_REQUIRED"):
        retire_canonical_generation(**args(tmp_path), lease=MirrorGenerationLease(tmp_path))


@pytest.mark.parametrize("epoch", [None, True, "unknown", "epoch-UNKNOWN", " epoch-old", "../epoch-old"])
def test_invalid_epoch(tmp_path, epoch):
    params = args(tmp_path); params["new_epoch_id"] = epoch
    with pytest.raises(ResearchDeletionRejected, match="IDENTITY_INVALID"):
        retire_canonical_generation(**params)


def test_conflicting_marker_preserves_metadata(tmp_path):
    (tmp_path / METADATA[0]).write_text("old")
    (tmp_path / RETIRED_MARKER).write_text("conflicting")
    with pytest.raises(ResearchDeletionRejected, match="MARKER_CONFLICT"):
        retire_canonical_generation(**args(tmp_path))
    assert (tmp_path / METADATA[0]).read_text() == "old"


def test_wrong_held_lease(tmp_path):
    other = tmp_path / "other"; other.mkdir()
    lease = MirrorGenerationLease(other).acquire(timeout_seconds=0)
    try:
        with pytest.raises(ResearchDeletionRejected, match="LEASE_REQUIRED"):
            retire_canonical_generation(**args(tmp_path), lease=lease)
    finally: lease.release()


def test_directory_is_not_metadata(tmp_path):
    (tmp_path / METADATA[0]).mkdir()
    with pytest.raises(ResearchDeletionRejected): metadata_snapshot(tmp_path)


def test_growth_after_stat_is_bounded(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path / METADATA[0]; path.write_bytes(b"a")
    original = Path.open
    def growing(self, *a, **kw):
        if self == path and a == ("rb",):
            with original(self, "wb") as out: out.write(b"x" * 100)
        return original(self, *a, **kw)
    monkeypatch.setattr(Path, "open", growing)
    with pytest.raises(ResearchDeletionRejected, match="BUDGET"):
        metadata_snapshot(tmp_path, max_bytes=10)


def test_nested_reparse_root_rejected(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    import stat
    root = tmp_path / "nested"; root.mkdir()
    original = Path.lstat
    def reparse(self, *a, **kw):
        if str(self).removeprefix("\\\\?\\") == str(root):
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original(self, *a, **kw)
    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(ResearchDeletionRejected, match="REPARSE"):
        metadata_snapshot(root)
