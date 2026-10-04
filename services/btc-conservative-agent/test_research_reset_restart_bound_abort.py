"""Restart-bound retirement of reviewed pre-unlink reset attempts (82c947633a5aca8f44bccd9d)."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

import research_reset_predeletion_abort as module
from research.mirror_generation_lease import MirrorGenerationLease
from research_exact_deletion import ResearchDeletionRejected
from research_v3_contract import canonical_json

INCIDENT = "82c947633a5aca8f44bccd9d"
REVISION = "f187f4937ec7a1a0ce2fadddfed4181ea24521b2"


def anchored_epoch(anchor):
    import pandas as pd
    iso = pd.to_datetime(float(anchor), unit="s", utc=True).isoformat()
    return "epoch-" + hashlib.sha256(f"fresh_research_epoch_v1|SHOWCASE_FRESH_COLLECTION|{iso}".encode()).hexdigest()[:24]


@pytest.fixture(autouse=True)
def kernel_continuity_must_not_be_consulted(monkeypatch):
    def refuse(**_kw):
        raise AssertionError("restart-bound incidents must not use same-process kernel continuity")
    monkeypatch.setattr("research_reset_kernel_continuity.verify_handled_reset_kernel_continuity", refuse)


def build(volume, monkeypatch, *, anchor=100.0, mtime=200.0, rejection="EXPECTED_SHA256_MISMATCH"):
    root = volume / "runtime"
    root.mkdir()
    directory = root / "research_reset_receipts" / INCIDENT
    directory.mkdir(parents=True)
    evidence = {"deployed_revision": REVISION, "active_pointers": []}
    proof = dict(schema="research_reset_boundary_proof_v1", runtime_root=str(root), retired_epoch_id="epoch-old",
                 new_epoch_id=anchored_epoch(anchor), source_revision=REVISION,
                 recovery_receipt_sha256=hashlib.sha256(canonical_json(evidence).encode()).hexdigest(),
                 writers_quiesced=True, paper_only=True, live_disarmed=True, epoch_retired=True,
                 pending_paper_orders=0, open_paper_positions=0, pending_wal_records=0, pending_recovery_records=0)
    binding = {"proof": proof, "boundary_evidence": evidence, "physical_scopes": [], "reset_anchor": anchor}
    operation = {**binding, "stage": "FAILED", "failed_stage": "PAYLOAD_DELETION", "rejection_code": rejection}
    active = {"reset_id": INCIDENT, "binding_sha256": hashlib.sha256(canonical_json(binding).encode()).hexdigest()}
    hashes = {}
    paths = {"active": directory.parent / "ACTIVE_RESET.json", "binding": directory / "binding.json",
             "operation": directory / "operation.json"}
    for key, row in (("active", active), ("binding", binding), ("operation", operation)):
        raw = canonical_json(row).encode()
        paths[key].write_bytes(raw)
        hashes[key] = hashlib.sha256(raw).hexdigest()
    os.utime(paths["operation"], (mtime, mtime))
    record = dict(module.ADDITIONAL_REVIEWED_ATTEMPTS[INCIDENT])
    record["hashes"] = hashes
    record["restart_continuity"] = {"operation_mtime": mtime, "reset_anchor": anchor,
                                    "new_epoch_id": anchored_epoch(anchor), "retired_epoch_id": "epoch-old"}
    monkeypatch.setattr(module, "ADDITIONAL_REVIEWED_ATTEMPTS", {INCIDENT: record})
    probe = dict(execution_paused=True, paper_only=True, live_disarmed=True, epoch_id="epoch-old",
                 pending_orders=0, open_positions=0)
    return root, directory, (lambda: dict(probe))


def retire(volume, root, probe, lease):
    return module.retire_registered_active_attempt(root=root, volume_root=lambda: volume,
                                                   held_lease=lease, quiescence_probe=probe)


def test_registered_incident_is_pinned_to_the_exact_fly_receipts():
    record = module.ADDITIONAL_REVIEWED_ATTEMPTS[INCIDENT]
    assert record["revision"] == REVISION and record["rejection_code"] == "EXPECTED_SHA256_MISMATCH"
    assert record["hashes"] == {
        "active": "364bf8a392b76a4d02d09160ee489c7025d5e4528f869fb917eb8865bd3729f3",
        "binding": "38f7f4c8dff5d75c87b1aca8ad904bb7ed59160c43a8d848483f6e2c06733be6",
        "operation": "9cf1f2e536e0d0bb9a3045fe0d217abfa10367c6ee41785148af07f0e68e665f"}
    pinned = record["restart_continuity"]
    assert anchored_epoch(pinned["reset_anchor"]) == pinned["new_epoch_id"] == "epoch-bf1b84db46d495e8bcd81061"
    assert pinned["reset_anchor"] < pinned["operation_mtime"]


def deleter_blob(revision):
    return subprocess.check_output(
        ["git", "show", f"{revision}:services/btc-conservative-agent/research_exact_deletion.py"],
        cwd=Path(__file__).parent)


@pytest.fixture
def shipped_deleter(tmp_path_factory, monkeypatch):
    """The image ships the LF blob; a Windows checkout may hold CRLF bytes."""
    import research_exact_deletion
    path = tmp_path_factory.mktemp("deleter") / "research_exact_deletion.py"
    path.write_bytes(deleter_blob(REVISION))
    monkeypatch.setattr(research_exact_deletion, "__file__", str(path))


def test_reviewed_deleter_is_the_one_shipped_and_still_current():
    shipped = hashlib.sha256(deleter_blob(REVISION)).hexdigest()
    current = Path(module.__file__).with_name("research_exact_deletion.py").read_bytes().replace(b"\r\n", b"\n")
    assert shipped == module.REVIEWED_FAILED_DELETERS[REVISION]
    assert hashlib.sha256(current).hexdigest() == shipped


def test_restart_bound_incident_retires_pointer_and_preserves_evidence(tmp_path, monkeypatch, shipped_deleter):
    root, directory, probe = build(tmp_path, monkeypatch)
    with MirrorGenerationLease(tmp_path) as lease:
        receipt = retire(tmp_path, root, probe, lease)
    assert receipt["status"] == "PREDELETION_ABORTED" and receipt["reset_id"] == INCIDENT
    assert receipt["basis"] == "EXACT_HANDLED_ATTEMPT_HASH_BOUND_PREUNLINK_FAILURE_ACROSS_RESTART"
    assert receipt["kernel_continuity"]["continuity"] == "HASH_BOUND_RECEIPTS_ACROSS_RESTART"
    assert not (directory.parent / "ACTIVE_RESET.json").exists()
    assert (directory / "binding.json").exists() and (directory / "operation.json").exists()
    assert json.loads((directory / "predeletion-aborted.json").read_text())["reset_id"] == INCIDENT
    from research_reset_receipt_state import active_reset_receipt_exists
    assert active_reset_receipt_exists(root) is False


@pytest.mark.parametrize("defect", ["mtime", "anchor", "hash", "rejection", "later_stage", "lease", "unpaused", "epoch"])
def test_restart_bound_incident_fails_closed(tmp_path, monkeypatch, defect, shipped_deleter):
    root, directory, probe = build(tmp_path, monkeypatch,
                                   rejection="OTHER" if defect == "rejection" else "EXPECTED_SHA256_MISMATCH")
    record = module.ADDITIONAL_REVIEWED_ATTEMPTS[INCIDENT]
    if defect == "mtime":
        os.utime(directory / "operation.json", (201.0, 201.0))
    if defect == "anchor":
        record["restart_continuity"]["reset_anchor"] = 101.0
    if defect == "hash":
        record["hashes"]["operation"] = "0" * 64
    if defect == "later_stage":
        (directory / "deletion.json.progress.jsonl").write_text("")
    if defect == "unpaused":
        probe = lambda: dict(execution_paused=False, paper_only=True, live_disarmed=True, epoch_id="epoch-old",
                             pending_orders=0, open_positions=0)
    if defect == "epoch":
        probe = lambda: dict(execution_paused=True, paper_only=True, live_disarmed=True, epoch_id="epoch-new",
                             pending_orders=0, open_positions=0)
    lease = MirrorGenerationLease(tmp_path)
    if defect != "lease":
        lease.acquire(timeout_seconds=0)
    try:
        with pytest.raises(ResearchDeletionRejected):
            retire(tmp_path, root, probe, lease)
    finally:
        lease.release()
    assert (directory.parent / "ACTIVE_RESET.json").exists()
    assert not (directory / "predeletion-aborted.json").exists()


def test_no_pointer_or_unregistered_pointer_is_left_untouched(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    (root / "research_reset_receipts").mkdir(parents=True)
    assert retire(tmp_path, root, lambda: {}, None) is None
    pointer = root / "research_reset_receipts" / "ACTIVE_RESET.json"
    pointer.write_text(json.dumps({"reset_id": module.REVIEWED_HANDLED_ATTEMPT}))
    assert retire(tmp_path, root, lambda: {}, None) is None
    pointer.write_text(json.dumps({"reset_id": "f" * 24}))
    assert retire(tmp_path, root, lambda: {}, None) is None
    assert pointer.exists()
