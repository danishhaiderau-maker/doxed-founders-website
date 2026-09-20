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
SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
READINESS_SCOPE = "local_research_owners_and_relaunch_authorities_v1"
_OWNER_CATEGORY_ALLOWLIST = frozenset(
    {
        "sync_owner",
        "analyzer_owner",
        "dashboard_owner",
        "stability_supervisor_owner",
        "batch_resume_owner",
        "relaunch_supervisor_owner",
        "archive_writer_owner",
        "migration_owner",
        "generation_maintenance_owner",
    }
)
_RELAUNCH_CATEGORY_ALLOWLIST = frozenset(
    {
        "sync_relaunch_authority",
        "stability_supervisor_authority",
        "showcase_autostart_authority",
        "stack_supervisor_authority",
        "unknown_relaunch_authority",
    }
)
_BLOCKER_CATEGORY_ALLOWLIST = frozenset(
    {
        "active_local_research_owner",
        "enabled_relaunch_authority",
        "owner_audit_invalid",
        "owner_audit_unavailable",
    }
)


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


def _validate_scope_topology(
    canonical_root: Path,
    archive_root: Path,
    *,
    expected_canonical_root: Path | None = None,
    expected_archive_root: Path | None = None,
    runtime_agent_root: Path | None = None,
    allow_missing_archive: bool = False,
) -> tuple[Path, Path]:
    canonical = _validate_root(canonical_root, expected=expected_canonical_root)
    archives = _validate_root(
        archive_root,
        expected=expected_archive_root,
        allow_missing=allow_missing_archive,
    )
    if archives == canonical or canonical in archives.parents or archives in canonical.parents:
        _reject("LOCAL_RESET_ROOTS_OVERLAP")
    if canonical.exists() and archives.exists():
        try:
            if os.path.samefile(canonical, archives):
                _reject("LOCAL_RESET_DUPLICATE_TARGET")
        except OSError as exc:
            raise LocalFreshCollectionRejected("LOCAL_RESET_TOPOLOGY_UNAVAILABLE") from exc
    if runtime_agent_root is not None:
        runtime = _validate_root(Path(runtime_agent_root))
        if runtime != canonical.parent:
            _reject("LOCAL_RESET_RUNTIME_ROOT_MISMATCH")
        try:
            if not os.path.samefile(runtime, canonical.parent):
                _reject("LOCAL_RESET_RUNTIME_ROOT_MISMATCH")
        except OSError as exc:
            raise LocalFreshCollectionRejected("LOCAL_RESET_TOPOLOGY_UNAVAILABLE") from exc
    return canonical, archives


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


def _local_protected_reason(relative: str) -> str | None:
    reason = _research_reset_protected_reason(relative)
    if reason:
        return reason
    if PurePosixPath(relative.lower()).name.endswith("-journal"):
        return "ESSENTIAL_RECOVERY_OR_OWNER_STATE"
    return None


def _known_sqlite_dependency_base(relative: str) -> bool:
    if relative in {
        "research_accumulator/research_trades_v983.db",
        "v3/lifecycle_bundle_index/lifecycle_index.sqlite3",
    }:
        return True
    parts = PurePosixPath(relative).parts
    return (
        len(parts) == 4
        and parts[:2] == ("derived", "policy-evidence")
        and parts[2].startswith("generation-")
        and len(parts[2]) == len("generation-") + 64
        and all(char in "0123456789abcdef" for char in parts[2][len("generation-"):])
        and parts[3] == "results.sqlite"
    )


