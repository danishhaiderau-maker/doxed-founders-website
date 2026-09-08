import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import lifecycle_owner_identity as identity
import lifecycle_pipeline_runtime as runtime
from test_lifecycle_pipeline_runtime import _runtime


def test_legacy_prior_boot_reclaimed_even_when_pid_reused(tmp_path, monkeypatch):
    instance = _runtime(tmp_path)
    instance.owner_path.write_text(json.dumps({"pid": os.getpid(), "created_unix": 1788864183.105313}))
    monkeypatch.setattr(identity, "boot_time", lambda: 1788873405)
    assert instance._claim_owner()
    instance._release_owner()


def test_legacy_live_unproven_not_reclaimed(tmp_path, monkeypatch):
    instance = _runtime(tmp_path)
    old = json.dumps({"pid": os.getpid(), "created_unix": 1788873406})
    instance.owner_path.write_text(old)
    monkeypatch.setattr(identity, "boot_time", lambda: 1788873405)
    assert not instance._claim_owner()
    assert instance.owner_path.read_text() == old


def test_process_identity_distinguishes_reused_pid():
    old = {"process_identity": {"boot_id": "old", "start_ticks": 107}}
    current = {"boot_id": "new", "start_ticks": 107}
    assert identity.stale(old, current, True, current, None)
    current = {"boot_id": "old", "start_ticks": 108}
    assert identity.stale(old, current, True, current, None)
    assert not identity.stale(old, old["process_identity"], True, old["process_identity"], None)


def test_stable_lock_excludes_other_process(tmp_path):
    path = tmp_path / "owner.lock"
    held = identity.acquire(path)
    assert held is not None
    try:
        result = subprocess.run([sys.executable, "-c", "import lifecycle_owner_identity as m,sys; h=m.acquire(sys.argv[1]);sys.exit(7 if h is None else 9)", str(path)], capture_output=True)
        assert result.returncode == 7, result.stderr
    finally:
        identity.release(held)
    retry = identity.acquire(path)
    assert retry is not None
    identity.release(retry)


def test_failed_start_status_retained(tmp_path, monkeypatch):
    candidate = _runtime(tmp_path)
    monkeypatch.setattr(runtime, "_default_runtime", None)
    monkeypatch.setattr(runtime, "LifecyclePipelineRuntime", lambda *a, **k: candidate)
    monkeypatch.setattr(candidate, "_claim_owner", lambda: False)
    assert not runtime.start(tmp_path)
    assert runtime.status()["last_outcome"] == "DUPLICATE_OWNER_REJECTED"


def test_corrupt_owner_preserved_and_lock_released(tmp_path):
    instance = _runtime(tmp_path)
    instance.owner_path.write_text("[]")
    assert not instance._claim_owner()
    assert instance._status["last_error_code"] == "OWNER_RECORD_INVALID"
    assert instance.owner_path.read_text() == "[]"
    assert instance._owner_handle is None


def test_identity_parser_handles_parentheses(monkeypatch):
    monkeypatch.setattr(identity, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(Path, "read_text", lambda p: "boot-x" if "boot_id" in str(p) else
                        "662 (worker ) nested)) " + " ".join(["S"] + ["0"] * 18 + ["107"]))
    assert identity.identity(662) == {"boot_id": "boot-x", "start_ticks": 107}


def test_extra_fields_not_identity_drift_and_invalid_ticks_failclosed():
    current = {"boot_id": "x", "start_ticks": 107}
    assert not identity.stale({"process_identity": dict(current, extra=True)}, current, True, current, None)
    for ticks in (0, -1, True, "107"):
        assert not identity.stale({"process_identity": {"boot_id": "x", "start_ticks": ticks},
                                   "created_unix": 1}, current, True, current, 100)


def test_actual_claim_competing_processes_stale_record(tmp_path):
    instance = _runtime(tmp_path)
    instance.owner_path.write_text(json.dumps({"pid": 99999999, "created_unix": 1}))
    code = """import sys
from pathlib import Path
from test_lifecycle_pipeline_runtime import _runtime
r = _runtime(Path(sys.argv[1]))
print('READY', flush=True)
sys.stdin.readline()
claimed = r._claim_owner()
print(str(claimed), flush=True)
sys.stdin.readline()
if claimed: r._release_owner()
"""
    processes = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        for child in processes:
            assert child.stdout.readline().strip() == "READY"
        for child in processes:
            child.stdin.write("go\n"); child.stdin.flush()
        assert sorted(child.stdout.readline().strip() for child in processes) == ["False", "True"]
    finally:
        for child in processes:
            child.communicate("release\n", timeout=10)
