import http.client
import io
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fly_failure_paper_resume as resume  # noqa: E402


def _ready(**overrides):
    status = {
        "source_git_rev": "4e24c650abcdef0123456789abcdef0123456789",
        "force_paper_mode": True, "bitfinex_live_enabled": False, "live_armed": False,
        "execution_paused": True, "manual_admin_pause": True, "pause_owner": "DEPLOY_MAINTENANCE",
        "process_alive": True, "system_ready": True,
        "lifecycle_pipeline": {"owner": True, "running": True,
                               "receipt_bootstrap": {"required": True, "status": "COMPLETE", "complete": True}},
    }
    status.update(overrides)
    return status


ACTIVE = {"execution_paused": False, "manual_admin_pause": False, "pause_owner": None}


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.5)


def _client(responses):
    posts = []

    def request_json(path, payload=None):
        if payload is not None:
            posts.append((path, payload))
        value = responses[path].pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    return request_json, posts


def _run(responses, prior=None, timeout=600):
    clock = Clock()
    request_json, posts = _client(responses)
    result = resume.guaranteed_resume(request_json, prior or {"captured": True, "operator_paused": False},
                                      monotonic=clock.monotonic, sleep=clock.sleep, timeout=timeout)
    return result, posts


def test_deploy_maintenance_pause_left_by_failed_deploy_is_resumed_once():
    result, posts = _run({
        "/api/status": [_ready(), _ready(**ACTIVE)],
        "/api/resume": [{"status": "resumed", "execution_paused": False}],
    })
    assert result["status"] == "ACTIVE" and result["revision"] == "4e24c650abcd"
    assert posts == [("/api/resume", {"clear_admin_manual_pause": True, "owner": "DEPLOY_MAINTENANCE"})]


def test_starved_runtime_status_and_ambiguous_resume_are_reobserved_then_retried():
    result, posts = _run({
        "/api/status": [TimeoutError(), http.client.IncompleteRead(b"x", 10), _ready(), _ready(), _ready(**ACTIVE)],
        "/api/resume": [http.client.RemoteDisconnected("closed"), {"status": "resumed", "execution_paused": False}],
    })
    assert result["status"] == "ACTIVE" and len(posts) == 2


def test_already_active_runtime_is_not_mutated():
    result, posts = _run({"/api/status": [_ready(**ACTIVE)]})
    assert result["status"] == "ACTIVE" and posts == []


@pytest.mark.parametrize("owner", ["OPERATOR", "SAFETY"])
def test_operator_and_safety_pauses_are_retained(owner):
    result, posts = _run({"/api/status": [_ready(pause_owner=owner)]})
    assert result["status"] == "OPERATOR_PAUSE_RETAINED" and posts == []


def test_prior_operator_pause_is_retained_even_if_owner_was_rewritten():
    result, posts = _run({"/api/status": [_ready()]}, prior={"captured": True, "operator_paused": True})
    assert result["status"] == "OPERATOR_PAUSE_RETAINED" and posts == []


def test_server_side_operator_retention_is_accepted():
    result, posts = _run({
        "/api/status": [_ready()],
        "/api/resume": [{"status": "operator_pause_retained", "pause_owner": "OPERATOR"}],
    })
    assert result["status"] == "OPERATOR_PAUSE_RETAINED" and len(posts) == 1


@pytest.mark.parametrize("unsafe", [
    {"live_armed": True}, {"bitfinex_live_enabled": True}, {"force_paper_mode": False},
    {"live_armed": None},
])
def test_non_paper_identity_is_never_resumed(unsafe):
    with pytest.raises(RuntimeError, match="non-paper identity"):
        _run({"/api/status": [_ready(**unsafe)]})


def test_unready_revision_waits_then_fails_closed_without_mutation():
    unready = _ready(system_ready=False)
    request_json, posts = _client({"/api/status": [unready] * 200})
    clock = Clock()
    with pytest.raises(RuntimeError, match="deadline expired"):
        resume.guaranteed_resume(request_json, {"captured": False},
                                 monotonic=clock.monotonic, sleep=clock.sleep, timeout=60)
    assert posts == []


@pytest.mark.parametrize("pipeline", [
    {"owner": True, "running": True, "receipt_bootstrap": {"required": True, "status": "BLOCKED", "blocked": True}},
    {"owner": True, "running": True, "receipt_bootstrap": {"required": True, "status": "RUNNING", "complete": False}},
    {"owner": False, "running": True, "receipt_bootstrap": {}},
])
def test_blocked_or_incomplete_lifecycle_is_not_resumed(pipeline):
    assert resume.readiness_failures(_ready(lifecycle_pipeline=pipeline))


def test_resume_attempts_are_bounded():
    status = [_ready()] * 20
    with pytest.raises(RuntimeError, match="exhausted"):
        _run({"/api/status": status, "/api/resume": [{"status": "resumed"}] * 5})


def test_non_transient_error_propagates():
    error = urllib.error.HTTPError("https://x", 401, "unauthorized", {}, io.BytesIO(b"{}"))
    with pytest.raises(urllib.error.HTTPError):
        _run({"/api/status": [error]})


def test_parse_prior_tolerates_missing_or_invalid_state():
    assert resume.parse_prior("") == {}
    assert resume.parse_prior("not json") == {"captured": False}
    assert resume.parse_prior("[]") == {"captured": False}