def _sqlite_dependency_protection(rows: list[dict]) -> tuple[dict[str, str], list[str]]:
    """Mirror the shared reset planner's known SQLite sidecar guard."""
    relative_paths = {row["relative_path"] for row in rows}
    protected: dict[str, str] = {}
    blockers: list[str] = []
    for relative in sorted(relative_paths):
        if not _known_sqlite_dependency_base(relative):
            continue
        sidecars = [
            relative + suffix
            for suffix in SQLITE_SIDECAR_SUFFIXES
            if relative + suffix in relative_paths
        ]
        if not sidecars:
            continue
        reason = "ESSENTIAL_RECOVERY_OR_OWNER_STATE:SQLITE_SIDECAR_DEPENDENCY"
        protected[relative] = reason
        for sidecar in sidecars:
            protected[sidecar] = reason
        blockers.append("PROTECTED_SQLITE_SIDECAR_REQUIRES_AUDIT:" + relative)
    return protected, blockers


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
                reason = _local_protected_reason(source)
                if reason:
                    protected_source_reasons.append(reason)
            prefix = "" if str(metadata_parent) == "." else metadata_parent.as_posix() + "/"
            direct_session_reasons = [
                reason
                for candidate in rows
                if not prefix or candidate["relative_path"].startswith(prefix)
                for reason in [_local_protected_reason(candidate["relative_path"])]
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
        reason = _local_protected_reason(row["relative_path"])
        if reason:
            protected[row["relative_path"]] = reason
            if reason in RECOVERY_PROTECTION_REASONS:
                blockers.append(
                    "PROTECTED_RECOVERY_REQUIRES_AUDIT:" + row["relative_path"]
                )
    sqlite_protected, sqlite_blockers = _sqlite_dependency_protection(rows)
    protected.update(sqlite_protected)
    blockers.extend(sqlite_blockers)
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
    runtime_agent_root: Path | None = None,
) -> tuple[dict, bool]:
    canonical, archives = _validate_scope_topology(
        Path(canonical_root),
        Path(archive_root),
        expected_canonical_root=expected_canonical_root,
        expected_archive_root=expected_archive_root,
        runtime_agent_root=runtime_agent_root,
        allow_missing_archive=True,
    )
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


def capability_status(
    *, canonical_root, archive_root, expected_canonical_root: Path | None = None,
    expected_archive_root: Path | None = None, runtime_agent_root: Path | None = None,
) -> dict:
    canonical, archives = _validate_scope_topology(
        Path(canonical_root),
        Path(archive_root),
        expected_canonical_root=expected_canonical_root,
        expected_archive_root=expected_archive_root,
        runtime_agent_root=runtime_agent_root,
        allow_missing_archive=True,
    )
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
        "root_topology_ready": True,
        "readiness_scope": READINESS_SCOPE,
        "fly_mutation_supported": False,
        "fly_mutation_requested": False,
    }


def _write_status(path: Path, receipt: dict, status: str, **fields) -> None:
    receipt.update(fields)
    receipt["status"] = status
    receipt["updated_at"] = time.time()
    _atomic_json(path, receipt)


def _allowed_categories(value: object, allowlist: frozenset[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value if isinstance(item, str) and item in allowlist})


def _sanitized_owner_evidence(value: object) -> dict:
    """Return bounded categories only; never persist commands, paths, or arguments."""
    if not isinstance(value, dict) or type(value.get("safe")) is not bool:
        return {
            "schema": "local_reset_readiness_evidence_v1",
            "readiness_scope": READINESS_SCOPE,
            "safe": False,
            "blocker_categories": ["owner_audit_invalid"],
            "active_owner_categories": [],
            "relaunch_authority_categories": [],
        }
    owners = _allowed_categories(
        value.get("active_owner_categories"), _OWNER_CATEGORY_ALLOWLIST
    )
    relaunch = _allowed_categories(
        value.get("relaunch_authority_categories"), _RELAUNCH_CATEGORY_ALLOWLIST
    )
    blockers = _allowed_categories(
        value.get("blocker_categories"), _BLOCKER_CATEGORY_ALLOWLIST
    )
    # Backward-compatible injected auditors remain useful in fixture tests, but
    # their raw rows are never copied into the durable operation receipt.
    if value.get("owners"):
        blockers.append("active_local_research_owner")
    if value.get("running_tasks") or value.get("enabled_tasks"):
        blockers.append("enabled_relaunch_authority")
    blockers = sorted(set(blockers))
    safe = value.get("safe") is True and not blockers and not owners and not relaunch
    if not safe and not blockers:
        blockers = ["owner_audit_invalid"]
    evidence = {
        "schema": "local_reset_readiness_evidence_v1",
        "readiness_scope": READINESS_SCOPE,
        "safe": safe,
        "blocker_categories": blockers,
        "active_owner_categories": owners,
        "relaunch_authority_categories": relaunch,
    }
    checked_at = value.get("checked_at")
    if isinstance(checked_at, (int, float)) and not isinstance(checked_at, bool):
        evidence["checked_at"] = checked_at
    return evidence


def _preflight_blocked(
    path: Path, receipt: dict, *, error: str, evidence: dict
) -> dict:
    if receipt.get("fence") is not None or receipt.get("deleted"):
        _reject("LOCAL_RESET_PREFLIGHT_STATE_INVALID")
    _write_status(
        path,
        receipt,
        "BLOCKED",
        error=error,
        readiness_scope=READINESS_SCOPE,
        blocker_categories=evidence["blocker_categories"],
        owner_evidence={"preflight": evidence},
        retryable_preflight=True,
        mutation_started=False,
        fence_persisted=False,
        deleted_file_count=0,
        deleted_bytes=0,
    )
    return receipt


