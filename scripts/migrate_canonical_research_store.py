"""Verified, copy-only migration into the canonical desktop research store."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = REPO_ROOT / "services" / "btc-conservative-agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from research.canonical_data_store import (  # noqa: E402
    append_manifest,
    default_store_root,
    initialize_store,
    publish_parity_status,
)


TERMINAL_MEMBERSHIP_SCHEMA = "fly_terminal_transfer_membership_receipt_v1"
TERMINAL_MEMBERSHIP_DIRECTORY = Path("receipts") / "terminal-transfer-membership"
TERMINAL_MEMBERSHIP_MAX_BYTES = 32 * 1024 * 1024
TERMINAL_MEMBERSHIP_NAME = re.compile(
    r"terminal-transfer-membership-([0-9a-f]{64})-([0-9a-f]{32})\.json"
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
# Terminal promotion is a local-authority boundary: it must bind one full
# local Git object, never merely a remote abbreviated observation.
SOURCE_REVISION_RE = re.compile(r"[0-9a-f]{40}")
LOCAL_CONTENT_CANONICALIZATION = (
    "UTF8_PATH_BYTE_LENGTH_RELATIVE_PATH_SIZE_BYTES_SHA256_UTF8_LF_V1"
)
PAGE_CANONICALIZATION = "PAGE_INDEX_PAGE_SHA256_FILE_COUNT_TOTAL_BYTES_UTF8_LF_V1"


def _json(path: Path) -> dict:
    payload = json.loads(_windows_long_path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _windows_long_path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_lines(path: Path) -> int:
    path_io = _windows_long_path(path)
    if not path_io.is_file():
        return 0
    count = 0
    with path_io.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            count += chunk.count(b"\n")
    return count


def _validated_heartbeat(path: Path) -> tuple[dict, str]:
    heartbeat = _json(path)
    if heartbeat.get("ok") is not True or heartbeat.get("inProgress") is True:
        raise RuntimeError("Canonical store refused: mirror sync is incomplete")
    if str(heartbeat.get("revisionParity") or "").upper() != "MATCH":
        raise RuntimeError("Canonical store refused: mirror revision parity is not MATCH")
    revision = str(heartbeat.get("sourceRevision") or "").strip().lower()
    mirrored = str(heartbeat.get("mirroredSourceRevision") or "").strip().lower()
    if (not SOURCE_REVISION_RE.fullmatch(revision)
            or not SOURCE_REVISION_RE.fullmatch(mirrored)
            or revision != mirrored):
        raise RuntimeError("Canonical store refused: source/mirror revision must be exact full SHA")
    return heartbeat, revision


def _deployed_revision(heartbeat: dict) -> str:
    """Return only explicitly observed deployment identity.

    Older heartbeat receipts did not carry this field.  They remain usable for
    historical indexing, but UNKNOWN is retained rather than copying the
    source revision and manufacturing deployment provenance.
    """
    revision = str(heartbeat.get("deployedRevision") or "").strip().lower()
    return revision or "UNKNOWN"


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.fullmatch(value))


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _revision_matches(first: object, second: object) -> bool:
    """Match only the full local commit identity at the promotion boundary."""
    if not isinstance(first, str) or not isinstance(second, str):
        return False
    first, second = first.lower(), second.lower()
    return bool(
        SOURCE_REVISION_RE.fullmatch(first)
        and SOURCE_REVISION_RE.fullmatch(second)
        and first == second
    )


def _timestamp_is_valid(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _absolute_contained_path(base: Path, candidate: Path, code: str) -> Path:
    """Refuse lexical escapes and links/reparse points before opening a file."""
    base_absolute = Path(os.path.abspath(str(base)))
    candidate_absolute = Path(os.path.abspath(str(candidate)))
    try:
        candidate_absolute.relative_to(base_absolute)
    except ValueError as exc:
        raise RuntimeError(code) from exc
    current = candidate_absolute
    while True:
        current_io = _windows_long_path(current)
        if current_io.exists():
            attributes = getattr(current_io.lstat(), "st_file_attributes", 0)
            if current_io.is_symlink() or attributes & 0x400:
                raise RuntimeError(code)
        if current == base_absolute:
            break
        if current.parent == current:
            raise RuntimeError(code)
        current = current.parent
    try:
        _windows_long_path(candidate_absolute).resolve().relative_to(
            _windows_long_path(base_absolute).resolve()
        )
    except ValueError as exc:
        raise RuntimeError(code) from exc
    return candidate_absolute


def _terminal_receipt_path(root: Path, supplied: Path) -> Path:
    receipt_directory = Path(os.path.abspath(str(root / TERMINAL_MEMBERSHIP_DIRECTORY)))
    candidate = _absolute_contained_path(
        receipt_directory, Path(supplied), "TERMINAL_MEMBERSHIP_RECEIPT_PATH_INVALID"
    )
    _require(candidate.parent == receipt_directory, "TERMINAL_MEMBERSHIP_RECEIPT_PATH_INVALID")
    _require(TERMINAL_MEMBERSHIP_NAME.fullmatch(candidate.name) is not None,
             "TERMINAL_MEMBERSHIP_RECEIPT_NAME_INVALID")
    return candidate


def _safe_member_path(root: Path, relative_path: object) -> Path:
    _require(isinstance(relative_path, str) and relative_path, "TERMINAL_MEMBERSHIP_PATH_INVALID")
    try:
        utf8 = relative_path.encode("utf-8")
    except UnicodeError as exc:
        raise RuntimeError("TERMINAL_MEMBERSHIP_PATH_INVALID") from exc
    parts = relative_path.split("/")
    _require(
        len(utf8) <= 1024
        and not relative_path.startswith((".", "/"))
        and "\\" not in relative_path
        and ":" not in relative_path
        and all(part not in ("", ".", "..") for part in parts)
        and not any(ord(character) < 32 or ord(character) == 127 for character in relative_path),
        "TERMINAL_MEMBERSHIP_PATH_INVALID",
    )
    return _absolute_contained_path(
        root, Path(root).joinpath(*parts), "TERMINAL_MEMBERSHIP_PATH_INVALID"
    )


def _ordinal_key(value: str) -> bytes:
    # PowerShell writes the membership with StringComparer.Ordinal (UTF-16 code
    # unit ordering), not locale-dependent filesystem ordering.
    return value.encode("utf-16-be")


def _local_content_digest(rows: list[dict]) -> str:
    payload = "".join(
        f"{len(row['relative_path'].encode('utf-8'))}:{row['relative_path']}:"
        f"{row['size_bytes']}:{row['sha256']}\n"
        for row in rows
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _page_descriptor_digest(rows: list[dict]) -> str:
    payload = "".join(
        f"{row['page_index']}:{row['page_sha256']}:{row['file_count']}:"
        f"{row['total_bytes']}\n"
        for row in rows
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _receipt_payload(path: Path) -> tuple[dict, str, int]:
    path_io = _windows_long_path(path)
    _require(path_io.is_file(), "TERMINAL_MEMBERSHIP_RECEIPT_REQUIRED")
    try:
        raw = path_io.read_bytes()
    except OSError as exc:
        raise RuntimeError("TERMINAL_MEMBERSHIP_RECEIPT_UNREADABLE") from exc
    _require(len(raw) <= TERMINAL_MEMBERSHIP_MAX_BYTES, "TERMINAL_MEMBERSHIP_RECEIPT_TOO_LARGE")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("TERMINAL_MEMBERSHIP_RECEIPT_JSON_INVALID") from exc
    _require(isinstance(payload, dict), "TERMINAL_MEMBERSHIP_RECEIPT_OBJECT_REQUIRED")
    return payload, hashlib.sha256(raw).hexdigest(), len(raw)


def _validate_manifest_pages(receipt: dict, file_count: int, total_bytes: int) -> str:
    pages = receipt.get("manifest_pages")
    _require(isinstance(pages, dict), "TERMINAL_MEMBERSHIP_PAGES_INVALID")
    _require(
        pages.get("descriptor_schema") == "fly_manifest_page_descriptor_v1"
        and pages.get("canonicalization") == PAGE_CANONICALIZATION
        and _is_sha256(pages.get("sorted_page_digest_sha256")),
        "TERMINAL_MEMBERSHIP_PAGES_INVALID",
    )
    descriptors = pages.get("descriptors")
    _require(isinstance(descriptors, list) and descriptors, "TERMINAL_MEMBERSHIP_PAGES_INVALID")
    normalized: list[dict] = []
    for expected_index, descriptor in enumerate(descriptors):
        _require(
            isinstance(descriptor, dict)
            and set(descriptor) == {"page_index", "page_sha256", "file_count", "total_bytes"}
            and descriptor.get("page_index") == expected_index
            and _is_sha256(descriptor.get("page_sha256"))
            and _is_nonnegative_int(descriptor.get("file_count"))
            and _is_nonnegative_int(descriptor.get("total_bytes")),
            "TERMINAL_MEMBERSHIP_PAGES_INVALID",
        )
        normalized.append({
            "page_index": descriptor["page_index"],
            "page_sha256": descriptor["page_sha256"],
            "file_count": descriptor["file_count"],
            "total_bytes": descriptor["total_bytes"],
        })
    _require(
        sum(row["file_count"] for row in normalized) == file_count
        and sum(row["total_bytes"] for row in normalized) == total_bytes
        and _page_descriptor_digest(normalized) == pages["sorted_page_digest_sha256"],
        "TERMINAL_MEMBERSHIP_PAGES_MISMATCH",
    )
    return pages["sorted_page_digest_sha256"]


def _validate_local_membership(
    root: Path,
    receipt_path: Path,
    receipt: dict,
    file_count: int,
    total_bytes: int,
) -> tuple[list[dict], str]:
    coverage = receipt.get("content_coverage")
    _require(isinstance(coverage, dict), "TERMINAL_MEMBERSHIP_CONTENT_COVERAGE_INVALID")
    _require(
        coverage.get("remote_manifest_page_descriptors") == "COMPLETE_IMMUTABLE_PAGE_METADATA"
        and coverage.get("remote_per_file_content_sha256") == "UNAVAILABLE_NOT_DECLARED_BY_MANIFEST"
        and coverage.get("local_content_coverage_complete") is True
        and coverage.get("promotion_content_hash_status") == "LOCAL_COMPLETE_FRESH_RECOMPUTED"
        and coverage.get("promotion_consumer_must_verify_local_content_digest") is True,
        "TERMINAL_MEMBERSHIP_CONTENT_COVERAGE_INVALID",
    )
    local = coverage.get("local_full_file_sha256")
    _require(
        isinstance(local, dict)
        and local.get("status") == "COMPLETE_FRESH_RECOMPUTED"
        and local.get("canonicalization") == LOCAL_CONTENT_CANONICALIZATION
        and _is_nonnegative_int(local.get("file_count"))
        and _is_nonnegative_int(local.get("total_bytes"))
        and _is_sha256(local.get("sorted_file_digest_sha256")),
        "TERMINAL_MEMBERSHIP_CONTENT_COVERAGE_INVALID",
    )
    files = local.get("files")
    _require(isinstance(files, list), "TERMINAL_MEMBERSHIP_CONTENT_MEMBERSHIP_REQUIRED")
    receipt_relative = receipt_path.relative_to(Path(os.path.abspath(str(root)))).as_posix()
    normalized: list[dict] = []
    for row in files:
        _require(
            isinstance(row, dict)
            and set(row) == {"relative_path", "size_bytes", "sha256"}
            and _is_nonnegative_int(row.get("size_bytes"))
            and _is_sha256(row.get("sha256")),
            "TERMINAL_MEMBERSHIP_CONTENT_MEMBERSHIP_INVALID",
        )
        relative = row.get("relative_path")
        local_path = _safe_member_path(root, relative)
        _require(relative != receipt_relative, "TERMINAL_MEMBERSHIP_RECEIPT_RECURSION")
        _require(_windows_long_path(local_path).is_file(), "TERMINAL_MEMBERSHIP_LOCAL_FILE_MISSING")
        expected_size = row["size_bytes"]
        before_size = _windows_long_path(local_path).stat().st_size
        _require(before_size == expected_size, "TERMINAL_MEMBERSHIP_LOCAL_SIZE_MISMATCH")
        actual_sha = _sha256(_windows_long_path(local_path))
        after_size = _windows_long_path(local_path).stat().st_size
        _require(after_size == expected_size, "TERMINAL_MEMBERSHIP_LOCAL_FILE_CHANGED")
        _require(actual_sha == row["sha256"], "TERMINAL_MEMBERSHIP_LOCAL_CHECKSUM_MISMATCH")
        normalized.append({
            "relative_path": relative,
            "size_bytes": expected_size,
            "sha256": row["sha256"],
        })
    paths = [row["relative_path"] for row in normalized]
    _require(
        paths == sorted(paths, key=_ordinal_key) and len(paths) == len(set(paths)),
        "TERMINAL_MEMBERSHIP_CONTENT_MEMBERSHIP_INVALID",
    )
    _require(
        len(normalized) == file_count == local["file_count"]
        and sum(row["size_bytes"] for row in normalized) == total_bytes == local["total_bytes"]
        and _local_content_digest(normalized) == local["sorted_file_digest_sha256"],
        "TERMINAL_MEMBERSHIP_CONTENT_DIGEST_MISMATCH",
    )
    return normalized, local["sorted_file_digest_sha256"]


def _validate_terminal_membership_receipt(
    root: Path, receipt_supplied: Path, heartbeat: dict, revision: str
) -> dict:
    root = Path(os.path.abspath(str(root)))
    receipt_path = _terminal_receipt_path(root, receipt_supplied)
    receipt, receipt_sha256, receipt_size = _receipt_payload(receipt_path)
    _require(receipt.get("schema") == TERMINAL_MEMBERSHIP_SCHEMA, "TERMINAL_MEMBERSHIP_SCHEMA_INVALID")
    _require(_timestamp_is_valid(receipt.get("receipt_written_at")), "TERMINAL_MEMBERSHIP_TIMESTAMP_INVALID")
    generation = receipt.get("inventory_generation_id")
    inventory_sha256 = receipt.get("inventory_sha256")
    source_revision = receipt.get("source_git_rev")
    epoch = receipt.get("collection_epoch_id")
    registry = receipt.get("tile_registry_signature")
    _require(
        _is_sha256(generation)
        and inventory_sha256 == generation
        and _revision_matches(source_revision, revision)
        and isinstance(epoch, str)
        and bool(epoch)
        and isinstance(receipt.get("collection_epoch_field"), str)
        and bool(receipt["collection_epoch_field"])
        and isinstance(registry, str)
        and bool(registry)
        and _timestamp_is_valid(receipt.get("inventory_generated_at")),
        "TERMINAL_MEMBERSHIP_IDENTITY_INVALID",
    )
    ack = receipt.get("remote_final_ack")
    _require(isinstance(ack, dict), "TERMINAL_MEMBERSHIP_FINAL_ACK_INVALID")
    ack_session_id = ack.get("ack_session_id")
    name_match = TERMINAL_MEMBERSHIP_NAME.fullmatch(receipt_path.name)
    _require(
        name_match is not None
        and name_match.group(1) == generation
        and isinstance(ack_session_id, str)
        and name_match.group(2) == ack_session_id
        and bool(re.fullmatch(r"[0-9a-f]{32}", ack_session_id)),
        "TERMINAL_MEMBERSHIP_RECEIPT_NAME_MISMATCH",
    )
    _require(
        ack.get("ok") is True
        and ack.get("outcome") == "FINALIZE_VALIDATED"
        and ack.get("operation") == "FINALIZE"
        and ack.get("inventory_status") == "VALIDATED"
        and ack.get("manifest_pages_complete") is True
        and all(_is_nonnegative_int(ack.get(name)) for name in (
            "expected_count", "accepted_count", "rejected_count"
        )),
        "TERMINAL_MEMBERSHIP_FINAL_ACK_INVALID",
    )
    file_count = receipt.get("manifest_file_count")
    total_bytes = receipt.get("manifest_total_bytes")
    _require(
        _is_nonnegative_int(file_count)
        and file_count > 0
        and _is_nonnegative_int(total_bytes)
        and ack["expected_count"] == ack["accepted_count"] == file_count
        and ack["rejected_count"] == 0
        and receipt.get("post_ack_identity_fence") == "PASSED",
        "TERMINAL_MEMBERSHIP_FINAL_ACK_MISMATCH",
    )
    _require(
        heartbeat.get("ok") is True
        and heartbeat.get("inProgress") is False
        and heartbeat.get("phase") == "complete"
        and heartbeat.get("revisionParity") == "MATCH"
        and heartbeat.get("completionAuthority") == "REMOTE_ACK_FINALIZED"
        and heartbeat.get("ackPending") is False
        and heartbeat.get("inventoryGenerationId") == generation
        and heartbeat.get("inventorySha256") == generation
        and heartbeat.get("inventoryGeneratedAt") == receipt["inventory_generated_at"]
        and heartbeat.get("collectionEpochId") == epoch
        and heartbeat.get("tileRegistrySignature") == registry
        and _revision_matches(source_revision, heartbeat.get("sourceRevision")),
        "TERMINAL_MEMBERSHIP_HEARTBEAT_IDENTITY_MISMATCH",
    )
    for receipt_field, heartbeat_field in (
        ("expected_count", "ackExpectedCount"),
        ("accepted_count", "ackAcceptedCount"),
        ("rejected_count", "ackRejectedCount"),
    ):
        _require(heartbeat.get(heartbeat_field) == ack[receipt_field],
                 "TERMINAL_MEMBERSHIP_HEARTBEAT_ACK_MISMATCH")
    _require(
        heartbeat.get("ackAccepted") is True
        and heartbeat.get("ackFinalized") is True
        and heartbeat.get("ackCoverageComplete") is True
        and heartbeat.get("ackManifestPagesComplete") is True
        and heartbeat.get("ackOperation") == "FINALIZE"
        and heartbeat.get("ackInventoryStatus") == "VALIDATED"
        and heartbeat.get("ackSessionId") == ack_session_id
        and heartbeat.get("ackInventoryFileCount") == file_count
        and heartbeat.get("fileIndex") == heartbeat.get("fileCount") == file_count,
        "TERMINAL_MEMBERSHIP_HEARTBEAT_ACK_MISMATCH",
    )
    page_digest = _validate_manifest_pages(receipt, file_count, total_bytes)
    files, content_digest = _validate_local_membership(
        root, receipt_path, receipt, file_count, total_bytes
    )
    return {
        "path": receipt_path,
        "name": receipt_path.name,
        "receipt_sha256": receipt_sha256,
        "receipt_size": receipt_size,
        "content_digest_sha256": content_digest,
        "manifest_page_digest_sha256": page_digest,
        "files": files,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "generation": generation,
        "source_revision": source_revision,
        "epoch": epoch,
        "registry": registry,
        "ack_session_id": ack_session_id,
    }


def _session_epoch(root: Path, membership: dict) -> tuple[dict, str]:
    _require(
        any(row["relative_path"] == "research_session.json" for row in membership["files"]),
        "TERMINAL_MEMBERSHIP_SESSION_NOT_COVERED",
    )
    session = _json(_safe_member_path(root, "research_session.json"))
    epoch = str(
        session.get("collector_v22_epoch_id")
        or session.get("epoch_id")
        or session.get("collection_epoch")
        or ""
    ).strip()
    _require(epoch and epoch == membership["epoch"], "CANONICAL_STORE_EPOCH_MISMATCH")
    return session, epoch


def _commit_terminal_membership_manifest(
    destination: Path,
    heartbeat: dict,
    revision: str,
    deployed_revision: str,
    membership: dict,
    session: dict,
    epoch: str,
) -> dict:
    checksum_payload = json.dumps(
        {
            "revision": revision,
            "epoch": epoch,
            "terminal_membership_content_digest_sha256": membership["content_digest_sha256"],
            "files": membership["files"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    opportunity_count = _count_lines(destination / "v3" / "ledgers" / "opportunity.jsonl")
    row_count = sum(
        _count_lines(destination / "v3" / "ledgers" / f"{name}.jsonl")
        for name in ("opportunity", "decision", "order_intent", "execution", "lifecycle")
    )
    manifest = append_manifest(
        destination,
        {
            "dataset_epoch": epoch,
            "source_revision": revision,
            "deployed_revision": deployed_revision,
            "tile_config_signature": str(heartbeat.get("tileRegistrySignature") or ""),
            "collection_started_at": session.get("fresh_collection_started_at")
            or session.get("started_at")
            or session.get("session_start")
            or heartbeat.get("syncedAt"),
            "collection_observed_at": heartbeat.get("syncedAt"),
            "row_count": row_count,
            "opportunity_count": opportunity_count,
            "dataset_checksum": hashlib.sha256(checksum_payload).hexdigest(),
            "analyzer_status": "PENDING_CANONICAL_ANALYZER_RUN",
            "analyzer_completed_at": None,
            "analyzer_schema_version": "v62",
            "sync_direction": "FLY_TO_LOCAL_ONLY",
            "file_count": membership["file_count"],
            "byte_count": membership["total_bytes"],
            "terminal_membership_receipt_name": membership["name"],
            "terminal_membership_receipt_sha256": membership["receipt_sha256"],
            "terminal_membership_content_digest_sha256": membership["content_digest_sha256"],
            "terminal_membership_manifest_page_digest_sha256": membership[
                "manifest_page_digest_sha256"
            ],
            "terminal_membership_schema": TERMINAL_MEMBERSHIP_SCHEMA,
        },
    )
    parity = publish_parity_status(
        destination,
        {
            "dataset_epoch": epoch,
            "source_revision": revision,
            "deployed_revision": deployed_revision,
            "tile_config_signature": str(heartbeat.get("tileRegistrySignature") or ""),
        },
    )
    if not parity["ok"]:
        raise RuntimeError("Canonical store refused: committed manifest parity mismatch")
    return manifest


def _windows_long_path(path: Path) -> Path:
    """Enable Win32 long-path access for deep lifecycle_transfer_bundles trees."""
    text = str(path)
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        text = "\\\\?\\" + text
    return Path(text)


def _copy_verified(
    source: Path, destination: Path, expected_size: int, expected_sha256: str, code: str
) -> None:
    source_io = _windows_long_path(source)
    destination_io = _windows_long_path(destination)
    _require(source_io.is_file(), code)
    _require(source_io.stat().st_size == expected_size, code)
    _require(_sha256(source_io) == expected_sha256, code)
    _windows_long_path(destination.parent).mkdir(parents=True, exist_ok=True)
    fd, candidate_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".migration", dir=_windows_long_path(destination.parent)
    )
    os.close(fd)
    candidate = Path(candidate_name)
    try:
        candidate_io = _windows_long_path(candidate)
        shutil.copy2(source_io, candidate_io)
        _require(
            candidate_io.stat().st_size == expected_size
            and _sha256(candidate_io) == expected_sha256,
            code,
        )
        os.replace(candidate_io, destination_io)
    finally:
        _windows_long_path(candidate).unlink(missing_ok=True)


def record_existing_store(
    destination: Path, heartbeat_path: Path, terminal_membership_receipt: Path
) -> dict:
    """Promote only a receipt-covered, already-synchronized generation."""
    destination = Path(os.path.abspath(str(destination)))
    heartbeat, revision = _validated_heartbeat(heartbeat_path)
    membership = _validate_terminal_membership_receipt(
        destination, terminal_membership_receipt, heartbeat, revision
    )
    session, epoch = _session_epoch(destination, membership)
    destination = initialize_store(destination, REPO_ROOT)
    manifest = _commit_terminal_membership_manifest(
        destination,
        heartbeat,
        revision,
        _deployed_revision(heartbeat),
        membership,
        session,
        epoch,
    )
    return {
        "schema": "canonical_research_existing_store_receipt_v2",
        "source_authority": "FLY_PERSISTENT_VOLUME:/app/data",
        "destination": str(destination),
        "files_verified": membership["file_count"],
        "bytes_verified": membership["total_bytes"],
        "terminal_membership_receipt_name": membership["name"],
        "terminal_membership_receipt_sha256": membership["receipt_sha256"],
        "terminal_membership_content_digest_sha256": membership["content_digest_sha256"],
        "manifest_entry_hash": manifest["entry_hash"],
        "source_deleted": False,
    }


def migrate(
    source: Path,
    destination: Path,
    heartbeat_path: Path,
    terminal_membership_receipt: Path,
) -> dict:
    """Copy only receipt-covered bytes; the source mirror is retained."""
    source = Path(os.path.abspath(str(source)))
    destination = Path(os.path.abspath(str(destination)))
    heartbeat, revision = _validated_heartbeat(heartbeat_path)
    source_membership = _validate_terminal_membership_receipt(
        source, terminal_membership_receipt, heartbeat, revision
    )
    _session_epoch(source, source_membership)
    destination = initialize_store(destination, REPO_ROOT)
    for row in source_membership["files"]:
        _copy_verified(
            _safe_member_path(source, row["relative_path"]),
            _safe_member_path(destination, row["relative_path"]),
            row["size_bytes"],
            row["sha256"],
            "CANONICAL_COPY_MEMBERSHIP_MISMATCH",
        )
    destination_receipt = _terminal_receipt_path(
        destination,
        destination / TERMINAL_MEMBERSHIP_DIRECTORY / source_membership["name"],
    )
    _copy_verified(
        source_membership["path"],
        destination_receipt,
        source_membership["receipt_size"],
        source_membership["receipt_sha256"],
        "CANONICAL_COPY_RECEIPT_MISMATCH",
    )
    destination_heartbeat = _absolute_contained_path(
        destination,
        destination / ".fly-data-sync-loop.heartbeat.json",
        "CANONICAL_COPY_HEARTBEAT_PATH_INVALID",
    )
    source_heartbeat = Path(heartbeat_path)
    source_heartbeat_io = _windows_long_path(source_heartbeat)
    _require(source_heartbeat_io.is_file(), "CANONICAL_COPY_HEARTBEAT_MISSING")
    _copy_verified(
        source_heartbeat,
        destination_heartbeat,
        source_heartbeat_io.stat().st_size,
        _sha256(source_heartbeat_io),
        "CANONICAL_COPY_HEARTBEAT_MISMATCH",
    )
    copied_heartbeat, copied_revision = _validated_heartbeat(destination_heartbeat)
    _require(copied_revision == revision, "CANONICAL_COPY_HEARTBEAT_MISMATCH")
    membership = _validate_terminal_membership_receipt(
        destination, destination_receipt, copied_heartbeat, copied_revision
    )
    _require(
        membership["receipt_sha256"] == source_membership["receipt_sha256"]
        and membership["content_digest_sha256"] == source_membership["content_digest_sha256"],
        "CANONICAL_COPY_RECEIPT_MISMATCH",
    )
    session, epoch = _session_epoch(destination, membership)
    manifest = _commit_terminal_membership_manifest(
        destination,
        copied_heartbeat,
        copied_revision,
        _deployed_revision(copied_heartbeat),
        membership,
        session,
        epoch,
    )
    receipt = {
        "schema": "canonical_research_migration_v2",
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": str(source),
        "destination": str(destination),
        "source_deleted": False,
        "files_verified": membership["file_count"],
        "bytes_verified": membership["total_bytes"],
        "terminal_membership_receipt_name": membership["name"],
        "terminal_membership_receipt_sha256": membership["receipt_sha256"],
        "terminal_membership_content_digest_sha256": membership["content_digest_sha256"],
        "manifest_entry_hash": manifest["entry_hash"],
        "fly_parity_claim": "MATCH_AT_HEARTBEAT_TIMESTAMP",
    }
    (destination / "migration" / "migration_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source")
    parser.add_argument("--record-existing", action="store_true")
    parser.add_argument("--heartbeat", required=True)
    parser.add_argument("--terminal-membership-receipt", required=True)
    parser.add_argument("--destination", default=str(default_store_root(REPO_ROOT)))
    args = parser.parse_args()
    if args.record_existing:
        if args.source:
            parser.error("--source cannot be combined with --record-existing")
        receipt = record_existing_store(
            Path(args.destination),
            Path(args.heartbeat),
            Path(args.terminal_membership_receipt),
        )
    else:
        if not args.source:
            parser.error("--source is required unless --record-existing is used")
        receipt = migrate(
            Path(args.source),
            Path(args.destination),
            Path(args.heartbeat),
            Path(args.terminal_membership_receipt),
        )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
