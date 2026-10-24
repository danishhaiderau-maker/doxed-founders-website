"""Flask test-client coverage for /api/admin/ledgers/delete-by-sha and delete-rollback-snapshot."""
import hashlib
import json

import bot
import ledger_delete_by_sha as lds
from research_v3_store import V3EvidenceStore

AUTH = {"X-Bot-Admin-Token": "ledger-del-test-token"}
URL = "/api/admin/ledgers/delete-by-sha"


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "ledger-del-test-token")
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_data_sync_runtime_root", lambda: str(tmp_path))
    s = V3EvidenceStore(tmp_path, epoch_id="epoch-route")
    for i in range(3):
        assert s.append("decision", {"record_id": f"decision-{i}", "i": i})["written"]
    line = s.ledger_path("decision").read_bytes().splitlines()[1]
    text = json.dumps({"stream": "v3/ledgers/decision.jsonl",
                       "row_sha256": hashlib.sha256(line).hexdigest()}) + "\n"
    return s, text, lds.list_sha256(text)


def test_route_auth_default_dry_run_refusal_and_real_run(tmp_path, monkeypatch):
    s, text, sha = _setup(tmp_path, monkeypatch)
    c = bot.app.test_client()
    env = {"REMOTE_ADDR": "198.51.100.9"}
    body = {"list_jsonl": text, "list_sha256": sha, "actor": "danish", "reason": "R17 cleanup test"}
    assert c.post(URL, json=body, environ_base=env).status_code == 401
    assert c.post(URL, json={**body, "actor": ""}, headers=AUTH, environ_base=env).status_code == 400
    assert c.post(URL, json={**body, "reason": ""}, headers=AUTH, environ_base=env).status_code == 400
    before = s.ledger_path("decision").read_bytes()
    r = c.post(URL, json=body, headers=AUTH, environ_base=env)  # dry_run omitted -> dry run
    assert r.status_code == 200 and r.get_json()["dry_run"] is True
    assert s.ledger_path("decision").read_bytes() == before
    r = c.post(URL, json={**body, "list_sha256": "0" * 64, "dry_run": False}, headers=AUTH, environ_base=env)
    assert r.status_code == 409 and r.get_json()["refused"] == "LIST_SHA_MISMATCH"
    assert "ledger-del-test-token" not in r.get_data(as_text=True)
    r = c.post(URL, json={**body, "dry_run": False}, headers=AUTH, environ_base=env)
    j = r.get_json()
    assert r.status_code == 200 and j["dry_run"] is False and j["files"]["decision"]["after_count_read_back"] == 2
    snap_url = "/api/admin/ledgers/delete-rollback-snapshot"
    sb = {"snapshot_path": j["snapshot_path"], "actor": "danish", "reason": "verified"}
    assert c.post(snap_url, json=sb, environ_base=env).status_code == 401
    assert c.post(snap_url, json={**sb, "actor": ""}, headers=AUTH, environ_base=env).status_code == 400
    assert c.post(snap_url, json={**sb, "reason": ""}, headers=AUTH, environ_base=env).status_code == 400
    r = c.post(snap_url, json=sb, headers=AUTH, environ_base=env)
    assert r.status_code == 200 and r.get_json()["exists_after"] is False


def test_boot_recover_noop_without_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "_data_sync_runtime_root", lambda: str(tmp_path))
    bot._ledger_delete_boot_recover()
