import ast
import copy
import errno
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from combo_pathway_config import COMBO_LANE_SPECS
from relay_event_outbox import RelayEventOutbox


def fixture(tmp_path, lane):
    names = {"_commit_local_paper_lifecycle_transition", "_commit_paper_lifecycle_transition", "_commit_relay_limit_chase", "_apply_family_policy_chase"}
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    order = {"trade_id": COMBO_LANE_SPECS[lane]["id_prefix"] + "-test", "research_lane": lane,
             "status": "PENDING", "qty": 0.123, "original_requested_qty": 0.2,
             "limit_price": 90.0, "original_limit_price": 80.0, "limit_chase_count": 0,
             "created_ts": 10, "signal_dir": "LONG", "relay_eligible": False}
    signal = copy.deepcopy(order); paused = []; schedules = []; relay = []
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    def build(reason):
        return {"schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False,
                "pending_orders": [copy.deepcopy(order)], "positions": [], "reason": reason}
    outbox._atomic_write(outbox.decorate_lifecycle(build("initial")))
    ns = {"copy": copy, "COMBO_LANE_SPECS": COMBO_LANE_SPECS,
          "PLATFORM_RELAY_ELIGIBLE_LANES": frozenset({"CONTINUOUS"}),
          "RELAY_CANONICAL_TRANSITION_EVENTS": frozenset({"ORDER_PLACED", "LIMIT_UPDATED", "POSITION_OPENED", "POSITION_REDUCED", "POSITION_CLOSED", "ORDER_CANCELLED", "ORDER_EXPIRED"}),
          "state": {"live_armed": False, "bitfinex_live_enabled": False, "bid": 99, "ask": 101},
          "trade_lock": threading.RLock(), "state_lock": threading.RLock(),
          "paper_lifecycle_transition_lock": threading.RLock(), "paper_lifecycle_file_lock": threading.RLock(),
          "pending_orders": [order], "open_positions": [], "_relay_event_outbox": outbox,
          "is_active_dashboard_owner": lambda: True, "_force_paper_mode_active": lambda: True,
          "_build_paper_lifecycle_payload": build, "set_execution_paused": paused.append,
          "utc_iso": lambda: "test", "_resolve_fill_model": lambda *args: {"kind": "depth"},
          "_build_showcase_relay_event_payload": lambda *a, **k: relay.append(a),
          "append_research_reprice_interval": lambda *a, **k: schedules.append(k),
          "is_patient_chase_lane": lambda _: True, "_normalize_lane_key": lambda row: row["research_lane"],
          "_patient_chase_policy": lambda _: SimpleNamespace(chase_due=lambda **k: True,
              marketable_quote_at_limit=lambda **k: False, CHASE_STEP=0.5),
          "chase_age_window_may_reprice": lambda _: True,
          "_normalize_order_side_to_dir": lambda _: "LONG",
          "_compute_limit_chase_target": lambda *a, **k: (95.0, "LIMIT_CHASE"),
          "_limit_chase_market_gap": lambda *a: 5.0, "fmt": str,
          "logger": SimpleNamespace(info=lambda *a: None, debug=lambda *a: None)}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "bot.py", "exec"), ns)
    return ns, order, signal, outbox, paused, schedules, relay


@pytest.mark.parametrize("lane", list(COMBO_LANE_SPECS))
def test_actual_family_chase_commits_snapshot_without_relay(tmp_path, lane):
    ns, order, signal, outbox, paused, schedules, relay = fixture(tmp_path, lane)
    assert ns["_apply_family_policy_chase"](order, signal, 100.0, 200.0) is True
    saved = json.loads(outbox.path.read_text())
    assert saved["pending_orders"][0]["limit_price"] == order["limit_price"] == 95.0
    assert saved["pending_orders"][0]["qty"] == 0.123
    assert saved["pending_orders"][0]["original_requested_qty"] == 0.2
    assert order["original_limit_price"] == 80 and order["relay_eligible"] is False
    assert saved["generation"] == 1 and saved["relay_events"]["pending"] == []
    assert outbox.pending_count() == 0 and relay == [] and paused == []
    assert schedules[0]["chase_step_index"] == 1 and schedules[0]["limit_price"] == 95
    assert RelayEventOutbox(outbox.path).healthy


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EACCES])
def test_disk_failure_preserves_live_and_old_snapshot(tmp_path, monkeypatch, code):
    ns, order, signal, outbox, paused, schedules, relay = fixture(tmp_path, next(iter(COMBO_LANE_SPECS)))
    before = outbox.path.read_bytes()
    def fail(*args): raise OSError(code, "injected")
    monkeypatch.setattr(outbox, "_atomic_write", fail)
    with pytest.raises(RuntimeError, match="local paper lifecycle commit failed"):
        ns["_apply_family_policy_chase"](order, signal, 100, 200)
    assert outbox.path.read_bytes() == before and order["limit_price"] == 90
    assert signal["limit_price"] == 90 and schedules == [] and relay == []
    assert paused == ["PAPER_LIFECYCLE_COMMIT_FAILED"]


