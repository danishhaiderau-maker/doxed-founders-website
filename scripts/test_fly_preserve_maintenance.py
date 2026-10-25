"""Preserve must not re-assert DEPLOY_MAINTENANCE after a clear resume."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fly_preserve_maintenance as preserve  # noqa: E402


def _status(**overrides):
    status = {
        "source_git_rev": "b57014c0eb28abcdef",
        "force_paper_mode": True,
        "bitfinex_live_enabled": False,
        "live_armed": False,
        "execution_paused": True,
        "manual_admin_pause": True,
        "pause_owner": "DEPLOY_MAINTENANCE",
        "strategy_progress": {"open_positions": 0, "pending_orders": 0, "ok": True},
    }
    status.update(overrides)
    return status


ACTIVE = {
    "execution_paused": False,
    "manual_admin_pause": False,
    "pause_owner": None,
}


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


def test_preserve_skips_when_paper_already_safely_resumed():
    """Mutation proof: already-resumed safe paper must never see /api/pause."""
    request_json, posts = _client({
        "/api/status?fresh=1": [_status(**ACTIVE, strategy_progress={"open_positions": 0, "pending_orders": 0})],
    })
    result = preserve.preserve_maintenance(request_json, attempts=1, sleep=lambda _s: None)
    assert result["status"] == "SKIPPED_ALREADY_RESUMED"
    assert posts == []


def test_preserve_reasserts_when_still_held():
    paused = _status()
    after = _status()
    request_json, posts = _client({
        "/api/status?fresh=1": [paused, after],
        "/api/pause": [{"status": "paused", "execution_paused": True}],
    })
    result = preserve.preserve_maintenance(request_json, attempts=1, sleep=lambda _s: None)
    assert result["status"] == "PRESERVED"
    assert posts == [("/api/pause", {"owner": "DEPLOY_MAINTENANCE"})]


def test_preserve_refuses_unsafe_identity_without_pause_post():
    request_json, posts = _client({
        "/api/status?fresh=1": [_status(live_armed=True)],
    })
    with pytest.raises(RuntimeError, match="unsafe identity"):
        preserve.preserve_maintenance(request_json, attempts=1, sleep=lambda _s: None)
    assert posts == []


def test_already_resumed_safe_requires_flat_paper_only():
    assert preserve.already_resumed_safe(_status(**ACTIVE)) is True
    assert preserve.already_resumed_safe(_status()) is False
    assert preserve.already_resumed_safe(_status(**ACTIVE, live_armed=True)) is False
    assert preserve.already_resumed_safe(
        _status(**ACTIVE, strategy_progress={"open_positions": 1, "pending_orders": 0})
    ) is False


def test_workflow_wires_preserve_helper_not_blind_reassert():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/fly-bot-deploy.yml").read_text(
        encoding="utf-8"
    )
    step = "      - name: Best-effort preserve safe paper maintenance after failed guarded deploy"
    block_start = workflow.index(step)
    nxt = workflow.find("\n      - name: ", block_start + 10)
    block = workflow[block_start:] if nxt == -1 else workflow[block_start:nxt]
    assert "python scripts/fly_preserve_maintenance.py" in block
    # Blind inline /api/pause loop must not remain — that is the sticky re-assert.
    assert 'data=b\'{"owner": "DEPLOY_MAINTENANCE"}\'' not in block
    assert "/api/pause" not in block
    assert "fly_preserve_maintenance.py" in block
