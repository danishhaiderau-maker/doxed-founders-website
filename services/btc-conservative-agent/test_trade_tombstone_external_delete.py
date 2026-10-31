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
    monkeypatch.setattr(bot, "_csv_fallback_pending_targets", set())
    monkeypatch.setattr(bot, "open_positions", [])
    monkeypatch.setattr(bot, "pending_orders", [])
    monkeypatch.setitem(bot.state, "live_armed", False)
    set_relay(monkeypatch)
    return p, tomb


def set_relay(monkeypatch, armed=(), output=False, reachable=True):
    """The Fly-direct arm is retired; the relay account view is the live arm signal."""
    monkeypatch.setattr(bot, "_relay_armed_view", lambda: {
        "live_armed": bool(armed), "relay_armed_accounts": list(armed), "live_copy_output_enabled": output,
        "relay_reachable": reachable})


def r21_receipt(ids, path=None):
    """Caller-supplied R21 sha-listed delete receipt (one row sha per id, for CSV_TRADES)."""
    entries = [{"trade_id": i, "path": path or bot.CSV_TRADES, "row_sha256": hashlib.sha256(i.encode()).hexdigest()}
               for i in ids]
    return {"entries": entries, "receipt_sha256": bot.r21_receipt_sha256(entries)}


_NO = object()


def _post(ids=DEL, dry_run=False, token=AUTH, sha=None, receipt=_NO):
    body = {"ids": ids, "list_sha256": sha or m.list_sha256(ids), "dry_run": dry_run, "actor": "rk-test",
            "reason": "r21 episode", "r21_receipt": r21_receipt(ids) if receipt is _NO else receipt}
    if dry_run is None:
        body.pop("dry_run")
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
    body = {"ids": DEL, "dry_run": False, "actor": "rk-test", "reason": "x", "r21_receipt": r21_receipt(DEL)}  # no sha
    r = bot.app.test_client().post(URL, json=body, headers=AUTH)
    assert r.get_json()["refused"] == "LIST_SHA_MISMATCH" and not tomb.exists()
    ok = _post()
    assert ok.status_code == 200 and ok.get_json()["receipt"] == bot.TOMBSTONE_EXTERNAL_RECEIPTS_FILE
    rec = _receipts()
    assert [x["outcome"] for x in rec] == ["REFUSED", "REFUSED", "REFUSED", "PENDING", "APPLIED"]
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


# ---- HM #888 B1: live / fallback / position / order / R21-receipt refusals (each checked to change nothing) ----

def _assert_refused(p, tomb, code, **kw):
    r = _post(**kw)
    assert r.status_code == 409 and r.get_json()["refused"] == code, r.get_json()
    assert not tomb.exists() and len(bot.trades) == len(ALL)
    assert _receipts()[-1]["outcome"] == "REFUSED" and _receipts()[-1]["refused"] == code


def test_refuses_csv_fallback_pending(env):
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    bot._csv_fallback_pending_targets.add(bot.CSV_TRADES)
    _assert_refused(p, tomb, "CSV_FALLBACK_PENDING")
    _assert_refused(p, tomb, "CSV_FALLBACK_PENDING", dry_run=True)


def test_refuses_live_armed(env):
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    bot.state["live_armed"] = True
    _assert_refused(p, tomb, "LIVE_ARMED")


def test_refuses_open_position(env):
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    bot.open_positions.append({"trade_id": "open-1"})
    _assert_refused(p, tomb, "OPEN_POSITIONS")


def test_refuses_open_order(env):
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    bot.pending_orders.append({"order_id": "o-1"})
    _assert_refused(p, tomb, "OPEN_ORDERS")


