import hashlib
import json
import pytest
from emergency_evidence_wal import EmergencyEvidenceWal

IDENTITY = dict(epoch_id="epoch-old", source_revision="a"*40,
                deployed_revision="a"*40, tile_config_signature="b"*64)

def hashes(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}

def test_empty_existing_inspection_does_not_write(tmp_path):
    EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    before = hashes(tmp_path)
    result = EmergencyEvidenceWal.inspect_existing(tmp_path, identity=IDENTITY, extents=1)
    assert result["deferred_count"] == 0 and result["records"] == []
    assert hashes(tmp_path) == before

def test_deferred_is_not_empty_and_not_replayed(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    wal.defer(ledger="lifecycle", record_id="row-1", payload=b"{}\n")
    before = hashes(tmp_path)
    result = EmergencyEvidenceWal.inspect_existing(tmp_path, identity=IDENTITY, extents=1)
    assert result["deferred_count"] == 1
    assert result["records"][0]["state"] == "DEFERRED"
    assert hashes(tmp_path) == before

def test_corruption_is_not_repaired(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    wal.control_path.write_bytes(b"bad")
    before = hashes(tmp_path)
    with pytest.raises(RuntimeError):
        EmergencyEvidenceWal.inspect_existing(tmp_path, identity=IDENTITY, extents=1)
    assert hashes(tmp_path) == before

def test_wrong_identity_does_not_rebind(tmp_path):
    EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    before = hashes(tmp_path)
    with pytest.raises(RuntimeError):
        EmergencyEvidenceWal.inspect_existing(tmp_path, identity={**IDENTITY, "epoch_id":"epoch-new"}, extents=1)
    assert hashes(tmp_path) == before

def test_missing_is_not_provisioned(tmp_path):
    root = tmp_path / "absent"
    with pytest.raises(FileNotFoundError):
        EmergencyEvidenceWal.inspect_existing(root, identity=IDENTITY, extents=1)
    assert not root.exists()


def test_replayed_record_is_still_retained_until_ack(tmp_path):
    root = tmp_path / "v3/emergency_evidence_wal_v2"
    wal = EmergencyEvidenceWal(root, identity=IDENTITY, extents=1)
    payload = b'{"record_id":"terminal:1"}\n'
    row = wal.defer(ledger="lifecycle", record_id="terminal:1", payload=payload)
    ledger = tmp_path / "v3/ledgers/lifecycle.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(payload)
    receipt = tmp_path / "v3/receipts/terminal.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"schema": "emergency_record_idempotency_v1",
        "state": "COMMITTED", "ledger": "lifecycle", "record_id": "terminal:1",
        "row_sha256": hashlib.sha256(payload).hexdigest(), "offset": 0,
        "length": len(payload), "identity": IDENTITY}), encoding="utf-8")
    wal.mark_replayed(row["generation"], canonical_ledger=ledger, canonical_receipt=receipt)
    before = hashes(root)
    status = EmergencyEvidenceWal.inspect_existing(root, identity=IDENTITY, extents=1)
    assert status["deferred_count"] == 1
    assert status["records"][0]["state"] == "REPLAYED"
    assert hashes(root) == before

@pytest.mark.parametrize("attribute", ["data_path", "header_path"])
def test_truncated_empty_reserve_is_not_empty_proof(tmp_path, attribute):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    getattr(wal, attribute).write_bytes(b"")
    before = hashes(tmp_path)
    with pytest.raises(RuntimeError, match="CAPACITY_MISMATCH"):
        EmergencyEvidenceWal.inspect_existing(tmp_path, identity=IDENTITY, extents=1)
    assert hashes(tmp_path) == before
