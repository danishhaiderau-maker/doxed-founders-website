"""Admin route for corrupt_evidence R21 — dry-run default + mismatch refusal."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import bot
import corrupt_evidence_r21 as ce

AUTH = {"X-Bot-Admin-Token": "ce-r21-test-token"}
URL = "/api/admin/corrupt-evidence-r21"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "ce-r21-test-token")
    monkeypatch.setattr(bot, "_data_sync_runtime_root", lambda: tmp_path)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    return tmp_path


def _aged(root: Path, name: str, age_sec: float, payload: bytes = b"x"):
    d = root / "corrupt_evidence_quarantine" / name
    d.mkdir(parents=True)
    f = d / "blob.bin"
    f.write_bytes(payload)
    past = time.time() - age_sec
    os.utime(d, (past, past))
    os.utime(f, (past, past))


def test_route_defaults_to_dry_run(env):
    _aged(env, "old", 3 * 86400)
    r = bot.app.test_client().post(
        URL, json={"actor": "hm-test", "reason": "plan"}, headers=AUTH,
        environ_base={"REMOTE_ADDR": "198.51.100.9"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["mode"] == "DRY_RUN" and body["execute"] is False
    assert body["count"] == 1 and body["plan_sha256"]


def test_route_confirm_refuses_bad_sha(env):
    _aged(env, "old", 3 * 86400)
    r = bot.app.test_client().post(
        URL,
        json={"actor": "hm-test", "reason": "delete", "dry_run": False, "plan_sha256": "0" * 64},
        headers=AUTH, environ_base={"REMOTE_ADDR": "198.51.100.9"},
    )
    assert r.status_code == 409
    assert r.get_json()["error"] == "PLAN_SHA_MISMATCH"
    assert (env / "corrupt_evidence_quarantine" / "old").is_dir()


def test_route_confirm_deletes_on_matching_sha(env):
    _aged(env, "old", 3 * 86400, b"abc")
    _aged(env, "fresh", 60, b"fresh")
    plan = ce.plan_expired(env, max_age_sec=ce.DEFAULT_MAX_AGE_SEC)
    r = bot.app.test_client().post(
        URL,
        json={
            "actor": "hm-test", "reason": "delete", "dry_run": False,
            "plan_sha256": plan["plan_sha256"], "max_age_sec": ce.DEFAULT_MAX_AGE_SEC,
        },
        headers=AUTH, environ_base={"REMOTE_ADDR": "198.51.100.9"},
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ok"] is True and body["deleted"] == ["old"]
    assert body["before_count"] == 2 and body["after_count"] == 1
    assert not (env / "corrupt_evidence_quarantine" / "old").exists()
    assert (env / "corrupt_evidence_quarantine" / "fresh").is_dir()
