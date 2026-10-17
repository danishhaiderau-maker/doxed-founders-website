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


# ---- HM review: heartbeat must not wait behind the reset barriers; start/abort semantics ----

def _bot_functions(names, env):
    import ast
    path = Path(__file__).with_name("bot.py")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), env)
    return env


def _heartbeat_env(gate, collection_lock):
    def maybe_record():  # stands in for _maybe_record_heartbeat_cycle_3m -> _safe_append_jsonl
        with gate:
            pass

    shutdown = threading.Event()
    env = {"time": time, "state": {}, "last_heartbeat": 0.0, "shutdown_event": shutdown,
           "PROCESS_HEARTBEAT_INTERVAL_SEC": 0.05, "_maybe_record_heartbeat_cycle_3m": maybe_record,
           "_fresh_collection_lock": collection_lock}
    return _bot_functions({"heartbeat_loop", "_heartbeat_research_write_blocked"}, env), shutdown


def _advances(env, seconds=0.6):
    first = env["state"].get("last_heartbeat", 0.0)
    time.sleep(seconds)
    return env["state"].get("last_heartbeat", 0.0) > first + 0.2


def test_heartbeat_keeps_advancing_while_reset_holds_research_write_gate():
    gate, collection = threading.Lock(), threading.Lock()
    env, shutdown = _heartbeat_env(gate, collection)
    gate.acquire()
    collection.acquire()  # the reset owns both for its whole duration
    thread = threading.Thread(target=env["heartbeat_loop"], daemon=True)
    thread.start()
    try:
        assert _advances(env)
    finally:
        shutdown.set()
        gate.release()
        thread.join(5)


def test_barrier_stall_control_heartbeat_would_block_without_the_skip():
    gate, collection = threading.Lock(), threading.Lock()  # collection lock NOT held, no job: old behaviour
    env, shutdown = _heartbeat_env(gate, collection)
    gate.acquire()
    thread = threading.Thread(target=env["heartbeat_loop"], daemon=True)
    thread.start()
    try:
        time.sleep(0.3)
        frozen = env["state"]["last_heartbeat"]
        time.sleep(0.5)
        assert env["state"]["last_heartbeat"] == frozen  # proves the test detects a barrier-induced stall
    finally:
        gate.release()
        shutdown.set()
        thread.join(5)


def test_heartbeat_skip_also_covers_an_active_background_job():
    gate, collection = threading.Lock(), threading.Lock()
    env, shutdown = _heartbeat_env(gate, collection)
    job.ACTIVE = {"operation_id": "x"}
    gate.acquire()
    thread = threading.Thread(target=env["heartbeat_loop"], daemon=True)
    thread.start()
    try:
        assert _advances(env)
    finally:
        job.ACTIVE = None
        shutdown.set()
        gate.release()
        thread.join(5)


def test_health_heartbeat_has_a_dedicated_thread_that_never_touches_the_gates():
    import ast
    src = (Path(__file__).with_name("bot.py")).read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    guarded = {"_research_write_gate", "_collector_epoch_lock", "_safe_append_jsonl", "_record_cycle_3m_universe"}
    body = funcs["heartbeat_loop"]
    names = {n.id for n in ast.walk(body) if isinstance(n, ast.Name)}
    assert not (names & guarded)  # only reaches the cycle-3m row via the guarded helper call
    assert "_heartbeat_research_write_blocked" in names and "_maybe_record_heartbeat_cycle_3m" in names
    gate = ast.unparse(funcs["_heartbeat_research_write_blocked"])
    assert "_fresh_collection_lock.locked()" in gate and "full_reset_job.ACTIVE" in gate
    health_names = {n.id for n in ast.walk(funcs["health"]) if isinstance(n, ast.Name)}
    assert not (health_names & {"state_lock", "_research_write_gate", "_collector_epoch_lock"})


def _start_env(tmp_path, perform):
    state = {"live_armed": False, "execution_paused": True, "last_heartbeat": time.time()}
    env = {"state": state, "state_lock": threading.RLock(), "threading": threading, "logger": __import__("logging").getLogger("t"),
           "_data_sync_runtime_root": lambda: tmp_path / "runtime", "perform_fresh_collection_reset": perform,
           "save_persistent_config": lambda: None, "last_heartbeat": time.time(),
           "_freeze_override_kwargs": lambda data: {}}
    return _bot_functions({"_start_full_reset_job"}, env), state


