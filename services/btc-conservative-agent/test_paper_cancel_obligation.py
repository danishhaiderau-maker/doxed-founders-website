import copy
import ast
from pathlib import Path
from types import SimpleNamespace
import json
import threading
import pytest
from paper_research_obligations import FIELD, enqueue_cancel, replay_one
from relay_event_outbox import RelayEventOutbox
from research_v3_bridge import dual_write_paper_cancel
import research_v3_store as module


def test_actual_bot_replay_supplies_cancel_writer(monkeypatch):
    import paper_research_obligations as obligations
    calls = []
    monkeypatch.setattr(obligations, 'replay_one', lambda *a, **kw: calls.append(kw))
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_replay_paper_research_obligation')
    ns = {'_paper_research_replay_next': 0, 'time': SimpleNamespace(monotonic=lambda: 10),
          '_relay_event_outbox': object(), 'paper_lifecycle_file_lock': threading.RLock(),
          'dual_write_paper_close': None, 'dual_write_paper_fill': None,
          'dual_write_paper_cancel': dual_write_paper_cancel,
          '_collector_v22_epoch_id': lambda: 'e1', '_runtime_git_rev_exact': lambda: 'a' * 40,
          'os': SimpleNamespace(getcwd=lambda: 'local'),
          'logger': SimpleNamespace(error=lambda *a: pytest.fail('replay wiring raised'))}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'actual-replay', 'exec'), ns)
    ns['_replay_paper_research_obligation']()
    assert calls[0]['cancel_writer'] is dual_write_paper_cancel


def order():
    return {"trade_id": "p1", "event_episode_id": "episode-p1", "dir": "LONG",
            "qty": 2, "requested_qty": 3, "filled_qty": 0,
            "paper_fill_accounting_schema": "paper_initial_unfilled_v1",
            "research_chase_schedule": {"authoritative": True, "terminal_ts": 2,
               "terminal_reason": "TTL_EXPIRED", "intervals": [{"start_ts": 1, "end_ts": 2}]}}


def test_cancel_uses_reservation_and_is_idempotent():
    from paper_evidence_capacity import initialize_legacy, reserve_entry, validate
    target = reserve_entry(initialize_legacy({"paper_only": True, "live_armed": False}, capacity=2), "p1")
    enqueue_cancel(target, order(), {}, "TTL_EXPIRED", epoch_id="epoch-e1")
    assert validate(target) == {"held": 1, "queued": 1, "reserved": 0, "available": 1}
    before = copy.deepcopy(target)
    enqueue_cancel(target, order(), {}, "TTL_EXPIRED", epoch_id="epoch-e1")
    assert target == before


@pytest.mark.parametrize("filled", [None, True, 0.1, -1, "0"])
def test_cancel_without_explicit_zero_rejected(filled):
    candidate = order()
    candidate["filled_qty"] = filled
    target = {"paper_only": True, "live_armed": False}
    with pytest.raises(ValueError):
        enqueue_cancel(target, candidate, {}, "TTL_EXPIRED", epoch_id="epoch-e1")
    assert FIELD not in target


def test_cancel_cross_deploy_retry_requires_both_durable_rows(tmp_path, monkeypatch):
    old = {"evidence_provenance_schema": "v3_collection_provenance_v1",
           "source_revision": "a" * 40, "deployed_revision": "a" * 40,
           "tile_config_signature": "b" * 64, "config_signature": "c" * 64}
    monkeypatch.setattr(module, "_collection_provenance", lambda: old)
    monkeypatch.setattr(module, "storage_blocks_new_nonessential_research", lambda _: False)
    target = {"paper_only": True, "live_armed": False}
    enqueue_cancel(target, order(), {}, "TTL_EXPIRED", epoch_id="epoch-e1", provenance=old)
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    outbox._atomic_write(outbox.decorate_lifecycle(target))
    append = module.V3EvidenceStore.append
    def crash(self, ledger, row, **kwargs):
        if ledger == "lifecycle":
            raise OSError("after execution")
        return append(self, ledger, row, **kwargs)
    monkeypatch.setattr(module.V3EvidenceStore, "append", crash)
    params = dict(data_dir=str(tmp_path), epoch_id="epoch-e1", cancel_writer=dual_write_paper_cancel)
    with pytest.raises(OSError):
        replay_one(outbox, threading.RLock(), None, **params)
    assert json.loads(outbox.path.read_text())[FIELD]
    monkeypatch.setattr(module.V3EvidenceStore, "append", append)
    monkeypatch.setattr(module, "_collection_provenance", lambda: {**old, "source_revision": "d" * 40, "deployed_revision": "d" * 40})
    assert replay_one(outbox, threading.RLock(), None, **params)
    store = module.V3EvidenceStore(tmp_path, epoch_id="epoch-e1")
    for ledger in ("execution", "lifecycle"):
        rows = [json.loads(line) for line in store.ledger_path(ledger).read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["source_revision"] == old["source_revision"]
        assert rows[0]["outcome_state"] == "NO_FILL"
        assert rows[0]["requested_qty"] == 3
        assert rows[0]["ranking_eligible"] is False
        assert "net_pnl_usd" not in rows[0]
    assert json.loads(outbox.path.read_text())[FIELD] == []
