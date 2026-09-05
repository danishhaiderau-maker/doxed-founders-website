"""Deterministic, generation-bound transport bundles for immutable sync rows.

This module is deliberately transport-only.  It neither serves HTTP nor writes
the canonical mirror and it does not replace per-path manifest acknowledgements.
Callers must promote the returned staged members and acknowledge the original
inventory rows using the existing data-sync v3 contract.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import uuid
from typing import Iterable, Mapping


SCHEMA = "fly_runtime_transport_bundle_v1"
MAX_MEMBERS = 256
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
# USTAR adds at most one header and one padding block per member plus two end
# blocks. Reject impossible package sizes before allocating/reading them.
MAX_PACKAGE_BYTES = MAX_PAYLOAD_BYTES + (MAX_MEMBERS * 1024) + tarfile.RECORDSIZE
_GENERATION_RE = re.compile(r"^[0-9a-f]{64}$")
_SEGMENT_RE = re.compile(r"^v3/market_segments/[0-9a-f]{2}/[0-9a-f]{64}\.json$")
_IDEMPOTENCY_RE = re.compile(
    r"^v3/receipts/emergency_record_idempotency_v1/"
    r"[a-z][a-z0-9_]{0,63}/[0-9a-f]{64}\.json$"
)


class BundleTransportError(ValueError):
    """The proposed bundle does not satisfy the fail-closed transport contract."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, separators=(",", ":"), sort_keys=True, ensure_ascii=True
    ).encode("utf-8")


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BundleTransportError(f"{label} must be an integer")
    return value


def _safe_member_path(raw: object) -> str:
    value = str(raw or "")
    if not value or "\\" in value or ":" in value or "\x00" in value:
        raise BundleTransportError("unsafe bundle member path")
    candidate = PurePosixPath(value)
    if (candidate.as_posix() != value or candidate.is_absolute()
            or any(part in {"", ".", ".."} for part in candidate.parts)):
        raise BundleTransportError("unsafe bundle member path")
    return candidate.as_posix()


def is_bundle_eligible_path(raw: object) -> bool:
    """Return whether a path is an immutable small-object transport candidate."""
    try:
        value = _safe_member_path(raw)
    except BundleTransportError:
        return False
    return bool(_SEGMENT_RE.fullmatch(value) or _IDEMPOTENCY_RE.fullmatch(value))


def _manifest_identity(row: Mapping[str, object]) -> tuple[int, int, int]:
    try:
        size = _strict_int(row["size"], "inventory size")
        mtime_ns = _strict_int(row["mtime_ns"], "inventory mtime_ns")
        inode = _strict_int(row["inode"], "inventory inode")
    except KeyError as exc:
        raise BundleTransportError("inventory row identity is incomplete") from exc
    if size < 0 or mtime_ns < 0 or inode < 0:
        raise BundleTransportError("inventory row identity is invalid")
    if str(row.get("consistency_mode") or "") != "strict_generation_v1":
        raise BundleTransportError("only strict immutable rows may be bundled")
    return size, mtime_ns, inode


