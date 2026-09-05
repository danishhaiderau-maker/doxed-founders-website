import hashlib
import pytest
from research_v3_store import V3EvidenceStore


def setup(root):
    store = V3EvidenceStore(root, epoch_id="epoch-old")
    store._read_identity_override = dict(epoch_id="epoch-old", source_revision="a"*40,
        deployed_revision="a"*40, tile_config_signature="b"*64)
    store.initialize_ledger_generation_authority("opportunity")
    pointer = store._generation_root("opportunity") / "ACTIVE.json"
    digest = hashlib.sha256(pointer.read_bytes()).hexdigest()
    proof = dict(schema="research_reset_boundary_proof_v1", runtime_root=str(root.resolve()),
        retired_epoch_id="epoch-old", new_epoch_id="epoch-new",
        source_revision=store._identity_binding()["source_revision"], recovery_receipt_sha256="a"*64,
        writers_quiesced=True, paper_only=True, live_disarmed=True, epoch_retired=True,
        pending_paper_orders=0, open_paper_positions=0, pending_wal_records=0, pending_recovery_records=0)
    return store, pointer, digest, proof


def test_retirement_allows_fresh_epoch_and_idempotent_retry(tmp_path):
    store, pointer, digest, proof = setup(tmp_path)
    result = store.retire_empty_epoch_authority("opportunity", expected_pointer_sha256=digest, boundary_proof=proof)
    assert result["retired"] and not pointer.exists()
    assert store.retire_empty_epoch_authority("opportunity", expected_pointer_sha256=digest, boundary_proof=proof)["already_retired"]
    fresh = V3EvidenceStore(tmp_path, epoch_id="epoch-new")
    assert fresh.initialize_ledger_generation_authority("opportunity")["generation"] == 1


def test_crash_after_receipt_retries_without_rewriting_identity(tmp_path):
    store, pointer, digest, proof = setup(tmp_path)
    with pytest.raises(RuntimeError, match="FAILPOINT"):
        store.retire_empty_epoch_authority("opportunity", expected_pointer_sha256=digest,
            boundary_proof=proof, failpoint="AFTER_RETIREMENT_RECEIPT")
    assert pointer.exists()
    assert store.retire_empty_epoch_authority("opportunity", expected_pointer_sha256=digest, boundary_proof=proof)["retired"]


@pytest.mark.parametrize("defect", ["payload", "hash", "proof", "append_head"])
def test_refuses_unsafe_retirement(tmp_path, defect):
    store, pointer, digest, proof = setup(tmp_path)
    if defect == "payload": store.ledger_path("opportunity").write_text("{}\n")
    if defect == "hash": digest = "0"*64
    if defect == "proof": proof["writers_quiesced"] = False
    if defect == "append_head":
        path = store._append_head_path("opportunity")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    with pytest.raises((RuntimeError, ValueError)):
        store.retire_empty_epoch_authority("opportunity", expected_pointer_sha256=digest, boundary_proof=proof)
    assert pointer.exists()


@pytest.mark.parametrize("failpoint", [None, "AFTER_FIRST_METADATA"])
def test_rotated_epoch_retirement_new_append_and_rotation(tmp_path, failpoint):
    store, pointer, _, proof = setup(tmp_path)
    store.append("opportunity", {"record_id": "old"})
    store.rotate_ledger("opportunity")
    digest = hashlib.sha256(pointer.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="SEALED_PAYLOAD"):
        store.retire_empty_epoch_authority("opportunity", expected_pointer_sha256=digest, boundary_proof=proof)
    store.ledger_path("opportunity").with_name("opportunity.jsonl.1").unlink()
    if failpoint:
        with pytest.raises(RuntimeError, match="FAILPOINT"):
            store.retire_empty_epoch_authority("opportunity", expected_pointer_sha256=digest,
                boundary_proof=proof, failpoint=failpoint)
    store.retire_empty_epoch_authority("opportunity", expected_pointer_sha256=digest, boundary_proof=proof)
    assert not list((store._generation_root("opportunity") / "rotations").glob("*.json"))
    fresh = V3EvidenceStore(tmp_path, epoch_id="epoch-new")
    fresh.initialize_ledger_generation_authority("opportunity")
    assert fresh.append("opportunity", {"record_id": "new"})["written"]
    fresh.rotate_ledger("opportunity")
