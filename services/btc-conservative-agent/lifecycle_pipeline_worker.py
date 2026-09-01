"""Credential-free low-priority entry point for the lifecycle pipeline."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lifecycle_pipeline import (
    DEFAULT_MAX_LIFECYCLE_BYTES,
    DEFAULT_MAX_LIFECYCLE_ROWS,
    DEFAULT_MAX_LIFECYCLES,
    MAX_LIFECYCLE_BYTES,
    MAX_LIFECYCLE_ROWS,
    MAX_LIFECYCLES,
    process_incremental_lifecycle_pipeline,
)


REQUEST_SCHEMA = "lifecycle_pipeline_worker_request_v1"
RESULT_SCHEMA = "lifecycle_pipeline_worker_result_v1"
MAX_SCAN_BYTES = 32 * 1024 * 1024
MAX_SCAN_ROWS = 50_000
MAX_RUNTIME_SEC = 120.0
_FIELDS = frozenset({
    "schema", "nonce", "data_root", "work_root", "source_revision",
    "launched_unix", "now", "max_lifecycles", "max_scan_bytes",
    "max_scan_rows", "max_lifecycle_rows", "max_lifecycle_bytes",
    "max_runtime_sec", "pressure_mode", "emergency_closure_mode",
})
_SENSITIVE = ("secret", "token", "password", "credential", "api_key", "private_key")
MAX_ERROR_CLASS_LENGTH = 80


def _sensitive(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            any(marker in str(key).lower() for marker in _SENSITIVE) or _sensitive(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_sensitive(item) for item in value)
    return False


def _directory(raw: Any, error: str) -> Path:
    lexical = Path(os.path.abspath(str(raw or "")))
    resolved = lexical.resolve(strict=True)
    if lexical != resolved or lexical.is_symlink() or not resolved.is_dir():
        raise ValueError(error)
    return resolved


def _bounded_int(payload: dict[str, Any], field: str, default: int, upper: int) -> int:
    value = payload.get(field, default)
    if isinstance(value, bool):
        raise ValueError(f"{field.upper()}_INVALID")
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field.upper()}_INVALID") from exc
    if value < 1 or value > upper:
        raise ValueError(f"{field.upper()}_OUT_OF_RANGE")
    return value


def _load(request_path: Path, result_path: Path, nonce: str) -> dict[str, Any]:
    if len(nonce) != 32 or any(char not in "0123456789abcdef" for char in nonce):
        raise ValueError("INVALID_WORKER_NONCE")
    request_lexical = Path(os.path.abspath(request_path))
    result_lexical = Path(os.path.abspath(result_path))
    request_resolved = request_lexical.resolve(strict=True)
    work_root = _directory(request_lexical.parent, "WORK_ROOT_LINKED_OR_INVALID")
    if request_lexical != request_resolved or request_lexical.is_symlink():
        raise ValueError("WORKER_REQUEST_LINKED_OR_INVALID")
    if request_resolved.parent != work_root or result_lexical.parent != work_root:
        raise ValueError("WORKER_PATH_OUTSIDE_WORK_ROOT")
    if request_lexical.name != f"pipeline-request-{nonce}.json":
        raise ValueError("WORKER_REQUEST_NOT_NONCE_BOUND")
    if result_lexical.name != f"pipeline-result-{nonce}.json":
        raise ValueError("WORKER_RESULT_NOT_NONCE_BOUND")
    raw = request_resolved.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("WORKER_REQUEST_NOT_OBJECT")
    if payload.get("schema") != REQUEST_SCHEMA or payload.get("nonce") != nonce:
        raise ValueError("WORKER_REQUEST_IDENTITY_MISMATCH")
    if set(payload) - _FIELDS or _sensitive(payload):
        raise ValueError("WORKER_REQUEST_FIELD_NOT_ALLOWED")
    data_root = _directory(payload.get("data_root"), "DATA_ROOT_LINKED_OR_INVALID")
    declared_work = _directory(payload.get("work_root"), "WORK_ROOT_LINKED_OR_INVALID")
    if declared_work != work_root:
        raise ValueError("WORK_ROOT_MISMATCH")
    try:
        work_root.relative_to(data_root)
    except ValueError as exc:
        raise ValueError("WORK_ROOT_OUTSIDE_DATA_ROOT") from exc
    now = payload.get("now")
    if now is not None:
        try:
            now = float(now)
        except (TypeError, ValueError) as exc:
            raise ValueError("NOW_INVALID") from exc
        if now <= 0:
            raise ValueError("NOW_INVALID")
    runtime = payload.get("max_runtime_sec", 60.0)
    if isinstance(runtime, bool):
        raise ValueError("MAX_RUNTIME_SEC_INVALID")
    try:
        runtime = float(runtime)
    except (TypeError, ValueError) as exc:
        raise ValueError("MAX_RUNTIME_SEC_INVALID") from exc
    if runtime < 1.0 or runtime > MAX_RUNTIME_SEC:
        raise ValueError("MAX_RUNTIME_SEC_OUT_OF_RANGE")
    pressure = payload.get("pressure_mode", False)
    if not isinstance(pressure, bool):
        raise ValueError("PRESSURE_MODE_INVALID")
    emergency_closure = payload.get("emergency_closure_mode", False)
    if not isinstance(emergency_closure, bool):
        raise ValueError("EMERGENCY_CLOSURE_MODE_INVALID")
    if emergency_closure and not pressure:
        raise ValueError("EMERGENCY_CLOSURE_REQUIRES_PRESSURE_MODE")
    payload.update({
        "_data_root": data_root,
        "_now": now,
        "_runtime": runtime,
        "_pressure": pressure,
        "_emergency_closure": emergency_closure,
        "_max_lifecycles": _bounded_int(payload, "max_lifecycles", DEFAULT_MAX_LIFECYCLES, MAX_LIFECYCLES),
        "_max_scan_bytes": _bounded_int(payload, "max_scan_bytes", 8 * 1024 * 1024, MAX_SCAN_BYTES),
        "_max_scan_rows": _bounded_int(payload, "max_scan_rows", 10_000, MAX_SCAN_ROWS),
        "_max_lifecycle_rows": _bounded_int(payload, "max_lifecycle_rows", DEFAULT_MAX_LIFECYCLE_ROWS, MAX_LIFECYCLE_ROWS),
        "_max_lifecycle_bytes": _bounded_int(payload, "max_lifecycle_bytes", DEFAULT_MAX_LIFECYCLE_BYTES, MAX_LIFECYCLE_BYTES),
        "_request_sha256": hashlib.sha256(raw).hexdigest(),
    })
    return payload


def _lower_priority() -> None:
    if hasattr(os, "nice"):
        try:
            os.nice(10)
        except OSError:
            pass


def create_request(
    data_root: str | Path,
    work_root: str | Path,
    *,
    source_revision: str,
    now: float | None = None,
    pressure_mode: bool = False,
    emergency_closure_mode: bool = False,
    max_lifecycles: int = DEFAULT_MAX_LIFECYCLES,
    max_scan_bytes: int = 8 * 1024 * 1024,
    max_scan_rows: int = 10_000,
    max_lifecycle_rows: int = DEFAULT_MAX_LIFECYCLE_ROWS,
    max_lifecycle_bytes: int = DEFAULT_MAX_LIFECYCLE_BYTES,
    max_runtime_sec: float = 60.0,
) -> dict[str, Any]:
    """Atomically create the small credential-free subprocess request.

    The later runtime orchestrator owns subprocess launch, OS CPU/RSS limits,
    and termination at ``max_runtime_sec``.  This API owns request identity and
    path confinement so orchestration code does not have to duplicate them.
    """
    data = _directory(data_root, "DATA_ROOT_LINKED_OR_INVALID")
    work = _directory(work_root, "WORK_ROOT_LINKED_OR_INVALID")
    try:
        work.relative_to(data)
    except ValueError as exc:
        raise ValueError("WORK_ROOT_OUTSIDE_DATA_ROOT") from exc
    nonce = uuid.uuid4().hex
    request_path = work / f"pipeline-request-{nonce}.json"
    result_path = work / f"pipeline-result-{nonce}.json"
    payload = {
        "schema": REQUEST_SCHEMA,
        "nonce": nonce,
        "data_root": str(data),
        "work_root": str(work),
        "source_revision": str(source_revision or ""),
        "launched_unix": time.time(),
        "now": now,
        "max_lifecycles": max_lifecycles,
        "max_scan_bytes": max_scan_bytes,
        "max_scan_rows": max_scan_rows,
        "max_lifecycle_rows": max_lifecycle_rows,
        "max_lifecycle_bytes": max_lifecycle_bytes,
        "max_runtime_sec": max_runtime_sec,
        "pressure_mode": bool(pressure_mode),
        "emergency_closure_mode": bool(emergency_closure_mode),
    }
    # Validate the exact payload before publication.
    temporary = request_path.with_name(f"{request_path.name}.{uuid.uuid4().hex}.tmp")
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    try:
        with temporary.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, request_path)
        loaded = _load(request_path, result_path, nonce)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "nonce": nonce,
        "request_path": request_path,
        "result_path": result_path,
        "request_sha256": loaded["_request_sha256"],
        "max_runtime_sec": loaded["_runtime"],
    }


def verify_result(
    request_path: str | Path, result_path: str | Path, nonce: str
) -> dict[str, Any]:
    """Verify nonce/request/result binding and the cleanup safety invariant."""
    request = _load(Path(request_path), Path(result_path), str(nonce).lower())
    result_lexical = Path(os.path.abspath(result_path))
    result_resolved = result_lexical.resolve(strict=True)
    if result_lexical != result_resolved or result_lexical.is_symlink():
        raise ValueError("WORKER_RESULT_LINKED_OR_INVALID")
    raw = result_resolved.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("WORKER_RESULT_NOT_OBJECT")
    if payload.get("schema") != RESULT_SCHEMA or payload.get("nonce") != nonce:
        raise ValueError("WORKER_RESULT_IDENTITY_MISMATCH")
    if payload.get("request_sha256") != request["_request_sha256"]:
        raise ValueError("WORKER_RESULT_REQUEST_SHA256_MISMATCH")
    claimed = str(payload.get("result_sha256") or "")
    unsigned = dict(payload)
    unsigned.pop("result_sha256", None)
    calculated = hashlib.sha256(
        json.dumps(unsigned, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    if claimed != calculated:
        raise ValueError("WORKER_RESULT_SHA256_MISMATCH")
    if payload.get("source_cleanup_authorized") is not False:
        raise ValueError("WORKER_RESULT_CLEANUP_INVARIANT_FAILED")
    if payload.get("status") == "FAILED":
        failure = payload.get("failure")
        if not isinstance(failure, dict) or set(failure) != {"error_class", "error_code"}:
            raise ValueError("WORKER_FAILURE_RECEIPT_INVALID")
        error_class = str(failure.get("error_class") or "")
        if (
            not error_class or len(error_class) > MAX_ERROR_CLASS_LENGTH
            or not error_class.replace("_", "").isalnum()
            or failure.get("error_code") != "WORKER_PIPELINE_FAILED"
        ):
            raise ValueError("WORKER_FAILURE_RECEIPT_INVALID")
    return payload


def _result_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _write_result(result_path: Path, payload: dict[str, Any]) -> None:
    payload["result_sha256"] = _result_hash(payload)
    temporary = result_path.with_name(f"{result_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, result_path)
    finally:
        temporary.unlink(missing_ok=True)


def run(request_path: Path, result_path: Path, nonce: str) -> int:
    request: dict[str, Any] | None = None
    started: float | None = None
    try:
        _lower_priority()
        request = _load(request_path, result_path, nonce)
        started = time.time()
        deadline = time.monotonic() + request["_runtime"]
        pipeline = process_incremental_lifecycle_pipeline(
            request["_data_root"], now=request["_now"],
            max_lifecycles=request["_max_lifecycles"],
            max_scan_bytes=request["_max_scan_bytes"],
            max_scan_rows=request["_max_scan_rows"],
            max_lifecycle_rows=request["_max_lifecycle_rows"],
            max_lifecycle_bytes=request["_max_lifecycle_bytes"],
            max_runtime_sec=request["_runtime"], pressure_mode=request["_pressure"],
            emergency_closure_mode=request["_emergency_closure"],
        )
        # A caller must still terminate the subprocess at the same deadline to
        # cap wall time while a filesystem syscall is in flight.  Independently
        # refuse to publish a success receipt once the declared deadline has
        # elapsed, so an overrun can never be mistaken for a bounded success.
        if time.monotonic() > deadline:
            raise TimeoutError("LIFECYCLE_PIPELINE_RUNTIME_LIMIT_EXCEEDED")
        payload = {
            "schema": RESULT_SCHEMA,
            "status": "SUCCESS",
            "nonce": nonce,
            "source_revision": str(request.get("source_revision") or ""),
            "launched_unix": float(request.get("launched_unix") or 0.0),
            "started_unix": started,
            "generated_unix": time.time(),
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "request_sha256": request["_request_sha256"],
            "pipeline": pipeline,
            "hard_runtime_result_deadline_enforced": True,
            "source_cleanup_authorized": False,
        }
        _write_result(result_path, payload)
        return 0
    except BaseException as exc:
        # A valid, loaded request gives us enough trusted identity to publish a
        # small diagnostic receipt. Never serialize the exception message: it
        # may contain paths, row data, or credentials supplied by dependencies.
        if request is not None:
            error_class = type(exc).__name__[:MAX_ERROR_CLASS_LENGTH]
            if not error_class.replace("_", "").isalnum():
                error_class = "Exception"
            payload = {
                "schema": RESULT_SCHEMA,
                "status": "FAILED",
                "nonce": nonce,
                "source_revision": str(request.get("source_revision") or ""),
                "launched_unix": float(request.get("launched_unix") or 0.0),
                "started_unix": started,
                "generated_unix": time.time(),
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "request_sha256": request["_request_sha256"],
                "failure": {
                    "error_class": error_class,
                    "error_code": "WORKER_PIPELINE_FAILED",
                },
                "source_cleanup_authorized": False,
            }
            try:
                _write_result(result_path, payload)
            except BaseException:
                pass
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()
    return run(Path(args.request), Path(args.result), str(args.nonce).lower())


if __name__ == "__main__":
    raise SystemExit(main())
