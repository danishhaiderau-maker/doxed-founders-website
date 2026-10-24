"""HM #869 R2d: DELETE_UNCERTAIN is persisted and re-verified at boot (B1), only present ids are tombstoned (N1),
salvage stops at ']' and alerts on a truncated final id (N2), quarantine salvages older .corrupt.* files (N3)."""
import json
import logging
import pathlib

import bot
import trade_delete_by_id as m
from test_delete_by_id_reload import _ns
from test_delete_by_id_route_r2c import _post, env  # noqa: F401  (fixture)
from test_trade_delete_by_id import ALL, DEL, _ids, _mk


def _half(monkeypatch):
    real = m.delete_trades_by_id

    def half(path, ids, sha, dry_run=True, **k):
        if dry_run:
            return real(path, ids, sha, dry_run=True, **k)
        real(path, ids, sha, dry_run=False, **k)
        raise m.DeleteRefused("INVARIANT_FAILED_RESTORED", {})
    monkeypatch.setattr(m, "delete_trades_by_id", half)


def test_uncertain_marker_persisted_and_further_deletes_409(env, monkeypatch):
    p, tomb = env
    _half(monkeypatch)
    assert _post().status_code == 500
    marker = json.loads(pathlib.Path(str(tomb) + ".uncertain").read_text())
    assert marker["schema"] == "delete_uncertain_v1" and marker["ids"] == sorted(DEL)
    monkeypatch.setattr(m, "delete_trades_by_id", m.delete_trades_by_id.__wrapped__
                        if hasattr(m.delete_trades_by_id, "__wrapped__") else m.delete_trades_by_id)
    r = _post(["t-2"])
    assert r.status_code == 409 and r.get_json()["refused"] == "DELETE_UNCERTAIN" and "t-2" in _ids(p)
    bot._DELETED_TRADE_TOMBSTONE["status"] = "OK"  # memory lost (restart): the persisted marker still refuses
    assert _post(["t-2"]).get_json()["refused"] == "DELETE_UNCERTAIN"


def test_restart_after_uncertain_rows_back_in_csv_stays_red(tmp_path):
    p = _mk(tmp_path, ALL)  # restore DID land: the ids are rows again
    ns = _ns(p)
    pathlib.Path(ns["DELETED_TRADE_IDS_FILE"]).write_text(json.dumps(
        {"schema": "deleted_trade_ids_v1", "updated_at": "x", "ids": DEL, "list_sha256s": []}))
    ns["_mark_delete_uncertain"](DEL, "test")
    ns2 = _ns(p)  # fresh process
    ns2["DELETED_TRADE_IDS_FILE"] = ns["DELETED_TRADE_IDS_FILE"]
    ns2["load_deleted_trade_ids"]()
    assert ns2["_DELETED_TRADE_TOMBSTONE"]["status"] == "OK"
    assert ns2["reverify_delete_uncertain"](str(p)) == "UNCERTAIN"
    t = ns2["_DELETED_TRADE_TOMBSTONE"]
    assert t["status"] == "DELETE_UNCERTAIN" and "3 of 3" in t["error"] and t["uncertain_ids"] == sorted(DEL)
    assert ns2["_deleted_trade_health"]()["tombstone_uncertain_ids"] == 3
    assert pathlib.Path(ns["DELETED_TRADE_IDS_FILE"] + ".uncertain").exists()


def test_restart_after_uncertain_unambiguous_delete_auto_clears(tmp_path):
    p = _mk(tmp_path, [i for i in ALL if i not in DEL])
    ns = _ns(p)
    pathlib.Path(ns["DELETED_TRADE_IDS_FILE"]).write_text(json.dumps(
        {"schema": "deleted_trade_ids_v1", "updated_at": "x", "ids": DEL, "list_sha256s": []}))
    ns["_mark_delete_uncertain"](DEL, "test")
    ns2 = _ns(p)
    ns2["DELETED_TRADE_IDS_FILE"] = ns["DELETED_TRADE_IDS_FILE"]
    ns2["load_deleted_trade_ids"]()
    assert ns2["reverify_delete_uncertain"](str(p)) == "CLEARED"
    assert ns2["_DELETED_TRADE_TOMBSTONE"]["status"] == "OK"
    d = pathlib.Path(ns["DELETED_TRADE_IDS_FILE"]).parent
    assert not (d / "deleted_trade_ids.json.uncertain").exists()
    assert list(d.glob("deleted_trade_ids.json.uncertain.resolved.*"))  # kept, never deleted


