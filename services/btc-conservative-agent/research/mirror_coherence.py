"""Fail-closed coherence checks for the local Fly evidence mirror."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class MirrorCoherenceError(RuntimeError):
    """The mirror cannot safely back a new immutable analyzer generation."""


@dataclass(frozen=True)
class MirrorCoherenceToken:
    heartbeat_path: str
    identity: str
    revision: str
    epoch: str
    deployed_revision: str = ""
    manifest_entry_hash: str = ""
    dataset_checksum: str = ""


def _revision_matches(left: object, right: object) -> bool:
    a = str(left or "").strip().lower()
    b = str(right or "").strip().lower()
    return bool(a and b and (a == b or a.startswith(b) or b.startswith(a)))


def _parse_utc(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise MirrorCoherenceError("MIRROR_SYNC_RECEIPT_TIMESTAMP_MISSING")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MirrorCoherenceError("MIRROR_SYNC_RECEIPT_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _heartbeat_path(repo_root: Path, data_root: Path) -> Path:
    candidate = data_root / ".fly-data-sync-loop.heartbeat.json"
    if candidate.is_file():
        return candidate
    raise MirrorCoherenceError("MIRROR_SYNC_RECEIPT_MISSING")


def _read_json_object(path: Path, error_code: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise MirrorCoherenceError(error_code) from exc
    if not isinstance(payload, dict):
        raise MirrorCoherenceError(error_code)
    return payload


def _completed_mirror_identity(data_root: Path, revision: object) -> tuple[str, str]:
    """Hash immutable mirror-generation fields, excluding heartbeat timestamps."""

    state = _read_json_object(
        data_root / ".fly-sync-state.json", "MIRROR_SYNC_STATE_INVALID"
    )
    stable_files = {}
    for relative_path, record in state.items():
        if not isinstance(record, dict):
            raise MirrorCoherenceError("MIRROR_SYNC_STATE_INVALID")
        stable = {
            key: record[key]
            for key in ("inode", "size", "mtime_ns", "sha256")
            if key in record
        }
        if not {"inode", "size", "mtime_ns"}.issubset(stable):
            raise MirrorCoherenceError("MIRROR_SYNC_STATE_INVALID")
        stable_files[str(relative_path).replace("\\", "/")] = stable

    session = _read_json_object(
        data_root / "research_session.json", "MIRROR_EPOCH_RECEIPT_INVALID"
    )
    epoch = str(
        session.get("collector_v22_epoch_id")
        or session.get("epoch_id")
        or session.get("collection_epoch")
        or ""
    ).strip()
    if not epoch:
        raise MirrorCoherenceError("MIRROR_EPOCH_IDENTITY_MISSING")

    canonical = json.dumps(
        {
            "revision": str(revision or "").strip().lower(),
            "epoch": epoch,
            "files": stable_files,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest(), epoch


def assert_mirror_coherent(
    *,
    repo_root: str | os.PathLike[str],
    data_root: str | os.PathLike[str],
    expected_revision: str,
    expected_deployed_revision: str | None = None,
    expected_manifest_entry_hash: str | None = None,
    expected_dataset_checksum: str | None = None,
    previous: MirrorCoherenceToken | None = None,
    now: datetime | None = None,
    max_age_seconds: int | None = None,
    held_lease: object | None = None,
    require_canonical_manifest: bool = False,
) -> MirrorCoherenceToken:
    """Validate a completed, current, revision-matched mirror receipt.

    Calling this once before reading evidence and again immediately before
    publication prevents a report generation from spanning two sync states.
    """

    root = Path(repo_root).resolve()
    mirror = Path(data_root).resolve()
    path = _heartbeat_path(root, mirror)
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise MirrorCoherenceError("MIRROR_SYNC_RECEIPT_INVALID") from exc
    if not isinstance(payload, dict):
        raise MirrorCoherenceError("MIRROR_SYNC_RECEIPT_INVALID")
    if payload.get("inProgress") is True:
        raise MirrorCoherenceError("MIRROR_SYNC_IN_PROGRESS")
    if payload.get("ok") is not True:
        raise MirrorCoherenceError("MIRROR_SYNC_RECEIPT_FAILED")
    if str(payload.get("revisionParity") or "").upper() != "MATCH":
        raise MirrorCoherenceError("MIRROR_REVISION_PARITY_NOT_MATCH")

    revision_fields = (
        "sourceRevision",
        "observedSourceRevision",
        "mirroredSourceRevision",
    )
    values = [payload.get(field) for field in revision_fields]
    if not expected_revision or any(not _revision_matches(value, expected_revision) for value in values):
        raise MirrorCoherenceError("MIRROR_REVISION_IDENTITY_MISMATCH")
    if not all(_revision_matches(values[0], value) for value in values[1:]):
        raise MirrorCoherenceError("MIRROR_REVISION_IDENTITY_MISMATCH")

    # New terminal receipts state the authenticated deployed revision
    # explicitly. Older receipts may predate that field; only after a terminal
    # MATCH receipt and unanimous source identity have passed above is the
    # mirrored revision a safe compatibility value. An explicit divergent
    # deployedRevision is retained so canonical parity rejects it below.
    deployed_revision = payload.get("deployedRevision")
    if not str(deployed_revision or "").strip():
        deployed_revision = values[2]

    age_limit = int(
        max_age_seconds
        if max_age_seconds is not None
        else os.getenv("ANALYZER_MIRROR_SYNC_MAX_AGE_SEC", "600")
    )
    observed_now = now or datetime.now(timezone.utc)
    age = (observed_now.astimezone(timezone.utc) - _parse_utc(payload.get("syncedAt"))).total_seconds()
    same_held_generation = bool(
        previous is not None
        and held_lease is not None
        and getattr(held_lease, "held", False)
        and Path(getattr(held_lease, "path", "")).resolve()
        == (mirror / ".fly-mirror-generation.lease").resolve()
    )
    # Freshness is mandatory when an iteration starts.  At publication a long
    # calculation may legitimately outlive the receipt-age SLA, but only the
    # still-held cross-process lease plus the same token can waive age alone.
    if age < -60 or (age > age_limit and not same_held_generation):
        raise MirrorCoherenceError("MIRROR_SYNC_RECEIPT_STALE")

    # Heartbeat timestamps and relay-health observations can refresh while the
    # completed mirror remains unchanged. Bind analysis to the normalized file
    # generation and collection epoch instead of volatile whole-receipt bytes.
    identity, epoch = _completed_mirror_identity(mirror, values[2])
    deployed = str(deployed_revision).strip().lower()
    manifest_entry_hash = ""
    dataset_checksum = ""
    if require_canonical_manifest:
        if mirror.name != "canonical-research-data":
            raise MirrorCoherenceError("CANONICAL_STORE_ROOT_NOT_SELECTED")
        try:
            from research.canonical_data_store import require_analyzer_dataset

            current = require_analyzer_dataset(
                mirror,
                {
                    "dataset_epoch": epoch,
                    "source_revision": str(values[2]).strip().lower(),
                    "deployed_revision": deployed,
                    "tile_config_signature": str(payload.get("tileRegistrySignature") or ""),
                },
            )
            manifest_entry_hash = str(current.get("entry_hash") or "").strip().lower()
            dataset_checksum = str(current.get("dataset_checksum") or "").strip().lower()
            if expected_deployed_revision and not _revision_matches(
                deployed, expected_deployed_revision
            ):
                raise MirrorCoherenceError("MIRROR_DEPLOYED_REVISION_IDENTITY_MISMATCH")
            if expected_manifest_entry_hash and manifest_entry_hash != str(
                expected_manifest_entry_hash
            ).strip().lower():
                raise MirrorCoherenceError("MIRROR_MANIFEST_ENTRY_IDENTITY_MISMATCH")
            if expected_dataset_checksum and dataset_checksum != str(
                expected_dataset_checksum
            ).strip().lower():
                raise MirrorCoherenceError("MIRROR_DATASET_CHECKSUM_IDENTITY_MISMATCH")
        except Exception as exc:
            if isinstance(exc, MirrorCoherenceError):
                raise
            raise MirrorCoherenceError("CANONICAL_DATASET_MANIFEST_INVALID") from exc
    token = MirrorCoherenceToken(
        str(path), identity, str(values[2]), epoch, deployed,
        manifest_entry_hash, dataset_checksum,
    )
    if previous is not None and token != previous:
        raise MirrorCoherenceError("MIRROR_SYNC_IDENTITY_CHANGED_DURING_ANALYSIS")
    return token
