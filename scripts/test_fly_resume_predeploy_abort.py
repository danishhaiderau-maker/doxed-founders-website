import urllib.error

import pytest

from fly_resume_predeploy_abort import resume_incumbent, validate_failed_run


INCUMBENT = "1" * 12
CANDIDATE = "a" * 40


class Clock:
    def __init__(self): self.now = 0.0
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds


def status(*, paused=True, revision=INCUMBENT):
    return {
        "source_git_rev": revision, "process_alive": True, "system_ready": True,
        "force_paper_mode": True, "bitfinex_live_enabled": False, "live_armed": False,
        "execution_paused": paused, "manual_admin_pause": paused,
        "strategy_progress": {"ok": True, "trade_lock_progressing": True, "open_positions": 0, "pending_orders": 0},
        "lifecycle_pipeline": {"owner": True, "running": True, "source_revision_match": True,
            "receipt_bootstrap": {"required": True, "status": "COMPLETE", "complete": True, "blocked": False}},
    }


def proof(conclusion_overrides=None, extra_steps=None, extra_jobs=None):
    rows = [
        (10, "Enter durable authenticated paper maintenance boundary", "success"),
        (11, "Prove the current Fly owner and every relay account are flat", "failure"),
        (14, "Recheck maintenance boundary immediately before deploy", "skipped"),
        (15, "Deploy the exact source revision", "skipped"),
        (16, "Re-enter maintenance and flatten the exact deployed revision", "skipped"),
        (17, "Prove liveness, execution safety, and exact revision", "skipped"),
        (18, "Complete receipt bootstrap inside exact-revision maintenance", "skipped"),
        (19, "Resume paper execution after exact-revision acceptance", "skipped"),
        (20, "Best-effort preserve safe paper maintenance after failed guarded deploy", "success"),
    ]
    overrides = conclusion_overrides or {}
    steps = [{"number": n, "name": name, "conclusion": overrides.get(name, conclusion)} for n, name, conclusion in rows]
    steps.extend(extra_steps or [])
    run = {"head_sha": CANDIDATE, "status": "completed", "conclusion": "failure", "event": "workflow_dispatch",
           "name": "Deploy Fly BTC bot", "path": ".github/workflows/fly-bot-deploy.yml@refs/heads/x"}
    jobs = {"jobs": [{"name": "test-and-deploy", "conclusion": "failure", "steps": steps}, *(extra_jobs or [])]}
    return lambda path: jobs if "/jobs?" in path else run


def test_failed_run_topology_accepts_only_predeploy_abort():
    result = validate_failed_run(INCUMBENT, CANDIDATE, "33695733166", proof())
    assert result == {"candidate_sha": CANDIDATE, "failed_run_id": "33695733166", "incumbent_revision": INCUMBENT}


@pytest.mark.parametrize("name,conclusion", [
    ("Deploy the exact source revision", "success"),
    ("Deploy the exact source revision", "failure"),
    ("Prove liveness, execution safety, and exact revision", "success"),
    ("Complete receipt bootstrap inside exact-revision maintenance", "failure"),
    ("Resume paper execution after exact-revision acceptance", "success"),
    ("Best-effort preserve safe paper maintenance after failed guarded deploy", "failure"),
])
def test_rejects_deploy_acceptance_or_missing_preservation(name, conclusion):
    with pytest.raises(RuntimeError):
        validate_failed_run(INCUMBENT, CANDIDATE, "33695733166", proof({name: conclusion}))


def test_rejects_same_candidate_and_incumbent():
    with pytest.raises(RuntimeError, match="must differ"):
        validate_failed_run("a" * 12, CANDIDATE, "33695733166", proof())


def test_rejects_duplicate_deploy_record_even_when_one_is_skipped():
    duplicate = {"number": 21, "name": "Deploy the exact source revision", "conclusion": "success"}
    with pytest.raises(RuntimeError):
        validate_failed_run(INCUMBENT, CANDIDATE, "33695733166", proof(extra_steps=[duplicate]))