def test_restart_with_unreadable_marker_stays_red(tmp_path):
    p = _mk(tmp_path, ALL)
    ns = _ns(p)
    pathlib.Path(ns["DELETED_TRADE_IDS_FILE"] + ".uncertain").write_text('{"ids": ["t-1"')
    assert ns["reverify_delete_uncertain"](str(p)) == "UNCERTAIN"
    assert ns["_DELETED_TRADE_TOMBSTONE"]["status"] == "DELETE_UNCERTAIN"


def test_boot_calls_reverify_after_tombstone_load():
    src = pathlib.Path(bot.__file__).read_text()
    i = src.index("_n_tomb = load_deleted_trade_ids()")
    assert i < src.index("reverify_delete_uncertain(CSV_TRADES)", i) < src.index("replay_csv_write_fallback()", i)


def test_only_present_ids_are_tombstoned(env, monkeypatch):
    p, tomb = env
    real = m.delete_trades_by_id
    seen = []

    def spy(path, ids, sha, dry_run=True, **k):
        if dry_run:  # validation passes even though t-missing is absent (e.g. it vanished between reads)
            return {"dry_run": True}
        seen.append(set(json.load(open(tomb))["ids"]))
        return real(path, [i for i in ids if i in DEL], m.list_sha256(DEL), dry_run=False, **k)
    monkeypatch.setattr(m, "delete_trades_by_id", spy)
    _post(DEL + ["t-missing"])
    assert seen == [set(DEL)] and "t-missing" not in bot._DELETED_TRADE_IDS


def test_salvage_stops_at_closing_bracket(tmp_path):
    ns = _ns(_mk(tmp_path, ALL))
    f = tmp_path / "c.json"
    f.write_text('{"schema": "deleted_trade_ids_v1", "ids": ["t-1", "t-4"], "list_sha256s": ["abc123"], '
                 '"updated_at": "2026-10-25T00:00:00Z"')
    assert ns["_salvage_tombstone_ids"](str(f)) == {"t-1", "t-4"}


def test_salvage_truncated_final_id_alerts(tmp_path, caplog):
    ns = _ns(_mk(tmp_path, ALL))
    f = tmp_path / "c.json"
    f.write_text('{"schema": "deleted_trade_ids_v1", "ids": ["t-1", "t-4", "t-7')
    with caplog.at_level(logging.ERROR):
        assert ns["_salvage_tombstone_ids"](str(f)) == {"t-1", "t-4"}
    assert ns["_DELETED_TRADE_TOMBSTONE"]["salvage_truncated_ids"] == 1
    assert any("[ALERT]" in r.message and "truncated final id" in r.message for r in caplog.records)
    f.write_text('{"schema": "deleted_trade_ids_v1", "ids": ["t-1", ')  # clean cut between ids: no alert
    ns["_salvage_tombstone_ids"](str(f))
    assert ns["_DELETED_TRADE_TOMBSTONE"]["salvage_truncated_ids"] == 1


def test_quarantine_salvages_older_corrupt_files(tmp_path):
    ns = _ns(_mk(tmp_path, ALL))
    main = pathlib.Path(ns["DELETED_TRADE_IDS_FILE"])
    (tmp_path / (main.name + ".corrupt.20261024T000000.1")).write_text('{"ids": ["t-old"')
    main.write_text('{"schema": "deleted_trade_ids_v1", "ids": ["t-1"')
    ns["_quarantine_tombstone"]("test")
    assert {"t-old", "t-1"} <= ns["_DELETED_TRADE_IDS"] and ns["_DELETED_TRADE_TOMBSTONE"]["salvaged_ids"] == 2
