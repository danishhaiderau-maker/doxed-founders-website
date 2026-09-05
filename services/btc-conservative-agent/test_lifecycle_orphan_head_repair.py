import hashlib
import json
import time

import pytest

import lifecycle_orphan_head_repair as repair
from research_v3_contract import canonical_json
from research_v3_store import V3EvidenceStore


@pytest.fixture
def incident(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir()
    identity = {"epoch_id": repair.OLD_EPOCH, "source_revision": "b" * 40,
                "deployed_revision": "b" * 40, "tile_config_signature": repair.CONFIG_SHA256}
    store = V3EvidenceStore.open_read_only(root)
    ledger = store.ledger_path("lifecycle")
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b'{"other":true}\n')
    payload = '{"lost":true}\n'
    row = {"schema": "v3_ledger_append_head_v1", "state": "PREPARED", "ledger": "lifecycle",
           "identity": dict(identity, source_revision=repair.OLD_REVISION, deployed_revision=repair.OLD_REVISION),
           "offset": 0, "length": len(payload), "row_payload_utf8": payload,
           "row_sha256": hashlib.sha256(payload.encode()).hexdigest(), "record_id": "lost-row"}
    row["binding_sha256"] = repair._sha(canonical_json(row).encode())
    raw = canonical_json(row).encode()
    monkeypatch.setattr(repair, "SOURCE_SIZE", len(raw))
    monkeypatch.setattr(repair, "SOURCE_SHA256", repair._sha(raw))
    monkeypatch.setattr(repair, "OFFSET", 0)
    monkeypatch.setattr(repair, "ROW_LENGTH", len(payload))
    monkeypatch.setattr(repair, "ROW_SHA256", row["row_sha256"])
    monkeypatch.setattr(repair, "_no_open_fds", lambda _: None)
    monkeypatch.setattr(repair.EmergencyEvidenceWal, "inspect_existing", lambda *a, **kw:
                        {"records": [], "deferred_count": 0, "alarms": []})
    source = store._append_head_path("lifecycle").with_name(repair.TEMP_NAME)
    source.parent.mkdir(parents=True)
    source.write_bytes(raw)
    source_stat = source.stat()
    monkeypatch.setattr(repair, "SOURCE_INODE", source_stat.st_ino)
    monkeypatch.setattr(repair, "SOURCE_DEVICE", source_stat.st_dev)
    monkeypatch.setattr(repair, "SOURCE_MTIME_NS", source_stat.st_mtime_ns)
    def probe():
        return {"identity": identity, "observed_unix": time.time(), "inventory_active": False,
                "snapshot_active": False, "download_active": False, "lifecycle_active": False}
    return root, identity, source, ledger, raw, probe


def run(incident):
    root, identity, _, _, _, probe = incident
    return repair.preserve_exact_orphan(root, expected_identity=identity, runtime_probe=probe)


def test_preserves_exact_bytes_without_touching_ledger_or_replaying(incident):
    root, _, source, ledger, raw, _ = incident
    before = ledger.read_bytes()
    result = run(incident)
    assert not source.exists()
    assert ledger.read_bytes() == before
    assert result["state"] == "COMPLETED"
    assert result["replay_performed"] is False
    assert result["ranking_eligible"] is False
    from pathlib import Path
    assert Path(result["artifact"]).read_bytes() == raw
    assert run(incident) == result
    assert not (root / "v3/emergency_evidence_wal_v2").exists()


@pytest.mark.parametrize("defect", ["source", "head", "receipt", "range", "wal", "fd", "probe"])
def test_rejects_exact_counterexamples_before_unlink(incident, monkeypatch, defect):
    root, identity, source, ledger, raw, probe = incident
    store = V3EvidenceStore.open_read_only(root)
    if defect == "source": source.write_bytes(raw + b"x")
    if defect == "head": store._append_head_path("lifecycle").write_text("{}")
    if defect == "receipt":
        receipt = store._record_receipt_path("lifecycle", "lost-row")
        receipt.parent.mkdir(parents=True)
        receipt.write_text("{}")
    if defect == "range": ledger.write_bytes(b'{"lost":true}\n')
    if defect == "wal":
        monkeypatch.setattr(repair.EmergencyEvidenceWal, "inspect_existing", lambda *a, **kw:
                            {"records": [{"state": "PENDING"}], "deferred_count": 1, "alarms": []})
    if defect == "fd":
        def busy(_): raise ValueError("OPEN")
        monkeypatch.setattr(repair, "_no_open_fds", busy)
    if defect == "probe":
        def probe(): return {"identity": identity, "observed_unix": time.time(), "inventory_active": True}
    with pytest.raises(ValueError):
        repair.preserve_exact_orphan(root, expected_identity=identity, runtime_probe=probe)
    assert source.exists()


def test_crash_after_unlink_has_durable_preservation_and_can_complete(incident, monkeypatch):
    _, _, source, _, raw, _ = incident
    original = repair._write_once
    def crash(path, payload):
        if path.name == "COMPLETED.json": raise SystemExit("abrupt interruption")
        original(path, payload)
    monkeypatch.setattr(repair, "_write_once", crash)
    with pytest.raises(SystemExit): run(incident)
    assert not source.exists()
    artifacts = list(incident[0].glob("v3/receipts/orphan_append_head_forensics_v1/*/unpublished-head.json"))
    assert len(artifacts) == 1 and artifacts[0].read_bytes() == raw
    assert artifacts[0].with_name("PREPARED.json").is_file()
    monkeypatch.setattr(repair, "_write_once", original)
    assert run(incident)["state"] == "COMPLETED"


def test_materializer_lock_contention_preserves_source(incident):
    root, _, source, _, _, _ = incident
    with repair._exclusive_index_lock(root):
        with pytest.raises((ValueError, OSError)): run(incident)
    assert source.exists()


def test_volume_lease_contention_preserves_source(incident):
    root, _, source, _, _, _ = incident
    with repair.MirrorGenerationLease(root.parent):
        with pytest.raises(TimeoutError): run(incident)
    assert source.exists()


def test_interrupted_artifact_publish_does_not_leave_partial_authority(incident, monkeypatch):
    original = repair.os.link
    def crash(*args, **kwargs): raise SystemExit("crash before publish")
    monkeypatch.setattr(repair.os, "link", crash)
    with pytest.raises(SystemExit): run(incident)
    assert incident[2].exists()
    assert not list(incident[0].glob("v3/receipts/orphan_append_head_forensics_v1/*/unpublished-head.json"))
    monkeypatch.setattr(repair.os, "link", original)
    assert run(incident)["state"] == "COMPLETED"


def test_new_directory_ancestor_entries_fsynced_before_unlink(incident, monkeypatch):
    from pathlib import Path
    events = []
    original_unlink = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == incident[2]: events.append(("source_unlink", path))
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(repair, "_fsync_directory", lambda path: events.append(("fsync", path)))
    monkeypatch.setattr(Path, "unlink", unlink)
    result = run(incident)
    before_unlink = events[:events.index(("source_unlink", incident[2]))]
    artifact_dir = Path(result["artifact"]).parent
    for directory in (artifact_dir, artifact_dir.parent, artifact_dir.parent.parent):
        assert ("fsync", directory) in before_unlink
