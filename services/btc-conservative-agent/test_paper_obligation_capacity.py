"""Legacy capacity boundary and regression coverage for repaired retry fairness."""
import ast
import copy
import json
from pathlib import Path
import threading

import pytest

from paper_research_obligations import FIELD, enqueue_close, replay_one
from relay_event_outbox import RelayEventOutbox


def test_reproduces_full_queue_aborting_actual_close_target_before_publication(tmp_path):
    # Execute the real nested close target, without importing the live bot.
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    close = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "close_position")
    target_function = next(n for n in close.body if isinstance(n, ast.FunctionDef) and n.name == "target_mutator")
    position = {"trade_id": "protective-close", "research_lane": "TEST_PAPER"}
    namespace = {"trade_id": position["trade_id"], "pos": position,
                 "master": {}, "trade_row": {"close_ts": 129, "exit_reason": "HARD_STOP"},
                 "COMBO_LANE_SPECS": {"TEST_PAPER": {"paper_only": True}},
                 "_collector_v22_epoch_id": lambda: "e1"}
    exec(compile(ast.Module(body=[target_function], type_ignores=[]), "actual-close-target", "exec"), namespace)
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    snapshot = {"paper_only": True, "live_armed": False, "positions": [position]}
    for index in range(128):
        enqueue_close(snapshot, {"trade_id": f"old-{index}"}, {}, {"close_ts": index}, epoch_id="e1")
    outbox._atomic_write(outbox.decorate_lifecycle(snapshot))
    before = outbox.path.read_bytes()
    target = copy.deepcopy(snapshot)
    with pytest.raises(RuntimeError, match="research obligation capacity exhausted"):
        namespace["target_mutator"](target)
        outbox._atomic_write(outbox.decorate_lifecycle(target))
    # The target removed the position, but the exception prevented publication.
    assert target["positions"] == []
    assert outbox.path.read_bytes() == before
    assert json.loads(before)["positions"] == [position]


@pytest.mark.parametrize("failure", ["blocked_receipt", "exception"])
def test_failed_first_obligation_does_not_starve_later_valid_work(tmp_path, failure):
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    snapshot = {"schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False, "positions": []}
    for trade_id in ("bad", "good"):
        enqueue_close(snapshot, {"trade_id": trade_id}, {}, {"close_ts": 1}, epoch_id="e1")
    outbox._atomic_write(outbox.decorate_lifecycle(snapshot))
    attempts = []
    def writer(position, *args, **kwargs):
        trade_id = position["trade_id"]
        attempts.append(trade_id)
        if trade_id == "bad":
            if failure == "exception":
                raise ValueError("PAPER_CLOSE_RECOVERY_CONTENT_CONFLICT")
            return {"writes": [{"blocked": True}]}
        return {"writes": [{"written": True, "ledger": ledger, "record_id": record_id}
                           for ledger, record_id in (("execution", "execution:good:paper-close"),
                                                     ("lifecycle", "lifecycle:good:paper-closed"))]}
    for attempt in range(3):
        # Reopen the durable snapshot every time: fairness must survive restart.
        outbox = RelayEventOutbox(outbox.path)
        if failure == "exception" and attempt != 1:
            with pytest.raises(ValueError, match="CONTENT_CONFLICT"):
                replay_one(outbox, threading.RLock(), writer, data_dir=str(tmp_path), epoch_id="e1")
        else:
            assert replay_one(outbox, threading.RLock(), writer, data_dir=str(tmp_path), epoch_id="e1") == (attempt == 1)
    assert attempts == ["bad", "good", "bad"]
    assert [r["position"]["trade_id"] for r in json.loads(outbox.path.read_text())[FIELD]] == ["bad"]