def test_refuses_without_r21_receipt(env):
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    _assert_refused(p, tomb, "R21_RECEIPT_MISSING", receipt=None)
    bad = r21_receipt(DEL)
    bad["receipt_sha256"] = "0" * 64
    _assert_refused(p, tomb, "R21_RECEIPT_SHA_MISMATCH", receipt=bad)
    _assert_refused(p, tomb, "R21_RECEIPT_IDS_NOT_LISTED", receipt=r21_receipt(DEL[:-1]))
    _assert_refused(p, tomb, "R21_RECEIPT_IDS_NOT_LISTED", receipt=r21_receipt(DEL, path="/app/data/other.csv"))
    e = r21_receipt(DEL)["entries"]
    e[0]["row_sha256"] = "nothex"
    _assert_refused(p, tomb, "R21_RECEIPT_BAD_ENTRY", receipt={"entries": e, "receipt_sha256": bot.r21_receipt_sha256(e)})


# ---- HM #888 B2 survivors ----

def test_dry_run_is_the_default(env):  # kills S5 (default flipped to False)
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    r = _post(dry_run=None)
    assert r.status_code == 200 and r.get_json()["dry_run"] is True
    assert not tomb.exists() and len(bot.trades) == len(ALL) and _receipts()[-1]["outcome"] == "DRY_RUN"


def _fail_writes_on(monkeypatch, outcome):
    real = bot._write_tombstone_external_receipt

    def w(rec):
        if rec.get("outcome") == outcome:
            raise OSError("disk full")
        return real(rec)
    monkeypatch.setattr(bot, "_write_tombstone_external_receipt", w)


def test_final_receipt_failure_is_500_and_pending_receipt_stands(env, monkeypatch):  # kills S3
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    _fail_writes_on(monkeypatch, "APPLIED")
    r = _post()
    assert r.status_code == 500 and r.get_json()["error"] == "RECEIPT_WRITE_FAILED"
    rec = _receipts()
    assert rec[-1]["outcome"] == "PENDING" and rec[-1]["ids"] == DEL and rec[-1]["receipt_id"] == r.get_json()["receipt_id"]


def test_receipts_are_fsynced(env, monkeypatch):  # kills S4 (fsync removed)
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    synced = []
    real_fsync, real_write = bot.os.fsync, bot.os.write
    pending = {}
    monkeypatch.setattr(bot.os, "write", lambda fd, b: pending.__setitem__(fd, b) or real_write(fd, b))
    monkeypatch.setattr(bot.os, "fsync", 
                        lambda fd: (fd in pending and synced.append(json.loads(pending.pop(fd))["outcome"])) or real_fsync(fd))
    assert _post(sha="0" * 64).status_code == 409
    assert _post().status_code == 200
    assert synced == ["REFUSED", "PENDING", "APPLIED"] and not pending


# ---- HM #888 B3: PENDING receipt before apply ----

def test_pending_receipt_written_before_any_state_change(env, monkeypatch):
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    seen = []
    real = bot._tombstone_external_delete

    def apply(ids, sha):
        seen.append([x["outcome"] for x in _receipts()])
        assert not tomb.exists() and len(bot.trades) == len(ALL)
        return real(ids, sha)
    monkeypatch.setattr(bot, "_tombstone_external_delete", apply)
    assert _post().status_code == 200
    assert seen == [["PENDING"]]
    assert [x["outcome"] for x in _receipts()] == ["PENDING", "APPLIED"]
    assert _receipts()[0]["receipt_id"] == _receipts()[1]["receipt_id"]


def test_pending_receipt_failure_refuses_without_applying(env, monkeypatch):
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    _fail_writes_on(monkeypatch, "PENDING")
    r = _post()
    assert r.status_code == 500 and r.get_json()["error"] == "PENDING_RECEIPT_WRITE_FAILED"
    assert r.get_json()["applied"] is False
    assert not tomb.exists() and len(bot.trades) == len(ALL) and _receipts() == []


def test_refusal_receipt_failure_is_not_swallowed(env, monkeypatch):
    p, tomb = env
    _fail_writes_on(monkeypatch, "REFUSED")
    r = _post(sha="0" * 64)
    assert r.status_code == 500 and r.get_json()["error"] == "REFUSAL_RECEIPT_WRITE_FAILED"
    assert r.get_json()["refused"] == "LIST_SHA_MISMATCH" and not tomb.exists()


