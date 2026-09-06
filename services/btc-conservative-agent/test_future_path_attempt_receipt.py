import json
import subprocess
from types import SimpleNamespace

import pytest

from future_path_attempt_receipt import run_attempt, NAME, COUNTS


@pytest.mark.parametrize("mode,expected", [("ok", None), ("nonzero", "FUTURE_PATH_WORKER_NONZERO"),
    ("timeout", "FUTURE_PATH_WORKER_TIMEOUT"), ("invalid", "FUTURE_PATH_RESULT_INVALID"),
    ("exception", "FUTURE_PATH_INVOCATION_FAILED"), ("epoch", "FUTURE_PATH_RESULT_INVALID"),
    ("stale", "FUTURE_PATH_RESULT_INVALID")])
def test_bounded_last_attempt_does_not_leak_or_invent_success(tmp_path, mode, expected):
    result = tmp_path / ("future-path-worker-" + "a" * 32 + ".json")
    def invoke():
        if mode == "timeout": raise subprocess.TimeoutExpired("secret command", 30, stderr="secret")
        if mode == "exception": raise RuntimeError("private secret")
        value = {"schema": "all_opportunity_future_path_worker_result_v1", "epoch_id": "epoch-test",
                 **{k: 0 for k in COUNTS}, "now_ts": 100 if mode == "stale" else 101,
                 "source_tape_present": False, "private": "secret"}
        if mode == "epoch": value["epoch_id"] = "epoch-other"
        result.write_text("malformed secret" if mode == "invalid" else json.dumps(value))
        return SimpleNamespace(returncode=2 if mode == "nonzero" else 0)
    output = run_attempt(receipt_root=tmp_path, result_path=result, epoch_id="epoch-test",
                         source_revision="a" * 40, invoke=invoke, expected_now_ts=101)
    saved = json.loads((tmp_path / NAME).read_text())
    assert saved["failure_code"] == expected
    assert saved["status"] == ("SUCCESS" if mode == "ok" else "FAILED")
    assert "secret" not in (tmp_path / NAME).read_text()
    assert saved["authority"] == "DIAGNOSTIC_ONLY_NO_ACK_OR_QUALIFICATION"
    assert (output["worker_result"] is not None) == (mode == "ok")
    assert not (tmp_path / (NAME + ".tmp")).exists()


def test_invalid_identity_prevents_invocation(tmp_path):
    with pytest.raises(ValueError, match="IDENTITY"):
        run_attempt(receipt_root=tmp_path, result_path=tmp_path / "wrong", epoch_id="epoch-test",
                    source_revision="short", invoke=lambda: pytest.fail("invoked"))


def test_replace_failure_preserves_prior_receipt(tmp_path, monkeypatch):
    import future_path_attempt_receipt as module
    target = tmp_path / NAME
    target.write_text("prior")
    monkeypatch.setattr(module.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        run_attempt(receipt_root=tmp_path, result_path=tmp_path / ("future-path-worker-" + "a"*32 + ".json"),
                    epoch_id="epoch-test", source_revision="a"*40, invoke=lambda: SimpleNamespace(returncode=2))
    assert target.read_text() == "prior"


@pytest.mark.parametrize("revision", ["unknown", None, "private invalid revision"])
def test_missing_revision_is_durable_without_invoking_worker(tmp_path, revision):
    output = run_attempt(receipt_root=tmp_path,
        result_path=tmp_path / ("future-path-worker-" + "a" * 32 + ".json"),
        epoch_id="epoch-test", source_revision=revision,
        invoke=lambda: pytest.fail("worker must not run without identity"))
    saved = json.loads((tmp_path / NAME).read_text())
    assert saved["source_revision"] is None
    assert saved["revision_identity_status"] == "UNAVAILABLE"
    assert saved["failure_code"] == "FUTURE_PATH_REVISION_UNAVAILABLE"
    assert saved["status"] == "FAILED"
    assert output["worker_result"] is None
    assert "private" not in (tmp_path / NAME).read_text()
