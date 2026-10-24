"""R16: GET /api/monitor/ledger-unstamped is in the route census, read-only, auth required, RED when count > 0."""
import json

import bot
import ledger_stamp_monitor as mon

PATH = "/api/monitor/ledger-unstamped"


def _client():
    return bot.app.test_client()


def test_route_in_census_get_only_and_unauthenticated_remote_refused(monkeypatch):
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "t" * 24)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    assert PATH in bot._MONITOR_TILE_PATHS
    methods = {m for rule in bot.app.url_map.iter_rules() if rule.rule == PATH for m in rule.methods}
    assert methods and methods <= {"GET", "HEAD", "OPTIONS"}
    r = _client().get(PATH, environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert r.status_code == 401


def _write(tmp_path, rows):
    d = tmp_path / "v3" / "ledgers"
    d.mkdir(parents=True, exist_ok=True)
    (d / "decision.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_route_red_when_unstamped_since_and_green_otherwise(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "monitor_integrity_authorized", lambda: True)
    monkeypatch.setattr(bot, "_data_sync_runtime_root", lambda: tmp_path)
    _write(tmp_path, [{"record_id": "old", "ts": 1792000000.0}, {"record_id": "new", "ts": 1792002000.0}])
    body = _client().get(PATH + "?since=1792001000").get_json()
    assert body["check"] == mon.CHECK_ID and body["status"] == "RED" and body["alert"] is True
    assert body["unstamped_rows_since"] == 1  # the legacy row before since is not counted
    body = _client().get(PATH + "?since=1792003000").get_json()
    assert body["status"] == "GREEN" and body["unstamped_rows_since"] == 0


def test_route_bad_since_is_400(monkeypatch):
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "monitor_integrity_authorized", lambda: True)
    assert _client().get(PATH + "?since=abc").status_code == 400
