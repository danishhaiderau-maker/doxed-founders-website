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
    candidates = (
        repo_root / ".fly-data-sync-loop.heartbeat.json",
        data_root / ".fly-data-sync-loop.heartbeat.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise MirrorCoherenceError("MIRROR_SYNC_RECEIPT_MISSING")


def assert_mirror_coherent(
    *,
    repo_root: str | os.PathLike[str],
    data_root: str | os.PathLike[str],
    expected_revision: str,
    previous: MirrorCoherenceToken | None = None,
    now: datetime | None = None,
    max_age_seconds: int | None = None,
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

    age_limit = int(
        max_age_seconds
        if max_age_seconds is not None
        else os.getenv("ANALYZER_MIRROR_SYNC_MAX_AGE_SEC", "600")
    )
    observed_now = now or datetime.now(timezone.utc)
    age = (observed_now.astimezone(timezone.utc) - _parse_utc(payload.get("syncedAt"))).total_seconds()
    if age < -60 or age > age_limit:
        raise MirrorCoherenceError("MIRROR_SYNC_RECEIPT_STALE")

    # The receipt itself is the sync identity. Any replacement during analysis
    # invalidates the run, even when a later sync happens to carry the same rev.
    identity = hashlib.sha256(raw).hexdigest()
    token = MirrorCoherenceToken(str(path), identity, str(values[0]))
    if previous is not None and token != previous:
        raise MirrorCoherenceError("MIRROR_SYNC_IDENTITY_CHANGED_DURING_ANALYSIS")
    return token
