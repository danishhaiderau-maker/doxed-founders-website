"""Cross-deploy recovery must also finish an append interrupted before COMMITTED."""
import json
import threading
import hashlib

import pytest

from paper_research_obligations import FIELD, enqueue_fill, replay_one
from relay_event_outbox import RelayEventOutbox
from research_v3_bridge import dual_write_paper_fill
import research_v3_store as module


@pytest.mark.parametrize("crash_phase", ["after_prepared", "before_committed"])
@pytest.mark.parametrize("mutation", [None, "economics", "original_identity"])
def test_fill_prepared_append_recovers_across_deploy(tmp_path, monkeypatch, crash_phase, mutation):
    old = {"evidence_provenance_schema": "v3_collection_provenance_v1", "source_revision": "a" * 40,
           "deployed_revision": "a" * 40, "tile_config_signature": "b" * 64, "config_signature": "c" * 64}
    new = {**old, "source_revision": "d" * 40, "deployed_revision": "d" * 40}
    monkeypatch.setattr(module, "_collection_provenance", lambda: old)
    monkeypatch.setattr(module, "storage_blocks_new_nonessential_research", lambda _: False)
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    target = {"paper_only": True, "live_armed": False, "git_rev": old["source_revision"]}
    enqueue_fill(target, {"trade_id": "p1", "qty": 2, "requested_qty": 3,
                          "partial_fill": True, "fill_price": 100}, {},
                 {"trade_id": "p1", "qty": 2, "entry": 100, "entry_ts": 1,
                  "event_episode_id": "episode-p1", "dir": "LONG"},
                 epoch_id="epoch-e1", provenance=old)
    outbox._atomic_write(outbox.decorate_lifecycle(target))
    publish = module.V3EvidenceStore._publish_record_receipt
    atomic = module.V3EvidenceStore._atomic_json_receipt
    def atomic_crash(self, path, material):
        if (material.get("ledger") == "execution" and material.get("state") == "COMMITTED"
                and crash_phase == "before_committed"):
            raise OSError("crash before COMMITTED receipt")
        return atomic(self, path, material)
    def crash(self, ledger, record_id, **kwargs):
        if ledger == "execution" and kwargs["state"] == "COMMITTED" and crash_phase == "before_committed":
            raise OSError("crash before COMMITTED receipt")
        result = publish(self, ledger, record_id, **kwargs)
        if ledger == "execution" and kwargs["state"] == "PREPARED" and crash_phase == "after_prepared":
            raise OSError("crash after PREPARED receipt")
        return result
    monkeypatch.setattr(module.V3EvidenceStore, "_publish_record_receipt", crash)
    monkeypatch.setattr(module.V3EvidenceStore, "_atomic_json_receipt", atomic_crash)
    params = dict(data_dir=str(tmp_path), epoch_id="epoch-e1", source_revision=old["source_revision"],
                  fill_writer=dual_write_paper_fill)
    with pytest.raises(OSError, match="crash"):
        replay_one(outbox, threading.RLock(), None, **params)
    store = module.V3EvidenceStore(tmp_path, epoch_id="epoch-e1")
    head = json.loads(store._append_head_path("execution").read_text())
    receipt = json.loads(store._record_receipt_path("execution", "execution:p1:primary-fill").read_text())
    assert head["state"] == receipt["state"] == "PREPARED"
    assert json.loads(outbox.path.read_text())[FIELD]
    execution_path = store.ledger_path("execution")
    original_bytes = execution_path.read_bytes() if execution_path.exists() else b""
    original_head = store._append_head_path("execution").read_bytes()
    if mutation:
        changed = json.loads(outbox.path.read_text())
        row = changed[FIELD][0]
        if mutation == "economics":
            row["position"]["entry"] = 101
        else:
            row["provenance"]["source_revision"] = "e" * 40
            row["provenance"]["deployed_revision"] = "e" * 40
        row["id"] = hashlib.sha256(json.dumps({k: v for k, v in row.items() if k != "id"}, sort_keys=True).encode()).hexdigest()
        outbox._atomic_write(changed)
    monkeypatch.setattr(module, "_collection_provenance", lambda: new)
    monkeypatch.setattr(module.V3EvidenceStore, "_publish_record_receipt", publish)
    monkeypatch.setattr(module.V3EvidenceStore, "_atomic_json_receipt", atomic)
    receipts = []
    def retry(*args, **kwargs):
        result = dual_write_paper_fill(*args, **kwargs)
        receipts.append(result)
        return result
    params.update(source_revision=new["source_revision"], fill_writer=retry)
    recovered = replay_one(outbox, threading.RLock(), None, **params)
    if mutation:
        assert not recovered
        assert receipts[0]["writes"][0]["blocked"] is True
        assert receipts[0]["writes"][0]["reason"] == "PAPER_RECOVERY_PREPARED_CONTENT_CONFLICT"
        assert (execution_path.read_bytes() if execution_path.exists() else b"") == original_bytes
        assert store._append_head_path("execution").read_bytes() == original_head
        assert json.loads(outbox.path.read_text())[FIELD]
        return
    assert recovered, json.dumps([r["writes"] for r in receipts])
    assert execution_path.read_bytes() == head["row_payload_utf8"].encode("utf-8")
    committed = json.loads(store._record_receipt_path("execution", "execution:p1:primary-fill").read_text())
    assert committed["state"] == "COMMITTED"
    assert committed["identity"] == head["identity"]
    for ledger in ("execution", "lifecycle"):
        rows = [json.loads(line) for line in store.ledger_path(ledger).read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["source_revision"] == old["source_revision"]
    assert json.loads(outbox.path.read_text())[FIELD] == []
