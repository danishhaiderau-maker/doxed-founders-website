import hashlib
import pytest
from research_reset_predeletion_abort import abort_predeletion_reset, REVIEWED_FAILED_DELETERS, REVIEWED_HANDLED_ATTEMPT
from research.mirror_generation_lease import MirrorGenerationLease
from research_exact_deletion import ResearchDeletionRejected
from research_v3_contract import canonical_json


@pytest.fixture(autouse=True)
def isolated_kernel_fixture(monkeypatch):
    monkeypatch.setattr("research_reset_kernel_continuity.verify_handled_reset_kernel_continuity",
        lambda **kw: {"fixture_kernel": True})


def fixture(root, new_incident=False, rejection='EXPECTED_SHA256_MISMATCH'):
    volume = root
    root = volume / "runtime"; root.mkdir()
    reset_id = '5e6bafa7ac6ee68f37024cbe' if new_incident else REVIEWED_HANDLED_ATTEMPT
    directory = root / "research_reset_receipts" / reset_id; directory.mkdir(parents=True)
    import pandas as pd
    import subprocess
    from pathlib import Path
    new_epoch = "epoch-" + hashlib.sha256(
        f"fresh_research_epoch_v1|SHOWCASE_FRESH_COLLECTION|{pd.to_datetime(100., unit='s', utc=True).isoformat()}".encode()).hexdigest()[:24]
    revision, digest = next(iter(REVIEWED_FAILED_DELETERS.items()))
    if new_incident:
        revision = '140350a464c8a36d590aeefc75a1722640469fa6'
        digest = REVIEWED_FAILED_DELETERS[revision]
    blob = subprocess.check_output(["git", "show", revision + ":services/btc-conservative-agent/research_exact_deletion.py"], cwd=Path(__file__).parent)
    assert hashlib.sha256(blob).hexdigest() == digest
    artifact = root / "reviewed-deleter.py"; artifact.write_bytes(blob)
    evidence = {"deployed_revision": revision, "active_pointers": []}
    proof = dict(schema="research_reset_boundary_proof_v1", runtime_root=str(root), retired_epoch_id="epoch-old",
        new_epoch_id=new_epoch, source_revision=revision, recovery_receipt_sha256=hashlib.sha256(canonical_json(evidence).encode()).hexdigest(),
        writers_quiesced=True, paper_only=True, live_disarmed=True, epoch_retired=True,
        pending_paper_orders=0, open_paper_positions=0, pending_wal_records=0, pending_recovery_records=0)
    binding = {"proof": proof, "boundary_evidence": evidence, "physical_scopes": [], "reset_anchor":100}
    operation = {**binding, "stage": "FAILED", "failed_stage": "PAYLOAD_DELETION"}
    if new_incident:
        operation['rejection_code'] = rejection
    active = {"reset_id": reset_id, "binding_sha256": hashlib.sha256(canonical_json(binding).encode()).hexdigest()}
    paths = {"active": directory.parent / "ACTIVE_RESET.json", "binding": directory / "binding.json", "operation": directory / "operation.json"}
    hashes = {}
    for key, row in (("active", active), ("binding", binding), ("operation", operation)):
        raw = canonical_json(row).encode(); paths[key].write_bytes(raw); hashes[key] = hashlib.sha256(raw).hexdigest()
    probe = dict(execution_paused=True, paper_only=True, live_disarmed=True,
        epoch_id="epoch-old", pending_orders=0, open_positions=0, process_id=663,
        same_process_since_handled_failure=True, handled_exception_confirmed=True)
    return dict(root=root, volume_root=volume, reset_id=reset_id, expected_sha256=hashes, old_epoch="epoch-old",
        failed_revision=revision, quiescence_probe=lambda: probe, expected_new_epoch=new_epoch,
        reviewed_deleter_sha256=digest, reviewed_source_artifact=artifact), directory


