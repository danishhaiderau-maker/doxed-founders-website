"""System-health banner: shared sanitizer, Fly report routes, analyzer route."""
import json
import time
from datetime import datetime, timezone

import system_health_banner as shb

AUTH = {"X-Bot-Admin-Token": "health-test-token"}
REMOTE = {"REMOTE_ADDR": "198.51.100.7"}


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _report(verdict="RED", age=0):
    return {
        "schema": shb.SCHEMA,
        "generated_at": _iso(time.time() - age),
        "verdict": verdict,
        "counts": {"RED": 1, "GREEN": 24},
        "failing": [{
            "id": "ai.success", "status": "RED", "observed": "last AI success 14m ago",
            "threshold": "<12m", "hint": "DeepSeek timeouts", "runbook": "docs/x.md#ai-success",
            "secret_field": "must-not-leak",
        }],
        "open_alarms": [{"id": "ai.success", "since": _iso(time.time() - age)}],
    }


def test_sanitize_rejects_foreign_payloads_and_drops_unknown_fields():
    assert shb.sanitize_report({"verdict": "RED"}) is None
    assert shb.sanitize_report({"schema": shb.SCHEMA, "verdict": "PURPLE"}) is None
    clean = shb.sanitize_report(_report())
    assert clean["verdict"] == "RED"
    assert "secret_field" not in clean["failing"][0]
    big = _report()
    big["failing"][0]["observed"] = "x" * 10_000
    assert len(shb.sanitize_report(big)["failing"][0]["observed"]) <= 240


def test_staleness_downgrades_green_and_flags_missing():
    missing = shb.with_staleness(None)
    assert missing["stale"] and missing["verdict"] == "AMBER"
    old = shb.with_staleness(shb.sanitize_report(_report("GREEN", age=3600)))
    assert old["stale"] and old["verdict"] == "AMBER"
    assert old["failing"][-1]["id"] == "watcher.stale"
    fresh = shb.with_staleness(shb.sanitize_report(_report("GREEN", age=60)))
    assert not fresh["stale"] and fresh["verdict"] == "GREEN"


def test_inject_banner_only_touches_html_once():
    from flask import Flask

    app = Flask("t")
    with app.test_request_context():
        html = app.make_response(("<html><body><p>x</p></body></html>", 200, {"Content-Type": "text/html"}))
        out = shb.inject_banner(html)
        body = out.get_data(as_text=True)
        assert body.count(shb.BANNER_MARKER) == 1
        assert body.index(shb.BANNER_MARKER) < body.index("</body>")
        again = shb.inject_banner(out).get_data(as_text=True)
        assert again.count(shb.BANNER_MARKER) == 1
        js = app.make_response(('{"a":1}', 200, {"Content-Type": "application/json"}))
        assert shb.BANNER_MARKER not in shb.inject_banner(js).get_data(as_text=True)


def _bot(monkeypatch):
    import bot

    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "health-test-token")
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setitem(bot._SYSTEM_HEALTH_REPORT, "report", None)
    with bot.state_lock:
        monkeypatch.setitem(bot.state, "ws_last_tick", time.time())
        monkeypatch.setitem(bot.state, "execution_paused", False)
        monkeypatch.setitem(bot.state, "live_armed", False)
    return bot


def test_fly_report_post_requires_admin_and_valid_schema(monkeypatch):
    bot = _bot(monkeypatch)
    client = bot.app.test_client()
    assert client.post("/api/system-health/report", json=_report(), environ_base=REMOTE).status_code == 401
    bad = client.post("/api/system-health/report", json={"verdict": "RED"}, headers=AUTH, environ_base=REMOTE)
    assert bad.status_code == 400
    big = client.post("/api/system-health/report", data="x" * (shb.MAX_REPORT_BYTES + 1),
                      headers={**AUTH, "Content-Type": "application/json"}, environ_base=REMOTE)
    assert big.status_code == 413
    ok = client.post("/api/system-health/report", json=_report(), headers=AUTH, environ_base=REMOTE)
    assert ok.status_code == 200 and ok.get_json()["verdict"] == "RED"


def test_fly_view_public_is_redacted_and_admin_gets_hints(monkeypatch):
    bot = _bot(monkeypatch)
    client = bot.app.test_client()
    client.post("/api/system-health/report", json=_report(), headers=AUTH, environ_base=REMOTE)
    public = client.get("/api/system-health", environ_base=REMOTE).get_json()
    assert public["verdict"] == "RED" and public["stale"] is False
    assert "hint" not in public["failing"][0]
    admin = client.get("/api/system-health", headers=AUTH, environ_base=REMOTE).get_json()
    assert admin["failing"][0]["hint"] == "DeepSeek timeouts"


def test_fly_self_checks_escalate_even_without_laptop_report(monkeypatch):
    bot = _bot(monkeypatch)
    with bot.state_lock:
        monkeypatch.setitem(bot.state, "live_armed", True)
        monkeypatch.setitem(bot.state, "ws_last_tick", time.time() - 600)
    body = bot.app.test_client().get("/api/system-health", environ_base=REMOTE).get_json()
    assert body["verdict"] == "RED" and body["stale"] is True
    ids = {c["id"] for c in body["failing"]}
    assert {"fly.live_armed", "fly.ws_ticks", "watcher.stale"} <= ids


def test_analyzer_route_reads_laptop_report_and_bypasses_cache(monkeypatch, tmp_path):
    from research import research_dashboard as dashboard

    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "system-health-latest.json").write_text(json.dumps(_report("AMBER")), encoding="utf-8")
    monkeypatch.setenv("DOXXED_LAPTOP_CHAIN_STATE", str(tmp_path))
    client = dashboard.app.test_client()
    first = client.get("/api/system-health")
    assert first.status_code == 200 and first.get_json()["verdict"] == "AMBER"
    assert first.headers.get("X-Research-Cache") is None
    (tmp_path / "health" / "system-health-latest.json").write_text(json.dumps(_report("GREEN")), encoding="utf-8")
    assert client.get("/api/system-health").get_json()["verdict"] == "GREEN"
