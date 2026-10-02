import json
import hashlib
import threading
import pytest
import research_v3_bridge as bridge
import research_v3_store as store_module
from paper_research_obligations import FIELD, enqueue_close, replay_one
from relay_event_outbox import RelayEventOutbox


@pytest.mark.parametrize('duplicate', [False, True])
def test_invalid_complete_plan_rejected_before_any_ledger_append(tmp_path, monkeypatch, duplicate):
    rows = [{'ledger': 'execution', 'row': {'record_id': 'execution:p1:paper-close', 'event_id': 'p1'}}]
    if duplicate:
        rows = rows * 2 + [{'ledger': 'lifecycle', 'row': {'record_id': 'lifecycle:p1:paper-closed', 'event_id': 'p1'}}]
    plan = {'schema': 'paper_close_write_plan_v1', 'epoch_id': 'e1', 'event_id': 'p1', 'rows': rows, 'result': {}}
    plan['sha256'] = hashlib.sha256(json.dumps(plan, sort_keys=True, allow_nan=False).encode()).hexdigest()
    monkeypatch.setattr(store_module.V3EvidenceStore, 'append', lambda *a, **k: pytest.fail('invalid plan wrote ledger'))
    with pytest.raises(ValueError, match='INCOMPLETE_OR_DUPLICATE'):
        bridge.dual_write_paper_close({'trade_id': 'p1', 'event_episode_id': 'episode-p1'}, {}, {}, epoch_id='e1', data_dir=str(tmp_path), write_plan=plan)


@pytest.mark.parametrize('reserved_capacity', [False, True])
def test_close_retry_never_reloads_changed_tape_after_execution_commit(tmp_path, monkeypatch, reserved_capacity):
    provenance = {"evidence_provenance_schema": "v3_collection_provenance_v1",
                  "source_revision": "a" * 40, "deployed_revision": "a" * 40,
                  "tile_config_signature": "b" * 64, "config_signature": "c" * 64}
    monkeypatch.setattr(store_module, "_collection_provenance", lambda: provenance)
    monkeypatch.setattr(store_module, "storage_blocks_new_nonessential_research", lambda _: False)
    lock = threading.RLock()
    tape_reads = []
    def original_tape(*a, **k):
        assert not lock._is_owned()
        tape_reads.append(1)
        return [], {"row_count": 0, "reason": "ORIGINAL_TAPE_EMPTY"}
    monkeypatch.setattr(bridge, "_paper_market_segment", original_tape)
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    target = {"paper_only": True, "live_armed": False, "git_rev": provenance["source_revision"]}
    enqueue_close(target, {"trade_id": "p1", "entry_ts": 1, "entry": 100, "qty": 2,
                           "event_episode_id": "episode-p1", "dir": "LONG"}, {},
                  {"close_ts": 2, "exit": 101, "net_pnl_usd": 2}, epoch_id="epoch-e1", provenance=provenance)
    if reserved_capacity:
        from paper_evidence_capacity import initialize_legacy, validate
        target = initialize_legacy(target)
    outbox._atomic_write(outbox.decorate_lifecycle(target))
    append = store_module.V3EvidenceStore.append
    def crash(self, ledger, row, **kwargs):
        assert "write_plan" in json.loads(outbox.path.read_text())[FIELD][0]
        if ledger == "lifecycle":
            raise OSError("after execution")
        return append(self, ledger, row, **kwargs)
    monkeypatch.setattr(store_module.V3EvidenceStore, "append", crash)
    params = dict(data_dir=str(tmp_path), epoch_id="epoch-e1", source_revision=provenance["source_revision"])
    with pytest.raises(OSError):
        replay_one(outbox, lock, bridge.dual_write_paper_close, **params)
    saved_plan = json.loads(outbox.path.read_text())[FIELD][0]["write_plan"]
    if reserved_capacity:
        assert validate(json.loads(outbox.path.read_text()))['queued'] == 1
    store = store_module.V3EvidenceStore(tmp_path, epoch_id="epoch-e1")
    execution = store.ledger_path("execution").read_bytes()
    monkeypatch.setattr(bridge, "_paper_market_segment", lambda *a, **k: pytest.fail("mutable tape re-read"))
    monkeypatch.setattr(store_module.V3EvidenceStore, "append", append)
    assert replay_one(outbox, lock, bridge.dual_write_paper_close, **params)
    assert tape_reads == [1]
    assert store.ledger_path("execution").read_bytes() == execution
    lifecycle = json.loads(store.ledger_path("lifecycle").read_text())
    assert lifecycle["market_segment_coverage"]["reason"] == "ORIGINAL_TAPE_EMPTY"
    assert saved_plan["sha256"]
    assert json.loads(outbox.path.read_text())[FIELD] == []
    if reserved_capacity:
        assert validate(json.loads(outbox.path.read_text()))['held'] == 0


def test_oversize_plan_retains_unmodified_obligation(tmp_path):
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    target = {"paper_only": True, "live_armed": False}
    enqueue_close(target, {"trade_id": "p1"}, {}, {}, epoch_id="e1")
    outbox._atomic_write(outbox.decorate_lifecycle(target))
    before = outbox.path.read_bytes()
    def writer(*a, **k):
        assert k["prepare_only"] is True
        return {"oversize": "x" * (2 * 1024 * 1024)}
    writer.supports_durable_write_plan = True
    with pytest.raises(ValueError, match="PAPER_CLOSE_WRITE_PLAN_OVERSIZE"):
        replay_one(outbox, threading.RLock(), writer, data_dir=str(tmp_path), epoch_id="e1")
    final = json.loads(outbox.path.read_text())
    assert final[FIELD] == json.loads(before)[FIELD]
    assert "write_plan" not in final[FIELD][0]
    assert final["paper_research_replay"]["outcomes"][-1]["outcome"] == "RETAINED_VALIDATION_ERROR"


def test_plan_persistence_preserves_concurrently_added_obligation(tmp_path):
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    target = {"paper_only": True, "live_armed": False}
    enqueue_close(target, {"trade_id": "p1"}, {}, {}, epoch_id="e1")
    outbox._atomic_write(outbox.decorate_lifecycle(target))
    lock = threading.RLock()
    def writer(*a, **k):
        assert not lock._is_owned()
        if k.get("prepare_only"):
            latest = json.loads(outbox.path.read_text())
            enqueue_close(latest, {"trade_id": "p2"}, {}, {}, epoch_id="e1")
            outbox._atomic_write(outbox.decorate_lifecycle(latest))
            return {"frozen": True}
        assert k["write_plan"] == {"frozen": True}
        return {"writes": [{"ledger": "execution", "record_id": "execution:p1:paper-close", "written": True},
                           {"ledger": "lifecycle", "record_id": "lifecycle:p1:paper-closed", "written": True}]}
    writer.supports_durable_write_plan = True
    assert replay_one(outbox, lock, writer, data_dir=str(tmp_path), epoch_id="e1")
    remaining = json.loads(outbox.path.read_text())[FIELD]
    assert len(remaining) == 1 and remaining[0]["position"]["trade_id"] == "p2"
