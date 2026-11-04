"""LAPTOP_MONITOR_READ_TOKEN: GET-only bot.py monitor routes; never non-GET / admin."""
from __future__ import annotations

from pathlib import Path

import monitor_api
import monitor_integrity as mi
import pytest

ADMIN = "health-test-token"
MONITOR = "monitor-read-token-for-tests-0123456789"
LAPTOP = "laptop-monitor-read-token-for-tests-ABCDEF"
WRONG = "random-wrong-token-zzzzzzzzzzzzzzzzzz"
REMOTE = {"REMOTE_ADDR": "198.51.100.7"}
HERE = Path(__file__).resolve().parent


@pytest.fixture
def bot(monkeypatch):
    import bot as bot_module

    monkeypatch.setattr(bot_module, "_BOT_ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr(bot_module, "_MONITOR_READ_TOKEN", MONITOR)
    monkeypatch.setattr(bot_module, "_LAPTOP_MONITOR_READ_TOKEN", LAPTOP)
    monkeypatch.setattr(bot_module, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot_module, "BOT_INSTANCE_ID", "laptop-monitor-test-boot")
    monkeypatch.setattr(bot_module, "_API_RATE_MAX_PER_IP", 10_000)
    monkeypatch.setattr(bot_module, "_TAPE_CONTINUITY", mi.TapeContinuityTracker())
    monkeypatch.setattr(bot_module, "_AI_CALL_WINDOW", mi.AiCallWindow())
    monkeypatch.setattr(bot_module, "_FILL_MARKOUT_AGG", mi.FillMarkoutAggregator())
    monkeypatch.setitem(bot_module._MONITOR_DIGEST, "digest", None)
    monkeypatch.setitem(bot_module._MONITOR_DIGEST, "received_ts", None)
    return bot_module


def test_laptop_token_is_distinct_env_not_monitor_read_token():
    """Mutation: renaming LAPTOP getenv to MONITOR_READ_TOKEN must fail this assert."""
    src = (HERE / "bot.py").read_text(encoding="utf-8")
    assert 'os.getenv("LAPTOP_MONITOR_READ_TOKEN")' in src
    assert "_LAPTOP_MONITOR_READ_TOKEN = monitor_api.configured_laptop_monitor_token(" in src
    assert "configured_laptop_monitor_token" in (HERE / "monitor_api.py").read_text(encoding="utf-8")
    # Must not collapse into MONITOR_READ_TOKEN.
    assert 'os.getenv("MONITOR_READ_TOKEN")' in src
    assert src.count("_LAPTOP_MONITOR_READ_TOKEN") >= 3


def test_configured_laptop_token_refuses_empty_or_equal_monitor():
    """RK 12:27 (a): empty / equal MONITOR_READ_TOKEN → not authorisable."""
    assert monitor_api.configured_laptop_monitor_token("", ADMIN, MONITOR) == ""
    assert monitor_api.configured_laptop_monitor_token("short", ADMIN, MONITOR) == ""
    assert monitor_api.configured_laptop_monitor_token(None, ADMIN, MONITOR) == ""
    assert monitor_api.configured_laptop_monitor_token(MONITOR, ADMIN, MONITOR) == ""
    assert monitor_api.configured_laptop_monitor_token(f"  {MONITOR}  ", ADMIN, MONITOR) == ""
    assert monitor_api.configured_laptop_monitor_token(LAPTOP, ADMIN, MONITOR) == LAPTOP
    assert monitor_api.configured_laptop_monitor_token(LAPTOP, ADMIN, "") == LAPTOP
    assert monitor_api.configured_laptop_monitor_token(LAPTOP, ADMIN, None) == LAPTOP
    # Still refuse admin-equal (inherited from configured_monitor_token).
    assert monitor_api.configured_laptop_monitor_token(ADMIN + "-padded-to-24chars!!", ADMIN + "-padded-to-24chars!!", MONITOR) == ""


def test_laptop_token_accepted_on_get_monitor_routes(bot):
    client = bot.app.test_client()
    headers = {"Authorization": f"Bearer {LAPTOP}"}
    for path in ("/api/monitor/integrity", "/api/monitor/digest", "/api/monitor/tiles/specs"):
        ok = client.get(path, headers=headers, environ_base=REMOTE)
        assert ok.status_code == 200, path
        assert ok.headers.get("Cache-Control") == "no-store"


def test_random_wrong_token_refused_on_get_monitor_routes(bot):
    """RK 12:27 (b): a random wrong bearer is refused on GET monitor routes."""
    client = bot.app.test_client()
    headers = {"Authorization": f"Bearer {WRONG}"}
    for path in ("/api/monitor/integrity", "/api/monitor/digest", "/api/monitor/tiles/specs"):
        resp = client.get(path, headers=headers, environ_base=REMOTE)
        assert resp.status_code == 401, path


def test_laptop_token_rejected_on_any_non_get(bot):
    """GET-only: every non-GET with the laptop bearer must fail closed (401/405)."""
    client = bot.app.test_client()
    headers = {"Authorization": f"Bearer {LAPTOP}"}
    # Monitor paths that exist as GET — POST must not be authorized by laptop token.
    for path in (
        "/api/monitor/integrity",
        "/api/monitor/digest",
        "/api/monitor/tiles/specs",
        "/api/monitor/tiles/trades",
    ):
        post = client.post(path, headers=headers, json={}, environ_base=REMOTE)
        assert post.status_code in (401, 405), (path, post.status_code)
        put = client.put(path, headers=headers, json={}, environ_base=REMOTE)
        assert put.status_code in (401, 405), (path, put.status_code)
        delete = client.delete(path, headers=headers, environ_base=REMOTE)
        assert delete.status_code in (401, 405), (path, delete.status_code)
    # Mutation / admin surfaces must never accept the laptop bearer.
    assert client.post("/api/pause", headers=headers, json={}, environ_base=REMOTE).status_code == 401
    assert client.get("/debug_state", headers=headers, environ_base=REMOTE).status_code == 401
    assert client.get("/api/exchange_exposure_audit", headers=headers, environ_base=REMOTE).status_code == 401


def test_laptop_token_in_x_bot_admin_header_refused_on_admin_and_post(bot):
    """RK 12:27 (b): laptop secret in X-Bot-Admin-Token never grants admin or POST."""
    client = bot.app.test_client()
    headers = {"X-Bot-Admin-Token": LAPTOP}
    # Admin-gated GET
    assert client.get("/debug_state", headers=headers, environ_base=REMOTE).status_code == 401
    assert client.get("/api/exchange_exposure_audit", headers=headers, environ_base=REMOTE).status_code == 401
    # POST mutation
    assert client.post("/api/pause", headers=headers, json={}, environ_base=REMOTE).status_code == 401
    assert client.post("/api/system-health/report", headers=headers, json={"schema": "x"},
                       environ_base=REMOTE).status_code == 401
    # Monitor GET must not accept admin-header laptop token either (Bearer only).
    assert client.get("/api/monitor/integrity", headers=headers, environ_base=REMOTE).status_code == 401
    assert client.get("/api/monitor/digest", headers=headers, environ_base=REMOTE).status_code == 401


def test_empty_or_equal_monitor_laptop_token_does_not_authorise(bot, monkeypatch):
    """RK 12:27 (a): authorise path refuses when configured laptop token is empty."""
    monkeypatch.setattr(bot, "_LAPTOP_MONITOR_READ_TOKEN", "")
    client = bot.app.test_client()
    assert client.get(
        "/api/monitor/integrity",
        headers={"Authorization": f"Bearer {LAPTOP}"},
        environ_base=REMOTE,
    ).status_code == 401
    # Equal-to-MONITOR case: configured helper returns "" so auth uses MONITOR path only.
    assert monitor_api.configured_laptop_monitor_token(MONITOR, ADMIN, MONITOR) == ""
    monkeypatch.setattr(bot, "_LAPTOP_MONITOR_READ_TOKEN",
                        monitor_api.configured_laptop_monitor_token(MONITOR, ADMIN, MONITOR))
    # Presenting the shared value as laptop Bearer: MONITOR path may still accept if
    # _MONITOR_READ_TOKEN is set — helper itself must stay False.
    with bot.app.test_request_context(
            "/api/monitor/integrity", method="GET",
            headers={"Authorization": f"Bearer {MONITOR}"}):
        assert bot._laptop_monitor_read_authorized() is False


def test_laptop_helper_refuses_non_get_even_when_token_matches(bot, monkeypatch):
    """Direct helper proof: method gate is independent of Flask route methods=."""
    with bot.app.test_request_context(
            "/api/monitor/integrity", method="POST",
            headers={"Authorization": f"Bearer {LAPTOP}"}):
        assert bot._laptop_monitor_read_authorized() is False
        assert bot.monitor_integrity_authorized() is False
    with bot.app.test_request_context(
            "/api/monitor/integrity", method="GET",
            headers={"Authorization": f"Bearer {LAPTOP}"}):
        assert bot._laptop_monitor_read_authorized() is True
        assert bot.monitor_integrity_authorized() is True
    # Wrong / missing token on GET.
    with bot.app.test_request_context(
            "/api/monitor/integrity", method="GET",
            headers={"Authorization": f"Bearer {MONITOR}"}):
        # MONITOR still authorized via _MONITOR_READ_TOKEN path; laptop helper alone is False.
        assert bot._laptop_monitor_read_authorized() is False
    with bot.app.test_request_context("/api/monitor/integrity", method="GET"):
        assert bot._laptop_monitor_read_authorized() is False
    monkeypatch.setattr(bot, "_LAPTOP_MONITOR_READ_TOKEN", "")
    with bot.app.test_request_context(
            "/api/monitor/integrity", method="GET",
            headers={"Authorization": f"Bearer {LAPTOP}"}):
        assert bot._laptop_monitor_read_authorized() is False


def test_laptop_token_does_not_replace_monitor_read_when_unset(bot, monkeypatch):
    """Unset laptop token: MONITOR_READ_TOKEN still works; laptop bearer alone fails."""
    monkeypatch.setattr(bot, "_LAPTOP_MONITOR_READ_TOKEN", "")
    client = bot.app.test_client()
    assert client.get(
        "/api/monitor/integrity",
        headers={"Authorization": f"Bearer {LAPTOP}"},
        environ_base=REMOTE,
    ).status_code == 401
    assert client.get(
        "/api/monitor/integrity",
        headers={"Authorization": f"Bearer {MONITOR}"},
        environ_base=REMOTE,
    ).status_code == 200


def test_digest_serves_when_only_laptop_token_configured(bot, monkeypatch):
    monkeypatch.setattr(bot, "_MONITOR_READ_TOKEN", "")
    client = bot.app.test_client()
    assert client.get("/api/monitor/digest", environ_base=REMOTE).status_code == 401
    assert client.get(
        "/api/monitor/digest",
        headers={"Authorization": f"Bearer {MONITOR}"},
        environ_base=REMOTE,
    ).status_code == 401
    ok = client.get(
        "/api/monitor/digest",
        headers={"Authorization": f"Bearer {LAPTOP}"},
        environ_base=REMOTE,
    )
    assert ok.status_code == 200
    assert ok.get_json()["schema"] == monitor_api.DIGEST_SCHEMA


def test_digest_404_when_both_monitor_tokens_unset(bot, monkeypatch):
    monkeypatch.setattr(bot, "_MONITOR_READ_TOKEN", "")
    monkeypatch.setattr(bot, "_LAPTOP_MONITOR_READ_TOKEN", "")
    client = bot.app.test_client()
    assert client.get(
        "/api/monitor/digest",
        headers={"Authorization": f"Bearer {LAPTOP}"},
        environ_base=REMOTE,
    ).status_code == 404


def test_configured_token_rejects_short_or_admin_equal():
    assert monitor_api.configured_monitor_token("short", ADMIN) == ""
    assert monitor_api.configured_monitor_token(LAPTOP, LAPTOP) == ""
    assert monitor_api.configured_monitor_token(LAPTOP, ADMIN) == LAPTOP