def _clear_retryable_preflight(receipt: dict) -> None:
    for key in (
        "retryable_preflight",
        "mutation_started",
        "fence_persisted",
        "deleted_file_count",
        "deleted_bytes",
        "blocker_categories",
        "error",
    ):
        receipt.pop(key, None)


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
    runtime_agent_root: Path | None = None,
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
            runtime_agent_root=runtime_agent_root,
        )
    finally:
        operation_lease.release()


def _execute_operation_locked(
    *, state_root, operation_id: str, owner_auditor, crash_at: str | None = None,
    expected_canonical_root: Path | None = None,
    expected_archive_root: Path | None = None,
    runtime_agent_root: Path | None = None,
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
    canonical, archives = _validate_scope_topology(
        Path(receipt["canonical_root"]),
        Path(receipt["archive_root"]),
        expected_canonical_root=expected_canonical_root,
        expected_archive_root=expected_archive_root,
        runtime_agent_root=runtime_agent_root,
    )
    try:
        fence_payload = receipt.get("fence")
        if fence_payload is None:
            existing_fence = read_local_generation_fence(canonical)
            if existing_fence is not None:
                _clear_retryable_preflight(receipt)
                _write_status(
                    path,
                    receipt,
                    "BLOCKED",
                    error="LOCAL_RESET_FOREIGN_FENCE_PRESENT",
                    sync_state=str(existing_fence.get("state") or BLOCKED_STATE),
                )
                return receipt
            try:
                preflight = _sanitized_owner_evidence(owner_auditor(canonical, archives))
            except Exception:
                preflight = {
                    "schema": "local_reset_readiness_evidence_v1",
                    "readiness_scope": READINESS_SCOPE,
                    "safe": False,
                    "blocker_categories": ["owner_audit_unavailable"],
                    "active_owner_categories": [],
                    "relaunch_authority_categories": [],
                }
            if preflight["safe"] is not True:
                return _preflight_blocked(
                    path,
                    receipt,
                    error="LOCAL_RESET_READINESS_BLOCKED",
                    evidence=preflight,
                )
            if crash_at == "before_fence":
                raise InjectedResetCrash(crash_at)
            _clear_retryable_preflight(receipt)
            fence_payload = {
                "operation_id": operation_id,
                "local_generation": "local-reset-" + uuid.uuid4().hex,
                "tombstone_id": "tombstone-" + uuid.uuid4().hex,
                "created_at": time.time(),
                "mirrored_source_epoch": receipt.get("mirrored_source_epoch_before"),
                "fly_mutation_requested": False,
            }
            receipt["fence"] = fence_payload
            receipt["owner_evidence"] = {"preflight": preflight}
            _write_status(
                path, receipt, "RUNNING", sync_state=BLOCKED_STATE, fence_persisting=True
            )
        persisted = persist_local_generation_fence(canonical, fence_payload)
        receipt["fence"] = persisted
        _write_status(
            path,
            receipt,
            "RUNNING",
            mutation_started=True,
            fence_persisted=True,
            fence_persisting=False,
        )
        if crash_at == "after_fence":
            raise InjectedResetCrash(crash_at)

        with MirrorGenerationLease(canonical, owner="laptop-only-fresh-collection").acquire(
            timeout_seconds=0
        ) as lease:
            if not lease.held or lease.path != canonical / LEASE_FILE_NAME:
                _reject("LOCAL_RESET_MATCHING_LEASE_REQUIRED")
            try:
                second_audit = _sanitized_owner_evidence(owner_auditor(canonical, archives))
            except Exception:
                second_audit = {
                    "schema": "local_reset_readiness_evidence_v1",
                    "readiness_scope": READINESS_SCOPE,
                    "safe": False,
                    "blocker_categories": ["owner_audit_unavailable"],
                    "active_owner_categories": [],
                    "relaunch_authority_categories": [],
                }
            receipt.setdefault("owner_evidence", {})["under_lease"] = second_audit
            if second_audit["safe"] is not True:
                _write_status(
                    path,
                    receipt,
                    "BLOCKED",
                    error="LOCAL_RESET_ACTIVE_OWNER",
                    blocker_categories=second_audit["blocker_categories"],
                )
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
