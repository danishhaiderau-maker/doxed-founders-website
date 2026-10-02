import ast
import copy
import json
from pathlib import Path
import threading

import pytest
from paper_research_obligations import FIELD, enqueue_close, replay_one
from relay_event_outbox import RelayEventOutbox
from test_paper_family_chase_durability import fixture
from combo_pathway_config import COMBO_LANE_SPECS
from research_order_schedule import append_reprice_interval


def complete_receipt():
    return {"writes": [{"duplicate": True, "ledger": "execution", "record_id": "execution:p1:paper-close"},
                       {"written": True, "ledger": "lifecycle", "record_id": "lifecycle:p1:paper-closed"}]}


def setup(tmp_path):
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    target = {"paper_only": True, "live_armed": False, "positions": []}
    enqueue_close(target, {"trade_id": "p1"}, {}, {"close_ts": 1}, epoch_id="e1")
    outbox._atomic_write(outbox.decorate_lifecycle(target))
    return outbox, threading.RLock()


@pytest.mark.parametrize("failure", ["before", "between", "ack"])
def test_replay_crash_boundaries_exact_once(tmp_path, monkeypatch, failure):
    outbox, lock = setup(tmp_path)
    durable = set()
    def writer(*args, **kwargs):
        if failure == "before" and not durable:
            durable.add("attempt")
            raise OSError("crash before emit")
        durable.add("execution")
        if failure == "between" and "attempt" not in durable:
            durable.add("attempt")
            raise OSError("crash between emit")
        durable.add("lifecycle")
        return complete_receipt()
    original = outbox._atomic_write
    if failure == "ack":
        monkeypatch.setattr(outbox, "_atomic_write", lambda value: (_ for _ in ()).throw(OSError("ACK crash")))
    with pytest.raises(OSError):
        replay_one(outbox, lock, writer, data_dir=str(tmp_path), epoch_id="e1")
    assert json.loads(outbox.path.read_text())[FIELD]
    monkeypatch.setattr(outbox, "_atomic_write", original)
    assert replay_one(outbox, lock, writer, data_dir=str(tmp_path), epoch_id="e1")
    assert durable - {"attempt"} == {"execution", "lifecycle"}
    assert json.loads(outbox.path.read_text())[FIELD] == []
    assert outbox.pending_count() == 0


@pytest.mark.parametrize("receipt", [{"writes": []}, {"writes": [{"deferred": True, "written": True}]},
                                     {"writes": [{"blocked": True}]}, {"ready": True}])
def test_not_durable_retains(tmp_path, receipt):
    outbox, lock = setup(tmp_path)
    assert not replay_one(outbox, lock, lambda *a, **k: receipt, data_dir=str(tmp_path), epoch_id="e1")
    assert json.loads(outbox.path.read_text())[FIELD]


def test_ordinary_save_and_concurrent_mutation_preserve_obligations(tmp_path):
    outbox, lock = setup(tmp_path)
    outbox._atomic_write(outbox.decorate_lifecycle({"paper_only": True, "live_armed": False}))
    assert json.loads(outbox.path.read_text())[FIELD]
    def writer(*a, **k):
        latest = json.loads(outbox.path.read_text())
        latest["positions"] = [{"trade_id": "new"}]
        enqueue_close(latest, {"trade_id": "p2"}, {}, {"close_ts": 2}, epoch_id="e1")
        outbox._atomic_write(outbox.decorate_lifecycle(latest))
        return complete_receipt()
    assert replay_one(outbox, lock, writer, data_dir=str(tmp_path), epoch_id="e1")
    latest = json.loads(outbox.path.read_text())
    assert latest["positions"] == [{"trade_id": "new"}]
    assert len(latest[FIELD]) == 1 and latest[FIELD][0]["position"]["trade_id"] == "p2"


def test_actual_chase_snapshot_contains_schedule_even_before_live_swap(tmp_path):
    ns, order, signal, outbox, *_ = fixture(tmp_path, next(iter(COMBO_LANE_SPECS)))
    order["research_chase_schedule"] = {"authoritative": True, "trade_id": order["trade_id"],
        "intervals": [{"start_ts": 10, "end_ts": None, "reference_price": 100}], "quantity_events": []}
    ns["append_research_reprice_interval"] = append_reprice_interval
    assert ns["_apply_family_policy_chase"](order, signal, 100, 200)
    saved = json.loads(outbox.path.read_text())["pending_orders"][0]
    assert saved["research_chase_schedule"] == order["research_chase_schedule"]
    assert len(saved["research_chase_schedule"]["intervals"]) == 2
    assert saved["research_chase_schedule"]["intervals"][-1]["limit_price"] == 95


