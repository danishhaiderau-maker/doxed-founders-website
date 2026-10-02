import json
import threading
import ast
import copy
from pathlib import Path
from types import SimpleNamespace
import pytest
from paper_research_obligations import FIELD, enqueue_fill, replay_one
from relay_event_outbox import RelayEventOutbox


def test_fill_frozen_deduplicated_and_ack_requires_both_records(tmp_path):
    target = {"paper_only": True, "live_armed": False, "git_rev": "old"}
    order = {"trade_id": "p1", "qty": 2, "requested_qty": 3,
             "research_chase_schedule": {"terminal_reason": "PARTIAL_FILL_SIM_RESIDUAL_CANCELLED"}}
    position = {"trade_id": "p1", "entry": 100}
    provenance = {"source_revision": "old"}
    enqueue_fill(target, order, {}, position, epoch_id="e1", provenance=provenance)
    enqueue_fill(target, order, {}, position, epoch_id="e1", provenance=provenance)
    assert len(target[FIELD]) == 1
    order["qty"] = 9
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    outbox._atomic_write(outbox.decorate_lifecycle(target))
    writes = []
    def writer(frozen_order, signal, frozen_position, **kwargs):
        assert frozen_order["qty"] == 2
        assert frozen_order["requested_qty"] == 3
        assert kwargs["recovery_provenance"]["source_revision"] == "old"
        return {"writes": writes}
    params = dict(data_dir=str(tmp_path), epoch_id="e1", source_revision="new", fill_writer=writer)
    assert not replay_one(outbox, threading.RLock(), None, **params)
    writes.append({"ledger": "execution", "record_id": "execution:p1:primary-fill", "written": True})
    assert not replay_one(outbox, threading.RLock(), None, **params)
    writes.append({"ledger": "lifecycle", "record_id": "lifecycle:p1:paper-filled", "written": True})
    assert replay_one(outbox, threading.RLock(), None, **params)
    assert json.loads(outbox.path.read_text())[FIELD] == []


def test_fill_writer_crash_retains_obligation(tmp_path):
    target = {"paper_only": True, "live_armed": False}
    enqueue_fill(target, {"trade_id": "p1"}, {}, {"trade_id": "p1"}, epoch_id="e1")
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    outbox._atomic_write(outbox.decorate_lifecycle(target))
    def crash(*a, **k):
        raise OSError("after execution")
    with pytest.raises(OSError):
        replay_one(outbox, threading.RLock(), None, data_dir=str(tmp_path), epoch_id="e1", fill_writer=crash)
    assert json.loads(outbox.path.read_text())[FIELD]


