import ast
import copy
import json
from pathlib import Path
import threading

import pytest

from relay_event_outbox import RelayEventOutbox


def enqueue(box, trade, owner, seq=0):
    payload = {"event": "LIMIT_UPDATED", "trade_id": trade,
               "event_id": f"{trade}:{seq}", "event_seq": seq, "ts": "test"}
    if owner is not None:
        payload["bot_instance_id"] = owner
    return box.enqueue_next(payload, suggested=seq)


def forbidden(*args, **kwargs):
    raise AssertionError("Scheduling must not persist, fail or acknowledge")


def test_stale_head_blocks_current_successor_without_blocking_other_trade(tmp_path, monkeypatch):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    old = enqueue(box, "restarted-trade", "old-owner")
    later = enqueue(box, "restarted-trade", "current-owner", 1)
    current = enqueue(box, "new-trade", "current-owner")
    before = box.path.read_bytes()
    pending = copy.deepcopy(box._pending)
    highwater = copy.deepcopy(box._highwater)
    monkeypatch.setattr(box, "_persist", forbidden)
    monkeypatch.setattr(box, "fail", forbidden)
    monkeypatch.setattr(box, "acknowledge", forbidden)

    plan = box.delivery_plan(enforce_owner=True, active_owner_id="current-owner")
    assert [row["event_id"] for row in plan["records"]] == [current["event_id"]]
    assert plan["counts"]["pending_total"] == 3
    assert plan["counts"]["stale_owner_pending"] == 1
    assert plan["counts"]["blocked_by_stale_owner_predecessor"] == 1
    assert plan["source_cleanup_authorized"] is False
    for row, reason in [(old, "STALE_OWNER_PENDING"), (later, "BLOCKED_BY_STALE_OWNER_PREDECESSOR")]:
        target = box.delivery_plan(enforce_owner=True, active_owner_id="current-owner", event_id=row["event_id"])
        assert target["records"] == []
        assert target["target_block_reason"] == reason
    assert box.path.read_bytes() == before
    assert box._pending == pending and box._highwater == highwater
    restarted = RelayEventOutbox(box.path)
    assert restarted._pending == pending and restarted._highwater == highwater
    assert restarted.pending_count() == 3