def test_concurrent_start_is_refused_409_then_status_completes(tmp_path):
    (tmp_path / "runtime").mkdir()
    release = threading.Event()

    def perform(**kw):
        job.note("ALL_SCOPES_PREFLIGHT")
        release.wait(10)
        return {"ok": True}

    env, state = _start_env(tmp_path, perform)
    body, code = env["_start_full_reset_job"]({"background": True})
    assert code == 202 and body["operation_id"]
    again, code2 = env["_start_full_reset_job"]({"background": True})
    assert code2 == 409 and again["operation_id"] == body["operation_id"]
    view = job.read_status(tmp_path / "runtime")
    assert view["state"] == "RUNNING" and view["operation_id"] == body["operation_id"]
    assert view["deletion_started"] is False
    release.set()
    for _ in range(100):
        if job.read_status(tmp_path / "runtime")["state"] != "RUNNING":
            break
        time.sleep(0.05)
    assert job.read_status(tmp_path / "runtime")["state"] == "DONE" and job.ACTIVE is None
    armed_env, armed_state = _start_env(tmp_path, perform)
    armed_state["live_armed"] = True
    refused, code3 = armed_env["_start_full_reset_job"]({"background": True})
    assert code3 == 400 and "LIVE ARM" in refused["message"]


def test_guard_abort_in_job_ends_failed_paused_with_nothing_deleted(tmp_path, monkeypatch):
    monkeypatch.setenv("FULL_RESET_JOB_ABORT_AFTER_S", "2")
    args = settings(tmp_path)
    root = args["runtime_root"]
    target = put(root, "v3/market_segments/04/04" + "0" * 62 + ".json")
    base = root / "v3" / "receipts" / "emergency_record_idempotency_v1" / "000"
    base.mkdir(parents=True)
    for index in range(5000):
        (base / f"{index}.json").touch()

    def perform(**kw):
        job.note("ALL_SCOPES_PREFLIGHT")
        # the bot heartbeat the publisher reports is ancient, so the worker throttles then aborts
        job.heavy_execute(execution.execute_research_reset, root, "a" * 32, "b" * 24, None,
                          **_kwargs(args), validate_only=True)
        return {"ok": True}

    env, state = _start_env(tmp_path, perform)
    env["_data_sync_runtime_root"] = lambda: root
    state["last_heartbeat"] = time.time() - 3600
    env["last_heartbeat"] = time.time() - 3600
    body, code = env["_start_full_reset_job"]({"background": True})
    assert code == 202
    for _ in range(400):
        if job.read_status(root)["state"] != "RUNNING":
            break
        time.sleep(0.1)
    view = job.read_status(root)
    assert view["state"] == "FAILED" and view["outcome"] == "JOB_EXCEPTION"
    assert view["alert"]["level"] == "RED" and view["deletion_started"] is False
    assert target.exists() and state["execution_paused"] is True  # nothing deleted, pause untouched


def test_workflow_failure_text_distinguishes_started_from_not_started():
    text = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "fly-bot-deploy.yml").read_text()
    block = text[text.index("  full-reset-no-archive:"):]
    step = block[block.index("In-process guarded reset"):block.index("Stamp the fresh-start epoch")]
    assert "MAY HAVE STARTED" in step and "no research-reset deletion had started" in step
    assert "deletion state UNKNOWN" in step and "deletion_started" in step
    assert 'where(view)' in step and 'where(last_view or {})' in step


def test_worker_dies_with_parent_hook_is_installed():
    assert job._die_with_parent.__doc__ and "PR_SET_PDEATHSIG" in job._die_with_parent.__doc__
    assert "preexec_fn=_die_with_parent" in Path(job.__file__).read_text()


def test_receipt_summary_is_read_only_and_bounded(tmp_path):
    import json as _json
    d = tmp_path / "research_reset_receipts" / "abc"
    d.mkdir(parents=True)
    (d / "r.json").write_text(_json.dumps({"mode": "x", "files": [1, 2, 3], "n": 5}))
    (tmp_path / "full-reset-receipts" / "tok").mkdir(parents=True)
    (tmp_path / "full-reset-receipts" / "tok" / "i.txt").write_text("hi")
    out = job.read_receipt_summary(tmp_path)
    dirs = {x["dir"]: x for x in out["directories"]}
    assert dirs["research_reset_receipts/abc"]["files"][0]["shape"] == {"mode": "x", "files": "list[3]", "n": 5}
    assert dirs["full-reset-receipts/tok"]["bytes_listed"] == 2
    assert job.read_receipt_summary(tmp_path / "missing")["directories"] == []


def test_receipt_summary_masks_truncates_and_is_lazy(tmp_path):
    import json as _json
    d = tmp_path / "research_reset_receipts" / "abc"
    d.mkdir(parents=True)
    (d / "r.json").write_text(_json.dumps({"confirm_token": "DELETE-X", "api_key": "k", "note": "x" * 500, "n": 1}))
    for i in range(210):
        (d / f"f{i}.txt").write_text("x")
    out = job.read_receipt_summary(tmp_path)
    entry = out["directories"][0]
    assert entry["files_listed"] == 200 and entry["truncated"] is True
    shape = next(f["shape"] for f in entry["files"] if f["file"].endswith("r.json"))
    assert shape["confirm_token"] == "<masked>" and shape["api_key"] == "<masked>"
    assert len(shape["note"]) == 200 and shape["n"] == 1
