"""Bounded, fail-closed classification of the retained reset pointer."""
from __future__ import annotations

import functools
import json
import math
import re
import stat
import threading
from pathlib import Path
from typing import Any

from research_exact_deletion import _checked_path


MAX_POINTER_BYTES = 64 * 1024
MAX_OPERATION_BYTES = 64 * 1024 * 1024
_cache_lock = threading.Lock()


def _identity(info) -> tuple[int, int, int, int, int]:
    return (
        int(info.st_dev), int(info.st_ino), int(info.st_size),
        int(info.st_mtime_ns), int(info.st_ctime_ns),
    )


def _strict_json(payload: bytes) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("RESET_RECEIPT_DUPLICATE_KEY")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("RESET_RECEIPT_NONFINITE")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            return constant(value)
        return number

    try:
        decoded = json.loads(
            payload.decode("utf-8"), object_pairs_hook=pairs,
            parse_constant=constant, parse_float=finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("RESET_RECEIPT_INVALID_JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError("RESET_RECEIPT_OBJECT_REQUIRED")
    return decoded


def read_reset_receipt(path: Path, root: Path, max_bytes: int) -> dict[str, Any]:
    """Read one regular, contained receipt and reject replacement during I/O."""
    checked = _checked_path(path, root)
    before = checked.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size < 1
        or before.st_size > int(max_bytes)
    ):
        raise ValueError("RESET_RECEIPT_SIZE_OR_TYPE_INVALID")
    payload = checked.read_bytes()
    after = checked.lstat()
    if _identity(before) != _identity(after) or len(payload) != before.st_size:
        raise ValueError("RESET_RECEIPT_CHANGED_DURING_READ")
    return _strict_json(payload)


@functools.lru_cache(maxsize=8)
def _pointer_reset_id_cached(
    path_text: str, root_text: str, expected_identity: tuple[int, int, int, int, int],
) -> str:
    path, root = Path(path_text), Path(root_text)
    pointer = read_reset_receipt(path, root, MAX_POINTER_BYTES)
    if _identity(path.lstat()) != expected_identity:
        raise ValueError("RESET_RECEIPT_CHANGED_DURING_READ")
    reset_id = pointer.get("reset_id")
    if not re.fullmatch(r"[0-9a-f]{24}", str(reset_id or "")):
        raise ValueError("RESET_RESUME_POINTER_INVALID")
    return str(reset_id)


@functools.lru_cache(maxsize=8)
def _operation_stage_cached(
    path_text: str, root_text: str, expected_identity: tuple[int, int, int, int, int],
) -> str:
    path, root = Path(path_text), Path(root_text)
    receipt = read_reset_receipt(path, root, MAX_OPERATION_BYTES)
    if _identity(path.lstat()) != expected_identity:
        raise ValueError("RESET_RECEIPT_CHANGED_DURING_READ")
    stage = receipt.get("stage")
    if not isinstance(stage, str) or not stage:
        raise ValueError("RESET_OPERATION_STAGE_INVALID")
    return stage


def active_reset_receipt_exists(root: Path) -> bool:
    """False only for no pointer or a fully parsed COMPLETE operation."""
    root = Path(root).resolve()
    active = _checked_path(root / "research_reset_receipts" / "ACTIVE_RESET.json", root)
    try:
        pointer_info = active.lstat()
    except FileNotFoundError:
        return False
    pointer_identity = _identity(pointer_info)
    if (
        not stat.S_ISREG(pointer_info.st_mode)
        or pointer_identity[2] < 1
        or pointer_identity[2] > MAX_POINTER_BYTES
    ):
        raise ValueError("RESET_RECEIPT_SIZE_OR_TYPE_INVALID")
    with _cache_lock:
        reset_id = _pointer_reset_id_cached(str(active), str(root), pointer_identity)
    operation = _checked_path(active.parent / str(reset_id) / "operation.json", root)
    info = operation.lstat()
    operation_identity = _identity(info)
    if (
        not stat.S_ISREG(info.st_mode)
        or operation_identity[2] < 1
        or operation_identity[2] > MAX_OPERATION_BYTES
    ):
        raise ValueError("RESET_RECEIPT_SIZE_OR_TYPE_INVALID")
    with _cache_lock:
        stage = _operation_stage_cached(str(operation), str(root), operation_identity)
    if (
        _identity(active.lstat()) != pointer_identity
        or _identity(operation.lstat()) != operation_identity
    ):
        raise ValueError("RESET_RECEIPT_CHANGED_DURING_CLASSIFICATION")
    return stage != "COMPLETE"
