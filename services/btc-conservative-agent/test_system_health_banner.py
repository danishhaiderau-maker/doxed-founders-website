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


def test_sanitize_keeps_fly_revision_observed_fields():
    """Boss 22:20 AEDT (3): deploy_run_id/conclusion/failed_steps/paper_resumed_by survive push."""
    raw = _report("AMBER")
    raw["failing"] = [{
        "id": "fly.revision", "status": "AMBER",
        "observed": "fly=e8303023949c master=ec0aafda42c6; deploy_run_id=40184559814 conclusion=failure",
        "threshold": "Fly tip's guarded deploy concluded success",
        "hint": "serving deploy failed",
        "runbook": "docs/SYSTEM_HEALTH_RUNBOOK.md#fly-revision",
        "observed_fields": {
            "deploy_run_id": 40184559814,
            "conclusion": "failure",
            "failed_steps": ["Complete receipt bootstrap inside exact-revision maintenance"],
            "paper_resumed_by": "guaranteed",
            "secret_nested": {"x": 1},
        },
    }]
    clean = shb.sanitize_report(raw)
    fields = clean["failing"][0]["observed_fields"]
    assert fields["deploy_run_id"] == 40184559814
    assert fields["conclusion"] == "failure"
    assert fields["failed_steps"] == [
        "Complete receipt bootstrap inside exact-revision maintenance"
    ]
    assert fields["paper_resumed_by"] == "guaranteed"
    assert "secret_nested" not in fields


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
    bot._DEPLOY_MAINTENANCE_SHED["requested_at"] = 0.0
    with bot.state_lock:
        monkeypatch.setitem(bot.state, "ws_last_tick", time.time())
        monkeypatch.setitem(bot.state, "execution_paused", False)
        monkeypatch.setitem(bot.state, "live_armed", False)
        monkeypatch.setitem(bot.state, "manual_admin_pause", False)
        monkeypatch.setitem(bot.state, "pause_intent", None)
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


def test_fly_paused_self_check_turns_red_after_45_minutes(monkeypatch):
    """HM 25 Oct: Fly self-check fly.paused stayed AMBER forever while laptop went RED."""
    import runtime_uptime as ru

    bot = _bot(monkeypatch)
    now = time.time()
    with bot.state_lock:
        monkeypatch.setitem(bot.state, "execution_paused", True)
        monkeypatch.setitem(bot.state, "manual_admin_pause", True)
        monkeypatch.setitem(bot.state, "pause_intent", "DEPLOY_MAINTENANCE")
        monkeypatch.setitem(bot.state, "ws_last_tick", now)
    bot._DEPLOY_MAINTENANCE_SHED["requested_at"] = now - 10 * 60
    amber = {c["id"]: c for c in bot._system_health_fly_self_checks(now)}["fly.paused"]
    assert amber["status"] == "AMBER"
    bot._DEPLOY_MAINTENANCE_SHED["requested_at"] = now - (ru.DEPLOY_PAUSE_AMBER_MAX_SEC + 120)
    red = {c["id"]: c for c in bot._system_health_fly_self_checks(now)}["fly.paused"]
    assert red["status"] == "RED" and "DEPLOY_MAINTENANCE" in red["observed"]
    # Mutation proof: clearing the age clock must not leave a stuck deploy AMBER
    # when we force an old shed timestamp — the age gate is what flips RED.
    assert ru.fly_paused_monitor_status(
        paused=True, pause_owner="DEPLOY_MAINTENANCE",
        paused_for_sec=ru.DEPLOY_PAUSE_AMBER_MAX_SEC + 1,
    ) == "RED"
    assert ru.fly_paused_monitor_status(
        paused=True, pause_owner="DEPLOY_MAINTENANCE",
        paused_for_sec=ru.DEPLOY_PAUSE_AMBER_MAX_SEC,
    ) == "AMBER"


def test_merge_fly_self_checks_dedupes_fly_paused_worst_wins():
    """Boss later (b): system-health listed fly.paused twice (laptop RED + Fly AMBER)."""
    report = {
        "schema": shb.SCHEMA,
        "verdict": "RED",
        "stale": False,
        "counts": {"RED": 1, "AMBER": 0, "GREEN": 20},
        "failing": [{
            "id": "fly.paused", "status": "RED",
            "observed": "paused owner=DEPLOY_MAINTENANCE for 52m",
            "threshold": "unpaused; DEPLOY_MAINTENANCE <= 45m",
            "hint": "guarded deploy boundary",
            "runbook": "docs/SYSTEM_HEALTH_RUNBOOK.md#fly-paused",
        }],
        "open_alarms": [],
    }
    self_checks = [
        {"id": "fly.ws_ticks", "status": "GREEN", "observed": "3s", "threshold": "<=120s"},
        {"id": "fly.paused", "status": "AMBER",
         "observed": "paused owner=DEPLOY_MAINTENANCE for 2m",
         "threshold": "unpaused; DEPLOY_MAINTENANCE <= 45m"},
        {"id": "fly.live_armed", "status": "GREEN", "observed": "disarmed", "threshold": "disarmed"},
        {"id": "fly.only_on_fly", "status": "AMBER", "observed": "x", "threshold": "y"},
    ]
    out = shb.merge_fly_self_checks(report, self_checks)
    paused = [c for c in out["failing"] if c["id"] == "fly.paused"]
    assert len(paused) == 1 and paused[0]["status"] == "RED"
    assert paused[0]["hint"] == "guarded deploy boundary"  # laptop hint kept when Fly wins nothing
    assert [c["id"] for c in out["failing"]].count("fly.paused") == 1
    assert any(c["id"] == "fly.only_on_fly" and c["status"] == "AMBER" for c in out["failing"])
    # Laptop RED already counted; Fly AMBER must not add a second fly.paused or bump AMBER for it.
    # Fly-only AMBER (fly.only_on_fly) and Fly GREEN checks still adjust counts.
    assert out["counts"]["RED"] == 1
    assert out["counts"]["AMBER"] == 1  # fly.only_on_fly only
    assert out["counts"]["GREEN"] == 22  # 20 laptop + fly.ws_ticks + fly.live_armed
    assert out["verdict"] == "RED"


