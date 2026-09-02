import urllib.error

import pytest

from fly_resume_bootstrap import continue_bootstrap, observe_status, preserve_maintenance, validate_failed_run


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
        "strategy_progress": {"ok": True, "trade_lock_available": True, "trade_lock_busy_transient": False, "trade_lock_progressing": True, "open_positions": 0, "pending_orders": 0},
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
    lambda row: row["strategy_progress"].update(trade_lock_progressing=False),
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


def test_unsafe_status_reports_exact_allowlisted_failed_predicates():
    clock = Clock(); row = status()
    row["process_alive"] = False
    row["strategy_progress"]["pending_orders"] = 2
    row["lifecycle_pipeline"]["owner"] = False
    row["secret_token"] = "must-not-leak"
    def request(path, payload):
        return row
    with pytest.raises(RuntimeError) as caught:
        continue_bootstrap(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)
    message = str(caught.value)
    assert '"failed_predicates":["lifecycle_owner","pending_orders_zero","process_alive"]' in message
    assert '"pending_orders":2' in message
    assert '"required_paused":true' in message
    assert "must-not-leak" not in message
    assert "secret_token" not in message


@pytest.mark.parametrize("malformed", ["unknown", "0", 0.5, False])
def test_malformed_exposure_count_is_diagnostic_and_never_treated_as_zero(malformed):
    clock = Clock(); row = status()
    row["strategy_progress"]["open_positions"] = malformed
    def request(path, payload):
        return row
    with pytest.raises(RuntimeError) as caught:
        continue_bootstrap(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)
    message = str(caught.value)
    assert '"failed_predicates":["open_positions_zero"]' in message
    assert '"open_positions":null' in message


def test_failure_path_preserves_exact_flat_paper_maintenance_with_one_pause():
    clock = Clock(); rows = [status(paused=False), status(paused=True)]; pauses = []
    def request(path, payload):
        if path == "/api/pause":
            pauses.append(payload); return {"status": "paused", "execution_paused": True}
        return rows.pop(0)
    final = preserve_maintenance(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)
    assert final["manual_admin_pause"] is True
    assert pauses == [{}]


def test_failure_path_refuses_hard_drift_without_pause():
    clock = Clock(); row = status(paused=False, source_git_rev="b" * 12); pauses = []
    def request(path, payload):
        if path == "/api/pause": pauses.append(payload)
        return row
    with pytest.raises(RuntimeError, match="refused non-exact, unsafe, or invalid-schema"):
        preserve_maintenance(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)
    assert pauses == []


def test_failure_path_pauses_before_reporting_new_nonflat_paper_exposure():
    clock = Clock(); pauses = []
    before = status(paused=False); before["strategy_progress"]["pending_orders"] = 1
    after = status(paused=True); after["strategy_progress"]["pending_orders"] = 1
    rows = [before, after]
    def request(path, payload):
        if path == "/api/pause":
            pauses.append(payload); return {"status": "paused", "execution_paused": True}
        return rows.pop(0)
    with pytest.raises(RuntimeError, match="maintenance preserved with nonflat paper exposure") as caught:
        preserve_maintenance(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)
    assert pauses == [{}]
    assert '"open_positions":0' in str(caught.value)
    assert '"pending_orders":1' in str(caught.value)


def test_failure_path_never_retries_ambiguous_pause():
    clock = Clock(); pauses = []
    def request(path, payload):
        if path == "/api/pause":
            pauses.append(payload); raise urllib.error.URLError("ambiguous")
        return status(paused=False)
    with pytest.raises(urllib.error.URLError):
        preserve_maintenance(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)
    assert pauses == [{}]


def test_pending_bootstrap_waits_then_resumes_exactly_once():
    clock = Clock(); statuses = [status(complete=False), status(), status(paused=False)]; posts = []
    def request(path, payload):
        if path == "/api/resume":
            posts.append(payload); return {"status": "resumed", "execution_paused": False}
        return statuses.pop(0)
    result = continue_bootstrap(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)
    assert result["execution_paused"] is False
    assert posts == [{}]


def test_zero_wait_lock_busy_is_accepted_when_progressing_is_true():
    clock = Clock(); posts = []
    pre = status()
    pre["strategy_progress"].update(trade_lock_available=False, trade_lock_busy_transient=True, trade_lock_progressing=True)
    post = status(paused=False)
    post["strategy_progress"].update(trade_lock_available=False, trade_lock_busy_transient=True, trade_lock_progressing=True)
    rows = [pre, post]
    def request(path, payload):
        if path == "/api/resume":
            posts.append(payload); return {"status": "resumed", "execution_paused": False}
        return rows.pop(0)
    assert continue_bootstrap(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)["execution_paused"] is False
    assert posts == [{}]


def test_nonprogressing_lock_is_reobserved_until_deadline_without_resume():
    clock = Clock(); status_calls = 0; posts = []
    row = status(); row["strategy_progress"]["trade_lock_progressing"] = False
    def request(path, payload):
        nonlocal status_calls
        if path == "/api/resume":
            posts.append(payload); return {"status": "resumed"}
        status_calls += 1; return row
    with pytest.raises(RuntimeError, match="bounded deadline expired") as caught:
        continue_bootstrap(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)
    assert status_calls > 1 and posts == []
    assert '"failed_readiness_predicates":["trade_lock_progressing"]' in str(caught.value)


def test_resume_requires_complete_and_every_readiness_gate_in_one_snapshot():
    clock = Clock(); posts = []
    complete_not_ready = status()
    complete_not_ready["strategy_progress"]["trade_lock_progressing"] = False
    ready_not_complete = status(complete=False)
    rows = [complete_not_ready, ready_not_complete, status(), status(paused=False)]
    def request(path, payload):
        if path == "/api/resume":
            posts.append((payload, len(rows)))
            return {"status": "resumed", "execution_paused": False}
        return rows.pop(0)
    continue_bootstrap(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=40)
    # Three pre-resume observations were required; neither partial snapshot was
    # sufficient on its own. One post-resume observation remains at mutation.
    assert posts == [({}, 1)]


def test_hard_revision_drift_fails_on_first_snapshot():
    clock = Clock(); calls = 0; posts = []
    row = status(source_git_rev="b" * 12)
    def request(path, payload):
        nonlocal calls
        calls += 1
        if path == "/api/resume": posts.append(payload)
        return row
    with pytest.raises(RuntimeError, match="UNSAFE_BOOTSTRAP_STATUS"):
        continue_bootstrap(REV, request, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20)
    assert calls == 1 and posts == []


def test_post_resume_bootstrap_regression_deadline_fails_after_one_post():
    clock = Clock(); posts = []
    def request(path, payload):
        if path == "/api/resume":
            posts.append(payload); return {"status": "resumed", "execution_paused": False}
        return status() if not posts else status(paused=False, complete=False)
    with pytest.raises(RuntimeError, match="post-resume readiness deadline expired"):
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
