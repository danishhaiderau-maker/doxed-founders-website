import json

import lifecycle_pipeline_worker as worker


REVISION = "a" * 40


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
