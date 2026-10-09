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


class _OutputView:
    """Test stand-in for live_copy_control.LiveCopyOutput (reads scenario state)."""

    def __init__(self, state):
        self._state = state

    @property
    def enabled(self):
        return bool(self._state.get("live_armed") and self._state.get("bitfinex_live_enabled"))

    @property
    def enabled_at_ts(self):
        return self._state.get("live_armed_at_ts")


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


def drain_ns(box, guard, *, live=False, armed_at=None, active=True, epoch=None):
    sent = []
    state = {"live_armed": live, "bitfinex_live_enabled": live}
    if armed_at is not None:
        state["live_armed_at_ts"] = armed_at
    ns = load_functions(["_drain_relay_event_outbox_once"], {
        "_relay_outbox_data_epoch": lambda: epoch,
        "_relay_event_drain_lock": threading.Lock(), "state_lock": threading.RLock(),
        "state": state, "_force_paper_mode_active": lambda: not live,
        "BOT_INSTANCE_ID": "current-owner", "is_active_dashboard_owner": lambda: active,
        "_relay_event_outbox": box, "_relay_push_state": {}, "_relay_delivery_guard": guard,
        # Option 1: the drain's "armed" state is Fly's Live copy output switch;
        # modelled here from the same state flags the scenarios set.
        "_get_live_copy_output": lambda: _OutputView(state),
        "_deliver_relay_outbox_record": lambda row, **kwargs: sent.append(row["event_id"]) or False,
        # Per-tile live-orders switch is exercised separately (test_two_tier_relay_gate).
        "_filter_relay_rows_by_live_switch": lambda rows, armed=False, now=None: list(rows),
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


# ------------------------------------------------- pre-epoch retirement (relay_outbox_retirement_v1)
EPOCH_START = 1_791_000_000.0  # data epoch start (final-e style)
EPOCH = {"epoch_id": "ce-test-final-e", "started_at_ts": EPOCH_START}


def _backlog(n=22, created=EPOCH_START - 6 * 86400, owner="dashboard-7002-pid-662-old"):
    return [{"event_id": f"t{i}:0", "trade_id": f"t{i}", "event_type": "LIMIT_UPDATED", "event_seq": 0,
             "payload_sha256": f"{i:064x}", "created_at_unix": created + i, "bot_instance_id": owner}
            for i in range(n)]


def test_pre_epoch_stale_owner_backlog_retires_and_alarm_clears(tmp_path):
    clock = {"now": EPOCH_START + 3600}
    qpath = tmp_path / guard_mod.QUARANTINE_FILE
    guard = RelayDeliveryGuard(qpath, clock=lambda: clock["now"])
    backlog = _backlog()
    # Live today: quarantined on an earlier pass, alarm latched after 30 min.
    guard.observe(backlog, owner_id="me", armed=False, armed_at_ts=None)
    clock["now"] += guard_mod.STALE_OWNER_ALARM_SEC + 1
    guard.observe(backlog, owner_id="me", armed=False, armed_at_ts=None)
    assert guard.stale_owner_alarm() is True
    quarantine_before = qpath.read_bytes()

    observed = guard.observe(backlog, owner_id="me", armed=False, armed_at_ts=None, epoch=EPOCH)
    assert observed["retired_now"] == 22 and observed["retired_pre_epoch_pending"] == 22
    assert observed["stale_owner_pending"] == 0 and observed["pending_total"] == 0
    assert guard.stale_owner_alarm() is False
    status = guard.status()
    assert status["stale_owner_alarm"] is False and status["retired_pre_epoch_total"] == 22
    assert status["retirement_ledger"] == guard_mod.RETIRED_FILE and status["last_retired_ts"] == round(clock["now"], 3)
    assert qpath.read_bytes() == quarantine_before  # quarantine ledger untouched
    rows = [json.loads(line) for line in guard.retired_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 22
    assert set(rows[0]) >= {"event_id", "trade_id", "event_type", "event_seq", "payload_sha256", "created_at_unix",
                            "bot_instance_id", "quarantine_reason", "retired_reason", "data_epoch_id",
                            "data_epoch_started_at", "retired_at_unix"}
    assert {(r["schema"], r["retired_reason"], r["quarantine_reason"], r["data_epoch_id"]) for r in rows} == {
        (guard_mod.RETIREMENT_SCHEMA, "PRE_EPOCH_STALE_OWNER", "STALE_OWNER", "ce-test-final-e")}
    assert rows[0]["data_epoch_started_at"] == "2026-10-03T04:00:00Z"


def test_in_epoch_stale_owner_event_still_alarms(tmp_path):
    clock = {"now": EPOCH_START + 7200}
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE, clock=lambda: clock["now"])
    fresh = _backlog(1, created=EPOCH_START + 60)  # stale owner, but born inside the epoch
    guard.observe(fresh, owner_id="me", armed=False, armed_at_ts=None, epoch=EPOCH)
    clock["now"] += guard_mod.STALE_OWNER_ALARM_SEC + 1
    observed = guard.observe(fresh, owner_id="me", armed=False, armed_at_ts=None, epoch=EPOCH)
    assert observed["retired_now"] == 0 and observed["stale_owner_pending"] == 1
    assert guard.stale_owner_alarm() is True
    assert not guard.retired_path.exists()


@pytest.mark.parametrize("case", ["armed", "no_epoch", "owner_unverified", "not_quarantined_yet_pre_arming"])
def test_retirement_preconditions(tmp_path, case):
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE, clock=lambda: EPOCH_START + 3600)
    backlog = _backlog(2)
    kwargs = {"owner_id": "me", "armed": False, "armed_at_ts": None, "epoch": EPOCH}
    if case == "armed":
        kwargs.update(armed=True, armed_at_ts=EPOCH_START)
    elif case == "no_epoch":
        kwargs["epoch"] = None
    elif case == "owner_unverified":
        kwargs["owner_id"] = None
    else:  # current owner, pre-arming hold: sticky but never a stale-owner retirement
        backlog = _backlog(2, owner="me")
        kwargs.update(armed=True, armed_at_ts=EPOCH_START + 10)
    observed = guard.observe(backlog, **kwargs)
    assert observed["retired_now"] == 0 and guard.status()["retired_pre_epoch_total"] == 0


def test_retired_event_is_never_deliverable_even_after_arming(tmp_path):
    box = RelayEventOutbox(tmp_path / "outbox.json")
    stale = enqueue(box, "old", "old-owner")
    with box._lock:  # the live backlog was created days before the epoch boundary
        box._pending[stale["event_id"]]["created_at_unix"] = EPOCH_START - 86400
        box._persist()
    before = box.path.read_bytes()
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE)
    ns, sent = drain_ns(box, guard, epoch=EPOCH)
    ns["_drain_relay_event_outbox_once"]()
    ns["_drain_relay_event_outbox_once"]()
    assert guard.status()["retired_pre_epoch_total"] == 1
    counts = ns["_relay_push_state"]["delivery_scheduler"]["counts"]
    assert counts["stale_owner_pending"] == 0 and counts["retired_pre_epoch_pending"] == 1
    assert counts["pending_total"] == 0
    # Arm (and even present the original owner identity): still held, still retained, never re-signed.
    ns["state"].update({"live_armed": True, "bitfinex_live_enabled": True, "live_armed_at_ts": EPOCH_START - 10 ** 6})
    ns["BOT_INSTANCE_ID"] = "old-owner"
    for _ in range(3):
        ns["_drain_relay_event_outbox_once"]()
        ns["_drain_relay_event_outbox_once"](stale["event_id"])
    assert sent == []
    assert box.pending_count() == 1 and box.path.read_bytes() == before
    assert guard.filter_deliverable([{**stale, "bot_instance_id": "old-owner"}], owner_id="old-owner",
                                    armed=True, armed_at_ts=1.0) == []


