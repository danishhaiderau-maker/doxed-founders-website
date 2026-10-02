"""Bounded-retry probe behaviour of the scheduled Fly monitor."""

import io
import json
import urllib.error

import pytest

import fly_monitor_run as runner


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, body):
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(json.dumps(body).encode()))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)


def test_ready_503_body_is_returned_with_reasons_after_retries(monkeypatch):
    calls = []

    def urlopen(url, timeout):
        calls.append(url)
        raise _http_error(503, {"ok": False, "status": "not_ready"})

    monkeypatch.setattr(runner.urllib.request, "urlopen", urlopen)
    status, payload = runner.probe("u", accept=lambda s, p: s == 200)
    assert (status, payload["status"]) == (503, "not_ready")
    assert len(calls) == runner.PROBE_ATTEMPTS


def test_transient_timeout_recovers_within_the_same_run(monkeypatch):
    outcomes = [TimeoutError("read timed out"), _Response(b'{"ok": true}')]

    def urlopen(url, timeout):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(runner.urllib.request, "urlopen", urlopen)
    assert runner.probe("u", accept=lambda s, p: s == 200) == (200, {"ok": True})


def test_persistent_timeouts_raise_probe_unavailable(monkeypatch):
    def urlopen(url, timeout):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(runner.urllib.request, "urlopen", urlopen)
    with pytest.raises(runner.ProbeUnavailable, match="3 attempts"):
        runner.probe("u", accept=lambda s, p: True)


class _FakeIssues:
    """In-memory GitHub issues API covering the calls sync_issue makes."""

    def __init__(self):
        self.issues = []
        self.comments = []

    def __call__(self):
        return self

    def call(self, path, method="GET", body=None):
        if method == "GET" and path.startswith("/issues?state=open"):
            return [i for i in self.issues if i["state"] == "open"]
        if path == "/labels":
            return {}
        if path == "/issues" and method == "POST":
            issue = {"number": len(self.issues) + 1, "state": "open", **body}
            self.issues.append(issue)
            return issue
        if path.endswith("/comments"):
            self.comments.append(body["body"])
            return {}
        number = int(path.rsplit("/", 1)[-1])
        self.issues[number - 1].update(body)
        return {}


def _cycle(fake, state, findings, now, informational=frozenset()):
    decisions, resolved = runner.alerts.evaluate(
        state, findings, now=now, maintenance=False, informational=informational
    )
    runner.sync_issue(state, decisions, resolved, now)
    return decisions


def test_one_issue_per_incident_comments_only_on_new_alerts_and_closes_on_recovery(monkeypatch):
    fake = _FakeIssues()
    monkeypatch.setattr(runner, "GitHub", fake)
    state = runner.alerts.empty_state()

    _cycle(fake, state, {"disk_warn": "72%"}, 0)
    assert len(fake.issues) == 1 and fake.comments == []
    for t in (900, 1800, 2700):
        _cycle(fake, state, {"disk_warn": "72%"}, t)
    assert len(fake.issues) == 1 and fake.comments == []

    _cycle(fake, state, {"disk_warn": "86%", "disk_critical": "URGENT 86%"}, 3600)
    assert len(fake.issues) == 1
    assert len(fake.comments) == 1 and "disk_critical" in fake.comments[0]
    assert "`disk_critical`" in fake.issues[0]["body"] and "`disk_warn`" in fake.issues[0]["body"]

    _cycle(fake, state, {}, 4500)
    assert fake.issues[0]["state"] == "open"
    _cycle(fake, state, {}, 5400)
    assert fake.issues[0]["state"] == "closed"
    assert fake.comments[-1].startswith("Recovered:")

    _cycle(fake, state, {"test_alert": "synthetic"}, 6300)
    assert len(fake.issues) == 2 and fake.issues[1]["state"] == "open"


def test_informational_findings_never_open_an_issue(monkeypatch):
    fake = _FakeIssues()
    monkeypatch.setattr(runner, "GitHub", fake)
    state = runner.alerts.empty_state()
    for t in (0, 3600, 7200, 10800):
        _cycle(fake, state, {"transfer_lag": "behind"}, t, informational=frozenset({"transfer_lag"}))
    assert fake.issues == [] and fake.comments == []


def test_informational_keys_follow_segments_live_flag(monkeypatch):
    monkeypatch.delenv("FLY_MONITOR_SEGMENTS_LIVE", raising=False)
    assert runner.informational_keys() == frozenset({"transfer_lag"})
    monkeypatch.setenv("FLY_MONITOR_SEGMENTS_LIVE", "1")
    assert runner.informational_keys() == frozenset()


def test_last_finished_deploy_skips_dispatches_that_did_not_deploy():
    runs = [
        {"id": 3, "status": "in_progress", "event": "push"},
        {"id": 2, "status": "completed", "event": "workflow_dispatch", "conclusion": "success"},
        {"id": 1, "status": "completed", "event": "push", "conclusion": "failure", "head_sha": "b" * 40,
         "updated_at": "2026-10-02T09:00:00Z"},
    ]
    jobs = {2: [{"name": runner.DEPLOY_JOB_NAME, "conclusion": "skipped"}]}
    last = runner.last_finished_deploy(runs, lambda rid: jobs.get(rid, []))
    assert last["id"] == 1 and last["conclusion"] == "failure"