@pytest.mark.parametrize("defect", ["armed", "exchange", "prefix", "lane", "non_owner", "not_paper"])
def test_local_authority_never_promotes_invalid_or_live_chase(tmp_path, defect):
    ns, order, signal, outbox, paused, schedules, relay = fixture(tmp_path, next(iter(COMBO_LANE_SPECS)))
    if defect == "armed": ns["state"]["live_armed"] = True
    if defect == "exchange": order["bitfinex_order_id"] = "external"
    if defect == "prefix": order["trade_id"] = "cont-test"
    if defect == "lane": signal["research_lane"] = "CONTINUOUS"
    if defect == "non_owner": ns["is_active_dashboard_owner"] = lambda: False
    if defect == "not_paper": ns["_force_paper_mode_active"] = lambda: False
    before = outbox.path.read_bytes()
    with pytest.raises(RuntimeError, match="authority rejected|local paper lifecycle commit failed"):
        ns["_apply_family_policy_chase"](order, signal, 100, 200)
    assert outbox.path.read_bytes() == before and order["limit_price"] == 90 and relay == []


def test_crash_after_durable_snapshot_recovers_new_price(tmp_path):
    ns, order, signal, outbox, paused, schedules, relay = fixture(tmp_path, next(iter(COMBO_LANE_SPECS)))
    def target(row): row["pending_orders"][0]["limit_price"] = 95
    def crash(): raise RuntimeError("crash before live publication")
    with pytest.raises(RuntimeError, match="local paper lifecycle commit failed"):
        ns["_commit_paper_lifecycle_transition"]("LIMIT_UPDATED", order["trade_id"], {"research_lane": order["research_lane"]},
            target_mutator=target, live_mutator=crash)
    assert order["limit_price"] == 90 and json.loads(outbox.path.read_text())["pending_orders"][0]["limit_price"] == 95
    assert RelayEventOutbox(outbox.path).healthy and outbox.pending_count() == 0
    assert paused == ["PAPER_LIFECYCLE_COMMIT_FAILED"]


@pytest.mark.parametrize("lane", list(COMBO_LANE_SPECS))
def test_full_paper_lifecycle_dispatch_preserves_quantities_and_existing_relay(tmp_path, lane):
    ns, order, signal, outbox, paused, schedules, relay = fixture(tmp_path, lane)
    state = {"schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False,
             "pending_orders": [], "positions": [], "awaiting_signals": []}
    ns["_build_paper_lifecycle_payload"] = lambda _: copy.deepcopy(state)
    outbox.enqueue({"event": "ORDER_PLACED", "trade_id": "cont-existing", "event_id": "old",
                    "event_seq": 0, "ts": "old"}, state_payload=state)
    existing = json.loads(outbox.path.read_text())["relay_events"]["pending"]
    lock = threading.Lock()
    def transition(event, change):
        def live():
            assert lock.locked()
            change(state)
        ns["_commit_paper_lifecycle_transition"](event, order["trade_id"], {"research_lane": lane},
            target_mutator=change, live_mutator=live, canonical_lock=lock)
        assert not lock.locked()
        persisted = json.loads(outbox.path.read_text())
        for key in ("pending_orders", "positions", "awaiting_signals"):
            assert persisted[key] == state[key]
        assert persisted["relay_events"]["pending"] == existing
        assert outbox.pending_count() == 1 and relay == [] and paused == []
    transition("ORDER_PLACED", lambda s: s["pending_orders"].append(copy.deepcopy(order)))
    transition("LIMIT_UPDATED", lambda s: s["pending_orders"][0].update(limit_price=95, limit_chase_count=1))
    def fill(s):
        row = s["pending_orders"].pop(); row.update(status="OPEN", fill_price=95)
        s["positions"].append(row)
    transition("POSITION_OPENED", fill)
    transition("POSITION_REDUCED", lambda s: s["positions"][0].update(qty=0.1, realized_pnl=0.5))
    assert state["positions"][0]["original_requested_qty"] == 0.2
    transition("POSITION_CLOSED", lambda s: s["positions"].clear())


