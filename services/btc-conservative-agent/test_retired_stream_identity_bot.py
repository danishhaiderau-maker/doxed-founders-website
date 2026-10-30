"""HM 25 Oct: boot-recover failure surfaces in system-health + alert routes; route works with stale retired pointer."""
import hashlib
import json
import os

import pytest

import bot
import retired_streams
from research_v3_store import V3EvidenceStore

AUTH = {"X-Bot-Admin-Token": "retired-test-token"}


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "_API_RATE_LOG", type(bot._API_RATE_LOG)(bot._API_RATE_LOG.default_factory))  # HM nit (c): no 429 in a full run
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "retired-test-token")
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_data_sync_runtime_root", lambda: str(tmp_path))
    s = V3EvidenceStore(tmp_path, epoch_id="epoch-route")
    for led in ("decision", "market_segment"):
        for i in range(3):
            assert s.append(led, {"record_id": f"{led}-{i}", "i": i})["written"]
    path = tmp_path / "v3" / "ledgers" / "market_segment.jsonl"
    st = os.stat(path)
    monkeypatch.setitem(retired_streams.RETIREMENT_FINGERPRINTS, "market_segment", {
        "rows": 3, "mtime": int(st.st_mtime), "size": st.st_size})
    p = tmp_path / "v3" / "receipts" / "emergency_record_idempotency_v1" / "market_segment" / "complete.json"
    row = json.loads(p.read_text()); row["identity"]["deployed_revision"] = "a724a214"; p.write_text(json.dumps(row))
    return s, path


def test_route_dry_run_works_with_stale_retired_pointer(tmp_path, monkeypatch):
    s, _ = _setup(tmp_path, monkeypatch)
    line = s.ledger_path("decision").read_bytes().splitlines()[1]
    text = json.dumps({"stream": "v3/ledgers/decision.jsonl", "row_sha256": hashlib.sha256(line).hexdigest()}) + "\n"
    import ledger_delete_by_sha as lds
    r = bot.app.test_client().post("/api/admin/ledgers/delete-by-sha", headers=AUTH,
                                   environ_base={"REMOTE_ADDR": "198.51.100.9"},
                                   json={"list_jsonl": text, "list_sha256": lds.list_sha256(text),
                                         "actor": "t", "reason": "t"})
    assert r.status_code == 200 and r.get_json()["dry_run"] is True


def test_boot_recover_contaminated_retired_stream_is_red(tmp_path, monkeypatch):
    """A grown retired file is an identity conflict (#917 soft-exclude reverted). Boot recover stays RED."""
    _, path = _setup(tmp_path, monkeypatch)
    (tmp_path / "v3" / ".ledger_delete_rollback").mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as fh:
        fh.write(b'{"record_id":"late"}\n')
    bot._ledger_delete_boot_recover()
    check = bot._ledger_delete_boot_recover_check()
    assert check["status"] == "RED" and "identity conflict" in check["observed"]
    with pytest.raises(ValueError, match="identity conflict"):
        V3EvidenceStore.open_read_only(tmp_path)
    assert any(c["id"] == "fly.ledger_delete_boot_recover" and c["status"] == "RED"
               for c in bot._system_health_fly_self_checks())


def test_boot_recover_hard_identity_conflict_still_red(tmp_path, monkeypatch):
    """Non-soft identity conflicts (e.g. epoch mismatch on retired pointer) still fail boot recover."""
    _setup(tmp_path, monkeypatch)
    (tmp_path / "v3" / ".ledger_delete_rollback").mkdir(parents=True, exist_ok=True)
    p = tmp_path / "v3" / "receipts" / "emergency_record_idempotency_v1" / "market_segment" / "complete.json"
    row = json.loads(p.read_text())
    row["identity"]["epoch_id"] = "epoch-other"
    p.write_text(json.dumps(row))
    bot._ledger_delete_boot_recover()
    check = bot._ledger_delete_boot_recover_check()
    assert check["status"] == "RED" and "identity conflict" in check["observed"]
    assert any(c["id"] == "fly.ledger_delete_boot_recover" and c["status"] == "RED"
               for c in bot._system_health_fly_self_checks())
    for hist in (bot._system_health_alert_history_full(),):
        assert hist["ledger_delete_boot_recover"]["status"] == "RED"
        assert any(a["id"] == "fly.ledger_delete_boot_recover" for a in hist["fly_alerts"])


def test_boot_recover_ok_is_green(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    bot._ledger_delete_boot_recover()
    assert bot._ledger_delete_boot_recover_check()["status"] == "GREEN"
