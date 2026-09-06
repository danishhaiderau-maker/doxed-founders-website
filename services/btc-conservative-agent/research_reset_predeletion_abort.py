"""Retire one proved pre-unlink reset attempt, preserving every incident file."""
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from research_exact_deletion import _checked_path, ResearchDeletionRejected
from research.mirror_generation_lease import MirrorGenerationLease, LEASE_FILE_NAME
from research_v3_contract import canonical_json

REVIEWED_FAILED_DELETERS = {
    "6d977a6d38d19a68efc7650625e830133ba33a8d":
        "47a23875125a393d77d48b14f0885e151dd508a113e1fc7b7bc680e9b031a916",
    '140350a464c8a36d590aeefc75a1722640469fa6':
        '200d3aa69b2c8b33636dc51c8b93c3f0d02d73b2755e9740327b99c546bf676b',
    '85e1072af97cdb634594e2d87fb23681100d7482':
        'a53b24ca9c8ae66c6d570c6238f704b314310818dd3e47529c5a9f3ce9f72fad',
}
REVIEWED_HANDLED_ATTEMPT = "66791b9ec3e200588082b1bc"
REVIEWED_RECEIPT_HASHES = {
    "active": "4e88456bcdb2d5037c852d1bfe9ea65ce93298d693bc2cfbbf43690b44fb24ec",
    "binding": "dc30eb23d5f87c93d1272bfd2863961b1909d8096eba00169ba698eba14a1260",
    "operation": "8c13c2e579c2033f4642eae35ff08ed3582d77b2deb358338dd0f5f2f81308e1",
}
ADDITIONAL_REVIEWED_ATTEMPTS = {
    '718a9dbb42fdd90b7abbd226': {
        'revision': '85e1072af97cdb634594e2d87fb23681100d7482',
        'rejection_code': 'RESET_TARGET_CHANGED_AFTER_PLAN',
        'hashes': {
            'active': '6e15dd033f96526dd6661c7a3edbead8e38f34b87d64e45eed8b23ab6da2ce22',
            'binding': '66ed8d84a49b58d7ff4b46e4996d22b8864f9dfc64637f90d3a1344f49e05bc3',
            'operation': '1aa20a2288531a67735fed10b8ca0a1b5afbe9bf9c1e5b3f741d460599e8f6db',
        },
    },
    '5e6bafa7ac6ee68f37024cbe': {
        'revision': '140350a464c8a36d590aeefc75a1722640469fa6',
        'rejection_code': 'EXPECTED_SHA256_MISMATCH',
        'hashes': {
            'active': 'a498959abe36e322c0b47d385256fde0e2a99baaac722215ea35cc0e4b20bb23',
            'binding': '0b6b45f6bcb92de2fe1803bf7d8b1b4b9f637e0d995240d9e1af0ab1285aded1',
            'operation': 'c30d21f9e87185aa67d1f16bfbd1734dfba6218440771c2c23bd5bb16b86d511',
        },
    },
}


