"""Execute the actual daemon AST with the real durable receipt helper."""
import ast
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import uuid

import pytest

from future_path_attempt_receipt import NAME, COUNTS


@pytest.mark.parametrize("mode", ["success", "nonzero", "timeout", "invalid", "stale", "epoch_changed", "final_lock_epoch_changed", "write_failure", "no_prior"])
def test_actual_daemon_attempt_is_durable_before_ephemeral_cleanup(tmp_path, monkeypatch, mode):
    source = Path(__file__).with_name("bot.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "all_opportunity_future_path_evidence_loop")
    monkeypatch.chdir(tmp_path)
    waits, calls = [], []
    class Shutdown:
        def wait(self, seconds):
            waits.append(seconds)
            return seconds == 300.0
        def is_set(self): return False
    def run(args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs["timeout"] == 30.0 and kwargs["check"] is False
        assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert args[args.index("--max-batch") + 1] == "64"
        assert "SECRET" not in kwargs["env"]
        assert kwargs["env"]["OPENBLAS_NUM_THREADS"] == "1"
        if mode == "timeout": raise subprocess.TimeoutExpired("private", 30)
        result = Path(args[args.index("--result") + 1])
        payload = {"schema": "all_opportunity_future_path_worker_result_v1",
                   "epoch_id": args[args.index("--epoch-id") + 1],
                   "now_ts": float(args[args.index("--now-ts") + 1]),
                   "source_tape_present": True, **dict.fromkeys(COUNTS, 1)}
        if mode == "stale": payload["now_ts"] -= 1
        result.write_text("invalid" if mode == "invalid" else json.dumps(payload))
        return SimpleNamespace(returncode=2 if mode in ("nonzero", "no_prior") else 0)
    real_unlink = Path.unlink
    def unlink(path, *args, **kwargs):
        if path.name.startswith("future-path-worker-"):
            assert (path.parent / NAME).is_file() or mode == "write_failure"
        return real_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    if mode == "write_failure":
        import future_path_attempt_receipt as helper
        monkeypatch.setattr(helper.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("private")))
    monkeypatch.setenv("SECRET", "must not pass")
    state = {"all_opportunity_future_path_evidence": {"complete_count": 7, "now_ts": 1}}
    if mode == "no_prior": state.clear()
    class PublicationLock:
        entries = 0
        def __enter__(self): self.entries += 1
        def __exit__(self, *args): return False
    publication_lock = PublicationLock()
    ns = dict(Path=Path, os=os, uuid=uuid, sys=sys, copy=copy, state=state,
              state_lock=publication_lock, shutdown_event=Shutdown(),
              subprocess=SimpleNamespace(run=run, DEVNULL=subprocess.DEVNULL),
              time=SimpleNamespace(time=lambda: 1234.5),
              _collector_v22_epoch_id=lambda: "epoch-new" if (
                  (mode == "epoch_changed" and calls) or
                  (mode == "final_lock_epoch_changed" and publication_lock.entries >= 3)) else "epoch-test",
              _runtime_git_rev_exact=lambda: "a" * 40,
              FUTURE_PATH_WORKER_WALL_TIMEOUT_SEC=30.0,
              logger=SimpleNamespace(info=lambda *a: None, error=lambda *a: None),
              __file__=str(source))
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), ns)
    ns[node.name]()
    assert len(calls) == 1 and waits == [60.0, 300.0]
    if mode == "write_failure":
        assert "all_opportunity_future_path_attempt" not in state
        assert state["all_opportunity_future_path_evidence"] == {"complete_count": 7, "now_ts": 1,
            "is_latest_attempt": False, "latest_attempt_status": "FAILED"}
        assert not (tmp_path / "v3" / "receipts" / NAME).exists()
        return
    saved = json.loads((tmp_path / "v3" / "receipts" / NAME).read_text())
    assert all(state["all_opportunity_future_path_attempt"][k] == v for k, v in saved.items())
    assert saved["source_revision"] == "a" * 40
    assert saved["epoch_id"] == "epoch-test"
    assert not list((tmp_path / "v3" / "receipts").glob("future-path-worker-*"))
    if mode == "no_prior":
        assert saved["status"] == "FAILED" and "all_opportunity_future_path_evidence" not in state
        return
    evidence = state["all_opportunity_future_path_evidence"]
    if mode == "success":
        assert saved["status"] == "SUCCESS" and evidence["is_latest_attempt"] is True
        assert evidence["now_ts"] == 1234.5
    else:
        assert saved["status"] == ("SUCCESS" if mode in ("epoch_changed", "final_lock_epoch_changed") else "FAILED")
        if mode in ("epoch_changed", "final_lock_epoch_changed"):
            assert state["all_opportunity_future_path_attempt"]["is_current_epoch"] is False
        assert evidence["is_latest_attempt"] is False
        assert evidence["complete_count"] == 7 and evidence["now_ts"] == 1
        assert evidence["latest_attempt_status"] == "FAILED"
