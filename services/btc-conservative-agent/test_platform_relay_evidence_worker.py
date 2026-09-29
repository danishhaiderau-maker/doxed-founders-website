"""Deterministic contracts for isolated platform relay-evidence validation."""
from __future__ import annotations

import ast
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import threading
import io

from platform_relay_streaming import BACKEND, validate_streaming
from research.platform_relay_evidence import _validate_platform_relay_evidence_payload


ROOT = Path(__file__).resolve().parent
BOT_PATH = ROOT / "bot.py"
WORKER = ROOT / "platform_relay_evidence_worker.py"
SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _payload(valid=True):
    payload = {
        "schema": "relay_lifecycle_evidence_v1",
        "generatedAt": "2026-08-30T00:00:00Z",
        "generatingRevision": "a" * 40,
        "runIdentity": "run-1",
        "agentSlug": "conservative-btc",
        "userId": "user-1",
        "records": [{
            "canonicalTradeId": "trade-1", "lifecycleId": "life-1",
            "participantId": "participant-1",
            "events": [{"id": "event-1", "eventType": "ORDER_ACK", "createdAt": "2026-08-30T00:00:01Z"}],
        }],
    }
    if not valid:
        payload.pop("runIdentity")
    return payload


def _run_worker(tmp_path, payload):
    nonce = uuid.uuid4().hex
    input_path = tmp_path / f"relay-input-{nonce}.json"
    request_path = tmp_path / f"relay-request-{nonce}.json"
    result_path = tmp_path / f"relay-result-{nonce}.json"
    raw = json.dumps(payload).encode()
    input_path.write_bytes(raw)
    request_path.write_text(json.dumps({
        "schema": "platform_relay_evidence_worker_request_v1",
        "nonce": nonce,
        "input_path": str(input_path),
        "expected_sha256": hashlib.sha256(raw).hexdigest(),
        "expected_size": len(raw),
    }), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(WORKER), "--request", str(request_path),
         "--result", str(result_path), "--nonce", nonce],
        capture_output=True, text=True, timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(result_path.read_text(encoding="utf-8"))


def test_worker_accepts_valid_and_rejects_invalid_payload_without_secrets(tmp_path):
    valid = _run_worker(tmp_path, _payload(True))
    assert valid["valid"] is True and valid["error_code"] == "OK"
    assert valid["records"] == 1 and valid["generating_revision"] == "a" * 40
    invalid = _run_worker(tmp_path, _payload(False))
    assert invalid["valid"] is False
    assert invalid["error_code"] == "PROVENANCE_INCOMPLETE"
    worker_source = WORKER.read_text(encoding="utf-8")
    assert "BOT_ADMIN_TOKEN" not in worker_source
    assert "BITFINEX" not in worker_source


def test_compiled_streaming_backend_is_pinned_and_missing_import_fails_closed():
    assert BACKEND == "yajl2_c"
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    worker_source = WORKER.read_text(encoding="utf-8")
    assert "ijson==3.5.1" in requirements
    assert "import platform_relay_streaming" in dockerfile
    assert "from platform_relay_streaming import validate_streaming" in worker_source


def _compiled_parent_helper(tmp_path, fake_run):
    function = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_validate_platform_relay_evidence_in_worker")
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = {
        "uuid": uuid, "Path": Path, "os": os, "json": json, "hmac": hmac,
        "subprocess": type("Subprocess", (), {
            "run": staticmethod(fake_run), "DEVNULL": subprocess.DEVNULL,
        }),
        "sys": sys, "time": time, "__file__": str(BOT_PATH),
        "_data_sync_inventory_work_root": lambda: tmp_path,
        "_PLATFORM_RELAY_EVIDENCE_WORKER_REQUEST_SCHEMA": "platform_relay_evidence_worker_request_v1",
        "_PLATFORM_RELAY_EVIDENCE_WORKER_RESULT_SCHEMA": "platform_relay_evidence_worker_result_v1",
        "_PLATFORM_RELAY_EVIDENCE_WORKER_TIMEOUT_SEC": 1.0,
    }
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace["_validate_platform_relay_evidence_in_worker"]


def test_parent_rejects_nonce_or_digest_tampered_worker_result(tmp_path):
    raw = b"{}"
    staged = tmp_path / "stage.json"
    staged.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()

    def tampering_run(command, **_kwargs):
        result_path = Path(command[command.index("--result") + 1])
        nonce = command[command.index("--nonce") + 1]
        result_path.write_text(json.dumps({
            "schema": "platform_relay_evidence_worker_result_v1",
            "nonce": nonce, "request_sha256": digest,
            "input_sha256": "0" * 64, "input_size": len(raw), "valid": True,
        }), encoding="utf-8")
        return type("Completed", (), {"returncode": 0})()

    helper = _compiled_parent_helper(tmp_path, tampering_run)
    try:
        helper(staged, digest, len(raw))
    except RuntimeError as exc:
        assert "result validation failed" in str(exc)
    else:
        raise AssertionError("tampered worker result was accepted")


