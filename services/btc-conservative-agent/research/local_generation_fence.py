"""Durable local-generation tombstone used by laptop data consumers."""
from __future__ import annotations

import json
import os
import stat
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
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LocalGenerationFenced("LOCAL_GENERATION_FENCE_INVALID") from exc
    if (not stat.S_ISREG(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        or metadata.st_size > 1024 * 1024):
        raise LocalGenerationFenced("LOCAL_GENERATION_FENCE_INVALID")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise LocalGenerationFenced("LOCAL_GENERATION_FENCE_INVALID") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != FENCE_SCHEMA
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
