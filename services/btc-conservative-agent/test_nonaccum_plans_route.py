"""GET /api/monitor/nonaccum-plans: read-only, auth required, background build, never deletes."""
import json, os, time
import bot


def _client():
    return bot.app.test_client()


def test_route_is_get_only_and_unauthenticated_remote_is_refused(monkeypatch):
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "t" * 24)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    assert "/api/monitor/nonaccum-plans" in bot._MONITOR_TILE_PATHS
    r = _client().get("/api/monitor/nonaccum-plans", environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert r.status_code == 401
    methods = {m for rule in bot.app.url_map.iter_rules() if rule.rule == "/api/monitor/nonaccum-plans" for m in rule.methods}
    assert methods <= {"GET", "HEAD", "OPTIONS"}


def test_worker_caches_summary_with_sha_and_no_item_lists(tmp_path, monkeypatch):
    import subprocess
    doc = {"plan_sha256": "ab" * 32, "epoch_ts": 1.0, "plans": {"C1": {"count": 2, "bytes": 3, "items_sha256": "x"}},
           "reference_scan": {}, "elapsed_s": 0.1}

    class P:
        returncode, stdout, stderr = 0, json.dumps(doc), ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: P())
    bot._nonaccum_plans_worker(str(tmp_path), 1792263671.26)
    c = dict(bot._NONACCUM_PLANS_CACHE)
    assert c["state"] == "READY" and c["plan_sha256"] == "ab" * 32 and c["deletes_nothing"] is True
    assert c["plans"]["C1"]["count"] == 2


def test_worker_error_is_reported_not_raised(tmp_path, monkeypatch):
    import subprocess

    class P:
        returncode, stdout, stderr = 1, "", "boom"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: P())
    bot._nonaccum_plans_worker(str(tmp_path), 1.0)
    assert bot._NONACCUM_PLANS_CACHE["state"] == "ERROR"
