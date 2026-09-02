import json
from pathlib import Path

import pytest

import lifecycle_pipeline_worker as worker


REVISION = "a" * 40


def _write_pressure_round_robin_ledgers(root: Path):
    ledger_dir = root / "v3" / "ledgers"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    first = json.dumps({"padding": "a" * 1_046_000}, separators=(",", ":")).encode() + b"\n"
    second = json.dumps({"padding": "b" * 5_000}, separators=(",", ":")).encode() + b"\n"
    (ledger_dir / "opportunity.jsonl").write_bytes(first)
    (ledger_dir / "pre_entry_features.jsonl").write_bytes(second)
    return first, second


def test_normal_worker_returns_verified_success_without_fragmenting_next_ledger(tmp_path):
    ledger_dir = tmp_path / "v3" / "ledgers"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    row = json.dumps({"padding": "a" * 2_096_000}, separators=(",", ":")).encode() + b"\n"
    first = row * 4
    second = json.dumps({"padding": "b" * 5_000}, separators=(",", ":")).encode() + b"\n"
    (ledger_dir / "opportunity.jsonl").write_bytes(first)
    (ledger_dir / "pre_entry_features.jsonl").write_bytes(second)
    work = tmp_path / "v3" / "lifecycle_worker"
    work.mkdir(parents=True)
    launch = worker.create_request(
        tmp_path, work, source_revision=REVISION, max_runtime_sec=5,
    )

    assert worker.run(
        launch["request_path"], launch["result_path"], launch["nonce"]
    ) == 0
    receipt = worker.verify_result(
        launch["request_path"], launch["result_path"], launch["nonce"]
    )

    assert receipt["status"] == "SUCCESS"
    assert receipt["pipeline"]["pressure_mode"] is False
    assert tuple(receipt["pipeline"]["scan"]["ledgers"]) == ("opportunity",)
    assert receipt["pipeline"]["scan"]["bytes_indexed"] == len(first)
    assert receipt["pipeline"]["scan"]["caught_up"] is False
    assert receipt["source_cleanup_authorized"] is False


def test_pressure_worker_returns_verified_bounded_success(tmp_path):
    first, _second = _write_pressure_round_robin_ledgers(tmp_path)
    work = tmp_path / "v3" / "lifecycle_worker"
    work.mkdir(parents=True)
    launch = worker.create_request(
        tmp_path,
        work,
        source_revision=REVISION,
        pressure_mode=True,
        max_runtime_sec=5,
    )

    assert worker.run(
        launch["request_path"], launch["result_path"], launch["nonce"]
    ) == 0
    receipt = worker.verify_result(
        launch["request_path"], launch["result_path"], launch["nonce"]
    )

    assert receipt["status"] == "SUCCESS"
    assert receipt["pipeline"]["pressure_mode"] is True
    assert tuple(receipt["pipeline"]["scan"]["ledgers"]) == ("opportunity",)
    assert receipt["pipeline"]["scan"]["bytes_indexed"] == len(first)
    assert receipt["pipeline"]["scan"]["bytes_indexed"] <= 1024 * 1024
    assert receipt["source_cleanup_authorized"] is False