def test_filter_before_batch_limit_preserves_unrelated_current_delivery(tmp_path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    for index in range(105):
        enqueue(box, f"old-{index}", "old-owner")
    first = enqueue(box, "new-1", "current-owner")
    second = enqueue(box, "new-2", "current-owner")
    plan = box.delivery_plan(limit=1, enforce_owner=True, active_owner_id="current-owner")
    assert [row["event_id"] for row in plan["records"]] == [first["event_id"]]
    assert plan["counts"]["stale_owner_pending"] == 105
    assert plan["counts"]["ready_trade_heads"] == 2
    target = box.delivery_plan(limit=1, enforce_owner=True, active_owner_id="current-owner", event_id=second["event_id"])
    assert [row["event_id"] for row in target["records"]] == [second["event_id"]]
    assert box.pending_count() == 107


@pytest.mark.parametrize("owner", [None, "", " current-owner ", 1])
def test_unknown_active_owner_withholds_all_without_mutation(tmp_path, owner):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    row = enqueue(box, "new", "current-owner")
    before = box.path.read_bytes()
    plan = box.delivery_plan(enforce_owner=True, active_owner_id=owner, event_id=row["event_id"])
    assert plan["records"] == []
    assert plan["target_block_reason"] == "OWNER_UNVERIFIED_PENDING"
    assert plan["counts"]["owner_unverified_pending"] == 1
    assert box.path.read_bytes() == before


def test_owner_change_reclassifies_without_rewriting_or_forging_ack(tmp_path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    row = enqueue(box, "trade", "owner-a")
    before = box.path.read_bytes()
    assert box.delivery_plan(enforce_owner=True, active_owner_id="owner-a")["records"] == [row]
    plan = box.delivery_plan(enforce_owner=True, active_owner_id="owner-b")
    assert plan["records"] == [] and plan["counts"]["stale_owner_pending"] == 1
    assert box.path.read_bytes() == before and box._acks == []


def test_missing_payload_owner_is_distinct_and_blocks_successor(tmp_path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    enqueue(box, "trade", None)
    successor = enqueue(box, "trade", "current-owner", 1)
    plan = box.delivery_plan(enforce_owner=True, active_owner_id="current-owner", event_id=successor["event_id"])
    assert plan["records"] == []
    assert plan["counts"]["missing_owner_pending"] == 1
    assert plan["target_block_reason"] == "BLOCKED_BY_MISSING_OWNER_PREDECESSOR"


def test_current_owner_backoff_and_sequence_remain_enforced(tmp_path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    head = enqueue(box, "trade", "current-owner")
    enqueue(box, "trade", "current-owner", 1)
    box.fail(head["event_id"], "401 is not proof of wrong owner", now=100)
    before = box.path.read_bytes()
    plan = box.delivery_plan(now=100.5, enforce_owner=True, active_owner_id="current-owner", event_id=head["event_id"])
    assert plan["records"] == [] and plan["target_block_reason"] == "RETRY_BACKOFF"
    assert plan["counts"]["stale_owner_pending"] == 0
    plan = box.delivery_plan(now=102, enforce_owner=True, active_owner_id="current-owner")
    assert [row["event_id"] for row in plan["records"]] == [head["event_id"]]
    assert box.path.read_bytes() == before


def test_default_due_keeps_legacy_unfiltered_behavior(tmp_path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    old = enqueue(box, "old", "old-owner")
    missing = enqueue(box, "missing", None)
    assert [row["event_id"] for row in box.due()] == [old["event_id"], missing["event_id"]]


def load_drain(box, *, active=True, force_paper=True, live=False, enabled=False):
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "_drain_relay_event_outbox_once"]
    sent = []
    ns = {
        "_relay_event_drain_lock": threading.Lock(), "state_lock": threading.RLock(),
        "state": {"live_armed": live, "bitfinex_live_enabled": enabled},
        "_force_paper_mode_active": lambda: force_paper,
        "BOT_INSTANCE_ID": "current-owner", "is_active_dashboard_owner": lambda: active,
        "_relay_event_outbox": box, "_relay_push_state": {},
        "_deliver_relay_outbox_record": lambda row, **kwargs: sent.append(row["event_id"]) or False,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), "bot.py", "exec"), ns)
    return ns, sent


def test_actual_paper_drain_withholds_stale_without_network_or_disk(tmp_path, monkeypatch):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    stale = enqueue(box, "old", "old-owner")
    current = enqueue(box, "new", "current-owner")
    before = box.path.read_bytes()
    monkeypatch.setattr(box, "fail", forbidden)
    monkeypatch.setattr(box, "_persist", forbidden)
    ns, sent = load_drain(box)
    result = ns["_drain_relay_event_outbox_once"](stale["event_id"])
    assert result == {"attempted": 0, "acked": 0, "busy": False}
    assert sent == []
    assert ns["_relay_push_state"]["delivery_scheduler"]["target_block_reason"] == "STALE_OWNER_PENDING"
    ns["_drain_relay_event_outbox_once"]()
    assert sent == [current["event_id"]]
    assert box.path.read_bytes() == before
    assert len(json.loads(before)["pending"]) == 2


def test_lost_singleton_does_not_guess_a_deliverable_owner(tmp_path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    enqueue(box, "new", "current-owner")
    ns, sent = load_drain(box, active=False)
    assert ns["_drain_relay_event_outbox_once"]()["attempted"] == 0
    assert sent == []
    assert ns["_relay_push_state"]["delivery_scheduler"]["counts"]["owner_unverified_pending"] == 1


@pytest.mark.parametrize("flags", [
    {"force_paper": False}, {"live": True}, {"enabled": True}, {"live": None},
])
def test_filter_never_changes_nonconfirmed_paper_money_path(tmp_path, flags):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    old = enqueue(box, "old", "old-owner")
    ns, sent = load_drain(box, **flags)
    ns["_drain_relay_event_outbox_once"]()
    assert sent == [old["event_id"]]
    assert "delivery_scheduler" not in ns["_relay_push_state"]


def test_leaving_paper_scope_clears_old_filter_diagnostic(tmp_path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    old = enqueue(box, "old", "old-owner")
    ns, sent = load_drain(box)
    ns["_drain_relay_event_outbox_once"]()
    assert sent == []
    assert ns["_relay_push_state"]["delivery_scheduler"]["owner_filter_applied"] is True
    ns["state"]["live_armed"] = True
    ns["_drain_relay_event_outbox_once"]()
    assert sent == [old["event_id"]]
    assert "delivery_scheduler" not in ns["_relay_push_state"]
