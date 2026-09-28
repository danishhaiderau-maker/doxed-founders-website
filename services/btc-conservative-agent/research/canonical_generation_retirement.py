"""Exact local mirror metadata retirement; never touches raw research payloads."""
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

from research.canonical_data_store import CURRENT_MANIFEST, MANIFEST_JOURNAL, PARITY_STATUS
from research.mirror_generation_lease import MirrorGenerationLease, LEASE_FILE_NAME
from research_exact_deletion import _checked_path, delete_exact_research_files, ResearchDeletionRejected

METADATA = (CURRENT_MANIFEST, MANIFEST_JOURNAL, PARITY_STATUS, ".fly-sync-state.json",
    ".fly-data-sync-loop.last-fresh.json", ".fly-sync-growth-state.json")
RETIRED_MARKER = "canonical_generation_retired.json"


def metadata_snapshot(root, *, max_bytes=32 * 1024**2):
    root = Path(root).absolute()
    if type(max_bytes) is not int or not 0 < max_bytes <= 64 * 1024**2:
        raise ValueError("INVALID_METADATA_BUDGET")
    result, remaining = {}, max_bytes
    for name in METADATA:
        path = _checked_path(root / name, root)
        if not path.exists():
            result[name] = None
            continue
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ResearchDeletionRejected("METADATA_NOT_REGULAR")
        if before.st_size > remaining:
            raise ResearchDeletionRejected("METADATA_BUDGET_EXCEEDED")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ResearchDeletionRejected("METADATA_CHANGED_DURING_SNAPSHOT")
            raw = stream.read(remaining + 1)
        after = path.lstat()
        if len(raw) > remaining:
            raise ResearchDeletionRejected("METADATA_BUDGET_EXCEEDED")
        if (len(raw) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev)
                != (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev)):
            raise ResearchDeletionRejected("METADATA_CHANGED_DURING_SNAPSHOT")
        remaining -= len(raw)
        result[name] = hashlib.sha256(raw).hexdigest()
    return result


def _immutable_marker(path, material):
    encoded = (json.dumps(material, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if path.exists():
        with path.open("rb") as stream:
            existing = stream.read(len(encoded) + 1)
        if existing != encoded:
            raise ResearchDeletionRejected("RETIREMENT_MARKER_CONFLICT")
        return
    fd, name = tempfile.mkstemp(prefix=".retire-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            with path.open("rb") as stream:
                if stream.read(len(encoded) + 1) != encoded:
                    raise ResearchDeletionRejected("RETIREMENT_MARKER_CONFLICT")
    finally:
        os.unlink(name)


def retire_canonical_generation(*, root, expected_snapshot, retired_epoch_id,
        new_epoch_id, receipt_path, quiescent, recovery_states, lease=None):
    """Caller quiesces owners; actual matching mirror lease is also mandatory.

    A tombstone is published before unlink so failure cannot imply fresh data.
    Heartbeat is preserved as diagnostic, explicitly superseded by this marker.
    """
    root = Path(root).absolute()
    if quiescent is not True:
        raise ResearchDeletionRejected("QUIESCENCE_NOT_PROVEN")
    valid_epoch = lambda value: (isinstance(value, str) and bool(re.fullmatch(r"epoch-[A-Za-z0-9._-]+", value))
        and value.lower() not in {"epoch-unknown", "epoch-unavailable", "epoch-none", "epoch-null"})
    if (not isinstance(expected_snapshot, dict) or set(expected_snapshot) != set(METADATA)
            or not all(digest is None or isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
                       for digest in expected_snapshot.values())
            or not valid_epoch(retired_epoch_id) or not valid_epoch(new_epoch_id) or retired_epoch_id == new_epoch_id):
        raise ResearchDeletionRejected("RETIREMENT_IDENTITY_INVALID")
    owned = lease is None
    if owned:
        lease = MirrorGenerationLease(root, owner="explicit-generation-retirement")
        lease.acquire(timeout_seconds=0)
    try:
        if (not isinstance(lease, MirrorGenerationLease) or not lease.held
                or lease.path != root / LEASE_FILE_NAME):
            raise ResearchDeletionRejected("MATCHING_MIRROR_LEASE_REQUIRED")
        if metadata_snapshot(root) != expected_snapshot:
            raise ResearchDeletionRejected("METADATA_SNAPSHOT_CHANGED")
        marker_path = _checked_path(root / RETIRED_MARKER, root)
        material = {"schema": "canonical_generation_retirement_v1", "retired_epoch_id": retired_epoch_id,
            "new_epoch_id": new_epoch_id, "generation_current": False, "ready": False,
            "status": "RETIRED_AWAITING_VERIFIED_FRESH_PROMOTION", "metadata_sha256": expected_snapshot,
            "heartbeat_authoritative": False, "raw_payloads_deleted": False}
        _immutable_marker(marker_path, material)
        paths = [root / name for name, digest in expected_snapshot.items() if digest is not None]
        receipt = delete_exact_research_files(root=root, targets=paths, allowed_paths=paths,
            receipt_path=receipt_path, quiescent=True, recovery_states=recovery_states,
            expected_sha256_by_path={str(root / name): digest for name, digest in expected_snapshot.items() if digest is not None})
        return {**material, "deletion_receipt": receipt}
    finally:
        if owned:
            lease.release()
