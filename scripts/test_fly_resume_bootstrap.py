import urllib.error

import pytest

from fly_resume_bootstrap import continue_bootstrap, observe_status, validate_failed_run


REV = "a" * 12


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def status(*, paused=True, complete=True, **updates):
    value = {
        "source_git_rev": REV,
        "process_alive": True,
        "force_paper_mode": True,
        "bitfinex_live_enabled": False,
        "live_armed": False,
        "execution_paused": paused,
        "manual_admin_pause": paused,
        "strategy_progress": {"ok": True, "trade_lock_available": True, "open_positions": 0, "pending_orders": 0},
        "lifecycle_pipeline": {"owner": True, "running": True, "source_revision_match": True, "receipt_bootstrap": {"required": True, "status": "COMPLETE" if complete else "PENDING", "complete": complete, "blocked": False}},
    }
    value.update(updates)
    return value


def http_error(code):
    return urllib.error.HTTPError("https://example.test", code, "test", {}, None)


def test_status_observation_recovers_from_503_then_returns_payload():
    clock = Clock(); calls = 0
    def request(path, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise http_error(503)
        return status()
    assert observe_status(request, until=10, monotonic=clock.monotonic, sleep=clock.sleep)["source_git_rev"] == REV
    assert calls == 2


def test_status_observation_persistent_transient_expires_at_deadline():
    clock = Clock(); calls = 0
    def request(path, payload):
        nonlocal calls
        calls += 1
        raise http_error(503)
    with pytest.raises(RuntimeError, match="bounded status observation unavailable"):
        observe_status(request, until=20, monotonic=clock.monotonic, sleep=clock.sleep)
    assert clock.now == 20
    assert calls > 4


def test_status_observation_401_fails_immediately():
    clock = Clock(); calls = 0
    def request(path, payload):
        nonlocal calls
        calls += 1
        raise http_error(401)
    with pytest.raises(urllib.error.HTTPError) as caught:
        observe_status(request, until=20, monotonic=clock.monotonic, sleep=clock.sleep)
    assert caught.value.code == 401
    assert calls == 1 and clock.now == 0


@pytest.mark.parametrize("mutate", [
    lambda row: row.update(source_git_rev="b" * 12),
    lambda row: row.update(process_alive=False),
    lambda row: row["strategy_progress"].update(ok=False),
    lambda row: row["strategy_progress"].update(trade_lock_available=False),
    lambda row: row["strategy_progress"].update(open_positions=1),
    lambda row: row["strategy_progress"].update(pending_orders=1),
    lambda row: row["lifecycle_pipeline"].update(owner=False),
    lambda row: row["lifecycle_pipeline"].update(running=False),
    lambda row: row["lifecycle_pipeline"].update(source_revision_match=False),
    lambda row: row["lifecycle_pipeline"]["receipt_bootstrap"].update(blocked=True, status="BLOCKED"),
])
def test_pre_resume_safety_and_owner_regressions_never_resume(mutate):
    clock = Clock(); row = status(); mutate(row); posts = []
    def request(path, payload):
        if path == "/api/resume": posts.append(payload)
        return row
    with pytest.raises(RuntimeError):
        continue_bootstrap(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)
    assert posts == []


def test_pending_bootstrap_waits_then_resumes_exactly_once():
    clock = Clock(); statuses = [status(complete=False), status(), status(paused=False)]; posts = []
    def request(path, payload):
        if path == "/api/resume":
            posts.append(payload); return {"status": "resumed", "execution_paused": False}
        return statuses.pop(0)
    result = continue_bootstrap(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)
    assert result["execution_paused"] is False
    assert posts == [{}]


def test_post_resume_bootstrap_regression_fails_after_one_post():
    clock = Clock(); posts = []
    def request(path, payload):
        if path == "/api/resume":
            posts.append(payload); return {"status": "resumed", "execution_paused": False}
        return status() if not posts else status(paused=False, complete=False)
    with pytest.raises(RuntimeError, match="receipt bootstrap is not complete"):
        continue_bootstrap(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)
    assert posts == [{}]


def test_ambiguous_resume_post_is_never_retried():
    clock = Clock(); posts = []
    def request(path, payload):
        if path == "/api/resume":
            posts.append(payload)
            raise urllib.error.URLError("ambiguous")
        return status()
    with pytest.raises(urllib.error.URLError):
        continue_bootstrap(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)
    assert posts == [{}]


def test_failed_run_proof_binds_sha_and_required_step_order():
    run = {"head_sha": REV + "b" * 28, "conclusion": "failure", "event": "workflow_dispatch", "name": "Deploy Fly BTC bot", "path": ".github/workflows/fly-bot-deploy.yml@refs/heads/test"}
    names = [
        ("Deploy the exact source revision", "success"),
        ("Prove liveness, execution safety, and exact revision", "success"),
        ("Complete receipt bootstrap inside exact-revision maintenance", "failure"),
        ("Resume paper execution after exact-revision acceptance", "skipped"),
        ("Best-effort preserve safe paper maintenance after failed guarded deploy", "success"),
    ]
    jobs = {"jobs": [{"name": "test-and-deploy", "conclusion": "failure", "steps": [{"name": name, "conclusion": conclusion, "number": number} for number, (name, conclusion) in enumerate(names, 1)]}]}
    fetch = lambda path: jobs if "/jobs?" in path else run
    assert validate_failed_run(REV, "33677658447", fetch)["head_sha"] == run["head_sha"]
    bad = dict(run, head_sha="b" * 40)
    with pytest.raises(RuntimeError, match="does not bind"):
        validate_failed_run(REV, "33677658447", lambda path: jobs if "/jobs?" in path else bad)