# ---- #888 R21 first pass: relay arm is the real live arm; PENDING always reaches a terminal outcome ----

def test_refuses_relay_armed(env, monkeypatch):
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    set_relay(monkeypatch, armed=["acct-1"])
    _assert_refused(p, tomb, "LIVE_ARMED")
    _assert_refused(p, tomb, "LIVE_ARMED", dry_run=True)


def test_refuses_live_copy_output_enabled(env, monkeypatch):
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    set_relay(monkeypatch, output=True)
    _assert_refused(p, tomb, "LIVE_ARMED")


def test_refuses_when_relay_arm_state_unknown(env, monkeypatch):  # fail closed
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    set_relay(monkeypatch, reachable=False)
    _assert_refused(p, tomb, "LIVE_STATE_UNKNOWN")


def test_live_state_read_does_not_take_state_lock_under_csv_lock(env, monkeypatch):
    p, tomb = env
    r21_row_sha_delete(p, DEL)

    class Boom:
        def __enter__(self):
            raise AssertionError("state_lock taken inside csv_lock")

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(bot, "state_lock", Boom())
    assert _post(dry_run=True).status_code == 200


@pytest.mark.parametrize("val", ["false", 0, None, "", "False", [], "no"])
def test_only_json_false_applies(env, val):  # any non-boolean-false value stays a dry run
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    body = {"ids": DEL, "list_sha256": m.list_sha256(DEL), "dry_run": val, "actor": "rk-test", "reason": "x",
            "r21_receipt": r21_receipt(DEL)}
    r = bot.app.test_client().post(URL, json=body, headers=AUTH)
    assert r.status_code == 200 and r.get_json()["dry_run"] is True
    assert not tomb.exists() and len(bot.trades) == len(ALL)


def test_apply_failure_after_pending_leaves_terminal_receipt(env, monkeypatch):
    p, tomb = env
    r21_row_sha_delete(p, DEL)

    def boom(ids, sha):
        raise RuntimeError("reload exploded")
    monkeypatch.setattr(bot, "_tombstone_external_delete", boom)
    r = _post()
    assert r.status_code == 500 and r.get_json()["error"] == "APPLY_FAILED" and r.get_json()["applied"] == "UNKNOWN"
    assert [x["outcome"] for x in _receipts()] == ["PENDING", "APPLY_FAILED"]
    assert _receipts()[0]["receipt_id"] == _receipts()[1]["receipt_id"]


def test_blockers_checked_before_pending_receipt_and_dry_run_never_applies(env):
    p, tomb = env
    r21_row_sha_delete(p, DEL)
    bot.open_positions.append({"trade_id": "open-1"})
    _assert_refused(p, tomb, "OPEN_POSITIONS")
    assert [x["outcome"] for x in _receipts()] == ["REFUSED"]  # never a PENDING for a refused request


def test_census_note_has_no_unverifiable_route_count():
    import json as _j
    from pathlib import Path
    root = Path(bot.__file__).resolve().parent
    note = _j.loads((root / "ci" / "route_census_fields.json").read_text())["note"]
    assert "913 -> 914" not in note and "#888" in note
    contract = _j.loads((root / "cleanup_characterization_contract.fixture").read_text())
    assert ["POST", "/api/admin/trades/tombstone-external-delete",
            "api_admin_trades_tombstone_external_delete"] in contract["routes"]
    # Historical #888 / #943 bumps stay in the note; #957 adds retained GET → exact 150.
    assert "routes 149 -> 150" in note
    assert "routes 152 -> 149" in note and "#943" in note
    assert "#957" in note and "/api/runtime_log/retained" in note
    assert len(contract["routes"]) == 150
    paths = {r[1] for r in contract["routes"]}
    assert "/api/monitor/shadow-exits" not in paths
    assert "/api/research/shadow_exits" not in paths
    assert "/api/shadow_exits/summary" not in paths
    assert "/api/runtime_log/retained" in paths
    assert ["GET", "/api/runtime_log/retained",
            "api_runtime_log_retained"] in contract["routes"]
