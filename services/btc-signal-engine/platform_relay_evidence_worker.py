"""Low-priority, secret-free validator for platform relay evidence uploads."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from research.platform_relay_evidence import _validate_platform_relay_evidence_payload


REQUEST_SCHEMA = "platform_relay_evidence_worker_request_v1"
RESULT_SCHEMA = "platform_relay_evidence_worker_result_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_request(request_path: Path, result_path: Path, nonce: str) -> dict:
    if len(nonce) != 32 or any(ch not in "0123456789abcdef" for ch in nonce):
        raise ValueError("invalid nonce")
    request_path = request_path.resolve(strict=True)
    work_root = request_path.parent
    if request_path.name != f"relay-request-{nonce}.json":
        raise ValueError("request is not nonce-bound")
    if result_path.parent.resolve(strict=True) != work_root or result_path.name != f"relay-result-{nonce}.json":
        raise ValueError("result is not nonce-bound")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("schema") != REQUEST_SCHEMA or request.get("nonce") != nonce:
        raise ValueError("request identity mismatch")
    input_path = Path(str(request.get("input_path") or "")).resolve(strict=True)
    if input_path.parent != work_root or input_path.name != f"relay-input-{nonce}.json":
        raise ValueError("input is not nonce-bound")
    request["_input_path"] = input_path
    return request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()
    try:
        if hasattr(os, "nice"):
            os.nice(10)
        request_path = Path(args.request)
        result_path = Path(args.result)
        request = _load_request(request_path, result_path, args.nonce)
        input_path = request["_input_path"]
        size = input_path.stat().st_size
        digest = _sha256(input_path)
        if size != int(request.get("expected_size") or -1) or digest != request.get("expected_sha256"):
            valid, code, payload = False, "INPUT_INTEGRITY_INVALID", None
        else:
            try:
                payload = json.loads(input_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                valid, code, payload = False, "JSON_INVALID", None
            else:
                valid, code = _validate_platform_relay_evidence_payload(payload)
        result = {
            "schema": RESULT_SCHEMA,
            "nonce": args.nonce,
            "request_sha256": str(request.get("expected_sha256") or ""),
            "input_sha256": digest,
            "input_size": size,
            "generated_unix": time.time(),
            "valid": bool(valid),
            "error_code": str(code),
            "payload_schema": payload.get("schema") if isinstance(payload, dict) else None,
            "records": len(payload.get("records") or []) if isinstance(payload, dict) else None,
            "generating_revision": payload.get("generatingRevision") if isinstance(payload, dict) else None,
        }
        temporary = result_path.with_name(f"{result_path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        os.replace(temporary, result_path)
        return 0
    except BaseException:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
