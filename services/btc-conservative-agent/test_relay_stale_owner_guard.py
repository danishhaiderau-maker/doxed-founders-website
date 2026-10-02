"""Stale-owner and pre-arming relay events can never be delivered."""
import ast
import json
import threading
import time
from pathlib import Path

import pytest

import relay_delivery_guard as guard_mod
from relay_delivery_guard import RelayDeliveryGuard, hold_reason
from relay_event_outbox import RelayEventOutbox

BOT = Path(__file__).with_name("bot.py")


def enqueue(box, trade, owner, seq=0):
    payload = {"event": "LIMIT_UPDATED", "trade_id": trade,
               "event_id": f"{trade}:{seq}", "event_seq": seq, "ts": "test"}
    if owner is not None:
        payload["bot_instance_id"] = owner
    return box.enqueue_next(payload, suggested=seq)


def load_functions(names, ns):
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot.py", "exec"), ns)
    return ns


def drain_ns(box, guard, *, live=False, armed_at=None, active=True):
    sent = []
    state = {"live_armed": live, "bitfinex_live_enabled": live}
    if armed_at is not None:
        state["live_armed_at_ts"] = armed_at
    ns = load_functions(["_drain_relay_event_outbox_once"], {
        "_relay_event_drain_lock": threading.Lock(), "state_lock": threading.RLock(),
        "state": state, "_force_paper_mode_active": lambda: not live,
        "BOT_INSTANCE_ID": "current-owner", "is_active_dashboard_owner": lambda: active,
        "_relay_event_outbox": box, "_relay_push_state": {}, "_relay_delivery_guard": guard,
        "_deliver_relay_outbox_record": lambda row, **kwargs: sent.append(row["event_id"]) or False,
    })
    return ns, sent


@pytest.mark.parametrize("record, kwargs, expected", [
    ({"bot_instance_id": "me", "created_at_unix": 5}, {"owner_id": "me", "armed": False, "armed_at_ts": None}, None),
    ({"bot_instance_id": "old", "created_at_unix": 5}, {"owner_id": "me", "armed": False, "armed_at_ts": None}, "STALE_OWNER"),
    ({"payload": {"bot_instance_id": "old"}}, {"owner_id": "me", "armed": False, "armed_at_ts": None}, "STALE_OWNER"),
    ({"created_at_unix": 5}, {"owner_id": "me", "armed": False, "armed_at_ts": None}, "MISSING_OWNER"),
    ({"bot_instance_id": "me"}, {"owner_id": None, "armed": False, "armed_at_ts": None}, "OWNER_UNVERIFIED"),
    ({"bot_instance_id": " me "}, {"owner_id": " me ", "armed": False, "armed_at_ts": None}, "OWNER_UNVERIFIED"),
    ({"bot_instance_id": "me", "created_at_unix": 5}, {"owner_id": "me", "armed": True, "armed_at_ts": 10}, "PRE_ARMING"),
    ({"bot_instance_id": "me", "created_at_unix": 0}, {"owner_id": "me", "armed": True, "armed_at_ts": 10}, "PRE_ARMING"),
    ({"bot_instance_id": "me", "created_at_unix": 15}, {"owner_id": "me", "armed": True, "armed_at_ts": 10}, None),
    ({"bot_instance_id": "me", "created_at_unix": 15}, {"owner_id": "me", "armed": True, "armed_at_ts": None}, "ARMING_TS_UNKNOWN"),
    ({"bot_instance_id": "old", "created_at_unix": 15}, {"owner_id": "me", "armed": True, "armed_at_ts": 10}, "STALE_OWNER"),
])
def test_hold_reason_matrix(record, kwargs, expected):
    assert hold_reason(record, **kwargs) == expected


@pytest.mark.parametrize("live", [False, True])
def test_stale_owner_event_is_never_delivered_and_never_deleted(tmp_path, live):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    armed_at = time.time() - 1000
    stale = enqueue(box, "old", "old-owner")
    before = box.path.read_bytes()
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE)
    ns, sent = drain_ns(box, guard, live=live, armed_at=armed_at if live else None)
    for _ in range(3):
        ns["_drain_relay_event_outbox_once"]()
        ns["_drain_relay_event_outbox_once"](stale["event_id"])
    assert sent == []
    assert box.path.read_bytes() == before
    assert box.pending_count() == 1
    rows = [json.loads(line) for line in guard.path.read_text(encoding="utf-8").splitlines()]
    assert [(row["event_id"], row["reason"], row["outbox_record_retained"]) for row in rows] == [
        (stale["event_id"], "STALE_OWNER", True)
    ]


def test_quarantine_is_sticky_across_restart_and_owner_change(tmp_path):
    path = tmp_path / guard_mod.QUARANTINE_FILE
    record = {"event_id": "e1", "bot_instance_id": "old-owner", "created_at_unix": 100.0}
    guard = RelayDeliveryGuard(path)
    assert guard.filter_deliverable([record], owner_id="new-owner", armed=False, armed_at_ts=None) == []
    reloaded = RelayDeliveryGuard(path)
    # Even if the original owner identity were somehow current again, the hold stands.
    assert reloaded.filter_deliverable([record], owner_id="old-owner", armed=False, armed_at_ts=None) == []
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_pre_arming_event_is_never_delivered_even_after_disarm(tmp_path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    early = enqueue(box, "early", "current-owner")
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE)
    ns, sent = drain_ns(box, guard, live=True, armed_at=time.time() + 1000)
    ns["_drain_relay_event_outbox_once"]()
    assert sent == []
    ns["state"].update({"live_armed": False, "bitfinex_live_enabled": False})
    ns["state"].pop("live_armed_at_ts")
    ns["_drain_relay_event_outbox_once"]()
    assert sent == []
    assert box.pending_count() == 1
    assert guard.status()["quarantined_events_total"] == 1
    assert early["event_id"] in guard.path.read_text(encoding="utf-8")


