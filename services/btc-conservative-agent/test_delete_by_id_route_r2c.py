"""HM #869 R2c (b): Flask test-client coverage for /api/admin/trades/delete-by-id tombstone ordering."""
import json

import pytest

import bot
import trade_delete_by_id as m
from test_trade_delete_by_id import ALL, DEL, _ids, _mk

AUTH = {"X-Bot-Admin-Token": "r2c-test-token"}
URL = "/api/admin/trades/delete-by-id"


@pytest.fixture
def env(tmp_path, monkeypatch):
    p = _mk(tmp_path, ALL)
    tomb = tmp_path / "deleted_trade_ids.json"
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "r2c-test-token")
    monkeypatch.setattr(bot, "CSV_TRADES", p)
    monkeypatch.setattr(bot, "DELETED_TRADE_IDS_FILE", str(tomb))
    monkeypatch.setattr(bot, "_DELETED_TRADE_IDS", set())
    monkeypatch.setattr(bot, "_UNTOMBSTONED", set())
    monkeypatch.setattr(bot, "_DELETED_TRADE_TOMBSTONE", {**bot._DELETED_TRADE_TOMBSTONE, "status": "OK", "error": None})
    monkeypatch.setattr(bot, "_csv_fallback_pending_targets", set())
    monkeypatch.setattr(bot, "load_session_trades_from_csv", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    return p, tomb


def _post(ids=DEL):
    body = {"ids": ids, "list_sha256": m.list_sha256(ids), "dry_run": False, "actor": "hm-test", "reason": "r2c"}
    return bot.app.test_client().post(URL, json=body, headers=AUTH, environ_base={"REMOTE_ADDR": "198.51.100.9"})


def test_corrupt_tombstone_returns_409_and_deletes_nothing(env):
    p, tomb = env
    bot._DELETED_TRADE_TOMBSTONE["status"] = "CORRUPT"
    r = _post()
    assert r.status_code == 409 and r.get_json()["refused"] == "TOMBSTONE_CORRUPT"
    assert _ids(p) == ALL and not tomb.exists()


def test_persist_failure_returns_500_with_no_rows_deleted(env, monkeypatch):
    p, tomb = env
    monkeypatch.setattr(bot, "DELETED_TRADE_IDS_FILE", str(tomb.parent / "no_dir" / "deleted_trade_ids.json"))
    r = _post()
    assert r.status_code == 500
    assert r.get_json()["error"] == "TOMBSTONE_PERSIST_FAILED" and r.get_json()["deleted"] is False
    assert _ids(p) == ALL and not bot._DELETED_TRADE_IDS
    assert bot._DELETED_TRADE_TOMBSTONE["status"] == "PERSIST_FAILED"


def test_tombstone_is_durable_before_the_csv_write_and_success_path(env, monkeypatch):
    p, tomb = env
    real = m.delete_trades_by_id
    seen = []

    def spy(*a, dry_run=True, **k):
        if not dry_run:
            seen.append((set(json.load(open(tomb))["ids"]), list(_ids(p))))
        return real(*a, dry_run=dry_run, **k)
    monkeypatch.setattr(m, "delete_trades_by_id", spy)
    r = _post()
    assert r.status_code == 200, r.get_json()
    assert seen == [(set(DEL), ALL)]  # tombstoned on disk while the CSV was still untouched
    assert _ids(p) == [t for t in ALL if t not in DEL]
    assert set(json.load(open(tomb))["ids"]) == set(DEL)


def test_confirmed_restore_untombstones_then_409(env, monkeypatch):
    p, tomb = env
    real = m.delete_trades_by_id

    def restored(*a, dry_run=True, **k):
        if dry_run:
            return real(*a, dry_run=True, **k)
        raise m.DeleteRefused("INVARIANT_FAILED_RESTORED", {})  # CSV left intact = restore confirmed
    monkeypatch.setattr(m, "delete_trades_by_id", restored)
    r = _post()
    assert r.status_code == 409 and r.get_json()["refused"] == "INVARIANT_FAILED_RESTORED"
    assert json.load(open(tomb))["ids"] == [] and not bot._DELETED_TRADE_IDS


def test_unconfirmed_restore_keeps_tombstone_alerts_500(env, monkeypatch):
    p, tomb = env
    real = m.delete_trades_by_id

    def half(path, ids, sha, dry_run=True, **k):
        if dry_run:
            return real(path, ids, sha, dry_run=True, **k)
        real(path, ids, sha, dry_run=False, **k)  # rows really gone ...
        raise m.DeleteRefused("INVARIANT_FAILED_RESTORED", {})  # ... but the restore did not land
    monkeypatch.setattr(m, "delete_trades_by_id", half)
    r = _post()
    assert r.status_code == 500 and r.get_json()["error"] == "RESTORE_UNCONFIRMED"
    assert set(json.load(open(tomb))["ids"]) == set(DEL) and bot._DELETED_TRADE_IDS == set(DEL)
    assert bot._DELETED_TRADE_TOMBSTONE["status"] == "DELETE_UNCERTAIN"


def test_exception_after_write_keeps_tombstone_500(env, monkeypatch):
    p, tomb = env
    real = m.delete_trades_by_id

    def boom(path, ids, sha, dry_run=True, **k):
        if dry_run:
            return real(path, ids, sha, dry_run=True, **k)
        real(path, ids, sha, dry_run=False, **k)
        raise OSError("fsync dir failed")
    monkeypatch.setattr(m, "delete_trades_by_id", boom)
    r = _post()
    assert r.status_code == 500 and r.get_json()["error"] == "DELETE_STATE_UNCERTAIN"
    assert set(json.load(open(tomb))["ids"]) == set(DEL)


def test_refused_before_write_tombstones_nothing(env):
    p, tomb = env
    r = _post(DEL + ["t-missing"])
    assert r.status_code == 409 and r.get_json()["refused"] == "IDS_NOT_PRESENT"
    assert not tomb.exists() and _ids(p) == ALL