def _stat_identity(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return int(stat.st_size), int(stat.st_mtime_ns), int(getattr(stat, "st_ino", 0) or 0)


def _is_link_or_reparse(path: Path) -> bool:
    stat = path.lstat()
    if int(getattr(stat, "st_file_attributes", 0) or 0) & 0x400:
        return True
    return path.is_symlink()


def _resolve_regular_source(root: Path, rel: str) -> Path:
    """Reject every link/reparse component before resolving containment."""
    lexical = root
    for part in PurePosixPath(rel).parts:
        lexical = lexical / part
        if _is_link_or_reparse(lexical):
            raise BundleTransportError("source path contains a link or reparse point")
    resolved = lexical.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BundleTransportError("source path escaped root") from exc
    if not resolved.is_file():
        raise BundleTransportError("source is not a regular file")
    return resolved


def _read_source_bytes(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(limit)


def _read_package_bytes(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(limit)


def _read_fenced(path: Path, expected: tuple[int, int, int]) -> bytes:
    before = _stat_identity(path)
    if before != expected:
        raise BundleTransportError("source generation differs from inventory")
    payload = _read_source_bytes(path, expected[0] + 1)
    after = _stat_identity(path)
    if after != before or len(payload) != expected[0]:
        raise BundleTransportError("source generation changed while bundling")
    return payload


def _validate_committed_receipt(rel: str, payload: bytes) -> None:
    if not _IDEMPOTENCY_RE.fullmatch(rel):
        return
    try:
        receipt = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleTransportError("idempotency receipt is not valid JSON") from exc
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != "emergency_record_idempotency_v1"
        or receipt.get("state") != "COMMITTED"
    ):
        raise BundleTransportError("idempotency receipt is not COMMITTED")


def _validate_generation(generation: Mapping[str, object]) -> str:
    generation_id = str(generation.get("inventory_generation_id") or "").lower()
    inventory_sha = str(generation.get("inventory_sha256") or generation_id).lower()
    if not _GENERATION_RE.fullmatch(generation_id) or inventory_sha != generation_id:
        raise BundleTransportError("invalid inventory generation identity")
    if generation.get("ack_eligible") is not True:
        raise BundleTransportError("inventory generation is not acknowledgement eligible")
    for key in ("source_git_rev", "collection_epoch_id", "tile_registry_signature"):
        if not str(generation.get(key) or ""):
            raise BundleTransportError(f"generation identity missing {key}")
    return generation_id


def build_bundle(
    generation: Mapping[str, object],
    rows: Iterable[Mapping[str, object]],
    source_root: Path | str,
    output_root: Path | str,
    *,
    max_members: int = MAX_MEMBERS,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
) -> dict:
    """Build one deterministic uncompressed TAR and return its descriptor.

    The destination filename is the SHA-256 of the complete TAR.  Publication
    uses an atomic rename; a failure never exposes a partial package.
    """
    generation_id = _validate_generation(generation)
    max_members = _strict_int(max_members, "member budget")
    max_payload_bytes = _strict_int(max_payload_bytes, "payload budget")
    if not 1 <= max_members <= MAX_MEMBERS:
        raise BundleTransportError("member budget is outside the hard limit")
    if not 1 <= max_payload_bytes <= MAX_PAYLOAD_BYTES:
        raise BundleTransportError("payload budget is outside the hard limit")
    root = Path(source_root).resolve(strict=True)
    output = Path(output_root).resolve()
    selected: list[tuple[str, Mapping[str, object], tuple[int, int, int]]] = []
    seen: set[str] = set()
    declared_payload_bytes = 0
    for index, row in enumerate(rows):
        if index >= max_members:
            raise BundleTransportError("member budget exceeded")
        if not isinstance(row, Mapping):
            raise BundleTransportError("bundle row is not an object")
        rel = _safe_member_path(row.get("path"))
        if not is_bundle_eligible_path(rel):
            raise BundleTransportError(f"path is not bundle eligible: {rel}")
        if rel in seen:
            raise BundleTransportError("duplicate bundle member")
        seen.add(rel)
        expected = _manifest_identity(row)
        declared_payload_bytes += expected[0]
        if declared_payload_bytes > max_payload_bytes:
            raise BundleTransportError("payload budget exceeded")
        selected.append((rel, row, expected))
    selected.sort(key=lambda item: item[0])
    if not selected:
        raise BundleTransportError("member budget exceeded")

    payloads: list[tuple[str, bytes, dict]] = []
    total = 0
    for rel, row, expected in selected:
        source = _resolve_regular_source(root, rel)
        payload = _read_fenced(source, expected)
        _validate_committed_receipt(rel, payload)
        total += len(payload)
        if total > max_payload_bytes:
            raise BundleTransportError("payload budget exceeded")
        descriptor = {
            "path": rel,
            "size": expected[0],
            "mtime_ns": expected[1],
            "inode": expected[2],
            "consistency_mode": "strict_generation_v1",
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        payloads.append((rel, payload, descriptor))

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for rel, payload, _ in payloads:
            info = tarfile.TarInfo(rel)
            info.size = len(payload)
            info.mtime = 0
            info.mode = 0o600
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(payload))
    package = stream.getvalue()
    package_sha = hashlib.sha256(package).hexdigest()
    members = [item[2] for item in payloads]
    member_tree_sha = hashlib.sha256(_canonical_json(members)).hexdigest()
    descriptor = {
        "schema": SCHEMA,
        "inventory_generation_id": generation_id,
        "inventory_sha256": generation_id,
        "source_git_rev": str(generation["source_git_rev"]),
        "collection_epoch_id": str(generation["collection_epoch_id"]),
        "tile_registry_signature": str(generation["tile_registry_signature"]),
        "package_sha256": package_sha,
        "package_size": len(package),
        "member_tree_sha256": member_tree_sha,
        "member_count": len(members),
        "payload_bytes": total,
        "members": members,
    }
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"{package_sha}.tar"
    temporary = output / f".{package_sha}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("wb") as handle:
            handle.write(package)
            handle.flush()
            os.fsync(handle.fileno())
        written = _read_package_bytes(temporary, len(package) + 1)
        if len(written) != len(package) or hashlib.sha256(written).hexdigest() != package_sha:
            raise BundleTransportError("published package checksum mismatch")
        if destination.exists():
            if destination.stat().st_size != len(package):
                raise BundleTransportError("content-addressed package collision")
            existing = _read_package_bytes(destination, len(package) + 1)
            if len(existing) != len(package) or hashlib.sha256(existing).hexdigest() != package_sha:
                raise BundleTransportError("content-addressed package collision")
        else:
            os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {**descriptor, "package_path": str(destination)}


def _descriptor_members(descriptor: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    raw = descriptor.get("members")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_MEMBERS:
        raise BundleTransportError("invalid descriptor members")
    members: dict[str, Mapping[str, object]] = {}
    for row in raw:
        if not isinstance(row, Mapping):
            raise BundleTransportError("invalid member descriptor")
        rel = _safe_member_path(row.get("path"))
        if not is_bundle_eligible_path(rel) or rel in members:
            raise BundleTransportError("unsafe or duplicate member descriptor")
        members[rel] = row
    if hashlib.sha256(_canonical_json(raw)).hexdigest() != str(
        descriptor.get("member_tree_sha256") or ""
    ):
        raise BundleTransportError("member tree checksum mismatch")
    return members


def extract_verified_bundle(
    package_path: Path | str,
    descriptor: Mapping[str, object],
    expected_generation_id: str,
    staging_root: Path | str,
) -> dict:
    """Verify every member, then return an isolated staging directory.

    No caller-visible staged result is returned unless the complete package and
    every member pass.  Canonical promotion is intentionally out of scope.
    """
    expected_generation_id = str(expected_generation_id or "").lower()
    if not _GENERATION_RE.fullmatch(expected_generation_id):
        raise BundleTransportError("invalid expected generation")
    if descriptor.get("schema") != SCHEMA or any(
        str(descriptor.get(key) or "").lower() != expected_generation_id
        for key in ("inventory_generation_id", "inventory_sha256")
    ):
        raise BundleTransportError("bundle generation mismatch")
    members = _descriptor_members(descriptor)
    if _strict_int(descriptor.get("member_count"), "bundle member count") != len(members):
        raise BundleTransportError("bundle member count mismatch")
    declared_payload_bytes = _strict_int(
        descriptor.get("payload_bytes"), "bundle payload bytes"
    )
    if declared_payload_bytes < 0 or declared_payload_bytes > MAX_PAYLOAD_BYTES:
        raise BundleTransportError("bundle payload exceeds hard limit")
    package = Path(package_path)
    try:
        declared_package_size = _strict_int(
            descriptor.get("package_size"), "bundle package size"
        )
    except BundleTransportError:
        raise
    if declared_package_size < 1 or declared_package_size > MAX_PACKAGE_BYTES:
        raise BundleTransportError("bundle package exceeds hard limit")
    if package.stat().st_size != declared_package_size:
        raise BundleTransportError("bundle package size mismatch")
    raw = _read_package_bytes(package, declared_package_size + 1)
    if len(raw) != declared_package_size:
        raise BundleTransportError("bundle package size mismatch")
    if hashlib.sha256(raw).hexdigest() != str(descriptor.get("package_sha256") or ""):
        raise BundleTransportError("bundle package checksum mismatch")

    stage_parent = Path(staging_root).resolve()
    stage_parent.mkdir(parents=True, exist_ok=True)
    # Keep the staging component short enough for legacy Windows MAX_PATH when
    # immutable receipt names already consume most of the path budget.
    stage = stage_parent / f"b-{uuid.uuid4().hex[:8]}"
    stage.mkdir()
    observed: set[str] = set()
    staged: list[dict] = []
    payload_total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            infos = archive.getmembers()
            if len(infos) != len(members) or len(infos) > MAX_MEMBERS:
                raise BundleTransportError("archive member count mismatch")
            # Validate the complete archive structure before extracting or
            # writing the first payload. A bad final entry must not cause even
            # temporary partial staging.
            for info in infos:
                rel = _safe_member_path(info.name)
                if rel in observed or rel not in members:
                    raise BundleTransportError("unexpected or duplicate archive member")
                if not info.isfile() or info.issym() or info.islnk():
                    raise BundleTransportError("archive links and special files are forbidden")
                expected = members[rel]
                expected_size = _strict_int(expected.get("size"), "member size")
                if expected_size < 0 or info.size != expected_size:
                    raise BundleTransportError("archive member size mismatch")
                observed.add(rel)
            if observed != set(members):
                raise BundleTransportError("bundle is incomplete")
            observed.clear()
            for info in infos:
                rel = _safe_member_path(info.name)
                expected = members[rel]
                source = archive.extractfile(info)
                if source is None:
                    raise BundleTransportError("archive member is unreadable")
                payload = source.read()
                payload_total += len(payload)
                if payload_total > MAX_PAYLOAD_BYTES:
                    raise BundleTransportError("extracted payload exceeds hard limit")
                if hashlib.sha256(payload).hexdigest() != str(expected.get("sha256") or ""):
                    raise BundleTransportError("archive member checksum mismatch")
                target = (stage / Path(*PurePosixPath(rel).parts)).resolve()
                try:
                    target.relative_to(stage)
                except ValueError as exc:
                    raise BundleTransportError("staged member escaped root") from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                observed.add(rel)
                staged.append({"path": rel, "staged_path": str(target), **dict(expected)})
        if observed != set(members) or payload_total != declared_payload_bytes:
            raise BundleTransportError("bundle is incomplete")
        return {"staging_path": str(stage), "members": staged}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