@pytest.mark.parametrize("event", ["ORDER_CANCELLED", "ORDER_EXPIRED"])
def test_paper_terminal_dispatch_without_network_ack(tmp_path, event):
    ns, order, signal, outbox, paused, schedules, relay = fixture(tmp_path, next(iter(COMBO_LANE_SPECS)))
    assert ns["_commit_paper_lifecycle_transition"](event, order["trade_id"],
        {"research_lane": order["research_lane"]}, target_mutator=lambda s: s["pending_orders"].clear(),
        live_mutator=lambda: order.update(status="EXPIRED"), wait_for_durable_receipt=True) is True
    assert json.loads(outbox.path.read_text())["pending_orders"] == [] and relay == []


def test_unresolved_relay_wal_is_not_overwritten_by_local_transition(tmp_path):
    ns, order, signal, outbox, paused, schedules, relay = fixture(tmp_path, next(iter(COMBO_LANE_SPECS)))
    outbox.prepare_transition(json.loads(outbox.path.read_text()),
        {"event": "LIMIT_UPDATED", "trade_id": "cont-prior", "ts": "old"})
    before = outbox.path.read_bytes()
    with pytest.raises(RuntimeError, match="local paper lifecycle commit failed"):
        ns["_apply_family_policy_chase"](order, signal, 100, 200)
    assert outbox.path.read_bytes() == before and order["limit_price"] == 90


def test_unknown_lane_still_uses_fail_closed_relay_builder(tmp_path):
    ns, order, signal, outbox, paused, schedules, relay = fixture(tmp_path, next(iter(COMBO_LANE_SPECS)))
    with pytest.raises(RuntimeError, match="relay lifecycle event rejected"):
        ns["_commit_paper_lifecycle_transition"]("LIMIT_UPDATED", "unknown-x", {"research_lane": "UNKNOWN"},
            target_mutator=lambda _: None, live_mutator=lambda: None)
    assert len(relay) == 1


@pytest.mark.parametrize("call_index", range(7))
def test_actual_callsite_payloads_dispatch_to_local_commit(tmp_path, call_index):
    # Exercise the exact source call expressions (including caller-specific
    # payload construction and canonical_lock), without importing live bot.
    ns, order, signal, outbox, paused, schedules, relay = fixture(tmp_path, next(iter(COMBO_LANE_SPECS)))
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    calls = sorted([n for n in ast.walk(tree) if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "_commit_paper_lifecycle_transition"], key=lambda n: n.lineno)
    assert len(calls) == 7
    lane = order["research_lane"]
    ns.update(order=order, signal=signal, pos={**order, "dir": "LONG"}, master=signal,
              trade_id=order["trade_id"], tid=order["trade_id"],
              relay_extra={"research_lane": lane}, event={"research_lane": lane},
              terminal_extra={"research_lane": lane}, relay_terminal_event="ORDER_EXPIRED",
              prior_qty=0.2, close_qty=0.077, price=100, fill_px=95,
              position_opened_relay_ts="test", position_close_lock=threading.RLock(),
              exit_reason="TP", PHANTOM_CANCEL_REASON="PHANTOM", direction="LONG",
              entry_price=95, research_lane=lane, caller_reason="test",
              target_mutator=lambda target: target["pending_orders"][0].update(limit_price=95),
              live_mutator=lambda: order.update(limit_price=95))
    expression = ast.fix_missing_locations(ast.Module(body=[ast.Expr(value=calls[call_index])], type_ignores=[]))
    exec(compile(expression, "actual-lifecycle-callsite", "exec"), ns)
    assert json.loads(outbox.path.read_text())["pending_orders"][0]["limit_price"] == 95
    assert order["limit_price"] == 95 and relay == [] and paused == []