@pytest.mark.parametrize('rejection', ['EXPECTED_SHA256_MISMATCH', 'OTHER', None])
def test_additional_incident_requires_exact_failure(tmp_path, monkeypatch, rejection):
    import research_reset_predeletion_abort as module
    args, directory = fixture(tmp_path, new_incident=True, rejection=rejection)
    record = dict(module.ADDITIONAL_REVIEWED_ATTEMPTS[args['reset_id']])
    record['hashes'] = args['expected_sha256']
    monkeypatch.setattr(module, 'ADDITIONAL_REVIEWED_ATTEMPTS', {args['reset_id']: record})
    with MirrorGenerationLease(tmp_path) as lease:
        if rejection == 'EXPECTED_SHA256_MISMATCH':
            assert abort_predeletion_reset(**args, held_lease=lease)['status'] == 'PREDELETION_ABORTED'
        else:
            with pytest.raises(ResearchDeletionRejected, match='NOT_PROVED_PREDELETION'):
                abort_predeletion_reset(**args, held_lease=lease)
            assert (directory.parent/'ACTIVE_RESET.json').exists()


def test_exact_predeletion_abort_preserves_artifacts(tmp_path, monkeypatch):
    args, directory = fixture(tmp_path)
    monkeypatch.setattr("research_reset_predeletion_abort.REVIEWED_RECEIPT_HASHES", dict(args["expected_sha256"]))
    lease = MirrorGenerationLease(tmp_path).acquire(timeout_seconds=0)
    try: result = abort_predeletion_reset(**args, held_lease=lease)
    finally: lease.release()
    assert result["status"] == "PREDELETION_ABORTED"
    assert not (directory.parent / "ACTIVE_RESET.json").exists()
    assert (directory / "binding.json").exists() and (directory / "operation.json").exists()


@pytest.mark.parametrize("defect", ["receipt", "progress", "hash", "epoch", "lease", "source", "anchor", "revision", "alternate_source"])
def test_abort_refuses_ambiguous_or_changed_state(tmp_path, defect, monkeypatch):
    args, directory = fixture(tmp_path)
    monkeypatch.setattr("research_reset_predeletion_abort.REVIEWED_RECEIPT_HASHES", dict(args["expected_sha256"]))
    if defect == "receipt": (directory / "deletion.json").write_text("{}")
    if defect == "progress": (directory / "deletion.json.progress.jsonl").write_text("")
    if defect == "hash": args["expected_sha256"]["operation"] = "0"*64
    if defect == "epoch": args["old_epoch"] = "epoch-other"
    if defect == "source": args["reviewed_deleter_sha256"] = "0"*64
    if defect == "anchor": args["expected_new_epoch"] = "epoch-other"
    if defect == "revision": args["failed_revision"] = "f"*40
    if defect == "alternate_source":
        args["reviewed_source_artifact"].write_bytes(b"different valid source")
        args["reviewed_deleter_sha256"] = hashlib.sha256(b"different valid source").hexdigest()
    lease = MirrorGenerationLease(tmp_path)
    if defect != "lease": lease.acquire(timeout_seconds=0)
    try:
        with pytest.raises(ResearchDeletionRejected): abort_predeletion_reset(**args, held_lease=lease)
    finally: lease.release()
    assert (directory.parent / "ACTIVE_RESET.json").exists()


def test_arbitrary_consistent_incident_hashes_not_accepted(tmp_path):
    args, directory = fixture(tmp_path)
    lease = MirrorGenerationLease(tmp_path).acquire(timeout_seconds=0)
    try:
        with pytest.raises(ResearchDeletionRejected, match="IDENTITY_INVALID"):
            abort_predeletion_reset(**args, held_lease=lease)
    finally: lease.release()


def test_runtime_lease_does_not_exclude_production_reset(tmp_path, monkeypatch):
    args, directory = fixture(tmp_path)
    monkeypatch.setattr("research_reset_predeletion_abort.REVIEWED_RECEIPT_HASHES", dict(args["expected_sha256"]))
    lease = MirrorGenerationLease(args["root"]).acquire(timeout_seconds=0)
    try:
        with pytest.raises(ResearchDeletionRejected, match="ACTUAL_LEASE"):
            abort_predeletion_reset(**args, held_lease=lease)
    finally: lease.release()