def test_actual_chase_crash_after_write_keeps_durable_schedule(tmp_path, monkeypatch):
    ns, order, signal, outbox, *_ = fixture(tmp_path, next(iter(COMBO_LANE_SPECS)))
    order["research_chase_schedule"] = {"authoritative": True, "trade_id": order["trade_id"],
        "intervals": [{"start_ts": 10, "end_ts": None, "reference_price": 100}], "quantity_events": []}
    ns["append_research_reprice_interval"] = append_reprice_interval
    original = outbox._atomic_write
    def crash(value):
        original(value)
        raise SystemExit("process died after atomic replace")
    monkeypatch.setattr(outbox, "_atomic_write", crash)
    with pytest.raises(SystemExit):
        ns["_apply_family_policy_chase"](order, signal, 100, 200)
    saved = json.loads(outbox.path.read_text())["pending_orders"][0]
    assert order["limit_price"] == 90 and len(order["research_chase_schedule"]["intervals"]) == 1
    assert saved["limit_price"] == 95 and len(saved["research_chase_schedule"]["intervals"]) == 2


def test_foreign_revision_or_epoch_retained_not_relabelled(tmp_path):
    outbox, lock = setup(tmp_path)
    original = outbox.path.read_bytes()
    def never(*a, **k):
        pytest.fail("foreign source must not be written with current provenance")
    assert not replay_one(outbox, lock, never, data_dir=str(tmp_path), epoch_id="other")
    assert not replay_one(outbox, lock, never, data_dir=str(tmp_path), epoch_id="e1", source_revision="new")
    assert outbox.path.read_bytes() == original


def test_actual_close_target_persists_obligation_before_removing_position(tmp_path):
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    target = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "target_mutator"
                  and any(isinstance(c, ast.Name) and c.id == "enqueue_close" for c in ast.walk(n)))
    lane = next(iter(COMBO_LANE_SPECS))
    pos = {"trade_id": "p1", "research_lane": lane}
    ns = {"pos": pos, "trade_id": "p1", "master": {}, "trade_row": {"close_ts": 1},
          "COMBO_LANE_SPECS": COMBO_LANE_SPECS, "_collector_v22_epoch_id": lambda: "e1"}
    exec(compile(ast.Module(body=[target], type_ignores=[]), "bot.py", "exec"), ns)
    snapshot = {"positions": [pos], "paper_only": True, "live_armed": False}
    ns["target_mutator"](snapshot)
    assert snapshot["positions"] == [] and snapshot[FIELD][0]["position"] == pos


