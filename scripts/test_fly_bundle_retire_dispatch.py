"""Offline execution acceptance: no test contacts Fly or deletes artifacts."""
import base64
import gzip
import hashlib
import json
import re
from types import SimpleNamespace

import pytest

import fly_bundle_retire_dispatch as dispatch


REV, OLD, STATE, CURRENT = "d" * 40, "a" * 64, "e" * 64, "b" * 64
OWNER = {"id": "123456789abc", "state": "started"}


def _receipt():
    return {"schema": "fly_bundle_retirement_operation_v1", "runtime_revision": REV,
            "old_generation": OLD, "current_generation": CURRENT, "state_sha256": STATE,
            "status": "COMPLETE", "raw_source_deleted": False, "retirement_performed": True}


def _mock(monkeypatch, *, owners=None, receipt=None, transport_code=0):
    for key, value in {"RETIRE_EXPECTED_REVISION": REV, "RETIRE_OLD_GENERATION": OLD,
                       "RETIRE_STATE_SHA": STATE, "RETIRE_CURRENT_GENERATION": CURRENT,
                       "RETIRE_EXECUTE": "1"}.items():
        monkeypatch.setenv(key, value)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["machines", "list"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps([OWNER] if owners is None else owners).encode())
        assert command[1:3] == ["machine", "exec"]
        assert kwargs["timeout"] == 190
        # This models completed remote deletion even when transport reports error.
        return SimpleNamespace(returncode=transport_code, stdout=json.dumps({
            "exit_code": 0, "stdout": json.dumps(_receipt() if receipt is None else receipt)}).encode())
    monkeypatch.setattr(dispatch.subprocess, "run", run)
    return calls


def test_successful_remote_deletion_with_transport_failure_never_retries(monkeypatch, capsys):
    calls = _mock(monkeypatch, transport_code=1)
    with pytest.raises(ValueError, match="REMOTE_RESULT_UNAVAILABLE_INSPECT_BEFORE_RETRY"):
        dispatch.main()
    assert len(calls) == 2
    assert sum(command[1:3] == ["machine", "exec"] for command in calls) == 1
    printed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(printed) == 1 and "status" not in printed[0]


def test_source_hash_receipt_matches_exact_shipped_sources(monkeypatch, capsys):
    calls = _mock(monkeypatch)
    dispatch.main()
    printed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    blob = re.search(r"b64decode\('([A-Za-z0-9+/=]+)'\)", calls[1][-1]).group(1)
    shipped = json.loads(gzip.decompress(base64.b64decode(blob)))
    assert printed[0] == {"reviewed_sources": {
        key: hashlib.sha256(value.encode()).hexdigest() for key, value in shipped["sources"].items()}, "execute": True}
    assert set(shipped["sources"]) == {"data_sync_bundle_storage", "data_sync_bundle_retirement", "fly_bundle_retire"}
    assert shipped["args"][-1] == "--execute"
    assert printed[1] == _receipt()


@pytest.mark.parametrize("owners", [[], [OWNER, {**OWNER, "id": "abcdef012345"}],
                                   [{**OWNER, "state": "starting"}], [{**OWNER, "id": "invalid"}]])
def test_rejects_unproven_sole_owner_without_remote_execution(monkeypatch, owners):
    calls = _mock(monkeypatch, owners=owners)
    with pytest.raises(ValueError, match="ONE_STARTED_OWNER_REQUIRED"):
        dispatch.main()
    assert len(calls) == 1


@pytest.mark.parametrize("key,value", [
    ("runtime_revision", "f" * 40), ("old_generation", "f" * 64),
    ("current_generation", "f" * 64), ("state_sha256", "f" * 64),
    ("schema", "wrong"), ("status", "INSPECTED"), ("status", "BUILDING"),
    ("raw_source_deleted", True), ("retirement_performed", False),
])
def test_rejects_tampered_terminal_receipt_without_retry(monkeypatch, capsys, key, value):
    calls = _mock(monkeypatch, receipt={**_receipt(), key: value})
    with pytest.raises(ValueError, match="REMOTE_TERMINAL_RECEIPT_INVALID"):
        dispatch.main()
    assert len(calls) == 2
    assert len(capsys.readouterr().out.splitlines()) == 1
