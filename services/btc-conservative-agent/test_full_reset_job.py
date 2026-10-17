"""Background full-reset job: isolated worker, heartbeat guard, status route, kill + rerun."""
import json
import os
import threading
import time
from pathlib import Path

import pytest

import full_reset_job as job
import research_reset_execution as execution
from test_research_reset_execution import put, settings


def _kwargs(args, **extra):
    return {"runtime_root": str(args["runtime_root"]), "proof": args["proof"], "quiescent": True,
            "recovery_states": args["recovery_states"], "receipt_path": str(args["receipt_path"]), **extra}


@pytest.fixture
def heartbeat():
    started = []

    def start(root):
        publisher = job.HeartbeatPublisher(root, time.time)
        publisher.start()
        started.append(publisher)
        time.sleep(0.2)
        return publisher

    yield start
    for publisher in started:
        publisher.stop()
        publisher.join(5)


def test_status_view_idle_running_failed_stale_and_armed(tmp_path):
    root = tmp_path / "runtime"
    assert job.read_status(root)["state"] == "IDLE"
    status = job.JobStatus(root, "op1")
    status.stage("PAYLOAD_DELETION")
    view = job.read_status(root)
    assert view["state"] == "RUNNING" and view["stage"] == "PAYLOAD_DELETION" and view["alert"] is None
    assert view["operation_id"] == "op1" and "started_at" in view and "updated_at" in view
    assert job.read_status(root, live_armed=True)["alert"]["level"] == "RED"
    stale = job.read_status(root, now=time.time() + job.STALE_STATUS_S + 5)
    assert stale["outcome"] == "INTERRUPTED" and stale["alert"]["level"] == "RED"
    status.finish("RESET_INVENTORY_INCOMPLETE", blockers=["RESET_INVENTORY_INCOMPLETE"])
    failed = job.read_status(root)
    assert failed["state"] == "FAILED" and failed["alert"]["level"] == "RED"
    done = job.JobStatus(root, "op2")
    done.finish("COMPLETE")
    ok = job.read_status(root)
    assert ok["state"] == "DONE" and ok["alert"] is None