def test_actual_bridge_cross_deploy_partial_write_retry(tmp_path, monkeypatch):
    import research_v3_store as module
    from research_v3_bridge import dual_write_paper_close
    old = {"evidence_provenance_schema": "v3_collection_provenance_v1", "source_revision": "a" * 40,
           "deployed_revision": "a" * 40, "tile_config_signature": "b" * 64, "config_signature": "c" * 64}
    new = {**old, "source_revision": "d" * 40, "deployed_revision": "d" * 40}
    monkeypatch.setattr(module, "_collection_provenance", lambda: old)
    monkeypatch.setattr(module, "storage_blocks_new_nonessential_research", lambda _: False)
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    target = {"paper_only": True, "live_armed": False, "git_rev": old["source_revision"], "positions": []}
    enqueue_close(target, {"trade_id": "p1", "qty": 2, "entry": 100, "event_episode_id": "episode-p1", "dir": "LONG"}, {},
                  {"close_ts": 3, "exit": 101, "net_pnl_usd": 1.5, "gross_pnl_usd": 2,
                   "trading_fees_usd": 0.5, "funding_fees_usd": 0}, epoch_id="epoch-e1", provenance=old)
    outbox._atomic_write(outbox.decorate_lifecycle(target))
    append = module.V3EvidenceStore.append
    def fail_lifecycle(self, ledger, row, **kwargs):
        if ledger == "lifecycle":
            raise OSError("crash after execution durable")
        return append(self, ledger, row, **kwargs)
    monkeypatch.setattr(module.V3EvidenceStore, "append", fail_lifecycle)
    with pytest.raises(OSError):
        replay_one(outbox, threading.RLock(), dual_write_paper_close,
                   data_dir=str(tmp_path), epoch_id="epoch-e1", source_revision=new["source_revision"])
    assert json.loads(outbox.path.read_text())[FIELD]
    monkeypatch.setattr(module, "_collection_provenance", lambda: new)
    monkeypatch.setattr(module.V3EvidenceStore, "append", append)
    receipts = []
    def retry_writer(*args, **kwargs):
        receipt = dual_write_paper_close(*args, **kwargs)
        receipts.append(receipt)
        return receipt
    assert replay_one(outbox, threading.RLock(), retry_writer,
                      data_dir=str(tmp_path), epoch_id="epoch-e1", source_revision=new["source_revision"]), json.dumps([r["writes"] for r in receipts])
    store = module.V3EvidenceStore(tmp_path, epoch_id="epoch-e1")
    for ledger in ("execution", "lifecycle"):
        rows = [json.loads(line) for line in store.ledger_path(ledger).read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["source_revision"] == old["source_revision"]
        assert rows[0]["config_signature"] == old["config_signature"]
        assert rows[0]["processing_revision"] == (old if ledger == "execution" else new)["source_revision"]
        assert rows[0]["net_pnl_usd"] == 1.5
    assert json.loads(outbox.path.read_text())[FIELD] == []


def test_store_recovery_provenance_rejects_foreign_epoch(tmp_path):
    from research_v3_store import V3EvidenceStore
    with pytest.raises(ValueError, match="INVALID_PAPER_CLOSE_RECOVERY_PROVENANCE"):
        V3EvidenceStore(tmp_path, epoch_id="e1").append("lifecycle", {"record_id": "x"},
            paper_close_recovery_provenance={"epoch_id": "other"})


@pytest.mark.parametrize("variant", ["missing", "duplicate", "wrong_id"])
def test_ack_requires_exact_mandatory_emissions(tmp_path, variant):
    outbox, lock = setup(tmp_path)
    receipt = complete_receipt()
    if variant == "missing": receipt["writes"].pop()
    if variant == "duplicate": receipt["writes"].append(dict(receipt["writes"][0]))
    if variant == "wrong_id": receipt["writes"][0]["record_id"] = "execution:another:paper-close"
    assert not replay_one(outbox, lock, lambda *a, **k: receipt, data_dir=str(tmp_path), epoch_id="e1")
    assert json.loads(outbox.path.read_text())[FIELD]


def test_epoch_reset_during_emission_never_acknowledges_new_snapshot(tmp_path):
    outbox, lock = setup(tmp_path)
    reset = {}
    def writer(*a, **k):
        replacement = {"paper_only": True, "live_armed": False, FIELD: []}
        enqueue_close(replacement, {"trade_id": "new"}, {}, {}, epoch_id="new-epoch")
        outbox._atomic_write(outbox.decorate_lifecycle(replacement))
        reset["bytes"] = outbox.path.read_bytes()
        return complete_receipt()
    assert not replay_one(outbox, lock, writer, data_dir=str(tmp_path), epoch_id="e1")
    assert outbox.path.read_bytes() == reset["bytes"]


def test_tampered_obligation_is_not_emitted(tmp_path):
    outbox, lock = setup(tmp_path)
    value = json.loads(outbox.path.read_text())
    value[FIELD][0]["outcome"]["net_pnl_usd"] = 999
    outbox._atomic_write(value)
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        replay_one(outbox, lock, lambda *a, **k: pytest.fail("must not emit"),
                   data_dir=str(tmp_path), epoch_id="e1")


@pytest.mark.parametrize("corruption", ["economics", "bytes"])
def test_cross_revision_duplicate_requires_exact_economic_bytes(tmp_path, monkeypatch, corruption):
    import research_v3_store as module
    frozen = {"evidence_provenance_schema": "v3_collection_provenance_v1", "source_revision": "a" * 40,
              "deployed_revision": "a" * 40, "tile_config_signature": "b" * 64,
              "config_signature": "c" * 64, "epoch_id": "epoch-e1"}
    monkeypatch.setattr(module, "_collection_provenance", lambda: frozen)
    monkeypatch.setattr(module, "storage_blocks_new_nonessential_research", lambda _: False)
    store = module.V3EvidenceStore(tmp_path, epoch_id="epoch-e1")
    row = {"record_id": "execution:p1:paper-close", "net_pnl_usd": 2}
    assert store.append("execution", row, paper_close_recovery_provenance=frozen)["written"]
    monkeypatch.setattr(module, "_collection_provenance", lambda: {**frozen, "source_revision": "d" * 40})
    if corruption == "economics":
        row["net_pnl_usd"] = 999
    else:
        path = store.ledger_path("execution")
        path.write_bytes(path.read_bytes().replace(b'"net_pnl_usd":2', b'"net_pnl_usd":9'))
    with pytest.raises(ValueError, match="PAPER_CLOSE_RECOVERY_(CONTENT_CONFLICT|BYTES_UNPROVEN)"):
        store.append("execution", row, paper_close_recovery_provenance=frozen)
