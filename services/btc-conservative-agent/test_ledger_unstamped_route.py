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
    # HM R2: every non-allowlisted stream must show a write since the cutoff, else it is RED.
    for name in mon.LEDGERS:
        if name != "decision" and name not in mon.RETIRED_STREAM_ALLOWLIST:
            (d / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows if r.get("record_id") == "ok"))


def test_route_red_when_unstamped_since_and_green_otherwise(tmp_path, monkeypatch):
    import ledger_write_stamp as lws
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "monitor_integrity_authorized", lambda: True)
    monkeypatch.setattr(bot, "_data_sync_runtime_root", lambda: tmp_path)

    def st(now):
        return lws.build_write_stamp(data_epoch_id=lws.EXPECTED_DATA_EPOCH_ID, floor_ts=1791000000.0, now=now,
                                     realism={"fill_model": "X"})
    # M1: the legacy "ts" key is never a boundary; only write_stamp.written_at_ts is.
    _write(tmp_path, [{"record_id": "old", "write_stamp": st(1792000000.0)},
                      {"record_id": "new", "ts": 1792000500.0},
                      {"record_id": "ok", "write_stamp": st(1792002000.0)}])
    body = _client().get(PATH + "?since=1792001000").get_json()
    assert body["check"] == mon.CHECK_ID and body["status"] == "RED" and body["alert"] is True
    assert body["unstamped_rows_since"] == 1 and body["boundary_key"] == "write_stamp.written_at_ts"
    _write(tmp_path, [{"record_id": "old", "write_stamp": st(1792000000.0)},
                      {"record_id": "ok", "write_stamp": st(1792002000.0)}])
    body = _client().get(PATH + "?since=1792001000").get_json()
    # retired market_segment is IDLE with its reason, never GREEN (RK/HM R2)
    assert body["status"] == "IDLE" and body["unstamped_rows_since"] == 0
    assert body["idle_ledgers"] == {"market_segment": "MARKET_SEGMENTS_RETIRED_R17"}


def test_route_bad_since_is_400(monkeypatch):
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "monitor_integrity_authorized", lambda: True)
    assert _client().get(PATH + "?since=abc").status_code == 400
