import ast
import copy
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import pytest
from combo_pathway_config import COMBO_LANE_SPECS
from relay_event_outbox import RelayEventOutbox
from paper_fill_ownership import FillOwnership, FillSuperseded, wrap_fill


@pytest.mark.parametrize("interleave", ["removed_cancelled", "confirmed_pending", "terminal_pending", "missing_unexplained", "conflicting_open", "normal"])
def test_actual_locked_commit_prevents_cancelled_fill_without_swallowing_conflict(tmp_path, interleave):
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    fill = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "fill_order")
    target = next(n for n in fill.body if isinstance(n, ast.FunctionDef) and n.name == "target_mutator")
    commit = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_commit_local_paper_lifecycle_transition")
    lane = next(iter(COMBO_LANE_SPECS))
    order = {"trade_id": COMBO_LANE_SPECS[lane]["id_prefix"] + "-race", "status": "PENDING", "research_lane": lane}
    durable = {"schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False,
               "pending_orders": [copy.deepcopy(order)], "positions": []}
    lock = threading.RLock()
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    outbox._atomic_write(outbox.decorate_lifecycle(durable))
    paused, live = [], []
    ns = {"copy": copy, "order": order, "candidate_pos": {"trade_id": order["trade_id"], "qty": 1},
          "COMBO_LANE_SPECS": COMBO_LANE_SPECS, "PLATFORM_RELAY_ELIGIBLE_LANES": {"CONTINUOUS"},
          "RELAY_CANONICAL_TRANSITION_EVENTS": {"POSITION_OPENED"},
          "is_active_dashboard_owner": lambda: True, "_force_paper_mode_active": lambda: True,
          "state": {"live_armed": False, "bitfinex_live_enabled": False}, "_relay_event_outbox": outbox,
          "paper_lifecycle_transition_lock": threading.RLock(), "paper_lifecycle_file_lock": threading.RLock(),
          "trade_lock": lock, "set_execution_paused": paused.append,
          "_build_paper_lifecycle_payload": lambda *a: copy.deepcopy(durable),
          "_canonicalize_paper_position_snapshot": lambda x: x}
    exec(compile(ast.Module(body=[target, commit], type_ignores=[]), "actual-cancel-fill-boundary", "exec"), ns)
    owner = FillOwnership()
    def body(row):
        # Cancellation wins after wrapper ownership validation, before commit.
        if interleave == "removed_cancelled":
            row.update(status="CANCELLED", cancel_confirmed=True)
            durable["pending_orders"] = []
        elif interleave == "confirmed_pending":
            row["cancel_confirmed"] = True
            durable["pending_orders"][0]["cancel_confirmed"] = True
        elif interleave == "terminal_pending":
            durable["pending_orders"][0].update(status="CANCELLED", cancel_confirmed=True)
        elif interleave == "missing_unexplained":
            durable["pending_orders"] = []
        elif interleave == "conflicting_open":
            durable["positions"] = [{"trade_id": row["trade_id"], "qty": 2}]
        outbox._atomic_write(outbox.decorate_lifecycle(durable))
        ns["_commit_local_paper_lifecycle_transition"]("POSITION_OPENED", row["trade_id"],
            {"research_lane": lane}, target_mutator=ns["target_mutator"], live_mutator=lambda: live.append(True))
    guarded = wrap_fill(body, owner, lambda: lock)
    if interleave in {"removed_cancelled", "confirmed_pending", "terminal_pending"}:
        assert guarded(order) == {"filled": False, "reason": "PAPER_FILL_CANCEL_WON"}
        assert paused == []
    elif interleave != "normal":
        with pytest.raises(RuntimeError, match="local paper lifecycle commit failed"):
            guarded(order)
        assert paused == ["PAPER_LIFECYCLE_COMMIT_FAILED"]
    else:
        assert guarded(order) is None
        assert live == [True] and paused == [] and owner.claims == {}
        assert json.loads(outbox.path.read_text())["positions"] == [ns["candidate_pos"]]
        return
    assert live == [] and owner.claims == {}
    assert json.loads(outbox.path.read_text())["positions"] == durable["positions"]


def test_relay_branch_cancel_won_does_not_prepare_wal_or_pause():
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_commit_paper_lifecycle_transition")
    prepared, live, paused = [], [], []
    ns = {"COMBO_LANE_SPECS": {}, "_build_showcase_relay_event_payload": lambda *a: {"event": "POSITION_OPENED"},
          "paper_lifecycle_transition_lock": threading.RLock(), "paper_lifecycle_file_lock": threading.RLock(),
          "trade_lock": threading.RLock(), "_build_paper_lifecycle_payload": lambda **k: {},
          "_relay_event_outbox": SimpleNamespace(prepare_transition=lambda *a: prepared.append(True)),
          "set_execution_paused": paused.append}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "actual-relay-cancel", "exec"), ns)
    def cancelled(target):
        raise FillSuperseded("PAPER_FILL_CANCEL_WON")
    with pytest.raises(FillSuperseded):
        ns[fn.name]("POSITION_OPENED", "cont-a", {"research_lane": "CONTINUOUS"},
                    target_mutator=cancelled, live_mutator=lambda: live.append(True))
    assert prepared == [] and live == [] and paused == []
