"""Low-priority, nonce-bound lifecycle bundle materialization worker.

The worker accepts only a small file-based request, confines every worker path
to one declared data root, and atomically publishes a nonce-bound result.  It
does not import the trading runtime or accept credentials in its request.
"""
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

from lifecycle_bundles import (
    DEFAULT_MAX_SCAN_BYTES, DEFAULT_MAX_SCAN_ROWS, materialize_ready_bundles,
)


REQUEST_SCHEMA = "lifecycle_bundle_worker_request_v1"
RESULT_SCHEMA = "lifecycle_bundle_worker_result_v1"
MAX_BUNDLES_PER_RUN = 25
MAX_SCAN_BYTES_PER_RUN = 32 * 1024 * 1024
MAX_SCAN_ROWS_PER_RUN = 50_000
MAX_RUNTIME_SEC = 120.0
_ALLOWED_REQUEST_FIELDS = frozenset({
    "schema", "nonce", "data_root", "work_root", "source_revision",
    "launched_unix", "now", "max_bundles", "max_scan_bytes",
    "max_scan_rows", "max_runtime_sec",
})
_SENSITIVE_MARKERS = ("secret", "token", "password", "credential", "api_key", "private_key")


def _contains_sensitive_field(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            any(marker in str(key).lower() for marker in _SENSITIVE_MARKERS)
            or _contains_sensitive_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_field(item) for item in value)
    return False


def _resolved_unlinked_directory(raw: Any, error: str) -> Path:
    lexical = Path(os.path.abspath(str(raw or "")))
    resolved = lexical.resolve(strict=True)
    if not resolved.is_dir() or lexical != resolved or lexical.is_symlink():
        raise ValueError(error)
    return resolved


def _load_request(request_path: Path, result_path: Path, nonce: str) -> dict[str, Any]:
    if len(nonce) != 32 or any(char not in "0123456789abcdef" for char in nonce):
        raise ValueError("INVALID_WORKER_NONCE")
    request_lexical = Path(os.path.abspath(request_path))
    result_lexical = Path(os.path.abspath(result_path))
    request_resolved = request_lexical.resolve(strict=True)
    work_root = _resolved_unlinked_directory(request_lexical.parent, "WORK_ROOT_LINKED_OR_INVALID")
    if request_resolved.parent != work_root or result_lexical.parent != work_root:
        raise ValueError("WORKER_PATH_OUTSIDE_WORK_ROOT")
    if request_lexical.name != f"bundle-request-{nonce}.json" or result_lexical.name != f"bundle-result-{nonce}.json":
        raise ValueError("WORKER_PATH_NOT_NONCE_BOUND")
    request_bytes = request_resolved.read_bytes()
    payload = json.loads(request_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("WORKER_REQUEST_NOT_OBJECT")
    if payload.get("schema") != REQUEST_SCHEMA or payload.get("nonce") != nonce:
        raise ValueError("WORKER_REQUEST_IDENTITY_MISMATCH")
    if set(payload) - _ALLOWED_REQUEST_FIELDS or _contains_sensitive_field(payload):
        raise ValueError("WORKER_REQUEST_FIELD_NOT_ALLOWED")
    declared_work = _resolved_unlinked_directory(payload.get("work_root"), "WORK_ROOT_LINKED_OR_INVALID")
    data_root = _resolved_unlinked_directory(payload.get("data_root"), "DATA_ROOT_LINKED_OR_INVALID")
    if declared_work != work_root:
        raise ValueError("WORK_ROOT_MISMATCH")
    try:
        work_root.relative_to(data_root)
    except ValueError as exc:
        raise ValueError("WORK_ROOT_OUTSIDE_DATA_ROOT") from exc
    maximum = payload.get("max_bundles")
    if isinstance(maximum, bool):
        raise ValueError("MAX_BUNDLES_INVALID")
    try:
        maximum = int(maximum)
    except (TypeError, ValueError) as exc:
        raise ValueError("MAX_BUNDLES_INVALID") from exc
    if maximum < 1 or maximum > MAX_BUNDLES_PER_RUN:
        raise ValueError("MAX_BUNDLES_OUT_OF_RANGE")
    now = payload.get("now")
    if now is not None:
        try:
            now = float(now)
        except (TypeError, ValueError) as exc:
            raise ValueError("NOW_INVALID") from exc
        if now <= 0:
            raise ValueError("NOW_INVALID")
    limits = (
        ("max_scan_bytes", DEFAULT_MAX_SCAN_BYTES, 1, MAX_SCAN_BYTES_PER_RUN),
        ("max_scan_rows", DEFAULT_MAX_SCAN_ROWS, 1, MAX_SCAN_ROWS_PER_RUN),
    )
    for field, default, minimum, upper in limits:
        value = payload.get(field, default)
        if isinstance(value, bool):
            raise ValueError(f"{field.upper()}_INVALID")
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field.upper()}_INVALID") from exc
        if value < minimum or value > upper:
            raise ValueError(f"{field.upper()}_OUT_OF_RANGE")
        payload[f"_{field}"] = value
    runtime = payload.get("max_runtime_sec", 60.0)
    if isinstance(runtime, bool):
        raise ValueError("MAX_RUNTIME_SEC_INVALID")
    try:
        runtime = float(runtime)
    except (TypeError, ValueError) as exc:
        raise ValueError("MAX_RUNTIME_SEC_INVALID") from exc
    if runtime < 1.0 or runtime > MAX_RUNTIME_SEC:
        raise ValueError("MAX_RUNTIME_SEC_OUT_OF_RANGE")
    payload["_data_root"] = data_root
    payload["_max_bundles"] = maximum
    payload["_now"] = now
    payload["_max_runtime_sec"] = runtime
    payload["_request_sha256"] = hashlib.sha256(request_bytes).hexdigest()
    return payload


def _lower_priority() -> None:
    if hasattr(os, "nice"):
        try:
            os.nice(10)
        except OSError:
            pass
    if os.name == "nt":
        try:
            import ctypes
            below_normal_priority_class = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), below_normal_priority_class
            )
        except (AttributeError, OSError):
            pass


def run(request_path: Path, result_path: Path, nonce: str) -> int:
    temporary: Path | None = None
    try:
        _lower_priority()
        request = _load_request(request_path, result_path, nonce)
        started_unix = time.time()
        materialization = materialize_ready_bundles(
            request["_data_root"], now=request["_now"],
            max_bundles=request["_max_bundles"],
            max_scan_bytes=request["_max_scan_bytes"],
            max_scan_rows=request["_max_scan_rows"],
            max_runtime_sec=request["_max_runtime_sec"],
        )
        payload = {
            "schema": RESULT_SCHEMA,
            "nonce": nonce,
            "source_revision": str(request.get("source_revision") or ""),
            "launched_unix": float(request.get("launched_unix") or 0.0),
            "started_unix": started_unix,
            "generated_unix": time.time(),
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "requested_max_bundles": request["_max_bundles"],
            "request_sha256": request["_request_sha256"],
            "materialization": materialization,
            "source_cleanup_authorized": False,
        }
        payload["result_sha256"] = hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        temporary = result_path.with_name(f"{result_path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, result_path)
        return 0
    except BaseException:
        return 1
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()
    return run(Path(args.request), Path(args.result), str(args.nonce).lower())


if __name__ == "__main__":
    raise SystemExit(main())
