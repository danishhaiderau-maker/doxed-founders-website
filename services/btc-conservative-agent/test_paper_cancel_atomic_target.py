import ast
import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
import paper_research_obligations


def test_actual_cancel_to_real_ledger_ack(tmp_path, monkeypatch):
    from research_order_schedule import close_order_schedule
    from research_v3_bridge import dual_write_paper_cancel
    from research_v3_store import V3EvidenceStore
    from relay_event_outbox import RelayEventOutbox
    from paper_evidence_capacity import initialize_legacy, validate
    import research_v3_store as store_module
    real_enqueue = paper_research_obligations.enqueue_cancel
    ns, order, _ = fixture(monkeypatch)
    monkeypatch.setattr(paper_research_obligations, 'enqueue_cancel', real_enqueue)
    provenance = {'evidence_provenance_schema': 'v3_collection_provenance_v1',
                  'source_revision': 'a' * 40, 'deployed_revision': 'a' * 40,
                  'tile_config_signature': 'b' * 64, 'config_signature': 'c' * 64}
    monkeypatch.setattr(store_module, '_collection_provenance', lambda: provenance)
    monkeypatch.setattr(store_module, 'storage_blocks_new_nonessential_research', lambda _: False)
    order.update(qty=2, requested_qty=2, event_episode_id='episode-a', dir='LONG',
                 research_chase_schedule={'authoritative': True, 'intervals': [{'start_ts': 1}], 'quantity_events': []})
    ns['close_research_order_schedule'] = close_order_schedule
    ns['_collector_v22_epoch_id'] = lambda: 'epoch-e1'
    outbox = RelayEventOutbox(tmp_path / 'paper.json')
    def commit(event, tid, extra, *, target_mutator, live_mutator):
        target = initialize_legacy({'paper_only': True, 'live_armed': False,
                                    'pending_orders': [copy.deepcopy(order)], 'positions': []})
        target_mutator(target)
        outbox._atomic_write(outbox.decorate_lifecycle(target))
        live_mutator()
    ns['_commit_local_paper_lifecycle_transition'] = commit
    assert ns['_commit_local_paper_cancel'](order, 'TTL', dispatch_ts=1)['finalized']
    saved = json.loads(outbox.path.read_text())
    assert saved['pending_orders'] == [] and validate(saved)['held'] == 1
    assert paper_research_obligations.replay_one(outbox, threading.RLock(), None,
        data_dir=str(tmp_path), epoch_id='epoch-e1', cancel_writer=dual_write_paper_cancel)
    assert validate(json.loads(outbox.path.read_text()))['held'] == 0
    store = V3EvidenceStore(tmp_path, epoch_id='epoch-e1')
    for ledger in ('execution', 'lifecycle'):
        rows = [json.loads(line) for line in store.ledger_path(ledger).read_text().splitlines()]
        assert len(rows) == 1 and rows[0]['outcome_state'] == 'NO_FILL'


def fixture(monkeypatch):
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_commit_local_paper_cancel")
    order = {"trade_id": "a", "research_lane": "PAPER", "filled_qty": 0.0,
             "paper_fill_accounting_schema": "paper_initial_unfilled_v1"}
    saved = []
    def enqueue(target, frozen, signal, reason, **kwargs):
        target["cancel_evidence"] = copy.deepcopy(frozen)
    monkeypatch.setattr(paper_research_obligations, "enqueue_cancel", enqueue, raising=False)
    ns = {"copy": copy, "time": SimpleNamespace(time=lambda: 2), "trades_map": {},
          "_stable_pending_signal_copy": lambda x: {}, "_collector_v22_epoch_id": lambda: "e1",
          "close_research_order_schedule": lambda order, signal, **kw: order.update(research_chase_schedule={"terminal": True}),
          "_append_paper_action_receipt": lambda order, *a, **kw: order.update(action=kw),
          "lane_unregister_pending_order": lambda row: None}
    def commit(event, tid, extra, *, target_mutator, live_mutator):
        target = {"pending_orders": [copy.deepcopy(order)], "positions": []}
        target_mutator(target)
        saved.append(copy.deepcopy(target))
        live_mutator()
    ns["_commit_local_paper_lifecycle_transition"] = commit
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "actual-cancel", "exec"), ns)
    return ns, order, saved


def test_actual_cancel_target_retains_terminal_schedule_before_live_removal(monkeypatch):
    ns, order, saved = fixture(monkeypatch)
    result = ns["_commit_local_paper_cancel"](order, "TTL", dispatch_ts=1)
    assert result["finalized"]
    assert saved[0]["pending_orders"] == []
    assert saved[0]["cancel_evidence"]["research_chase_schedule"]["terminal"] is True
    assert saved[0]["cancel_evidence"]["action"]["action_type"] == "CANCEL_CONFIRMED"


@pytest.mark.parametrize("change", [{"filled_qty": None}, {"filled_qty": 1}, {"partial_fill": True}, {"paper_fill_accounting_schema": None}])
def test_missing_or_nonzero_accounting_never_releases(monkeypatch, change):
    ns, order, saved = fixture(monkeypatch)
    order.update(change)
    before = copy.deepcopy(order)
    result = ns["_commit_local_paper_cancel"](order, "TTL", dispatch_ts=1)
    assert result["failure_reason"] == "PAPER_CANCEL_ZERO_FILL_UNPROVEN"
    assert saved == [] and order == before