def test_parent_propagates_bounded_worker_timeout(tmp_path):
    staged = tmp_path / "stage.json"
    staged.write_bytes(b"{}")

    def timeout_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("worker", 1.0)

    helper = _compiled_parent_helper(tmp_path, timeout_run)
    try:
        helper(staged, hashlib.sha256(b"{}").hexdigest(), 2)
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("worker timeout did not fail closed")


def test_upload_handler_never_decodes_full_json_in_parent():
    function = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                    and node.name == "api_data_sync_platform_relay_evidence")
    body = ast.get_source_segment(SOURCE, function)
    assert "_validate_platform_relay_evidence_in_worker" in body
    assert "json.loads(raw" not in body
    assert "request.get_data" not in body
    assert "request.stream.read" in body
    assert "received_size > _PLATFORM_RELAY_EVIDENCE_MAX_BYTES" in body
    assert "os.replace(staged, destination)" in body
    assert "CHECKSUM_MISMATCH" in body
    assert "_PLATFORM_RELAY_EVIDENCE_INSTALL_LOCK" in body
    assert "_platform_relay_evidence_duplicate_receipt" in body


def _compiled_receipt_helpers():
    names = {
        "_platform_relay_evidence_receipt_path",
        "_platform_relay_evidence_duplicate_receipt",
        "_write_platform_relay_evidence_receipt",
    }
    functions = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in names]
    module = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
    namespace = {
        "Path": Path, "json": json, "hmac": hmac, "hashlib": hashlib, "os": os, "uuid": uuid,
        "_PLATFORM_RELAY_EVIDENCE_RECEIPT_SCHEMA": "platform_relay_evidence_install_receipt_v1",
    }
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace


def test_duplicate_receipt_accepts_exact_retry_and_rejects_genuine_delta_or_corruption(tmp_path):
    helpers = _compiled_receipt_helpers()
    destination = tmp_path / "relay.json"
    raw = b'{"validated":true}'
    destination.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    result = {
        "payload_schema": "relay_lifecycle_evidence_v1", "records": 7,
        "generating_revision": "a" * 40,
    }
    semantic = hashlib.sha256(b"semantic").hexdigest()
    helpers["_write_platform_relay_evidence_receipt"](
        destination, digest, semantic, len(raw), result
    )
    exact = helpers["_platform_relay_evidence_duplicate_receipt"](destination, digest, len(raw))
    assert exact is not None and exact["records"] == 7
    assert helpers["_platform_relay_evidence_duplicate_receipt"](
        destination, hashlib.sha256(b"genuine delta").hexdigest(), len(raw)
    ) is None
    destination.write_bytes(b"x" * len(raw))
    assert helpers["_platform_relay_evidence_duplicate_receipt"](
        destination, digest, len(raw)
    ) is None


def test_retry_installation_is_serialized_and_receipt_publish_is_atomic():
    assert isinstance(threading.RLock(), type(threading.RLock()))
    assert "with _PLATFORM_RELAY_EVIDENCE_INSTALL_LOCK:" in SOURCE
    writer = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_write_platform_relay_evidence_receipt")
    body = ast.get_source_segment(SOURCE, writer)
    assert "os.replace(temp, receipt_path)" in body


def test_endpoint_exact_duplicate_skips_but_semantic_claim_cannot_skip_changed_body(tmp_path):
    helpers = _compiled_receipt_helpers()
    route = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                 and node.name == "api_data_sync_platform_relay_evidence")
    route.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[route], type_ignores=[]))
    destination = tmp_path / "relay.json"
    calls = []

    def validate(staged, digest, size):
        calls.append(digest)
        return {
            "valid": True, "payload_schema": "relay_lifecycle_evidence_v1",
            "records": 1, "generating_revision": "a" * 40,
            "staged_path": staged,
        }

    class Request:
        pass

    request = Request()
    namespace = {
        **helpers, "request": request, "jsonify": lambda value: value,
        "Path": Path, "hashlib": hashlib, "hmac": hmac, "re": __import__("re"),
        "uuid": uuid, "os": os, "json": json, "subprocess": subprocess,
        "_PLATFORM_RELAY_EVIDENCE_MAX_BYTES": 25 * 1024 * 1024,
        "_PLATFORM_RELAY_EVIDENCE_INSTALL_LOCK": threading.RLock(),
        "_data_sync_inventory_work_root": lambda: tmp_path,
        "_data_sync_volume_root": lambda: tmp_path,
        "PLATFORM_RELAY_EVIDENCE_FILE": str(destination),
        "_validate_platform_relay_evidence_in_worker": validate,
    }
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    endpoint = namespace["api_data_sync_platform_relay_evidence"]

    def invoke(raw, declared=None, semantic=None):
        request.content_length = len(raw)
        request.stream = io.BytesIO(raw)
        request.headers = {
            "X-Content-SHA256": declared or hashlib.sha256(raw).hexdigest(),
            "X-Relay-Semantic-SHA256": semantic or hashlib.sha256(b"semantic").hexdigest(),
        }
        return endpoint()

    first = invoke(b'{"first":true}')
    assert first["duplicate"] is False and len(calls) == 1
    exact = invoke(b'{"first":true}')
    assert exact["duplicate"] is True and len(calls) == 1
    # Reusing the old semantic claim cannot bypass validation when raw bytes differ.
    changed = invoke(b'{"other":true}', semantic=first["semanticSha256"])
    assert changed["duplicate"] is False and len(calls) == 2
    mismatch = invoke(b'{"corrupt":true}', declared="0" * 64)
    assert mismatch[1] == 400 and mismatch[0]["errorCode"] == "CHECKSUM_MISMATCH"
    assert len(calls) == 2