def test_pipeline_exception_writes_sanitized_hash_bound_failure(tmp_path, monkeypatch):
    work = tmp_path / "v3" / "lifecycle_worker"
    work.mkdir(parents=True)
    launch = worker.create_request(
        tmp_path, work, source_revision=REVISION, max_runtime_sec=2,
    )
    secret = "do-not-serialize-this-secret"

    def fail(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(worker, "process_incremental_lifecycle_pipeline", fail)
    assert worker.run(
        launch["request_path"], launch["result_path"], launch["nonce"]
    ) == 1
    raw = launch["result_path"].read_text(encoding="utf-8")
    assert secret not in raw
    receipt = worker.verify_result(
        launch["request_path"], launch["result_path"], launch["nonce"]
    )
    assert receipt["status"] == "FAILED"
    assert receipt["request_sha256"] == launch["request_sha256"]
    assert receipt["failure"] == {
        "error_class": "RuntimeError",
        "error_code": "WORKER_PIPELINE_FAILED",
    }
    assert receipt["source_cleanup_authorized"] is False


@pytest.mark.parametrize("message,expected", [
    ("SOURCE_LEDGER_ROTATED:opportunity.jsonl", {
        "error_class": "ValueError", "error_code": "SOURCE_LEDGER_ROTATED",
        "ledger": "opportunity",
    }),
    ("INVALID_JSONL_ROW:lifecycle.jsonl:1406", {
        "error_class": "ValueError", "error_code": "INVALID_JSONL_ROW",
        "ledger": "lifecycle", "byte_offset": 1406,
    }),
])
def test_known_lifecycle_failure_is_classified_without_free_form_text(
    tmp_path, monkeypatch, message, expected,
):
    work = tmp_path / "v3" / "lifecycle_worker"; work.mkdir(parents=True)
    launch = worker.create_request(tmp_path, work, source_revision=REVISION)
    monkeypatch.setattr(
        worker, "process_incremental_lifecycle_pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError(message)),
    )
    assert worker.run(launch["request_path"], launch["result_path"], launch["nonce"]) == 1
    receipt = worker.verify_result(launch["request_path"], launch["result_path"], launch["nonce"])
    assert receipt["failure"] == expected
    assert ".jsonl" not in json.dumps(receipt["failure"])


def test_unknown_or_noncanonical_failure_remains_generic(tmp_path, monkeypatch):
    work = tmp_path / "v3" / "lifecycle_worker"; work.mkdir(parents=True)
    launch = worker.create_request(tmp_path, work, source_revision=REVISION)
    secret = "SOURCE_LEDGER_ROTATED:../../secret.jsonl"
    monkeypatch.setattr(
        worker, "process_incremental_lifecycle_pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError(secret)),
    )
    assert worker.run(launch["request_path"], launch["result_path"], launch["nonce"]) == 1
    raw = launch["result_path"].read_text()
    assert "secret" not in raw
    receipt = worker.verify_result(launch["request_path"], launch["result_path"], launch["nonce"])
    assert receipt["failure"]["error_code"] == "WORKER_PIPELINE_FAILED"


def test_failure_receipt_rejects_unbounded_or_message_shaped_diagnostics(tmp_path):
    work = tmp_path / "v3" / "lifecycle_worker"
    work.mkdir(parents=True)
    launch = worker.create_request(tmp_path, work, source_revision=REVISION)
    payload = {
        "schema": worker.RESULT_SCHEMA,
        "status": "FAILED",
        "nonce": launch["nonce"],
        "request_sha256": launch["request_sha256"],
        "source_cleanup_authorized": False,
        "failure": {
            "error_class": "RuntimeError",
            "error_code": "WORKER_PIPELINE_FAILED",
            "message": "must not be accepted",
        },
    }
    payload["result_sha256"] = worker._result_hash(payload)
    launch["result_path"].write_text(json.dumps(payload), encoding="utf-8")
    try:
        worker.verify_result(
            launch["request_path"], launch["result_path"], launch["nonce"]
        )
    except ValueError as exc:
        assert str(exc) == "WORKER_FAILURE_RECEIPT_INVALID"
    else:
        raise AssertionError("message-shaped worker diagnostic was accepted")


@pytest.mark.parametrize("failure", [
    {"error_class": "ValueError", "error_code": "SOURCE_LEDGER_ROTATED"},
    {"error_class": "ValueError", "error_code": "SOURCE_LEDGER_ROTATED", "ledger": "../../secret"},
    {"error_class": "ValueError", "error_code": "INVALID_JSONL_ROW", "ledger": "lifecycle", "byte_offset": -1},
    {"error_class": "ValueError", "error_code": "NOT_ALLOWLISTED", "ledger": "lifecycle"},
])
def test_failure_receipt_rejects_invalid_classified_schema(tmp_path, failure):
    work = tmp_path / "v3" / "lifecycle_worker"; work.mkdir(parents=True)
    launch = worker.create_request(tmp_path, work, source_revision=REVISION)
    payload = {
        "schema": worker.RESULT_SCHEMA, "status": "FAILED", "nonce": launch["nonce"],
        "request_sha256": launch["request_sha256"],
        "source_cleanup_authorized": False, "failure": failure,
    }
    payload["result_sha256"] = worker._result_hash(payload)
    launch["result_path"].write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="WORKER_FAILURE_RECEIPT_INVALID"):
        worker.verify_result(launch["request_path"], launch["result_path"], launch["nonce"])
