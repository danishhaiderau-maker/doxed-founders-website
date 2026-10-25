"""RK 25 Oct (a): R21-safe tombstone + memory drop for trade rows removed out-of-band (no restart)."""
import csv, hashlib, json

import pytest

import api_state_analytics as asa
import bot
import trade_delete_by_id as m
from test_trade_delete_by_id import ALL, DEL, _ids, _mk

AUTH = {"X-Bot-Admin-Token": "ext-test-token"}
URL = "/api/admin/trades/tombstone-external-delete"


def r21_row_sha_delete(path, ids):
    """Stand-in for the R21 episode tool: rewrite the CSV dropping rows whose sha matches, gate after=before-match."""
    with open(path, newline="") as f:
        lines = f.read().splitlines(keepends=True)
    header, rows = lines[0], lines[1:]
    want = {hashlib.sha256(l.encode()).hexdigest() for l in rows if l.split(",", 1)[0] in set(ids)}
    keep = [l for l in rows if hashlib.sha256(l.encode()).hexdigest() not in want]
    assert len(keep) == len(rows) - len(want)
    with open(path, "w", newline="") as f:
        f.write(header + "".join(keep))


@pytest.fixture
def env(tmp_path, monkeypatch):
    p = _mk(tmp_path, ALL)
    tomb = tmp_path / "deleted_trade_ids.json"
    with open(p, newline="") as f:
        mem = [dict(r) for r in csv.DictReader(f)]
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "ext-test-token")
    monkeypatch.setattr(bot, "CSV_TRADES", p)
    monkeypatch.setattr(bot, "DELETED_TRADE_IDS_FILE", str(tomb))
    monkeypatch.setattr(bot, "TOMBSTONE_EXTERNAL_RECEIPTS_FILE", str(tmp_path / "receipts.jsonl"))
    monkeypatch.setattr(bot, "_DELETED_TRADE_IDS", set())
    monkeypatch.setattr(bot, "_UNTOMBSTONED", set())
    monkeypatch.setattr(bot, "_DELETED_TRADE_TOMBSTONE", {**bot._DELETED_TRADE_TOMBSTONE, "status": "OK", "error": None})
    monkeypatch.setattr(bot, "trades", mem)
    monkeypatch.setattr(bot, "load_session_trades_from_csv", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    return p, tomb


def _post(ids=DEL, dry_run=False, token=AUTH, sha=None):
    body = {"ids": ids, "list_sha256": sha or m.list_sha256(ids), "dry_run": dry_run, "actor": "rk-test",
            "reason": "r21 episode"}
    return bot.app.test_client().post(URL, json=body, headers=token, environ_base={"REMOTE_ADDR": "198.51.100.9"})


def test_r21_tool_leaves_memory_ahead_of_disk_then_route_restores_parity(env):
    p, tomb = env
    r21_row_sha_delete(p, DEL)  # the R21 tool path: CSV rewritten, memory untouched
    assert _ids(p) == [t for t in ALL if t not in DEL]
    d = asa.id_diff(bot.trades, p, 1.0)
    assert d["memory_only_ids"] == sorted(DEL)  # the 25 Oct 339/341 defect, reproduced
    dry = _post(dry_run=True)
    assert dry.status_code == 200 and dry.get_json()["in_memory_matches"] == sorted(DEL) and not tomb.exists()
    r = _post()
    body = r.get_json()
    assert r.status_code == 200 and body["in_memory_dropped"] == 3
    assert body["after"]["memory_only_count"] == 0 and body["after"]["disk_only_count"] == 0
    assert sorted(t["trade_id"] for t in bot.trades) == _ids(p)  # memory == disk, no restart
    assert set(json.load(open(tomb))["ids"]) == set(DEL)
    bot.dynamic_csv_writer(p, {"trade_id": DEL[0]})  # re-save refused by the tombstone
    assert DEL[0] not in _ids(p)


def test_refuses_ids_still_on_disk_and_never_deletes(env):
    p, tomb = env
    r = _post()
    assert r.status_code == 409 and r.get_json()["refused"] == "IDS_STILL_ON_DISK"
    assert _ids(p) == ALL and not tomb.exists() and len(bot.trades) == len(ALL)


def test_auth_sha_and_corrupt_refusals(env):
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    assert _post(token={}).status_code == 401
    assert _post(sha="0" * 64).get_json()["refused"] == "LIST_SHA_MISMATCH"
    bot._DELETED_TRADE_TOMBSTONE["status"] = "CORRUPT"
    assert _post().get_json()["refused"] == "TOMBSTONE_CORRUPT"
    assert not tomb.exists() and len(bot.trades) == len(ALL)


def _receipts():
    try:
        return [json.loads(l) for l in open(bot.TOMBSTONE_EXTERNAL_RECEIPTS_FILE)]
    except FileNotFoundError:
        return []


def _assert_sha_guard_and_receipts(p, tomb):
    """Gate: only the id list + its caller-supplied sha is accepted; every decision leaves a receipt."""
    r21_row_sha_delete(p, DEL)
    bad = _post(sha="0" * 64)
    assert bad.status_code == 409 and bad.get_json()["refused"] == "LIST_SHA_MISMATCH"
    assert not tomb.exists() and len(bot.trades) == len(ALL)
    # sha of a different (superset) list must not authorise this list
    assert _post(sha=m.list_sha256(DEL + ["extra"])).get_json()["refused"] == "LIST_SHA_MISMATCH"
    body = {"ids": DEL, "dry_run": False, "actor": "rk-test", "reason": "x"}  # sha omitted
    r = bot.app.test_client().post(URL, json=body, headers=AUTH)
    assert r.get_json()["refused"] == "LIST_SHA_MISMATCH" and not tomb.exists()
    ok = _post()
    assert ok.status_code == 200 and ok.get_json()["receipt"] == bot.TOMBSTONE_EXTERNAL_RECEIPTS_FILE
    rec = _receipts()
    assert [x["outcome"] for x in rec] == ["REFUSED", "REFUSED", "REFUSED", "APPLIED"]
    assert all(x["refused"] == "LIST_SHA_MISMATCH" for x in rec[:3])
    last = rec[-1]
    assert last["ids"] == DEL and last["list_sha256_expected"] == last["list_sha256_computed"] == m.list_sha256(DEL)
    assert last["actor"] == "rk-test" and last["in_memory_dropped"] == 3 and last["after"]["memory_only_count"] == 0


def test_sha_guard_and_receipts(env):
    _assert_sha_guard_and_receipts(*env)


def test_mutation_proof_sha_guard_removed_is_caught(env, monkeypatch):
    """Mutant: sha check accepts anything (list_sha256 echoes the caller's value) -> the gate must fail."""
    real = m.list_sha256
    monkeypatch.setattr(m, "list_sha256", lambda ids: (bot.request.get_json() or {}).get("list_sha256") or real(ids))
    with pytest.raises(AssertionError):
        _assert_sha_guard_and_receipts(*env)


def test_mutation_proof_receipt_removed_is_caught(env, monkeypatch):
    """Mutant: receipt writer is a no-op -> the gate must fail."""
    monkeypatch.setattr(bot, "_write_tombstone_external_receipt", lambda rec: bot.TOMBSTONE_EXTERNAL_RECEIPTS_FILE)
    with pytest.raises(AssertionError):
        _assert_sha_guard_and_receipts(*env)