def test_streaming_validator_matches_pure_schema_codes(tmp_path):
    cases = []
    cases.append(_payload())
    missing = _payload(); missing.pop("runIdentity"); cases.append(missing)
    wrong_scope = _payload(); wrong_scope["agentSlug"] = "other"; cases.append(wrong_scope)
    bad_record = _payload(); bad_record["records"][0].pop("participantId"); cases.append(bad_record)
    bad_event = _payload(); bad_event["records"][0]["events"][0].pop("createdAt"); cases.append(bad_event)
    duplicate = _payload(); duplicate["records"].append({
        "canonicalTradeId": "trade-2", "lifecycleId": "life-2", "participantId": "participant-2",
        "events": [{"id": "event-1", "eventType": "FILL", "createdAt": "2026-08-30T00:00:02Z"}],
    }); cases.append(duplicate)
    for index, payload in enumerate(cases):
        path = tmp_path / f"case-{index}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        assert validate_streaming(path)[:2] == _validate_platform_relay_evidence_payload(payload)


def test_streaming_validator_preserves_top_level_error_precedence_when_records_come_first(tmp_path):
    duplicate_records = _payload()["records"] * 2
    cases = [
        {"records": [{"bad": True}], "schema": "wrong"},
        {
            "records": duplicate_records, "schema": "relay_lifecycle_evidence_v1",
            "generatedAt": "now", "generatingRevision": "rev", "agentSlug": "conservative-btc",
            "userId": "user",
        },
    ]
    for index, payload in enumerate(cases):
        path = tmp_path / f"precedence-{index}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert validate_streaming(path)[:2] == _validate_platform_relay_evidence_payload(payload)


def test_streaming_validator_rejects_utf8_bom_like_original_loader(tmp_path):
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(_payload()).encode("utf-8"))
    try:
        validate_streaming(path)
    except ValueError:
        pass
    else:
        raise AssertionError("UTF-8 BOM was accepted")


def test_streaming_validator_handles_unicode_escaped_braces_and_large_snapshot(tmp_path):
    payload = _payload()
    payload["records"] = []
    for index in range(12000):
        payload["records"].append({
            "canonicalTradeId": f"trade-{index}", "lifecycleId": f"life-{index}",
            "participantId": "participant-π",
            "events": [{
                "id": f"event-{index}", "eventType": "ORDER_{ACK}",
                "createdAt": "2026-08-30T00:00:01Z", "payload": "escaped \\\" } ] {雪}",
            }],
        })
    path = tmp_path / "large.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    valid, code, metadata = validate_streaming(path)
    assert path.stat().st_size > 2 * 1024 * 1024
    assert (valid, code, metadata["records"]) == (True, "OK", 12000)


def test_streaming_worker_rejects_malformed_without_publishing_success(tmp_path):
    nonce = uuid.uuid4().hex
    input_path = tmp_path / f"relay-input-{nonce}.json"
    request_path = tmp_path / f"relay-request-{nonce}.json"
    result_path = tmp_path / f"relay-result-{nonce}.json"
    raw = b'{"schema":"relay_lifecycle_evidence_v1","records":[{"events":[}]}'
    input_path.write_bytes(raw)
    request_path.write_text(json.dumps({
        "schema": "platform_relay_evidence_worker_request_v1", "nonce": nonce,
        "input_path": str(input_path), "expected_sha256": hashlib.sha256(raw).hexdigest(),
        "expected_size": len(raw),
    }), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(WORKER), "--request", str(request_path),
         "--result", str(result_path), "--nonce", nonce], timeout=10,
    )
    assert completed.returncode == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["valid"] is False and result["error_code"] == "JSON_INVALID"


def test_streaming_validator_rejects_malformed_unknown_root_value(tmp_path):
    path = tmp_path / "malformed-optional.json"
    raw = json.dumps(_payload(), separators=(",", ":"))
    path.write_text(raw[:-1] + ',"optional":truX}', encoding="utf-8")
    try:
        validate_streaming(path)
    except (json.JSONDecodeError, ValueError):
        pass
    else:
        raise AssertionError("malformed optional root value was accepted")
