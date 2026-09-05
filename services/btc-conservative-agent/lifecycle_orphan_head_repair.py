"""One incident only: preserve an unpublished append-head temp, never replay it.

The volume mirror lease excludes the real reset path; index and ledger locks
exclude materialization and append. A fresh caller probe additionally verifies
that no existing sync/inventory/snapshot worker is using the receipt namespace.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from emergency_evidence_wal import EmergencyEvidenceWal
from lifecycle_tail_repair import _exclusive_index_lock
from research_v3_contract import canonical_json
from research_v3_store import V3EvidenceStore, _fsync_directory
from research.mirror_generation_lease import MirrorGenerationLease

TEMP_NAME = ".lifecycle.json.k89houml.tmp"
SOURCE_SIZE = 5036
SOURCE_INODE = 36456
SOURCE_DEVICE = 65056
SOURCE_MTIME_NS = 1788515077581752842
SOURCE_SHA256 = "4c10a5638d32b9419e864750e02463646e7c2b009bb99fb6e5a60af3f23a0051"
OLD_REVISION = "df45887e15264d5708b75c4ed48424171d3da1a0"
OLD_EPOCH = "epoch-4661cc4ae5c1cb6648b82685"
CONFIG_SHA256 = "87ec52b3df04d50580e8fcd632de2a5996f253c0ae55bbe5be2eb12945dabafd"
OFFSET = 37896046
ROW_LENGTH = 3798
ROW_SHA256 = "6fb2318628cf7c36685e06f8a8dae1b5ce8373b34ca74086aa30980a12661b2a"
SCHEMA = "unpublished_lifecycle_head_preservation_v1"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _checked(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if not item.exists() and not item.is_symlink():
            continue
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("ORPHAN_REPAIR_LINK_REFUSED")
    return path


def _no_open_fds(path):
    if os.name != "posix" or not Path("/proc").is_dir():
        raise ValueError("ORPHAN_REPAIR_FD_PROOF_UNAVAILABLE")
    wanted = path.stat()
    started = time.monotonic()
    processes = descriptors = 0
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        processes += 1
        if processes > 1024 or time.monotonic() - started > 5:
            raise ValueError("ORPHAN_REPAIR_FD_PROOF_LIMIT")
        try:
            for fd in (proc / "fd").iterdir():
                descriptors += 1
                if descriptors > 100000 or time.monotonic() - started > 5:
                    raise ValueError("ORPHAN_REPAIR_FD_PROOF_LIMIT")
                try:
                    item = fd.stat()
                except FileNotFoundError:
                    continue
                if (item.st_dev, item.st_ino) == (wanted.st_dev, wanted.st_ino):
                    raise ValueError("ORPHAN_REPAIR_SOURCE_OPEN")
        except FileNotFoundError:
            continue


def _write_once(path, raw):
    _checked(path)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("ORPHAN_REPAIR_ARTIFACT_CONFLICT")
        return
    temporary = path.with_name(".preserve-" + uuid.uuid4().hex + ".tmp")
    # A crash during write leaves only an unpublished temp, never a partial
    # authoritative artifact. Hard-link publication is atomic and no-overwrite.
    with temporary.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError("ORPHAN_REPAIR_ARTIFACT_CONFLICT")
    _fsync_directory(path.parent)
    temporary.unlink()
    _fsync_directory(path.parent)


def _durable_mkdir(path):
    path = _checked(path)
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir()
        _fsync_directory(directory)
        _fsync_directory(directory.parent)
    _checked(path)


def _probe(probe, identity):
    evidence = probe()
    if (not isinstance(evidence, dict) or evidence.get("identity") != identity
            or type(evidence.get("observed_unix")) not in (int, float)
            or not 0 <= time.time() - evidence["observed_unix"] <= 5
            or any(evidence.get(key) is not False for key in (
                "inventory_active", "snapshot_active", "download_active", "lifecycle_active"))):
        raise ValueError("ORPHAN_REPAIR_CURRENT_OWNERSHIP_NOT_PROVEN")
    return evidence


@contextmanager
def _runtime_boundary_lease(root, identity, probe):
    if root.name != "runtime":
        raise ValueError("ORPHAN_REPAIR_RUNTIME_LAYOUT_REQUIRED")
    volume = _checked(root.parent)
    _checked(volume / ".fly-mirror-generation.lease")
    lease = MirrorGenerationLease(volume, owner="exact-orphan-head-preservation")
    lease.acquire(timeout_seconds=0)
    try:
        _probe(probe, identity)
        yield identity
    finally:
        lease.release()


def preserve_exact_orphan(root, *, expected_identity, runtime_probe):
    """Copy+fsync evidence before unlink, never replay; caller bounds process time.

    Probe must obtain current real worker/identity evidence, never constants.
    Run under an outer process deadline because the existing ledger advisory
    lock blocks. A killed process leaves either source or durable evidence intact.
    """
    root = _checked(root)
    if not callable(runtime_probe):
        raise ValueError("ORPHAN_REPAIR_RUNTIME_PROBE_REQUIRED")
    with _runtime_boundary_lease(root, expected_identity, runtime_probe) as guarded_identity:
        if guarded_identity != expected_identity or not isinstance(guarded_identity, dict):
            raise ValueError("ORPHAN_REPAIR_EPOCH_IDENTITY_CHANGED")
        if (expected_identity.get("epoch_id") != OLD_EPOCH
                or expected_identity.get("tile_config_signature") != CONFIG_SHA256):
            raise ValueError("ORPHAN_REPAIR_WRONG_EPOCH")
        store = V3EvidenceStore.open_read_only(root)
        ledger = _checked(store.ledger_path("lifecycle"))
        source = _checked(store._append_head_path("lifecycle").with_name(TEMP_NAME))
        destination = _checked(root / "v3/receipts/orphan_append_head_forensics_v1" / SOURCE_SHA256)
        with _exclusive_index_lock(root), store._exclusive(ledger):
            wal = EmergencyEvidenceWal.inspect_existing(
                _checked(root / "v3/emergency_evidence_wal_v2"), identity=expected_identity)
            if wal.get("records") != [] or type(wal.get("deferred_count")) is not int or wal["deferred_count"] != 0 or wal.get("alarms") != []:
                raise ValueError("ORPHAN_REPAIR_WAL_NOT_EMPTY")
            artifact = _checked(destination / "unpublished-head.json")
            prepared_path = _checked(destination / "PREPARED.json")
            completed_path = _checked(destination / "COMPLETED.json")
            if source.exists():
                source_stat = source.stat()
                if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
                    raise ValueError("ORPHAN_REPAIR_SOURCE_NOT_EXCLUSIVE_REGULAR")
                if (source_stat.st_ino, source_stat.st_dev, source_stat.st_mtime_ns) != (
                        SOURCE_INODE, SOURCE_DEVICE, SOURCE_MTIME_NS):
                    raise ValueError("ORPHAN_REPAIR_SOURCE_FINGERPRINT_CHANGED")
                _no_open_fds(source)
                raw = source.read_bytes()
            else:
                if not prepared_path.is_file() or not artifact.is_file():
                    raise ValueError("ORPHAN_REPAIR_SOURCE_MISSING_WITHOUT_RECEIPT")
                raw = artifact.read_bytes()
            if len(raw) != SOURCE_SIZE or _sha(raw) != SOURCE_SHA256:
                raise ValueError("ORPHAN_REPAIR_EXACT_SOURCE_MISMATCH")
            row = json.loads(raw)
            material = dict(row)
            binding = material.pop("binding_sha256", None)
            if binding != _sha(canonical_json(material).encode()):
                raise ValueError("ORPHAN_REPAIR_BINDING_INVALID")
            expected_old = dict(expected_identity, source_revision=OLD_REVISION, deployed_revision=OLD_REVISION)
            payload = row.get("row_payload_utf8", "").encode()
            if (row.get("schema") != "v3_ledger_append_head_v1" or row.get("state") != "PREPARED"
                    or row.get("ledger") != "lifecycle" or row.get("identity") != expected_old
                    or row.get("offset") != OFFSET or row.get("length") != ROW_LENGTH
                    or len(payload) != ROW_LENGTH or _sha(payload) != ROW_SHA256
                    or row.get("row_sha256") != ROW_SHA256):
                raise ValueError("ORPHAN_REPAIR_PAYLOAD_SCOPE_MISMATCH")
            head = _checked(store._append_head_path("lifecycle"))
            receipt = _checked(store._record_receipt_path("lifecycle", row["record_id"]))
            if head.exists() or receipt.exists():
                raise ValueError("ORPHAN_REPAIR_PUBLISHED_AUTHORITY_PRESENT")
            ledger_stat = ledger.stat()
            ledger_fingerprint = (ledger_stat.st_dev, ledger_stat.st_ino, ledger_stat.st_size, ledger_stat.st_mtime_ns)
            with ledger.open("rb") as handle:
                handle.seek(OFFSET)
                segment = handle.read(ROW_LENGTH)
            if len(segment) != ROW_LENGTH or segment == payload:
                raise ValueError("ORPHAN_REPAIR_RANGE_NOT_DIVERGED")
            prepared = {"schema": SCHEMA, "state": "PREPARED", "root": str(root),
                "source": str(source), "artifact": str(artifact), "size": SOURCE_SIZE,
                "source_inode": SOURCE_INODE, "source_device": SOURCE_DEVICE, "source_mtime_ns": SOURCE_MTIME_NS,
                "sha256": SOURCE_SHA256, "old_identity": row["identity"],
                "boundary_identity": expected_identity, "record_id": row["record_id"],
                "offset": OFFSET, "length": ROW_LENGTH, "ledger_slice_sha256": _sha(segment),
                "classification": "UNKNOWN_UNPUBLISHED_NOT_REPLAYED", "ranking_eligible": False}
            _durable_mkdir(destination)
            _write_once(artifact, raw)
            _write_once(prepared_path, (canonical_json(prepared) + "\n").encode())
            if source.exists():
                _probe(runtime_probe, expected_identity)
                _no_open_fds(source)
                current_source = _checked(source).stat()
                current_ledger = ledger.stat()
                if (source.read_bytes() != raw
                        or (current_source.st_ino, current_source.st_dev, current_source.st_mtime_ns) != (
                            SOURCE_INODE, SOURCE_DEVICE, SOURCE_MTIME_NS)
                        or (current_ledger.st_dev, current_ledger.st_ino, current_ledger.st_size, current_ledger.st_mtime_ns) != ledger_fingerprint
                        or head.exists() or receipt.exists()):
                    raise ValueError("ORPHAN_REPAIR_SOURCE_CHANGED")
                source.unlink()
                _fsync_directory(source.parent)
            completed = dict(prepared, state="COMPLETED", source_removed=True,
                             raw_payload_preserved=True, replay_performed=False)
            _write_once(completed_path, (canonical_json(completed) + "\n").encode())
            return completed
