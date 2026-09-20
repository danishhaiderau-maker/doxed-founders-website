"""Crash-recoverable laptop-only research reset.

The module has no HTTP client and cannot mutate Fly.  Production path pinning
lives in the CLI; tests inject disposable roots into this engine.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

from research.local_generation_fence import (
    BLOCKED_STATE,
    FENCE_FILE_NAME,
    persist_local_generation_fence,
    read_local_generation_fence,
)
from research.mirror_generation_lease import (
    LEASE_FILE_NAME,
    MirrorGenerationLease,
    MirrorGenerationLeaseTimeout,
)
from research_reset_inventory import _essential as _research_reset_protected_reason


PROTOCOL = "local_research_reset_protocol_v1"
SCOPE_VERSION = "laptop_research_scope_v1"
RECEIPT_SCHEMA = "local_fresh_collection_operation_v1"
CONFIRMATION = "DELETE LAPTOP RESEARCH ONLY"
PROTECTED_CANONICAL_NAMES = {LEASE_FILE_NAME, FENCE_FILE_NAME}
ARCHIVE_META_MAX_BYTES = 4 * 1024 * 1024
RECOVERY_PROTECTION_REASONS = frozenset({"ESSENTIAL_RECOVERY_OR_OWNER_STATE"})


class LocalFreshCollectionRejected(RuntimeError):
    pass


class InjectedResetCrash(BaseException):
    """Test-only crash which intentionally bypasses normal exception handling."""


def _reject(code: str):
    raise LocalFreshCollectionRejected(code)


def _is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _validate_root(
    path: Path, *, expected: Path | None = None, allow_missing: bool = False
) -> Path:
    path = path.absolute()
    if expected is not None and path != expected.absolute():
        _reject("LOCAL_RESET_CANONICAL_ROOT_REQUIRED")
    if not path.exists() and allow_missing:
        parent = path.parent
        if not parent.is_dir():
            _reject("LOCAL_RESET_ROOT_REQUIRED")
        current = parent
        while True:
            if _is_link_or_reparse(current):
                _reject("LOCAL_RESET_LINK_REFUSED")
            if current.parent == current:
                break
            current = current.parent
        return path
    if not path.is_dir():
        _reject("LOCAL_RESET_ROOT_REQUIRED")
    current = path
    while True:
        if _is_link_or_reparse(current):
            _reject("LOCAL_RESET_LINK_REFUSED")
        if current.parent == current:
            break
        current = current.parent
    return path


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, candidate = tempfile.mkstemp(prefix=".local-reset-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(candidate, path)
    finally:
        if os.path.exists(candidate):
            os.unlink(candidate)


def _read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32 * 1024**2:
        _reject("LOCAL_RESET_RECEIPT_INVALID")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise LocalFreshCollectionRejected("LOCAL_RESET_RECEIPT_INVALID") from exc
    if not isinstance(value, dict):
        _reject("LOCAL_RESET_RECEIPT_INVALID")
    return value


def _sha256_file(path: Path) -> tuple[str, int]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    after = path.stat()
    signature = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
    if signature(before) != signature(after):
        _reject("LOCAL_RESET_FILE_CHANGED")
    return digest.hexdigest(), before.st_size


def _relative_file_inventory(root: Path, *, protected_names=frozenset()) -> list[dict]:
    rows: list[dict] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                if _is_link_or_reparse(path):
                    _reject("LOCAL_RESET_LINK_REFUSED")
                relative = path.relative_to(root).as_posix()
                if directory == root and entry.name in protected_names:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                elif entry.is_file(follow_symlinks=False):
                    digest, size = _sha256_file(path)
                    rows.append({"relative_path": relative, "sha256": digest, "bytes": size})
                else:
                    _reject("LOCAL_RESET_NONREGULAR_FILE_REFUSED")
    return sorted(rows, key=lambda row: row["relative_path"])


def _safe_relative_text(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    parts = value.split("/")
    return not value.startswith("/") and all(part not in {"", ".", ".."} for part in parts)


def _archive_dependency_protection(root: Path, rows: list[dict]) -> tuple[dict[str, str], list[str]]:
    """Return archive-session closures and fail-closed recovery blockers.

    An archive receipt can hide a protected source behind a sequence/digest
    payload name.  If one source is protected, retain its complete session so
    the receipt never points at deleted siblings.  Malformed archive metadata
    is unresolved authority and blocks the reset before the first unlink.
    """
    row_by_relative = {row["relative_path"]: row for row in rows}
    protected: dict[str, str] = {}
    blockers: list[str] = []
    for metadata_row in rows:
        relative = metadata_row["relative_path"]
        if PurePosixPath(relative).name != "archive_meta.json":
            continue
        path = _safe_target(root, relative)
        try:
            if metadata_row["bytes"] > ARCHIVE_META_MAX_BYTES:
                raise ValueError("metadata too large")
            metadata = _read_json(path)
            digest, size = _sha256_file(path)
            if digest != metadata_row["sha256"] or size != metadata_row["bytes"]:
                raise ValueError("metadata changed")
            if metadata.get("schema") != "research_archive_receipt_v2":
                raise ValueError("unknown metadata schema")
            source_rows = metadata.get("source_inventory")
            if not isinstance(source_rows, list) or len(source_rows) > 200_000:
                raise ValueError("invalid source inventory")
            protected_source_reasons: list[str] = []
            metadata_parent = PurePosixPath(relative).parent
            for source_row in source_rows:
                if not isinstance(source_row, dict):
                    raise ValueError("invalid source row")
                source = source_row.get("path")
                preserved = source_row.get("preserved_path")
                if not _safe_relative_text(source) or not _safe_relative_text(preserved):
                    raise ValueError("unsafe archive binding")
                target = (metadata_parent / PurePosixPath(preserved)).as_posix()
                if target not in row_by_relative:
                    raise ValueError("archive payload missing")
                target_row = row_by_relative[target]
                expected_size = source_row.get("preserved_bytes")
                expected_hash = source_row.get("preserved_sha256")
                if (
                    type(expected_size) is not int
                    or expected_size != target_row["bytes"]
                    or not isinstance(expected_hash, str)
                    or len(expected_hash) != 64
                    or any(char not in "0123456789abcdef" for char in expected_hash)
                    or expected_hash != target_row["sha256"]
                ):
                    raise ValueError("archive payload binding mismatch")
                reason = _research_reset_protected_reason(source)
                if reason:
                    protected_source_reasons.append(reason)
            prefix = "" if str(metadata_parent) == "." else metadata_parent.as_posix() + "/"
            direct_session_reasons = [
                reason
                for candidate in rows
                if not prefix or candidate["relative_path"].startswith(prefix)
                for reason in [_research_reset_protected_reason(candidate["relative_path"])]
                if reason
            ]
            protected_source_reasons.extend(direct_session_reasons)
            if not protected_source_reasons:
                continue
            closure_reason = "PROTECTED_ARCHIVE_DEPENDENCY_CLOSURE:" + ",".join(
                sorted(set(protected_source_reasons))
            )
            for candidate in rows:
                if not prefix or candidate["relative_path"].startswith(prefix):
                    protected[candidate["relative_path"]] = closure_reason
            if any(reason in RECOVERY_PROTECTION_REASONS for reason in protected_source_reasons):
                blockers.append("PROTECTED_ARCHIVE_RECOVERY_REQUIRES_AUDIT:" + relative)
        except (LocalFreshCollectionRejected, OSError, TypeError, ValueError):
            blockers.append("UNRESOLVED_ARCHIVE_METADATA_REQUIRES_AUDIT:" + relative)
    return protected, blockers


def _classified_inventory(
    root: Path, *, root_label: str, protected_names=frozenset()
) -> tuple[list[dict], list[dict], list[str]]:
    rows = _relative_file_inventory(root, protected_names=protected_names)
    protected: dict[str, str] = {}
    blockers: list[str] = []
    for row in rows:
        reason = _research_reset_protected_reason(row["relative_path"])
        if reason:
            protected[row["relative_path"]] = reason
            if reason in RECOVERY_PROTECTION_REASONS:
                blockers.append(
                    "PROTECTED_RECOVERY_REQUIRES_AUDIT:" + row["relative_path"]
                )
    archive_protected, archive_blockers = _archive_dependency_protection(root, rows)
    protected.update(archive_protected)
    blockers.extend(archive_blockers)
    eligible, retained = [], []
    for row in rows:
        if row["relative_path"] in protected:
            retained.append(
                dict(row, root=root_label, reason=protected[row["relative_path"]])
            )
        else:
            eligible.append(dict(row, root=root_label))
    return eligible, retained, sorted(set(blockers))


def _safe_target(root: Path, relative: str) -> Path:
    parts = relative.split("/")
    if (
        not relative
        or "\\" in relative
        or relative.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
    ):
        _reject("LOCAL_RESET_PATH_ESCAPE")
    candidate = root.joinpath(*parts)
    try:
        candidate.relative_to(root)
    except ValueError:
        _reject("LOCAL_RESET_PATH_ESCAPE")
    parent = candidate.parent
    while True:
        if parent.exists() and _is_link_or_reparse(parent):
            _reject("LOCAL_RESET_LINK_REFUSED")
        if parent == root:
            break
        if parent.parent == parent:
            _reject("LOCAL_RESET_PATH_ESCAPE")
        parent = parent.parent
    return candidate


def _source_epoch(root: Path) -> str | None:
    path = root / "canonical_dataset_current.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return str(value.get("dataset_epoch") or value.get("collection_epoch_id") or "") or None


def operation_path(state_root: Path, operation_id: str) -> Path:
    if not isinstance(operation_id, str) or len(operation_id) != 32 or any(
        char not in "0123456789abcdef" for char in operation_id
    ):
        _reject("LOCAL_RESET_OPERATION_ID_INVALID")
    root = Path(state_root).absolute()
    path = root / "operations" / operation_id / "operation.json"
    current = path.parent
    while True:
        if current.exists() and _is_link_or_reparse(current):
            _reject("LOCAL_RESET_RECEIPT_LINK_REFUSED")
        if current == root:
            break
        if current.parent == current:
            _reject("LOCAL_RESET_RECEIPT_PATH_INVALID")
        current = current.parent
    ancestor = root
    while not ancestor.exists() and ancestor.parent != ancestor:
        ancestor = ancestor.parent
    while True:
        if _is_link_or_reparse(ancestor):
            _reject("LOCAL_RESET_RECEIPT_LINK_REFUSED")
        if ancestor.parent == ancestor:
            break
        ancestor = ancestor.parent
    return path


def queue_operation(
    *,
    canonical_root,
    archive_root,
    state_root,
    request: dict,
    expected_canonical_root: Path | None = None,
    expected_archive_root: Path | None = None,
) -> tuple[dict, bool]:
    canonical = _validate_root(Path(canonical_root), expected=expected_canonical_root)
    archives = _validate_root(
        Path(archive_root), expected=expected_archive_root, allow_missing=True
    )
    if archives == canonical or canonical in archives.parents or archives in canonical.parents:
        _reject("LOCAL_RESET_ROOTS_OVERLAP")
    archives.mkdir(exist_ok=True)
    archives = _validate_root(archives)
    if not isinstance(request, dict) or set(request) != {
        "request_id", "confirmation", "expected_local_generation"
    }:
        _reject("LOCAL_RESET_REQUEST_INVALID")
    request_id = request.get("request_id")
    if (
        not isinstance(request_id, str)
        or len(request_id) != 32
        or any(char not in "0123456789abcdef" for char in request_id)
    ):
        _reject("LOCAL_RESET_REQUEST_INVALID")
    expected_generation = request.get("expected_local_generation")
    if not isinstance(expected_generation, str) or not expected_generation:
        _reject("LOCAL_RESET_EXPECTED_GENERATION_REQUIRED")
    operation_id = request_id
    path = operation_path(Path(state_root), operation_id)
    body = {
        "request_id": request_id,
        "confirmation": request.get("confirmation"),
        "expected_local_generation": expected_generation,
    }
    binding_hash = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if path.exists():
        prior = _read_json(path)
        if prior.get("request_sha256") != binding_hash:
            _reject("LOCAL_RESET_REPLAY_CONFLICT")
        if prior.get("status") == "COMPLETE":
            _verify_completion_pin(path, prior)
        return prior, True
    if request.get("confirmation") != CONFIRMATION:
        _reject("LOCAL_RESET_REQUEST_INVALID")
    fence = read_local_generation_fence(canonical)
    if fence is not None:
        _reject("LOCAL_RESET_BLOCKED_PENDING_VERIFIED_IMPORT")
    current_generation = (
        str(fence["local_generation"]) if fence else (_source_epoch(canonical) or "unversioned-local")
    )
    if expected_generation != current_generation:
        _reject("LOCAL_RESET_LOCAL_GENERATION_CHANGED")
    now = time.time()
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "protocol": PROTOCOL,
        "scope_version": SCOPE_VERSION,
        "operation_id": operation_id,
        "request_id": request_id,
        "request_sha256": binding_hash,
        "status": "QUEUED",
        "queued_at": now,
        "canonical_root": str(canonical),
        "archive_root": str(archives),
        "mirrored_source_epoch_before": _source_epoch(canonical),
        "fly_mutation_requested": False,
        "remote_http_writes": 0,
        "sync_state": None,
        "retained_categories": [
            "local operation receipts",
            "mirror generation lease",
            "local generation fence",
            "source configuration and credentials outside eligible roots",
            "no Fly mutation requested by this operation",
        ],
    }
    _atomic_json(path, receipt)
    return receipt, False


def read_operation(*, state_root, operation_id: str) -> dict:
    path = operation_path(Path(state_root), operation_id)
    if not path.exists():
        _reject("LOCAL_RESET_OPERATION_NOT_FOUND")
    receipt = _read_json(path)
    if receipt.get("status") == "COMPLETE":
        _verify_completion_pin(path, receipt)
    return receipt


def _verify_completion_pin(operation_receipt_path: Path, receipt: dict) -> dict:
    completion_path = operation_receipt_path.parent / "completion.json"
    if receipt.get("completion_receipt_path") != str(completion_path):
        _reject("LOCAL_RESET_COMPLETION_PIN_INVALID")
    if not completion_path.is_file() or completion_path.is_symlink():
        _reject("LOCAL_RESET_COMPLETION_PIN_INVALID")
    digest, _size = _sha256_file(completion_path)
    if digest != receipt.get("completion_receipt_sha256"):
        _reject("LOCAL_RESET_COMPLETION_PIN_INVALID")
    completion = _read_json(completion_path)
    retained_rows = receipt.get("retained", [])
    retained_digest = hashlib.sha256(
        json.dumps(retained_rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        completion.get("schema") != "local_fresh_collection_completion_v1"
        or completion.get("operation_id") != receipt.get("operation_id")
        or completion.get("status") != "COMPLETE"
        or completion.get("remaining_rows") != []
        or completion.get("deleted_file_count") != receipt.get("deleted_file_count")
        or completion.get("deleted_bytes") != receipt.get("deleted_bytes")
        or completion.get("retained_file_count") != receipt.get("retained_file_count")
        or completion.get("retained_bytes") != receipt.get("retained_bytes")
        or completion.get("retained_inventory_sha256")
        != receipt.get("retained_inventory_sha256")
        or completion.get("retained_inventory_sha256") != retained_digest
        or completion.get("retained_rows") != retained_rows
        or completion.get("fly_mutation_requested") is not False
    ):
        _reject("LOCAL_RESET_COMPLETION_PIN_INVALID")
    return completion


def capability_status(*, canonical_root, archive_root) -> dict:
    canonical = _validate_root(Path(canonical_root))
    archives = _validate_root(Path(archive_root), allow_missing=True)
    fence = read_local_generation_fence(canonical)
    return {
        "ok": True,
        "protocol": PROTOCOL,
        "scope_version": SCOPE_VERSION,
        "scope": "LAPTOP_RESEARCH_ONLY",
        "canonical_root": str(canonical),
        "archive_root": str(archives),
        "current_local_generation": (
            str(fence["local_generation"]) if fence else (_source_epoch(canonical) or "unversioned-local")
        ),
        "sync_state": str(fence["state"]) if fence else "READY",
        "fly_mutation_supported": False,
        "fly_mutation_requested": False,
    }


def _write_status(path: Path, receipt: dict, status: str, **fields) -> None:
    receipt.update(fields)
    receipt["status"] = status
    receipt["updated_at"] = time.time()
    _atomic_json(path, receipt)


def _reconcile_and_delete(
    *, receipt_path: Path, receipt: dict, canonical: Path, archives: Path, crash_at: str | None
) -> None:
    inventory = receipt["inventory"]
    deleted = receipt.setdefault("deleted", [])
    deleted_keys = {(row["root"], row["relative_path"]) for row in deleted}
    for index, row in enumerate(inventory):
        key = (row["root"], row["relative_path"])
        root = canonical if row["root"] == "canonical" else archives
        target = _safe_target(root, row["relative_path"])
        if key in deleted_keys:
            if target.exists():
                _reject("LOCAL_RESET_DELETED_FILE_REAPPEARED")
            continue
        existed = target.exists()
        if existed:
            if _is_link_or_reparse(target) or not target.is_file():
                _reject("LOCAL_RESET_TARGET_TYPE_CHANGED")
            digest, size = _sha256_file(target)
            if digest != row["sha256"] or size != row["bytes"]:
                _reject("LOCAL_RESET_FILE_CHANGED")
            target.unlink()
            if crash_at == f"after_unlink_{index}":
                raise InjectedResetCrash(crash_at)
        deleted.append(dict(row, deleted_at=time.time(), recovered_missing_after_crash=not existed))
        _write_status(receipt_path, receipt, "RUNNING", deletion_started=True)
    for root, protected in ((canonical, PROTECTED_CANONICAL_NAMES), (archives, set())):
        for directory, child_dirs, _files in os.walk(root, topdown=False):
            here = Path(directory)
            for name in child_dirs:
                child = here / name
                if here == root and name in protected:
                    continue
                try:
                    child.rmdir()
                except OSError:
                    pass
    remaining: list[dict] = []
    current_retained: list[dict] = []
    reconciliation_blockers: list[str] = []
    for label, root, protected_names in (
        ("canonical", canonical, PROTECTED_CANONICAL_NAMES),
        ("archives", archives, set()),
    ):
        eligible, retained, blockers = _classified_inventory(
            root, root_label=label, protected_names=protected_names
        )
        remaining.extend(eligible)
        current_retained.extend(retained)
        reconciliation_blockers.extend(blockers)
    if reconciliation_blockers:
        receipt["protected_blockers"] = sorted(set(reconciliation_blockers))
        _reject("LOCAL_RESET_PROTECTED_RECOVERY_REQUIRES_AUDIT")
    if remaining:
        receipt["remaining"] = remaining
        _reject("LOCAL_RESET_RECONCILIATION_INCOMPLETE")
    retained_signature = lambda row: (
        row["root"], row["relative_path"], row["sha256"], row["bytes"], row["reason"]
    )
    if sorted(map(retained_signature, current_retained)) != sorted(
        map(retained_signature, receipt.get("retained", []))
    ):
        _reject("LOCAL_RESET_RETAINED_FILE_CHANGED")


def execute_operation(
    *, state_root, operation_id: str, owner_auditor, crash_at: str | None = None,
    expected_canonical_root: Path | None = None,
    expected_archive_root: Path | None = None,
) -> dict:
    operation_directory = operation_path(Path(state_root), operation_id).parent
    operation_lease = MirrorGenerationLease(
        operation_directory, owner="laptop-only-reset-operation"
    )
    try:
        operation_lease.acquire(timeout_seconds=0)
    except MirrorGenerationLeaseTimeout:
        # Another worker owns the same idempotent request. Status polling is
        # authoritative; a replay never starts a second deletion transaction.
        return read_operation(state_root=state_root, operation_id=operation_id)
    try:
        return _execute_operation_locked(
            state_root=state_root,
            operation_id=operation_id,
            owner_auditor=owner_auditor,
            crash_at=crash_at,
            expected_canonical_root=expected_canonical_root,
            expected_archive_root=expected_archive_root,
        )
    finally:
        operation_lease.release()


def _execute_operation_locked(
    *, state_root, operation_id: str, owner_auditor, crash_at: str | None = None,
    expected_canonical_root: Path | None = None,
    expected_archive_root: Path | None = None,
) -> dict:
    path = operation_path(Path(state_root), operation_id)
    receipt = _read_json(path)
    if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("operation_id") != operation_id:
        _reject("LOCAL_RESET_RECEIPT_INVALID")
    if receipt.get("status") == "COMPLETE":
        _verify_completion_pin(path, receipt)
        return receipt
    if (
        receipt.get("status") == "BLOCKED"
        and receipt.get("error") == "LOCAL_RESET_PROTECTED_RECOVERY_REQUIRES_AUDIT"
    ):
        return receipt
    canonical = _validate_root(
        Path(receipt["canonical_root"]), expected=expected_canonical_root
    )
    archives = _validate_root(Path(receipt["archive_root"]), expected=expected_archive_root)
    try:
        if crash_at == "before_fence":
            raise InjectedResetCrash(crash_at)
        fence_payload = receipt.get("fence")
        if fence_payload is None:
            fence_payload = {
                "operation_id": operation_id,
                "local_generation": "local-reset-" + uuid.uuid4().hex,
                "tombstone_id": "tombstone-" + uuid.uuid4().hex,
                "created_at": time.time(),
                "mirrored_source_epoch": receipt.get("mirrored_source_epoch_before"),
                "fly_mutation_requested": False,
            }
            receipt["fence"] = fence_payload
            _write_status(
                path, receipt, "RUNNING", sync_state=BLOCKED_STATE, fence_persisting=True
            )
        persisted = persist_local_generation_fence(canonical, fence_payload)
        receipt["fence"] = persisted
        _write_status(path, receipt, "RUNNING", fence_persisted=True, fence_persisting=False)
        if crash_at == "after_fence":
            raise InjectedResetCrash(crash_at)

        first_audit = owner_auditor(canonical, archives)
        if not isinstance(first_audit, dict) or first_audit.get("safe") is not True:
            _write_status(path, receipt, "BLOCKED", owner_evidence=first_audit,
                          error="LOCAL_RESET_ACTIVE_OWNER")
            return receipt
        with MirrorGenerationLease(canonical, owner="laptop-only-fresh-collection").acquire(
            timeout_seconds=0
        ) as lease:
            if not lease.held or lease.path != canonical / LEASE_FILE_NAME:
                _reject("LOCAL_RESET_MATCHING_LEASE_REQUIRED")
            second_audit = owner_auditor(canonical, archives)
            receipt["owner_evidence"] = {"before_lease": first_audit, "under_lease": second_audit}
            if not isinstance(second_audit, dict) or second_audit.get("safe") is not True:
                _write_status(path, receipt, "BLOCKED", error="LOCAL_RESET_ACTIVE_OWNER")
                return receipt
            if "inventory" not in receipt:
                canonical_rows, canonical_retained, canonical_blockers = _classified_inventory(
                    canonical,
                    root_label="canonical",
                    protected_names=PROTECTED_CANONICAL_NAMES,
                )
                archive_rows, archive_retained, archive_blockers = _classified_inventory(
                    archives, root_label="archives"
                )
                receipt["inventory"] = canonical_rows + archive_rows
                receipt["retained"] = canonical_retained + archive_retained
                receipt["planned_file_count"] = len(receipt["inventory"])
                receipt["planned_bytes"] = sum(row["bytes"] for row in receipt["inventory"])
                receipt["retained_file_count"] = len(receipt["retained"])
                receipt["retained_bytes"] = sum(row["bytes"] for row in receipt["retained"])
                receipt["retained_categories"] = sorted(
                    {row["reason"] for row in receipt["retained"]}
                    | {
                        "local operation receipts",
                        "mirror generation lease",
                        "local generation fence",
                        "source configuration and credentials outside eligible roots",
                        "no Fly mutation requested by this operation",
                    }
                )
                receipt["deleted"] = []
                _write_status(path, receipt, "RUNNING", inventory_persisted=True)
                protected_blockers = sorted(
                    set(canonical_blockers + archive_blockers)
                )
                if protected_blockers:
                    _write_status(
                        path,
                        receipt,
                        "BLOCKED",
                        error="LOCAL_RESET_PROTECTED_RECOVERY_REQUIRES_AUDIT",
                        protected_blockers=protected_blockers,
                    )
                    return receipt
            if crash_at == "after_inventory":
                raise InjectedResetCrash(crash_at)
            _reconcile_and_delete(
                receipt_path=path,
                receipt=receipt,
                canonical=canonical,
                archives=archives,
                crash_at=crash_at,
            )
            deleted = receipt.get("deleted", [])
            if len(deleted) != len(receipt["inventory"]):
                _reject("LOCAL_RESET_RECONCILIATION_INCOMPLETE")
            completion_path = path.parent / "completion.json"
            completion_receipt = {
                "schema": "local_fresh_collection_completion_v1",
                "operation_id": operation_id,
                "status": "COMPLETE",
                "reconciled_at": time.time(),
                "deleted_file_count": len(deleted),
                "deleted_bytes": sum(row["bytes"] for row in deleted),
                "inventory_sha256": hashlib.sha256(
                    json.dumps(receipt["inventory"], sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "deleted_rows": deleted,
                "remaining_rows": [],
                "retained_rows": receipt.get("retained", []),
                "retained_file_count": receipt.get("retained_file_count", 0),
                "retained_bytes": receipt.get("retained_bytes", 0),
                "retained_inventory_sha256": hashlib.sha256(
                    json.dumps(
                        receipt.get("retained", []),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                "fly_mutation_requested": False,
                "sync_state": BLOCKED_STATE,
            }
            _atomic_json(completion_path, completion_receipt)
            completion_sha256, _completion_bytes = _sha256_file(completion_path)
            _write_status(
                path,
                receipt,
                "COMPLETE",
                completed_at=time.time(),
                deleted_file_count=len(deleted),
                deleted_bytes=sum(row["bytes"] for row in deleted),
                retained_file_count=receipt.get("retained_file_count", 0),
                retained_bytes=receipt.get("retained_bytes", 0),
                retained_inventory_sha256=completion_receipt[
                    "retained_inventory_sha256"
                ],
                exact_hash_reconciliation=True,
                deletion_reconciled=True,
                completion_receipt_path=str(completion_path),
                completion_receipt_sha256=completion_sha256,
                sync_state=BLOCKED_STATE,
                fly_mutation_requested=False,
                remote_http_writes=0,
            )
            return receipt
    except InjectedResetCrash:
        raise
    except BaseException as exc:
        status = "PARTIAL" if receipt.get("deleted") else "FAILED"
        _write_status(path, receipt, status, error=str(exc))
        return receipt