def test_post_arming_current_owner_event_is_delivered(tmp_path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    armed_at = time.time() - 1000
    fresh = enqueue(box, "fresh", "current-owner")
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE)
    ns, sent = drain_ns(box, guard, live=True, armed_at=armed_at)
    ns["_drain_relay_event_outbox_once"]()
    assert sent == [fresh["event_id"]]


def test_armed_without_timestamp_holds_but_does_not_quarantine(tmp_path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    enqueue(box, "fresh", "current-owner")
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE)
    ns, sent = drain_ns(box, guard, live=True, armed_at=None)
    ns["_drain_relay_event_outbox_once"]()
    assert sent == []
    assert guard.status()["quarantined_events_total"] == 0


def test_arming_gate_requires_fresh_verified_observation(tmp_path):
    clock = {"now": 1000.0}
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE, clock=lambda: clock["now"])
    assert guard.arming_block_reason() == guard_mod.ARM_BLOCK_UNOBSERVED
    guard.observe([], owner_id=None, armed=False, armed_at_ts=None)
    assert guard.arming_block_reason() == guard_mod.ARM_BLOCK_OWNER_UNVERIFIED
    guard.observe([], owner_id="me", armed=False, armed_at_ts=None)
    assert guard.arming_block_reason() is None
    clock["now"] += guard_mod.OBSERVATION_MAX_AGE_SEC + 1
    assert guard.arming_block_reason() == guard_mod.ARM_BLOCK_UNOBSERVED


def test_arming_refused_while_stale_event_cannot_be_quarantined(tmp_path):
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("file", encoding="utf-8")
    guard = RelayDeliveryGuard(blocked / guard_mod.QUARANTINE_FILE)
    stale = {"event_id": "e1", "bot_instance_id": "old", "created_at_unix": time.time()}
    observed = guard.observe([stale], owner_id="me", armed=False, armed_at_ts=None)
    assert observed["unquarantined_held_pending"] == 1
    assert guard.arming_block_reason() == guard_mod.ARM_BLOCK_UNQUARANTINED
    assert guard.status()["quarantine_write_failures"] >= 1
    assert guard.filter_deliverable([stale], owner_id="me", armed=False, armed_at_ts=None) == []


def test_quarantined_stale_backlog_does_not_block_arming(tmp_path):
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE)
    stale = [{"event_id": f"e{i}", "bot_instance_id": "old", "created_at_unix": time.time()} for i in range(22)]
    observed = guard.observe(stale, owner_id="me", armed=False, armed_at_ts=None)
    assert observed["stale_owner_pending"] == 22 and observed["quarantined_pending"] == 22
    assert guard.arming_block_reason() is None


def test_stale_owner_alarm_and_health_fields(tmp_path):
    clock = {"now": 10_000.0}
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE, clock=lambda: clock["now"])
    stale = {"event_id": "e1", "bot_instance_id": "old", "created_at_unix": 9_000.0}
    guard.observe([stale], owner_id="me", armed=False, armed_at_ts=None, last_ack_ts=9_500.0)
    status = guard.status()
    assert status["stale_owner_pending"] == 1
    assert status["oldest_pending_age_sec"] == 1000.0
    assert status["last_ack_age_sec"] == 500.0
    assert status["stale_owner_alarm"] is False
    clock["now"] += guard_mod.STALE_OWNER_ALARM_SEC + 1
    guard.observe([stale], owner_id="me", armed=False, armed_at_ts=None)
    assert guard.stale_owner_alarm() is True
    guard.observe([], owner_id="me", armed=False, armed_at_ts=None)
    assert guard.stale_owner_alarm() is False


def arm_ns(guard, armable=True):
    saved = []
    return load_functions(["_arm_live_control"], {
        "_refresh_bitfinex_exposure_audit": lambda: {},
        "_live_control_lock": threading.Lock(), "state_lock": threading.RLock(),
        "state": {"live_armed": False, "bitfinex_live_enabled": False},
        "can_open_live_entry": lambda require_armed: (armable, "READY" if armable else "FORCE_PAPER_MODE", {}),
        "_relay_delivery_guard": guard, "save_persistent_config": lambda: saved.append(1),
        "time": time,
    })


def test_arm_live_control_refuses_while_guard_blocks(tmp_path):
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE)
    ns = arm_ns(guard)
    ok, reason, _, _ = ns["_arm_live_control"]()
    assert ok is False and reason == guard_mod.ARM_BLOCK_UNOBSERVED
    assert ns["state"]["live_armed"] is False and "live_armed_at_ts" not in ns["state"]


def test_arm_live_control_stamps_arming_time_when_clear(tmp_path):
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE)
    guard.observe([], owner_id="me", armed=False, armed_at_ts=None)
    ns = arm_ns(guard)
    before = time.time()
    ok, reason, _, _ = ns["_arm_live_control"]()
    assert ok is True and reason == "READY"
    assert ns["state"]["live_armed"] is True
    assert ns["state"]["live_armed_at_ts"] >= before


def test_existing_entry_gate_still_runs_first(tmp_path):
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE)
    guard.observe([], owner_id="me", armed=False, armed_at_ts=None)
    ok, reason, _, _ = arm_ns(guard, armable=False)["_arm_live_control"]()
    assert ok is False and reason == "FORCE_PAPER_MODE"


def test_status_and_ready_armable_apply_relay_gate():
    source = BOT.read_text(encoding="utf-8")
    assert source.count("relay_arm_block = _relay_delivery_guard.arming_block_reason(now)") == 2
    assert "_relay_event_outbox.due(" not in source
