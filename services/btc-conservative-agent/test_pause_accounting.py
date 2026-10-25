"""Durable pause total seconds + attempt count across boots (Boss later item a)."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pause_accounting as pa


T0 = 1_790_000_000.0


def test_begin_keeps_clock_and_counts_retries():
    fields = {"manual_admin_pause": False, "pause_intent": None}
    first = pa.begin_or_retry(fields, owner="DEPLOY_MAINTENANCE", now=T0)
    assert first == {
        "started_at": T0, "attempt_count": 1, "owner": "DEPLOY_MAINTENANCE", "event": "begin",
    }
    fields["manual_admin_pause"] = True
    fields["pause_intent"] = "DEPLOY_MAINTENANCE"
    second = pa.begin_or_retry(fields, owner="DEPLOY_MAINTENANCE", now=T0 + 600)
    assert second["event"] == "retry"
    assert second["started_at"] == T0
    assert second["attempt_count"] == 2
    assert fields["pause_started_at"] == T0
    assert fields["pause_attempt_count"] == 2


def test_owner_change_starts_new_episode():
    fields = {
        "manual_admin_pause": True,
        "pause_intent": "DEPLOY_MAINTENANCE",
        "pause_started_at": T0,
        "pause_attempt_count": 4,
    }
    out = pa.begin_or_retry(fields, owner="OPERATOR", now=T0 + 900)
    assert out["event"] == "begin"
    assert out["started_at"] == T0 + 900
    assert out["attempt_count"] == 1


def test_boot_increments_attempt_and_seeds_legacy():
    fields = {
        "manual_admin_pause": True,
        "pause_intent": "DEPLOY_MAINTENANCE",
        "pause_started_at": T0,
        "pause_attempt_count": 1,
    }
    assert pa.note_boot(fields, now=T0 + 60)["attempt_count"] == 2
    legacy = {"manual_admin_pause": True, "pause_intent": "DEPLOY_MAINTENANCE"}
    seeded = pa.note_boot(legacy, now=T0 + 120)
    assert seeded["event"] == "seed_boot"
    assert legacy["pause_started_at"] == T0 + 120
    assert legacy["pause_attempt_count"] == 1
    assert pa.note_boot({"manual_admin_pause": False}, now=T0) is None


def test_clear_and_age_and_snapshot_survive_boot_clock():
    fields = {
        "pause_started_at": T0,
        "pause_attempt_count": 5,
        "manual_admin_pause": True,
        "pause_intent": "DEPLOY_MAINTENANCE",
    }
    assert pa.age_sec(fields, now=T0 + 3 * 3600 + 45) == 3 * 3600 + 45
    snap = pa.snapshot(fields, now=T0 + 3 * 3600 + 45, paused=True, owner="DEPLOY_MAINTENANCE")
    assert snap["schema"] == pa.SCHEMA
    assert snap["active"] is True
    assert snap["owner"] == "DEPLOY_MAINTENANCE"
    assert snap["started_at"] == pa._iso(T0)
    assert snap["total_pause_sec"] == 3 * 3600 + 45
    assert snap["attempt_count"] == 5
    # boot_at-style reset must not wipe durable age
    assert snap["total_pause_sec"] > 0
    pa.clear(fields)
    cleared = pa.snapshot(fields, now=T0 + 9999, paused=False, owner=None)
    assert cleared == {
        "schema": pa.SCHEMA,
        "active": False,
        "owner": None,
        "started_at": None,
        "total_pause_sec": 0,
        "attempt_count": 0,
    }


def test_snapshot_iso_started_at_format():
    fields = {"pause_started_at": T0, "pause_attempt_count": 1}
    snap = pa.snapshot(fields, now=T0 + 10, paused=True, owner="OPERATOR")
    assert snap["started_at"].endswith("Z")
    assert "T" in snap["started_at"]


def test_bot_wires_pause_accounting_into_status_health_and_config():
    """Mutation proof: status/health expose the block; config persists the clocks."""
    import ast
    from pathlib import Path

    src = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    assert "import pause_accounting" in src
    assert '"pause_started_at"' in src and '"pause_attempt_count"' in src
    assert "pause_accounting.begin_or_retry" in src
    assert "pause_accounting.clear" in src
    assert "pause_accounting.note_boot" in src
    assert '"pause_accounting": _pause_accounting_public(now)' in src
    # /health also publishes the block
    assert 'payload["pause_accounting"] = _pause_accounting_public(now)' in src
    # Durable age preferred over in-memory shed / boot_at
    assert "pause_accounting.age_sec(state, now=now)" in src

    # Persistent keys include the durable fields
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_persistent_config_keys":
            body = ast.get_source_segment(src, node)
            assert "pause_started_at" in body and "pause_attempt_count" in body
            break
    else:
        raise AssertionError("_persistent_config_keys missing")


def test_api_pause_resume_persists_and_exposes_accounting(monkeypatch):
    os.environ.setdefault("FORCE_PAPER_MODE", "1")
    os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
    os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")
    import bot

    # monkeypatch only — never assign bot.* lambdas (pollutes later allowlist tests:
    # test_runtime_revision KeyError ws_transport_ready; test_http_emergency_admission
    # missing _disarm_live_control.__wrapped__).
    config_dir = tempfile.mkdtemp(prefix="pause-acct-")
    monkeypatch.setattr(
        bot, "get_config_file", lambda: os.path.join(config_dir, "config.json"),
    )
    monkeypatch.setattr(
        bot,
        "_disarm_live_control",
        lambda reason="TEST": {"cancel": {"failed": [], "cancelled": []}, "exit_only": {}},
    )
    monkeypatch.setattr(bot, "PAUSE_FINALIZE_WAIT_SEC", 0.2)
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "test-admin-token-pause-acct")
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "pipeline_state_sync", lambda: None)
    # Resume readiness: pretend system is ready so clear path runs.
    monkeypatch.setattr(
        bot,
        "_recompute_system_readiness",
        lambda now=None: {
            "system_ready": True,
            "signal_generation_ready": True,
            "ws_transport_ready": True,
            "readiness_reasons": [],
        },
    )

    with bot.state_lock:
        for key in bot._persistent_config_keys() + ["_threshold_locked", "bootstrap_done"]:
            bot.state.setdefault(key, None)
        bot.state.update({
            "manual_admin_pause": False,
            "pause_intent": None,
            "pause_started_at": None,
            "pause_attempt_count": 0,
            "execution_paused": False,
            "execution_reason": "",
            "_pause_priority": 0,
            "live_armed": False,
            "bitfinex_live_enabled": False,
        })
    bot._PAUSE_ACCOUNTING_BOOT_NOTED["done"] = False
    bot._DEPLOY_MAINTENANCE_SHED["requested_at"] = 0.0

    auth = {"REMOTE_ADDR": "127.0.0.1", "HTTP_X_BOT_ADMIN_TOKEN": "test-admin-token-pause-acct"}
    with bot.app.test_client() as client:
        r1 = client.post("/api/pause", json={"owner": "DEPLOY_MAINTENANCE"}, environ_base=auth)
        body1 = r1.get_json() or {}
        assert r1.status_code in (200, 202), body1
        acct1 = body1["pause_accounting"]
        assert acct1["active"] is True
        assert acct1["owner"] == "DEPLOY_MAINTENANCE"
        assert acct1["attempt_count"] == 1
        assert acct1["started_at"]
        started = acct1["started_at"]

        # Re-assert (preserve-style retry) keeps the clock and bumps attempts.
        time.sleep(0.05)
        r2 = client.post("/api/pause", json={"owner": "DEPLOY_MAINTENANCE"}, environ_base=auth)
        body2 = r2.get_json() or {}
        assert body2["pause_accounting"]["started_at"] == started
        assert body2["pause_accounting"]["attempt_count"] == 2

        saved = json.loads(Path(bot.get_config_file()).read_text(encoding="utf-8"))
        assert saved["pause_started_at"]
        assert saved["pause_attempt_count"] == 2
        assert saved["manual_admin_pause"] is True

        # Boot note increments without resetting the clock.
        with bot.state_lock:
            noted = pa.note_boot(bot.state, now=time.time())
        assert noted["attempt_count"] == 3
        assert bot.state["pause_attempt_count"] == 3

        status = client.get("/api/status?fresh=1").get_json() or {}
        assert status["pause_accounting"]["attempt_count"] == 3
        assert status["pause_accounting"]["total_pause_sec"] >= 0
        assert status["pause_accounting"]["started_at"] == started

        health = client.get("/health").get_json() or {}
        assert health["pause_accounting"]["attempt_count"] == 3
        assert health["pause_accounting"]["owner"] == "DEPLOY_MAINTENANCE"

        resumed = client.post(
            "/api/resume",
            json={"clear_admin_manual_pause": True, "owner": "DEPLOY_MAINTENANCE"},
            environ_base=auth,
        ).get_json() or {}
        assert resumed.get("status") in ("resumed", "resume_blocked_by_safety_gate")
        if resumed.get("status") == "resumed":
            assert resumed["pause_accounting"]["active"] is False
            assert resumed["pause_accounting"]["attempt_count"] == 0
            saved2 = json.loads(Path(bot.get_config_file()).read_text(encoding="utf-8"))
            assert saved2.get("pause_started_at") in (None, 0, 0.0)
            assert int(saved2.get("pause_attempt_count") or 0) == 0
