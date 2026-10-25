"""Receipt-bootstrap gate: complete wait, terminal stall fail-loud, resume waiver."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fly_failure_paper_resume as resume  # noqa: E402
import fly_receipt_bootstrap_gate as gate  # noqa: E402


EXPECTED = "39358c1bffbe"


def _status(**over):
    status = {
        "source_git_rev": EXPECTED,
        "force_paper_mode": True,
        "bitfinex_live_enabled": False,
        "live_armed": False,
        "execution_paused": True,
        "manual_admin_pause": True,
        "pause_owner": "DEPLOY_MAINTENANCE",
        "process_alive": True,
        "system_ready": True,
        "strategy_progress": {"open_positions": 0, "pending_orders": 0, "ok": True},
        "lifecycle_pipeline": {
            "owner": True,
            "running": True,
            "source_revision_match": True,
            "last_outcome": "SUCCESS",
            "last_error_code": None,
            "failure_count": 0,
            "receipt_bootstrap": {
                "required": True,
                "status": "PENDING",
                "complete": False,
                "blocked": False,
                "records_indexed": 0,
                "bytes_indexed": 0,
            },
        },
    }
    pipeline = dict(status["lifecycle_pipeline"])
    bootstrap = dict(pipeline["receipt_bootstrap"])
    if "lifecycle_pipeline" in over:
        pipeline.update(over.pop("lifecycle_pipeline"))
    if "receipt_bootstrap" in over:
        bootstrap.update(over.pop("receipt_bootstrap"))
        pipeline["receipt_bootstrap"] = bootstrap
    else:
        pipeline["receipt_bootstrap"] = bootstrap
    status["lifecycle_pipeline"] = pipeline
    status.update(over)
    return status


def _truncated(**over):
    pipeline = {
        "last_outcome": "WORKER_FAILED",
        "last_error_code": "SOURCE_LEDGER_TRUNCATED:order_intent.jsonl",
        "failure_count": 5,
        "backoff_sec": 1800.0,
    }
    bootstrap = {
        "required": True,
        "status": "PENDING",
        "complete": False,
        "blocked": False,
        "records_indexed": 0,
        "bytes_indexed": 0,
    }
    if "lifecycle_pipeline" in over:
        pipeline.update(over.pop("lifecycle_pipeline"))
    if "receipt_bootstrap" in over:
        bootstrap.update(over.pop("receipt_bootstrap"))
    return _status(lifecycle_pipeline=pipeline, receipt_bootstrap=bootstrap, **over)


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(float(seconds), 0.5)


def test_terminal_truncated_ledger_with_zero_progress_is_a_stall():
    stall = gate.bootstrap_terminal_stall(_truncated())
    assert stall is not None
    assert stall["code"] == "RECEIPT_BOOTSTRAP_TERMINAL_STALL"
    assert "SOURCE_LEDGER_TRUNCATED" in str(stall["last_error_code"])


def test_in_progress_bootstrap_is_not_a_stall():
    assert gate.bootstrap_terminal_stall(_status(
        receipt_bootstrap={"status": "PENDING", "records_indexed": 128, "bytes_indexed": 4096},
    )) is None
    assert gate.bootstrap_terminal_stall(_status(
        lifecycle_pipeline={"last_outcome": "SUCCESS"},
        receipt_bootstrap={"status": "PENDING"},
    )) is None


def test_blocked_bootstrap_is_not_classified_as_terminal_stall():
    assert gate.bootstrap_terminal_stall(_truncated(
        receipt_bootstrap={"blocked": True, "status": "BLOCKED"},
    )) is None


def test_wait_fails_loud_on_terminal_stall_without_waiting_out_budget():
    clock = Clock()
    posts = []

    def request_json(path, payload=None):
        posts.append((path, payload))
        return {"status": "paused", "execution_paused": True} if path == "/api/pause" else _truncated()

    with pytest.raises(RuntimeError, match="RECEIPT_BOOTSTRAP_TERMINAL_STALL"):
        gate.wait_for_bootstrap(
            request_json, EXPECTED, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20 * 60,
        )
    assert clock.now < 60
    assert posts[0] == ("/api/pause", {"owner": "DEPLOY_MAINTENANCE"})


def test_wait_hard_timeout_raises_receipt_bootstrap_timeout():
    clock = Clock()
    pending = _status()

    def request_json(path, payload=None):
        return {"status": "paused"} if path == "/api/pause" else pending

    # Must clear the audited scheduling floor (~42s) while still proving the
    # hard-timeout path (not the 20-minute Actions budget).
    with pytest.raises(RuntimeError, match="RECEIPT_BOOTSTRAP_TIMEOUT"):
        gate.wait_for_bootstrap(
            request_json, EXPECTED, monotonic=clock.monotonic, sleep=clock.sleep, timeout=45,
        )
    assert clock.now >= 45


def test_wait_returns_when_bootstrap_completes():
    clock = Clock()
    pending = _status()
    done = _status(receipt_bootstrap={"status": "COMPLETE", "complete": True, "records_indexed": 100})
    responses = {"/api/pause": [{"status": "paused", "execution_paused": True}],
                 "/api/status?fresh=1": [pending, done]}

    def request_json(path, payload=None):
        return responses[path].pop(0)

    final = gate.wait_for_bootstrap(
        request_json, EXPECTED, monotonic=clock.monotonic, sleep=clock.sleep, timeout=20 * 60,
    )
    assert gate.bootstrap_complete(final) is True


def test_failure_resume_releases_deploy_maintenance_on_terminal_stall():
    """Mutation-style proof: stall must not leave paper paused (R23-style assert)."""
    stalled = _truncated()
    active = dict(stalled)
    active.update(execution_paused=False, manual_admin_pause=False, pause_owner=None)
    # Mutating the stall detector off must keep readiness blocked (proof the waiver matters).
    assert resume.readiness_failures(stalled) == []
    assert "bootstrap_complete_if_required" in resume.readiness_failures(_status(
        lifecycle_pipeline={"last_outcome": "SUCCESS", "owner": True, "running": True},
        receipt_bootstrap={"required": True, "status": "PENDING", "complete": False},
    ))

    posts = []
    clock = Clock()

    def request_json(path, payload=None):
        if payload is not None:
            posts.append((path, payload))
            return {"status": "resumed", "execution_paused": False}
        return stalled if not posts else active

    result = resume.guaranteed_resume(
        request_json, {"captured": True, "operator_paused": False},
        monotonic=clock.monotonic, sleep=clock.sleep, timeout=600,
    )
    assert result["status"] == "ACTIVE"
    assert result["revision"] == EXPECTED
    assert posts == [("/api/resume", {"clear_admin_manual_pause": True, "owner": "DEPLOY_MAINTENANCE"})]


def test_failure_resume_waives_bootstrap_when_gate_hard_timeout_flag_set(monkeypatch):
    """Mutation proof: BOOTSTRAP_GATE_FAILED is what enables fall-through resume."""
    pending = _status(
        lifecycle_pipeline={"last_outcome": "SUCCESS", "owner": True, "running": True},
        receipt_bootstrap={"required": True, "status": "PENDING", "complete": False, "blocked": False},
    )
    assert "bootstrap_complete_if_required" in resume.readiness_failures(pending)
    monkeypatch.setenv("BOOTSTRAP_GATE_FAILED", "1")
    assert resume.bootstrap_gate_failed() is True
    assert resume.readiness_failures(pending) == []
    # Kill the flag → assert reappears (mutation proof the waiver is gated).
    monkeypatch.delenv("BOOTSTRAP_GATE_FAILED", raising=False)
    assert "bootstrap_complete_if_required" in resume.readiness_failures(pending)

    monkeypatch.setenv("BOOTSTRAP_GATE_FAILED", "1")
    active = dict(pending)
    active.update(execution_paused=False, manual_admin_pause=False, pause_owner=None)
    posts = []
    clock = Clock()

    def request_json(path, payload=None):
        if payload is not None:
            posts.append((path, payload))
            return {"status": "resumed", "execution_paused": False}
        return pending if not posts else active

    result = resume.guaranteed_resume(
        request_json, {"captured": True, "operator_paused": False},
        monotonic=clock.monotonic, sleep=clock.sleep, timeout=600,
    )
    assert result["status"] == "ACTIVE"
    assert posts == [("/api/resume", {"clear_admin_manual_pause": True, "owner": "DEPLOY_MAINTENANCE"})]


def test_blocked_bootstrap_still_refuses_resume(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_GATE_FAILED", "1")
    blocked = _truncated(receipt_bootstrap={"blocked": True, "status": "BLOCKED", "complete": False})
    assert "bootstrap_not_blocked" in resume.readiness_failures(blocked)


def test_stall_diagnostic_is_stable_json():
    stall = gate.bootstrap_terminal_stall(_truncated())
    assert stall is not None
    # Round-trip proves the fail-loud payload stays JSON-serializable for Actions logs.
    assert json.loads(json.dumps(stall, sort_keys=True))["code"] == "RECEIPT_BOOTSTRAP_TERMINAL_STALL"
