import json
import threading
import pytest
from relay_event_outbox import RelayEventOutbox
from paper_research_obligations import FIELD, REPLAY_FIELD, enqueue_close, enqueue_fill, replay_one


def test_failed_fill_blocks_own_close_but_not_other_trade_across_restart(tmp_path):
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    state = {"schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False}
    enqueue_fill(state, {"trade_id": "a"}, {}, {"trade_id": "a"}, epoch_id="e1")
    enqueue_close(state, {"trade_id": "a"}, {}, {}, epoch_id="e1")
    enqueue_close(state, {"trade_id": "b"}, {}, {}, epoch_id="e1")
    outbox._atomic_write(outbox.decorate_lifecycle(state))
    def fill(*a, **k):
        raise OSError("interrupted")
    calls = []
    def close(position, *a, **k):
        trade = position["trade_id"]
        calls.append(trade)
        return {"writes": [{"ledger": "execution", "record_id": f"execution:{trade}:paper-close", "written": True},
                           {"ledger": "lifecycle", "record_id": f"lifecycle:{trade}:paper-closed", "written": True}]}
    params = dict(data_dir=str(tmp_path), epoch_id="e1", fill_writer=fill)
    with pytest.raises(OSError):
        replay_one(outbox, threading.RLock(), close, **params)
    first = json.loads(outbox.path.read_text())
    assert first[REPLAY_FIELD]["cursor_trade_id"] == "a"
    assert first[REPLAY_FIELD]["outcomes"][-1]["outcome"] == "RETAINED_IO_ERROR"
    restarted = RelayEventOutbox(outbox.path)
    assert replay_one(restarted, threading.RLock(), close, **params)
    assert calls == ["b"]
    remaining = json.loads(outbox.path.read_text())[FIELD]
    assert [row["kind"] for row in remaining] == ["PAPER_FILL", "PAPER_CLOSE"]


def test_attempt_history_is_bounded_and_pending_never_dropped(tmp_path):
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    state = {"paper_only": True, "live_armed": False}
    enqueue_close(state, {"trade_id": "a"}, {}, {}, epoch_id="e1")
    outbox._atomic_write(outbox.decorate_lifecycle(state))
    for _ in range(35):
        assert not replay_one(outbox, threading.RLock(), lambda *a, **k: {"writes": []}, data_dir=str(tmp_path), epoch_id="e1")
    final = json.loads(outbox.path.read_text())
    assert len(final[FIELD]) == 1
    assert len(final[REPLAY_FIELD]["outcomes"]) == 32
    assert final[REPLAY_FIELD]["attempt_seq"] == 35