def test_merge_fly_self_checks_escalates_when_fly_is_worse():
    report = {
        "schema": shb.SCHEMA, "verdict": "AMBER", "stale": False,
        "counts": {"RED": 0, "AMBER": 1, "GREEN": 10},
        "failing": [{"id": "fly.paused", "status": "AMBER", "observed": "paused 10m",
                     "threshold": "unpaused"}],
        "open_alarms": [],
    }
    out = shb.merge_fly_self_checks(report, [
        {"id": "fly.paused", "status": "RED", "observed": "paused owner=DEPLOY_MAINTENANCE for 50m",
         "threshold": "unpaused; DEPLOY_MAINTENANCE <= 45m"},
    ])
    paused = [c for c in out["failing"] if c["id"] == "fly.paused"]
    assert len(paused) == 1 and paused[0]["status"] == "RED"
    assert "50m" in paused[0]["observed"]
    assert out["counts"]["AMBER"] == 0 and out["counts"]["RED"] == 1
    assert out["verdict"] == "RED"


def test_fly_view_lists_fly_paused_once_when_laptop_and_self_disagree(monkeypatch):
    bot = _bot(monkeypatch)
    now = time.time()
    laptop = {
        "schema": shb.SCHEMA,
        "generated_at": _iso(now),
        "verdict": "RED",
        "counts": {"RED": 1, "GREEN": 24},
        "failing": [{
            "id": "fly.paused", "status": "RED",
            "observed": "paused owner=DEPLOY_MAINTENANCE for 55m",
            "threshold": "unpaused; DEPLOY_MAINTENANCE <= 45m",
            "hint": "guarded deploy boundary",
            "runbook": "docs/SYSTEM_HEALTH_RUNBOOK.md#fly-paused",
        }],
        "open_alarms": [{"id": "fly.paused", "since": _iso(now - 55 * 60)}],
    }
    client = bot.app.test_client()
    assert client.post("/api/system-health/report", json=laptop, headers=AUTH, environ_base=REMOTE).status_code == 200
    with bot.state_lock:
        monkeypatch.setitem(bot.state, "execution_paused", True)
        monkeypatch.setitem(bot.state, "manual_admin_pause", True)
        monkeypatch.setitem(bot.state, "pause_intent", "DEPLOY_MAINTENANCE")
        monkeypatch.setitem(bot.state, "ws_last_tick", now)
    # Fresh reassert clock → Fly self-check would be AMBER alone; merge must keep one RED.
    bot._DEPLOY_MAINTENANCE_SHED["requested_at"] = now - 5 * 60
    body = client.get("/api/system-health", environ_base=REMOTE).get_json()
    paused = [c for c in body["failing"] if c["id"] == "fly.paused"]
    assert len(paused) == 1 and paused[0]["status"] == "RED"
    assert body["verdict"] == "RED"


def test_deploy_maintenance_shed_clock_survives_reassert(monkeypatch):
    bot = _bot(monkeypatch)
    monkeypatch.setattr(bot, "_recompute_system_readiness", lambda: {
        "system_ready": True, "ws_transport_ready": True, "readiness_reasons": [],
    })
    monkeypatch.setattr(bot, "_resume_active_reset_receipt_exists", lambda: False)
    client = bot.app.test_client()
    first = client.post("/api/pause", json={"owner": "DEPLOY_MAINTENANCE"},
                        headers=AUTH, environ_base=REMOTE)
    assert first.status_code == 200
    started = float(bot._DEPLOY_MAINTENANCE_SHED["requested_at"])
    assert started > 0
    time.sleep(0.05)
    again = client.post("/api/pause", json={"owner": "DEPLOY_MAINTENANCE"},
                        headers=AUTH, environ_base=REMOTE)
    assert again.status_code == 200
    assert float(bot._DEPLOY_MAINTENANCE_SHED["requested_at"]) == started
    resumed = client.post(
        "/api/resume",
        json={"clear_admin_manual_pause": True, "owner": "DEPLOY_MAINTENANCE"},
        headers=AUTH, environ_base=REMOTE,
    )
    assert resumed.status_code == 200, resumed.get_json()
    assert float(bot._DEPLOY_MAINTENANCE_SHED["requested_at"]) == 0.0


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
