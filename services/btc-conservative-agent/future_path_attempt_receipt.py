"""Single bounded diagnostic receipt for an isolated future-path attempt.

Never supplies analysis, ACK, cleanup or live-trading authority. Callers pass
the actual incumbent identity and existing subprocess invocation as a callback.
"""
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time

NAME = "future-path-last-attempt.json"
MAX_BYTES = 16384
COUNTS = ("candidate_count", "pending_count", "mature_selected", "complete_count", "unknown_count", "cursor")


def _safe(path, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)):
        raise ValueError("ATTEMPT_PATH_INVALID")
    return info


def run_attempt(*, receipt_root, result_path, epoch_id, source_revision, invoke, expected_now_ts=None, clock=time.time):
    """Invoke once; save only allowlisted metadata, never exception prose."""
    root = Path(receipt_root)
    result = Path(result_path)
    if (not root.is_absolute() or ".." in root.parts or result.parent != root
            or not re.fullmatch(r"future-path-worker-[0-9a-f]{32}\.json", result.name)
            or not isinstance(epoch_id, str) or not re.fullmatch(r"epoch-[A-Za-z0-9_-]{1,128}", epoch_id)
            or (expected_now_ts is not None and (type(expected_now_ts) not in (float, int)
                or not math.isfinite(expected_now_ts) or expected_now_ts <= 0))):
        raise ValueError("ATTEMPT_IDENTITY_OR_PATH_INVALID")
    for parent in (*reversed(root.parents), root):
        _safe(parent, True)
    revision_available = isinstance(source_revision, str) and re.fullmatch(r"[0-9a-f]{40}", source_revision) is not None
    started = clock()
    payload = {"schema": "future_path_attempt_v1", "epoch_id": epoch_id,
               "source_revision": source_revision if revision_available else None,
               "revision_identity_status": "AVAILABLE" if revision_available else "UNAVAILABLE",
               "started_at_ts": started,
               "status": "FAILED", "failure_code": "FUTURE_PATH_INVOCATION_FAILED",
               "authority": "DIAGNOSTIC_ONLY_NO_ACK_OR_QUALIFICATION"}
    worker = None
    try:
        if not revision_available:
            payload["failure_code"] = "FUTURE_PATH_REVISION_UNAVAILABLE"
            raise ValueError("REVISION_UNAVAILABLE")
        completed = invoke()
        if completed.returncode != 0:
            payload["failure_code"] = "FUTURE_PATH_WORKER_NONZERO"
        else:
            payload["failure_code"] = "FUTURE_PATH_RESULT_INVALID"
            before = _safe(result)
            if before.st_size > MAX_BYTES:
                raise ValueError("RESULT_LIMIT")
            with result.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            after = _safe(result)
            if (len(raw) != before.st_size or len(raw) > MAX_BYTES
                    or (before.st_ino, before.st_size, before.st_mtime_ns) !=
                       (after.st_ino, after.st_size, after.st_mtime_ns)):
                raise ValueError("RESULT_CHANGED")
            value = json.loads(raw)
            if (not isinstance(value, dict) or value.get("schema") != "all_opportunity_future_path_worker_result_v1"
                    or value.get("epoch_id") != epoch_id
                    or (expected_now_ts is not None and (type(value.get("now_ts")) not in (int, float)
                        or value["now_ts"] != expected_now_ts))
                    or any(type(value.get(k)) is not int or not 0 <= value[k] <= 2**63-1 for k in COUNTS)
                    or type(value.get("source_tape_present")) is not bool):
                raise ValueError("RESULT_INVALID")
            worker = value
            payload.update(status="SUCCESS", failure_code=None,
                           counts={k: value[k] for k in COUNTS}, source_tape_present=value["source_tape_present"])
    except subprocess.TimeoutExpired:
        payload["failure_code"] = "FUTURE_PATH_WORKER_TIMEOUT"
    except Exception:
        pass  # Preserve static stage code, never raw exception/path/credentials.
    payload["finished_at_ts"] = clock()
    target = root / NAME
    temporary = root / (NAME + ".tmp")
    for parent in (*reversed(root.parents), root):
        _safe(parent, True)
    for path in (target, temporary):
        if path.exists() or path.is_symlink():
            _safe(path)
    raw = json.dumps(payload, sort_keys=True, allow_nan=False).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("ATTEMPT_RECEIPT_LIMIT")
    with temporary.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    if os.name != "nt":
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return {"attempt": payload, "worker_result": worker}