def test_retirement_is_idempotent_across_restart(tmp_path):
    qpath = tmp_path / guard_mod.QUARANTINE_FILE
    backlog = _backlog()
    guard = RelayDeliveryGuard(qpath, clock=lambda: EPOCH_START + 3600)
    guard.observe(backlog, owner_id="me", armed=False, armed_at_ts=None, epoch=EPOCH)
    ledger = guard.retired_path.read_bytes()
    quarantine = qpath.read_bytes()
    restarted = RelayDeliveryGuard(qpath, clock=lambda: EPOCH_START + 7200)
    assert restarted.status()["retired_pre_epoch_total"] == 22
    assert restarted.status()["last_retired_ts"] == EPOCH_START + 3600
    observed = restarted.observe(backlog, owner_id="me-after-restart", armed=False, armed_at_ts=None, epoch=EPOCH)
    assert observed["retired_now"] == 0 and observed["retired_pre_epoch_pending"] == 22
    assert observed["stale_owner_pending"] == 0
    assert restarted.retired_path.read_bytes() == ledger and qpath.read_bytes() == quarantine


def test_retirement_write_failure_keeps_alarm(tmp_path):
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("file", encoding="utf-8")
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE, clock=lambda: EPOCH_START + 3600,
                               retired_path=blocked / guard_mod.RETIRED_FILE)
    observed = guard.observe(_backlog(3), owner_id="me", armed=False, armed_at_ts=None, epoch=EPOCH)
    assert observed["retired_now"] == 0 and observed["stale_owner_pending"] == 3
    assert guard.status()["retirement_write_failures"] == 1


def test_delivery_plan_counts_retired_separately_and_keeps_them_blocking():
    box = RelayEventOutbox.__new__(RelayEventOutbox)
    RelayEventOutbox.__init__(box, Path("/nonexistent/never-written.json"))
    rows = {
        "a:0": {"event_id": "a:0", "trade_id": "a", "event_seq": 0, "created_at_unix": 1.0,
                "payload": {"bot_instance_id": "old"}},
        "a:1": {"event_id": "a:1", "trade_id": "a", "event_seq": 1, "created_at_unix": 2.0,
                "payload": {"bot_instance_id": "me"}},
        "b:0": {"event_id": "b:0", "trade_id": "b", "event_seq": 0, "created_at_unix": 3.0,
                "payload": {"bot_instance_id": "me"}},
    }
    box._pending = rows
    plan = box.delivery_plan(now=10.0, enforce_owner=True, active_owner_id="me",
                             retired_event_ids=frozenset({"a:0"}))
    assert plan["counts"]["retired_pre_epoch_pending"] == 1
    assert plan["counts"]["stale_owner_pending"] == 0 and plan["counts"]["pending_total"] == 2
    assert plan["counts"]["blocked_by_retired_predecessor"] == 1
    assert [r["event_id"] for r in plan["records"]] == ["b:0"]
    legacy = box.delivery_plan(now=10.0, enforce_owner=True, active_owner_id="me")
    assert legacy["counts"]["stale_owner_pending"] == 1 and legacy["counts"]["retired_pre_epoch_pending"] == 0