def abort_predeletion_reset(*, root, volume_root, reset_id, expected_sha256, old_epoch,
        failed_revision, held_lease, quiescence_probe, reviewed_deleter_sha256,
        expected_new_epoch, reviewed_source_artifact=None):
    from research_reset_inventory import _proof_valid, _managed_fly_alias
    from research_v3_store import _fsync_directory
    import research_exact_deletion
    from research_reset_kernel_continuity import verify_handled_reset_kernel_continuity
    root = Path(root).absolute()
    volume_root = Path(volume_root).absolute()
    if root != volume_root / "runtime" or volume_root == Path(volume_root.anchor):
        raise ResearchDeletionRejected("ABORT_VOLUME_LAYOUT_INVALID")
    _checked_path(root / "research_reset_receipts", volume_root)
    source = Path(reviewed_source_artifact or research_exact_deletion.__file__)
    if (REVIEWED_FAILED_DELETERS.get(failed_revision) != reviewed_deleter_sha256
            or not re.fullmatch(r"[0-9a-f]{64}", str(reviewed_deleter_sha256))
            or hashlib.sha256(source.read_bytes()).hexdigest() != reviewed_deleter_sha256):
        raise ResearchDeletionRejected("ABORT_DELETER_SOURCE_NOT_REVIEWED")
    incident = ADDITIONAL_REVIEWED_ATTEMPTS.get(reset_id)
    reviewed_hashes = incident['hashes'] if incident else REVIEWED_RECEIPT_HASHES
    if ((reset_id != REVIEWED_HANDLED_ATTEMPT and incident is None)
            or (incident is not None and failed_revision != incident['revision'])
            or not re.fullmatch(r"[0-9a-f]{24}", str(reset_id))
            or not isinstance(expected_sha256, dict) or expected_sha256 != reviewed_hashes
            or set(expected_sha256) != {"active", "binding", "operation"}
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(v)) for v in expected_sha256.values())):
        raise ResearchDeletionRejected("ABORT_IDENTITY_INVALID")
    if (not isinstance(held_lease, MirrorGenerationLease) or not held_lease.held
            or held_lease.path != volume_root / LEASE_FILE_NAME):
        raise ResearchDeletionRejected("ABORT_ACTUAL_LEASE_REQUIRED")
    probe = quiescence_probe()
    if (not isinstance(probe, dict) or probe.get("execution_paused") is not True
            or probe.get("paper_only") is not True or probe.get("live_disarmed") is not True
            or probe.get("epoch_id") != old_epoch
            or any(type(probe.get(k)) is not int or probe[k] != 0 for k in
                   ("pending_orders", "open_positions"))):
        raise ResearchDeletionRejected("ABORT_QUIESCENCE_UNPROVEN")
    directory = root / "research_reset_receipts" / reset_id
    paths = {"active": directory.parent / "ACTIVE_RESET.json",
             "binding": directory / "binding.json", "operation": directory / "operation.json"}
    raw, rows = {}, {}
    def read(path):
        path = _checked_path(path, root)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode): raise ResearchDeletionRejected("ABORT_NONREGULAR")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            value = stream.read(16 * 1024**2 + 1)
        after = path.lstat()
        signature = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        if (len(value) > 16 * 1024**2 or signature(before) != signature(opened)
                or signature(before) != signature(after)):
            raise ResearchDeletionRejected("ABORT_RECEIPT_CHANGED")
        return value
    for key, path in paths.items():
        raw[key] = read(path)
        if len(raw[key]) > 16 * 1024**2 or hashlib.sha256(raw[key]).hexdigest() != expected_sha256[key]:
            raise ResearchDeletionRejected("ABORT_RECEIPT_CHANGED")
        rows[key] = json.loads(raw[key])
    active, binding, operation = (rows[key] for key in ("active", "binding", "operation"))
    kernel = verify_handled_reset_kernel_continuity(reset_anchor=binding.get("reset_anchor"),
        operation_mtime=paths["operation"].stat().st_mtime, reset_id=reset_id)
    proof = binding.get("proof") or {}
    evidence = binding.get("boundary_evidence") or {}
    import pandas as pd
    try:
        anchor = pd.to_datetime(float(binding["reset_anchor"]), unit="s", utc=True).isoformat()
        anchored_epoch = "epoch-" + hashlib.sha256(
            f"fresh_research_epoch_v1|SHOWCASE_FRESH_COLLECTION|{anchor}".encode()).hexdigest()[:24]
    except (KeyError, ValueError, TypeError, OverflowError):
        raise ResearchDeletionRejected("ABORT_ANCHOR_INVALID")
    if (not _proof_valid(root, proof) or proof.get("new_epoch_id") != expected_new_epoch
            or expected_new_epoch != anchored_epoch
            or hashlib.sha256(canonical_json(evidence).encode()).hexdigest() != proof.get("recovery_receipt_sha256")
            or active.get("reset_id") != reset_id or active.get("binding_sha256") != hashlib.sha256(canonical_json(binding).encode()).hexdigest()
            or operation.get("stage") != "FAILED" or operation.get("failed_stage") != "PAYLOAD_DELETION"
            or (incident is not None and operation.get('rejection_code') != incident['rejection_code'])
            or operation.get("proof") != proof or operation.get("boundary_evidence") != evidence
            or proof.get("retired_epoch_id") != old_epoch or evidence.get("deployed_revision") != failed_revision
            or operation.get("deletion") or operation.get("scope_deletions")
            or operation.get("genome_reset_completed") or operation.get("authority_retirements")):
        raise ResearchDeletionRejected("ABORT_NOT_PROVED_PREDELETION")
    forbidden = [directory / name for name in ("deletion.json", "deletion.json.progress.jsonl", "genome-deletion.json")]
    for scope in binding.get("physical_scopes", []):
        if scope not in {"research", "research_accumulator", "research_archive"}:
            raise ResearchDeletionRejected("ABORT_UNKNOWN_SCOPE")
        physical = _managed_fly_alias(root, root / scope)
        if physical is None: raise ResearchDeletionRejected("ABORT_SCOPE_LAYOUT_INVALID")
        forbidden.append(physical / "research_reset_receipts" / reset_id)
    for path in forbidden:
        if path.exists() or path.is_symlink():
            raise ResearchDeletionRejected("ABORT_LATER_STAGE_EVIDENCE_PRESENT")
    for pointer in evidence.get("active_pointers", []):
        from research_v3_contract import LEDGER_NAMES
        if pointer.get("ledger") not in LEDGER_NAMES: raise ResearchDeletionRejected("ABORT_POINTER_INVALID")
        path = _checked_path(root / "v3/receipts/ledger_generations_v1" / pointer["ledger"] / "ACTIVE.json", root)
        if hashlib.sha256(read(path)).hexdigest() != pointer["sha256"]:
            raise ResearchDeletionRejected("ABORT_AUTHORITY_CHANGED")
    receipt = {"schema": "predeletion_reset_abort_v1", "status": "PREDELETION_ABORTED",
        "reset_id": reset_id, "old_epoch": old_epoch, "failed_revision": failed_revision,
        "expected_sha256": expected_sha256, "quiescence_probe": probe,
        "reset_pointer_exclusion": "ACTUAL_HELD_MIRROR_LEASE", "kernel_continuity": kernel,
        "basis": "EXACT_HANDLED_ATTEMPT_SAME_PROCESS_PREUNLINK_FAILURE_NOT_CRASH_RECOVERY",
        "source_ordering_contract": "EXACT_DELETER_RECEIPT_AND_PROGRESS_PRECEDE_FIRST_UNLINK",
        "reviewed_deleter_sha256": reviewed_deleter_sha256,
        "incident_artifacts_preserved": True}
    target = _checked_path(directory / "predeletion-aborted.json", root)
    encoded = (canonical_json(receipt) + "\n").encode()
    if target.exists():
        if read(target) != encoded: raise ResearchDeletionRejected("ABORT_RECEIPT_CONFLICT")
    else:
        with target.open("xb") as stream:
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
    _fsync_directory(directory)
    if quiescence_probe() != probe: raise ResearchDeletionRejected("ABORT_BOUNDARY_CHANGED")
    if verify_handled_reset_kernel_continuity(reset_anchor=binding.get("reset_anchor"),
            operation_mtime=paths["operation"].stat().st_mtime, reset_id=reset_id) != kernel:
        raise ResearchDeletionRejected("ABORT_KERNEL_CHANGED")
    if any(read(path) != raw[key] for key, path in paths.items()):
        raise ResearchDeletionRejected("ABORT_POINTER_CHANGED")
    if any(path.exists() or path.is_symlink() for path in forbidden):
        raise ResearchDeletionRejected("ABORT_LATER_STAGE_EVIDENCE_PRESENT")
    for pointer in evidence.get("active_pointers", []):
        path = root / "v3/receipts/ledger_generations_v1" / pointer["ledger"] / "ACTIVE.json"
        if hashlib.sha256(read(path)).hexdigest() != pointer["sha256"]:
            raise ResearchDeletionRejected("ABORT_AUTHORITY_CHANGED")
    paths["active"].unlink()
    _fsync_directory(paths["active"].parent)
    return receipt
