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

from platform_relay_streaming import validate_streaming
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
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("malformed optional root value was accepted")
