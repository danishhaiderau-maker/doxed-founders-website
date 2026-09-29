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