def test_rejects_duplicate_test_and_deploy_job():
    duplicate = {"name": "test-and-deploy", "conclusion": "failure", "steps": []}
    with pytest.raises(RuntimeError, match="one failed test-and-deploy job"):
        validate_failed_run(INCUMBENT, CANDIDATE, "33695733166", proof(extra_jobs=[duplicate]))


def test_resume_proves_fresh_flat_incumbent_and_posts_once():
    clock = Clock(); calls = []; statuses = [status(), status(paused=False)]
    def request(path, payload):
        calls.append((path, payload))
        if path == "/api/status": return statuses.pop(0)
        if path == "/api/relay-execution-state?fresh=1": return {"money_state_generation": 7, "orders": [], "positions": []}
        if path == "/api/resume": return {"status": "resumed", "execution_paused": False}
        raise AssertionError(path)
    assert resume_incumbent(INCUMBENT, CANDIDATE, request, monotonic=clock.monotonic, sleep=clock.sleep)["execution_paused"] is False
    assert calls.count(("/api/resume", {})) == 1


@pytest.mark.parametrize("mutate", [
    lambda row: row.update(source_git_rev="2" * 12),
    lambda row: row.update(system_ready=False),
    lambda row: row.update(force_paper_mode=False),
    lambda row: row.update(bitfinex_live_enabled=True),
    lambda row: row.update(live_armed=True),
    lambda row: row.update(execution_paused=False),
    lambda row: row["strategy_progress"].update(open_positions=1),
    lambda row: row["strategy_progress"].update(pending_orders="0"),
    lambda row: row["lifecycle_pipeline"]["receipt_bootstrap"].update(status="PENDING", complete=False),
])
def test_unsafe_pre_resume_state_never_mutates(mutate):
    row = status(); mutate(row); calls = []
    def request(path, payload): calls.append(path); return row
    with pytest.raises(RuntimeError):
        resume_incumbent(INCUMBENT, CANDIDATE, request, monotonic=Clock().monotonic, sleep=lambda _: None)
    assert "/api/resume" not in calls


@pytest.mark.parametrize("relay", [
    {"money_state_generation": None, "orders": [], "positions": []},
    {"money_state_generation": 1, "orders": [{}], "positions": []},
    {"money_state_generation": 1, "orders": [], "positions": [{}]},
])
def test_unproven_fresh_flat_relay_state_never_resumes(relay):
    calls = []
    def request(path, payload):
        calls.append(path)
        return status() if path == "/api/status" else relay
    with pytest.raises(RuntimeError, match="not generation-current and flat"):
        resume_incumbent(INCUMBENT, CANDIDATE, request, monotonic=Clock().monotonic, sleep=lambda _: None)
    assert "/api/resume" not in calls


def test_ambiguous_resume_is_never_retried():
    calls = []
    def request(path, payload):
        calls.append(path)
        if path == "/api/status": return status()
        if path == "/api/relay-execution-state?fresh=1": return {"money_state_generation": 1, "orders": [], "positions": []}
        raise urllib.error.URLError("ambiguous")
    with pytest.raises(urllib.error.URLError):
        resume_incumbent(INCUMBENT, CANDIDATE, request, monotonic=Clock().monotonic, sleep=lambda _: None)
    assert calls.count("/api/resume") == 1


def test_post_resume_revision_or_safety_drift_fails_without_second_resume():
    calls = []; statuses = [status(), status(paused=False, revision="2" * 12)]
    def request(path, payload):
        calls.append(path)
        if path == "/api/status": return statuses.pop(0)
        if path == "/api/relay-execution-state?fresh=1": return {"money_state_generation": 1, "orders": [], "positions": []}
        return {"status": "resumed", "execution_paused": False}
    with pytest.raises(RuntimeError, match="unsafe predeploy-abort status"):
        resume_incumbent(INCUMBENT, CANDIDATE, request, monotonic=Clock().monotonic, sleep=lambda _: None)
    assert calls.count("/api/resume") == 1