def test_guard_throttles_then_resumes_and_aborts_when_sustained(tmp_path):
    root = tmp_path / "runtime"
    (root / "full-reset-receipts" / "_job").mkdir(parents=True)
    beat = root / "full-reset-receipts" / "_job" / job.HEARTBEAT_FILE
    clock = {"t": 1000.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds
        if len(sleeps) == 3:  # heartbeat recovers after 3 s of throttling
            beat.write_text(json.dumps({"written_at": clock["t"], "heartbeat_ts": clock["t"]}))

    beat.write_text(json.dumps({"written_at": 1000.0, "heartbeat_ts": 1000.0}))
    clock["t"] = 1010.0  # age 10 s > 5 s
    guard = job.Guard(root, sleep=fake_sleep, clock=lambda: clock["t"])
    guard.tick()
    assert len(sleeps) == 3 and guard.heartbeat_age() <= job.THROTTLE_AGE_S
    beat.write_text(json.dumps({"written_at": 1000.0, "heartbeat_ts": 1000.0}))
    stuck = job.Guard(root, sleep=fake_sleep, clock=lambda: clock["t"])
    with pytest.raises(job.HeartbeatAbort):
        stuck.tick()
    fresh = job.Guard(root, sleep=fake_sleep, clock=lambda: clock["t"])
    beat.write_text(json.dumps({"written_at": clock["t"], "heartbeat_ts": clock["t"]}))
    fresh.tick()  # healthy: no throttle


def test_isolated_execute_matches_direct_and_never_runs_in_process(tmp_path, heartbeat):
    args = settings(tmp_path)
    root = args["runtime_root"]
    heartbeat(root)
    targets = [put(root, f"v3/market_segments/{n:02x}/{n:02x}{n:062x}.json") for n in range(5)]
    keep = put(root, "v3/receipts/emergency_record_idempotency_v1/000/1.json", b"{}")
    pre = job.run_isolated(root, "execute", {"root": str(root), "attempt_id": "a" * 32, "reset_id": "b" * 24,
                                             "scope_name": None,
                                             "kwargs": _kwargs(args, validate_only=True)})
    assert pre["status"] == "VALIDATED" and pre["target_count"] == 5 and all(t.exists() for t in targets)
    progress = json.loads((job.job_dir(root) / job.PROGRESS_FILE).read_text())
    assert progress["total_targets"] == 5 and progress["heartbeat_age_s"] <= job.THROTTLE_AGE_S
    result = job.run_isolated(root, "execute", {"root": str(root), "attempt_id": "a" * 32, "reset_id": "b" * 24,
                                                "scope_name": None, "kwargs": _kwargs(args)})
    assert result["status"] == "COMPLETE" and not any(t.exists() for t in targets) and keep.exists()
    assert not list(job.job_dir(root).glob("task-*")) and not list(job.job_dir(root).glob("result-*"))


def test_isolated_sample_roundtrips_for_stability_check(tmp_path, heartbeat):
    from research_reset_target_stability import assert_targets_stable
    args = settings(tmp_path)
    root = args["runtime_root"]
    heartbeat(root)
    put(root, "v3/market_segments/01/01" + "0" * 62 + ".json")
    payload = {"root": str(root), "proof": args["proof"], "scope_names": [None]}
    first = job.run_isolated(root, "sample", payload)
    second = job.run_isolated(root, "sample", payload)
    first = {k: tuple(v) for k, v in first.items()}
    second = {k: tuple(v) for k, v in second.items()}
    assert assert_targets_stable(first, second)["status"] == "STABLE"


def test_worker_aborts_fail_closed_without_heartbeat_and_deletes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("FULL_RESET_JOB_ABORT_AFTER_S", "2")
    args = settings(tmp_path)
    root = args["runtime_root"]
    target = put(root, "v3/market_segments/02/02" + "0" * 62 + ".json")
    # Enough entries that the scan yields (hook fires every 2048 entries).
    base = root / "v3" / "receipts" / "emergency_record_idempotency_v1" / "000"
    base.mkdir(parents=True)
    for index in range(5000):
        (base / f"{index}.json").touch()
    with pytest.raises(job.IsolatedTaskError) as caught:
        job.run_isolated(root, "execute", {"root": str(root), "attempt_id": "a" * 32, "reset_id": "b" * 24,
                                           "scope_name": None, "kwargs": _kwargs(args)})
    assert caught.value.kind == "HeartbeatAbort" and target.exists()


def test_kill_mid_run_then_rerun_completes_idempotently(tmp_path, heartbeat, monkeypatch):
    args = settings(tmp_path)
    root = args["runtime_root"]
    heartbeat(root)
    targets = [put(root, f"v3/market_segments/{n:02x}/{n:02x}{n:062x}.json") for n in range(8)]
    base = root / "v3" / "receipts" / "emergency_record_idempotency_v1" / "000"
    base.mkdir(parents=True)
    for index in range(30000):
        (base / f"{index}.json").touch()
    procs = []
    real_popen = job.subprocess.Popen

    def spy(*a, **kw):
        proc = real_popen(*a, **kw)
        procs.append(proc)
        return proc

    monkeypatch.setattr(job.subprocess, "Popen", spy)
    payload = {"root": str(root), "attempt_id": "a" * 32, "reset_id": "b" * 24, "scope_name": None,
               "kwargs": _kwargs(args)}

    def killer():
        while not procs:
            time.sleep(0.01)
        time.sleep(0.05)
        procs[0].kill()

    thread = threading.Thread(target=killer)
    thread.start()
    with pytest.raises(job.IsolatedTaskError):
        job.run_isolated(root, "execute", payload)
    thread.join()
    # killed before/while scanning: retained evidence untouched, nothing half-done
    assert sum(1 for _ in os.scandir(base)) == 30000
    result = job.run_isolated(root, "execute", payload)
    assert result["status"] == "COMPLETE" and not any(t.exists() for t in targets)
    again = job.run_isolated(root, "execute", {**payload, "kwargs": _kwargs(
        args, validate_only=True, receipt_path=str(args["receipt_path"].with_name("r2.json")))})
    assert again["target_count"] == 0


def test_heartbeat_not_starved_during_410k_isolated_scan(tmp_path, heartbeat):
    args = settings(tmp_path)
    root = args["runtime_root"]
    heartbeat(root)
    put(root, "v3/market_segments/03/03" + "0" * 62 + ".json")
    base = root / "v3" / "receipts" / "emergency_record_idempotency_v1"
    for shard in range(200):
        (base / f"{shard:03d}").mkdir(parents=True)
    for index in range(410_000):
        with open(base / f"{index % 200:03d}" / f"{index}.json", "wb"):
            pass
    gaps = {"max": 0.0, "stop": False}

    def ticker():
        last = time.time()
        while not gaps["stop"]:
            time.sleep(0.05)
            now = time.time()
            gaps["max"] = max(gaps["max"], now - last)
            last = now

    thread = threading.Thread(target=ticker)
    thread.start()
    try:
        pre = job.run_isolated(root, "execute", {"root": str(root), "attempt_id": "a" * 32, "reset_id": "b" * 24,
                                                 "scope_name": None, "kwargs": _kwargs(args, validate_only=True)})
    finally:
        gaps["stop"] = True
        thread.join()
    assert pre["status"] == "VALIDATED" and pre["target_count"] == 1
    assert gaps["max"] < 1.0  # in-process heartbeat/ticker never starved by the scan
    beat = job.Guard(root).heartbeat_age()
    assert beat <= job.THROTTLE_AGE_S
    progress = json.loads((job.job_dir(root) / job.PROGRESS_FILE).read_text())
    assert progress["heartbeat_age_s"] <= job.THROTTLE_AGE_S


def test_status_route_and_start_are_wired_in_bot_source():
    src = (Path(__file__).parent / "bot.py").read_text()
    assert "@app.route('/api/monitor/full-reset', methods=[\"GET\"])" in src
    assert '"/api/monitor/full-reset"' in src
    assert 'data.get("background") is True' in src
    assert "_start_full_reset_job(data)" in src
    assert "threading.Thread(target=_run, name=\"full-reset-job\"" in src
    # heavy calls go through the isolating wrapper; no direct call with a progress callback remains
    import re
    assert len(re.findall(r"full_reset_job\.heavy_execute\(\s*execute_research_reset", src)) == 3
    assert src.count("full_reset_job.heavy_sample(root, proof") == 2


def test_workflow_polls_background_job_with_hard_timeout_and_never_resumes():
    text = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "fly-bot-deploy.yml").read_text()
    block = text[text.index("  full-reset-no-archive:"):]
    step = block[block.index("In-process guarded reset"):block.index("Stamp the fresh-start epoch")]
    assert '"background": True' in step and "/api/monitor/full-reset" in step
    assert "130 * 60" in step and "timeout-minutes: 140" in step
    assert "the file wipe was NOT started" in step
    assert "resume" not in step.lower().replace("resumes the pause", "")  # poll step never resumes paper
    assert "continue-on-error" not in step
