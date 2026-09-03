import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from relay_event_outbox import RelayEventOutbox


def test_enqueue_next_is_atomic_across_concurrent_event_types_and_restart(tmp_path):
    path = tmp_path / "outbox.json"
    box = RelayEventOutbox(path)
    rows = []
    barrier = threading.Barrier(9)

    def emit(index):
        barrier.wait()
        rows.append(box.enqueue_next({
            "event": "LIMIT_UPDATED" if index % 2 else "POSITION_REDUCED",
            "trade_id": "trade-race", "ts": f"t{index}",
        }))

    threads = [threading.Thread(target=emit, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(row["event_seq"] for row in rows) == list(range(8))
    restarted = RelayEventOutbox(path)
    ninth = restarted.enqueue_next({
        "event": "POSITION_CLOSED", "trade_id": "trade-race", "ts": "t9",
    })
    assert ninth["event_seq"] == 8
    assert [row["event_seq"] for row in restarted.due()] == [0]


def test_enqueue_wakes_idle_delivery_lane_without_polling_delay(tmp_path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    result = []
    started = time.perf_counter()
    waiter = threading.Thread(target=lambda: result.append(box.consume_wake(2.0)))
    waiter.start()
    time.sleep(0.03)
    box.enqueue_next({"event": "ORDER_PLACED", "trade_id": "wake", "ts": "now"})
    waiter.join()
    assert result == [True]
    assert time.perf_counter() - started < 0.5


def lifecycle(state="PENDING"):
    return {
        "schema": "paper_lifecycle_v1", "paper_only": True,
        "live_armed": False, "positions": [],
        "pending_orders": [{"trade_id": "trade", "status": state}],
        "awaiting_signals": [],
    }


def test_same_generation_contains_resulting_state_and_pending_event(tmp_path):
    path = tmp_path / "paper_lifecycle_v1.json"
    box = RelayEventOutbox(path)
    row = box.enqueue_next(
        {"event": "ORDER_PLACED", "trade_id": "trade", "ts": "now"},
        state_payload=lifecycle(),
    )
    committed = json.loads(path.read_text())
    assert committed["pending_orders"][0]["trade_id"] == "trade"
    assert committed["relay_events"]["pending"][0]["event_id"] == row["event_id"]
    assert RelayEventOutbox(path).pending_count() == 1


def test_prepare_is_not_deliverable_until_target_and_event_commit_together(tmp_path):
    path = tmp_path / "paper_lifecycle_v1.json"
    box = RelayEventOutbox(path)
    box._persist(state_payload={**lifecycle(), "pending_orders": []})
    target = lifecycle()
    record = box.prepare_transition(
        target,
        {"event": "ORDER_PLACED", "trade_id": "trade", "ts": "now"},
    )
    prepared = json.loads(path.read_text())
    assert prepared["pending_orders"] == []
    assert prepared["relay_events"]["pending"] == []
    assert prepared["transition_wal"]["transition_id"] == record["event_id"]
    assert box.due() == []

    box.commit_prepared(record["event_id"])
    committed = json.loads(path.read_text())
    assert committed["transition_wal"] is None
    assert committed["pending_orders"][0]["trade_id"] == "trade"
    assert committed["relay_events"]["pending"][0]["event_id"] == record["event_id"]


def test_restart_finishes_prepared_target_and_event_exactly_once(tmp_path):
    path = tmp_path / "paper_lifecycle_v1.json"
    box = RelayEventOutbox(path)
    box._persist(state_payload={**lifecycle(), "pending_orders": []})
    record = box.prepare_transition(
        lifecycle(),
        {"event": "ORDER_PLACED", "trade_id": "trade", "ts": "now"},
    )

    restarted = RelayEventOutbox(path)
    committed = json.loads(path.read_text())
    assert restarted.healthy is True
    assert restarted.pending_count() == 1
    assert [row["event_id"] for row in restarted.due()] == [record["event_id"]]
    assert committed["transition_wal"] is None
    assert committed["pending_orders"][0]["trade_id"] == "trade"


def test_abrupt_process_after_prepare_recovers_target_and_event(tmp_path):
    path = tmp_path / "paper_lifecycle_v1.json"
    module_dir = str(Path(__file__).parent)
    script = r'''
import os, sys
sys.path.insert(0, sys.argv[2])
from relay_event_outbox import RelayEventOutbox
b = RelayEventOutbox(sys.argv[1])
base = {"schema":"paper_lifecycle_v1","paper_only":True,"live_armed":False,
        "positions":[],"pending_orders":[],"awaiting_signals":[]}
b._persist(state_payload=base)
target = dict(base)
target["pending_orders"] = [{"trade_id":"trade","status":"PENDING"}]
b.prepare_transition(target, {"event":"ORDER_PLACED","trade_id":"trade","ts":"now"})
os._exit(23)
'''
    result = subprocess.run([sys.executable, "-c", script, str(path), module_dir])
    assert result.returncode == 23
    restarted = RelayEventOutbox(path)
    assert restarted.healthy is True
    assert restarted.pending_count() == 1
    assert json.loads(path.read_text())["pending_orders"][0]["trade_id"] == "trade"


def test_tampered_prepared_target_fails_closed_without_publishing(tmp_path):
    path = tmp_path / "paper_lifecycle_v1.json"
    box = RelayEventOutbox(path)
    box._persist(state_payload={**lifecycle(), "pending_orders": []})
    box.prepare_transition(
        lifecycle(),
        {"event": "ORDER_PLACED", "trade_id": "trade", "ts": "now"},
    )
    prepared = json.loads(path.read_text())
    prepared["transition_wal"]["target"]["pending_orders"] = []
    path.write_text(json.dumps(prepared))
    restarted = RelayEventOutbox(path)
    assert restarted.healthy is False
    assert restarted.pending_count() == 0
    assert "target hash mismatch" in restarted.recovery_error


def test_ordinary_persist_refuses_to_overwrite_unresolved_prepare(tmp_path):
    path = tmp_path / "paper_lifecycle_v1.json"
    box = RelayEventOutbox(path)
    box._persist(state_payload={**lifecycle(), "pending_orders": []})
    box.prepare_transition(
        lifecycle(),
        {"event": "ORDER_PLACED", "trade_id": "trade", "ts": "now"},
    )
    with pytest.raises(RuntimeError, match="WAL is unresolved"):
        box._persist(state_payload={**lifecycle(), "pending_orders": []})


def test_terminal_tombstone_survives_until_exact_ack(tmp_path):
    path = tmp_path / "paper_lifecycle_v1.json"
    box = RelayEventOutbox(path)
    row = box.enqueue_next(
        {"event": "POSITION_CLOSED", "trade_id": "trade", "ts": "closed"},
        state_payload={**lifecycle(), "pending_orders": []},
    )
    assert json.loads(path.read_text())["relay_events"]["pending"]
    assert box.acknowledge(row["event_id"], ack(row))
    committed = json.loads(path.read_text())
    assert committed["pending_orders"] == []
    assert committed["relay_events"]["pending"] == []
    assert committed["relay_events"]["acks"][0]["event_id"] == row["event_id"]


def test_abrupt_process_after_source_commit_replays_pending(tmp_path):
    path = tmp_path / "paper_lifecycle_v1.json"
    module_dir = str(Path(__file__).parent)
    script = (
        "import os,sys; sys.path.insert(0,sys.argv[2]); "
        "from relay_event_outbox import RelayEventOutbox; "
        "b=RelayEventOutbox(sys.argv[1]); "
        "b.enqueue_next({'event':'ORDER_PLACED','trade_id':'trade','ts':'now'},"
        "state_payload={'schema':'paper_lifecycle_v1','paper_only':True,"
        "'live_armed':False,'positions':[],'pending_orders':[{'trade_id':'trade'}],"
        "'awaiting_signals':[]}); os._exit(19)"
    )
    result = subprocess.run([sys.executable, "-c", script, str(path), module_dir])
    assert result.returncode == 19
    assert RelayEventOutbox(path).pending_count() == 1


def test_corrupt_generation_is_quarantined_and_fails_closed_without_loop(tmp_path):
    path = tmp_path / "paper_lifecycle_v1.json"
    path.write_bytes(b'{"schema":"paper_lifecycle_v1"')
    box = RelayEventOutbox(path)
    assert box.healthy is False
    assert box.due() == []
    assert list(tmp_path.glob("paper_lifecycle_v1.json.corrupt-*"))
    with pytest.raises(ValueError, match="corrupt"):
        box.enqueue_next({"event": "ORDER_PLACED", "trade_id": "trade"})
    # A second construction does not crash-loop on the quarantined bytes.
    assert RelayEventOutbox(path).healthy is True


@pytest.mark.parametrize("value", [[], {"schema": "unknown"}])
def test_valid_json_with_invalid_root_or_schema_is_quarantined(tmp_path, value):
    path = tmp_path / "paper_lifecycle_v1.json"
    path.write_text(json.dumps(value))
    box = RelayEventOutbox(path)
    assert box.healthy is False
    assert box.due() == []
    assert list(tmp_path.glob("paper_lifecycle_v1.json.corrupt-*"))


def test_ack_replace_interruption_keeps_event_pending_in_memory_and_on_restart(tmp_path, monkeypatch):
    path = tmp_path / "paper_lifecycle_v1.json"
    box = RelayEventOutbox(path)
    row = box.enqueue_next(
        {"event": "POSITION_CLOSED", "trade_id": "trade", "ts": "closed"},
        state_payload={**lifecycle(), "pending_orders": []},
    )
    original = box._persist
    monkeypatch.setattr(box, "_persist", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("replace interrupted")))
    with pytest.raises(OSError, match="interrupted"):
        box.acknowledge(row["event_id"], ack(row))
    assert box.pending_count() == 1
    monkeypatch.setattr(box, "_persist", original)
    assert RelayEventOutbox(path).pending_count() == 1


def test_enqueue_replace_interruption_does_not_publish_memory_only_event(tmp_path, monkeypatch):
    path = tmp_path / "paper_lifecycle_v1.json"
    box = RelayEventOutbox(path)
    monkeypatch.setattr(box, "_persist", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("replace interrupted")))
    with pytest.raises(OSError, match="interrupted"):
        box.enqueue_next(
            {"event": "ORDER_PLACED", "trade_id": "trade", "ts": "now"},
            state_payload=lifecycle(),
        )
    assert box.pending_count() == 0


def test_market_ack_and_next_event_share_one_generation_lock(tmp_path):
    path = tmp_path / "paper_lifecycle_v1.json"
    shared = threading.RLock()
    box = RelayEventOutbox(path, shared_lock=shared)
    first = box.enqueue_next(
        {"event": "LIMIT_UPDATED", "trade_id": "trade", "ts": "one"},
        state_payload={**lifecycle(), "generation": 1},
    )
    ack_entered = threading.Event()
    permit_ack = threading.Event()

    def finalize_and_ack():
        with shared:
            # This represents source finalization plus construction of the ACK
            # lifecycle generation in _deliver_relay_outbox_record.
            ack_entered.set()
            assert permit_ack.wait(timeout=5)
            state = json.loads(path.read_text())
            state["generation"] = 2
            state["pending_orders"][0]["limit_price"] = 101.0
            assert box.acknowledge(first["event_id"], ack(first), state_payload=state)

    def commit_next_event():
        assert ack_entered.wait(timeout=5)
        with shared:
            state = json.loads(path.read_text())
            state["generation"] = 3
            box.enqueue_next(
                {"event": "POSITION_OPENED", "trade_id": "peer", "ts": "two"},
                state_payload=state,
            )

    ack_thread = threading.Thread(target=finalize_and_ack)
    next_thread = threading.Thread(target=commit_next_event)
    ack_thread.start()
    next_thread.start()
    assert ack_entered.wait(timeout=5)
    time.sleep(0.03)
    assert next_thread.is_alive()  # blocked behind the uninterrupted ACK generation
    permit_ack.set()
    ack_thread.join(timeout=5)
    next_thread.join(timeout=5)
    assert not ack_thread.is_alive() and not next_thread.is_alive()
    committed = json.loads(path.read_text())
    assert committed["generation"] == 3
    assert committed["pending_orders"][0]["limit_price"] == 101.0
    assert [row["event_type"] for row in committed["relay_events"]["pending"]] == ["POSITION_OPENED"]


def payload(event_id="trade:ORDER_PLACED:0:t", event="ORDER_PLACED", seq=0):
    return {"event_id": event_id, "event": event, "event_seq": seq, "trade_id": "trade", "ts": "2026-09-03T00:00:00Z"}


def ack(row, **overrides):
    value = {
        "event_id": row["event_id"], "event_type": row["event_type"],
        "trade_id": row["trade_id"], "event_seq": row["event_seq"],
        "payload_sha256": row["payload_sha256"], "signal_cycle_event_id": "evt-db",
        "platform_received_at": "2026-09-03T00:00:01Z",
    }
    value.update(overrides)
    return {"persisted": True, "durable_ack": value}


def test_enqueue_is_durable_before_delivery_and_survives_restart(tmp_path: Path):
    path = tmp_path / "outbox.json"
    first = RelayEventOutbox(path)
    row = first.enqueue(payload())
    assert path.exists() and json.loads(path.read_text())["pending"]
    restarted = RelayEventOutbox(path)
    assert restarted.pending_count() == 1
    assert restarted.due()[0]["payload_sha256"] == row["payload_sha256"]


@pytest.mark.parametrize("field,value", [
    ("event_id", "wrong"), ("event_type", "LIMIT_UPDATED"),
    ("trade_id", "other"), ("event_seq", 9), ("payload_sha256", "0" * 64),
])
def test_receipt_mismatch_never_removes_source_event(tmp_path: Path, field, value):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    row = box.enqueue(payload())
    assert box.acknowledge(row["event_id"], ack(row, **{field: value})) is False
    assert box.pending_count() == 1


def test_exact_ack_removes_pending_and_is_restart_durable(tmp_path: Path):
    path = tmp_path / "outbox.json"
    box = RelayEventOutbox(path)
    row = box.enqueue(payload())
    assert box.acknowledge(row["event_id"], ack(row))
    assert RelayEventOutbox(path).pending_count() == 0


def test_timeout_backoff_and_idle_have_no_external_query(tmp_path: Path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    assert box.due(now=10) == []
    row = box.enqueue(payload())
    box.fail(row["event_id"], "timeout", now=10)
    assert box.due(now=10) == []
    assert box.due(now=12)[0]["event_id"] == row["event_id"]


def test_replay_and_conflict_are_idempotent(tmp_path: Path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    row = box.enqueue(payload())
    assert box.enqueue(payload())["payload_sha256"] == row["payload_sha256"]
    with pytest.raises(ValueError, match="conflicting"):
        box.enqueue({**payload(), "ts": "different"})


def test_terminal_event_is_top_level_and_sequences_are_monotonic(tmp_path: Path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    first = box.next_sequence("trade", 4)
    box.enqueue(payload("trade:POSITION_CLOSED:4:t", "POSITION_CLOSED", first))
    assert box.next_sequence("trade", 1) == 5


def test_concurrent_same_event_creates_one_pending_record(tmp_path: Path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    threads = [threading.Thread(target=lambda: box.enqueue(payload())) for _ in range(8)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert box.pending_count() == 1


def test_all_required_event_classes_are_accepted(tmp_path: Path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    events = ["ORDER_PLACED", "LIMIT_UPDATED", "POSITION_OPENED", "POSITION_REDUCED", "POSITION_CLOSED", "ORDER_EXPIRED", "ORDER_CANCELLED"]
    for seq, event in enumerate(events):
        row = payload(f"trade-{seq}:{event}:{seq}:t", event, seq)
        row["trade_id"] = f"trade-{seq}"
        box.enqueue(row)
    assert [row["event_type"] for row in box.due()] == events


def test_trade_head_backoff_blocks_reorder_without_starving_other_trade(tmp_path: Path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    first = box.enqueue(payload("trade:first:0", "ORDER_PLACED", 0))
    box.enqueue(payload("trade:second:1", "LIMIT_UPDATED", 1))
    peer = payload("peer:first:0", "ORDER_PLACED", 0)
    peer["trade_id"] = "peer"
    box.enqueue(peer)
    box.fail(first["event_id"], "timeout", now=10)
    assert [row["trade_id"] for row in box.due(now=10)] == ["peer"]
