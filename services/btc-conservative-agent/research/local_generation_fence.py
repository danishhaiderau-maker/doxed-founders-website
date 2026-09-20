"""Durable local-generation tombstone used by every laptop data consumer.

This fence is deliberately independent from the Fly collection epoch.  Once
present, local import, analysis, report publication and canonical promotion
remain blocked until a separate verified-import workflow replaces it.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


FENCE_FILE_NAME = ".local-generation-fence.json"
FENCE_SCHEMA = "local_research_generation_fence_v1"
BLOCKED_STATE = "BLOCKED_PENDING_VERIFIED_IMPORT"


class LocalGenerationFenced(RuntimeError):
    pass


def fence_path(data_root: str | os.PathLike[str]) -> Path:
    return Path(data_root).resolve() / FENCE_FILE_NAME


def read_local_generation_fence(data_root: str | os.PathLike[str]) -> dict | None:
    path = fence_path(data_root)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise LocalGenerationFenced("LOCAL_GENERATION_FENCE_INVALID")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise LocalGenerationFenced("LOCAL_GENERATION_FENCE_INVALID") from exc
    if (
        payload.get("schema") != FENCE_SCHEMA
        or payload.get("state") != BLOCKED_STATE
        or not payload.get("operation_id")
        or not payload.get("local_generation")
        or not payload.get("tombstone_id")
    ):
        raise LocalGenerationFenced("LOCAL_GENERATION_FENCE_INVALID")
    return payload


def assert_local_generation_available(
    data_root: str | os.PathLike[str], *, stage: str
) -> None:
    payload = read_local_generation_fence(data_root)
    if payload is not None:
        raise LocalGenerationFenced(
            "LOCAL_GENERATION_FENCED:" + str(stage) + ":" + str(payload["operation_id"])
        )


def persist_local_generation_fence(
    data_root: str | os.PathLike[str], payload: dict
) -> dict:
    root = Path(data_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = fence_path(root)
    document = dict(payload)
    document.update(schema=FENCE_SCHEMA, state=BLOCKED_STATE)
    encoded = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if path.exists():
        current = read_local_generation_fence(root)
        if current != document:
            raise LocalGenerationFenced("LOCAL_GENERATION_FENCE_CONFLICT")
        return current
    fd, candidate = tempfile.mkstemp(prefix=".local-fence-", dir=root)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(candidate, path)
    finally:
        if os.path.exists(candidate):
            os.unlink(candidate)
    return document