def test_actual_open_target_has_fill_schedule_before_live_promotion(monkeypatch):
    from research_order_schedule import close_order_schedule
    from combo_pathway_config import COMBO_LANE_SPECS
    import research_v3_store
    monkeypatch.setattr(research_v3_store, "_collection_provenance", lambda: {"source_revision": "old"})
    lane = next(key for key, spec in COMBO_LANE_SPECS.items() if spec.get("paper_only") is True)
    order = {"trade_id": "p1", "qty": 2, "filled_qty": 2, "requested_qty": 3,
             "partial_fill": True, "limit_price": 100, "research_lane": lane,
             "research_chase_schedule": {"authoritative": True, "intervals": [{"start_ts": 1}], "quantity_events": []}}
    captured = {}
    def commit(*args, target_mutator, **kwargs):
        target = {"paper_only": True, "live_armed": False, "pending_orders": [order], "positions": []}
        target_mutator(target)
        captured.update(copy.deepcopy(target))
        raise OSError("crash after durable target before live mutation")
    ns = dict(copy=copy, time=SimpleNamespace(time=lambda: 100.5),
              utc_iso=lambda: "2026-09-08T00:00:00Z", order=order, signal={}, ai={},
              _collector_v22_epoch_id=lambda: "e1", COMBO_LANE_SPECS=COMBO_LANE_SPECS,
              _build_open_position=lambda *a: {"trade_id": "p1", "entry": 100, "qty": 2, "research_lane": lane},
              paper_policy_identity_for_sources=lambda *a: {"paper_policy_spec": {"research_lane": lane}},
              _stable_pending_signal_copy=copy.deepcopy, close_research_order_schedule=close_order_schedule,
              _append_paper_action_receipt=lambda *a, **k: None,
              trades_map={}, _canonicalize_paper_position_snapshot=lambda x: x,
              _commit_paper_lifecycle_transition=commit, position_close_lock=None)
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "fill_order")
    start = next(i for i, node in enumerate(fn.body) if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "candidate_pos" for t in node.targets))
    end = next(i for i, node in enumerate(fn.body) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
               and isinstance(node.value.func, ast.Name) and node.value.func.id == "_commit_paper_lifecycle_transition")
    with pytest.raises(OSError):
        exec(compile(ast.Module(body=fn.body[start:end + 1], type_ignores=[]), "actual-fill-target", "exec"), ns)
    assert captured["pending_orders"] == []
    row = captured[FIELD][0]
    assert row["kind"] == "PAPER_FILL"
    assert row["order"]["requested_qty"] == 3
    assert row["order"]["qty"] == 2
    schedule = row["order"]["research_chase_schedule"]
    assert schedule["terminal_reason"] == "PARTIAL_FILL_SIM_RESIDUAL_CANCELLED"
    assert schedule["terminal_ts_exact"] == 100.5
    assert captured["positions"][0]["research_chase_schedule"] == schedule
    assert "terminal_ts" not in order["research_chase_schedule"]


def test_real_fill_bridge_cross_deploy_retry(tmp_path, monkeypatch):
    import research_v3_store as module
    from research_v3_bridge import dual_write_paper_fill
    old = {"evidence_provenance_schema": "v3_collection_provenance_v1", "source_revision": "a" * 40,
           "deployed_revision": "a" * 40, "tile_config_signature": "b" * 64, "config_signature": "c" * 64}
    new = {**old, "source_revision": "d" * 40, "deployed_revision": "d" * 40}
    monkeypatch.setattr(module, "_collection_provenance", lambda: old)
    monkeypatch.setattr(module, "storage_blocks_new_nonessential_research", lambda _: False)
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    target = {"paper_only": True, "live_armed": False, "git_rev": old["source_revision"]}
    position = {"trade_id": "p1", "qty": 2, "entry": 100, "entry_ts": 1,
                "event_episode_id": "episode-p1", "dir": "LONG"}
    enqueue_fill(target, {"trade_id": "p1", "qty": 2, "requested_qty": 3,
                          "partial_fill": True, "fill_price": 100}, {}, position,
                 epoch_id="epoch-e1", provenance=old)
    outbox._atomic_write(outbox.decorate_lifecycle(target))
    append = module.V3EvidenceStore.append
    def fail(self, ledger, row, **kwargs):
        if ledger == "lifecycle":
            raise OSError("crash after execution durable")
        return append(self, ledger, row, **kwargs)
    monkeypatch.setattr(module.V3EvidenceStore, "append", fail)
    params = dict(data_dir=str(tmp_path), epoch_id="epoch-e1", source_revision=new["source_revision"],
                  fill_writer=dual_write_paper_fill)
    with pytest.raises(OSError):
        replay_one(outbox, threading.RLock(), None, **params)
    monkeypatch.setattr(module, "_collection_provenance", lambda: new)
    monkeypatch.setattr(module.V3EvidenceStore, "append", append)
    assert replay_one(outbox, threading.RLock(), None, **params)
    store = module.V3EvidenceStore(tmp_path, epoch_id="epoch-e1")
    for ledger in ("execution", "lifecycle"):
        rows = [json.loads(line) for line in store.ledger_path(ledger).read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["source_revision"] == old["source_revision"]
        assert rows[0]["processing_revision"] == (old if ledger == "execution" else new)["source_revision"]
    assert json.loads(outbox.path.read_text())[FIELD] == []
