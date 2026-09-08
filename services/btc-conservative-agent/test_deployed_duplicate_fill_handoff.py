"""Immutable deployed-code reproducer: admission can hand off one order twice."""
import ast
import copy
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
import pytest
from paper_fill_ownership import FillOwnership

DEPLOYED = "2db7a09c8574908db84b55900902426a3d1b7500"


@pytest.mark.parametrize("repaired", [False, True])
def test_deployed_handoff_flag_does_not_prevent_second_admission(repaired):
    source = (Path(__file__).with_name("bot.py").read_text(encoding="utf-8") if repaired else
              subprocess.check_output(["git", "show", f"{DEPLOYED}:services/btc-conservative-agent/bot.py"],
                                      cwd=Path(__file__).parents[2]).decode("utf-8"))
    tree = ast.parse(source)
    process = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "process_pending_orders")
    admission = next(n for n in process.body if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == "ready_orders")
    order = {"trade_id": "fmg-race", "status": "PENDING", "qty": 2.0, "signal_dir": "SHORT"}
    handoffs = []
    price_calls = []
    def resolve(row):
        price_calls.append(True)
        row["filled_qty"] = 1.0
        row["partial_fill"] = True
        row["qty"] = 1.0
        return 100.0
    ns = {"ready_orders": [order], "pending_orders": [order], "open_positions": [],
          "trade_lock": threading.RLock(), "trades_map": {}, "time": SimpleNamespace(time=lambda: 1),
          "_order_signal_age_sec": lambda *a: 1, "chase_age_window_should_cancel": lambda a: False,
          "stale_fill_direction_conflict": lambda *a, **k: "", "latest_ai_for_fill": {},
          "latest_ai_ts_for_fill": 0, "fill_context_for_revalidation": {}, "copy": copy,
          "utc_iso": lambda: "t", "resolve_sim_fill_price": resolve,
          "fill_handoff_trade_ids": set(), "fills": handoffs, "cancelled_at_fill": [],
          "_paper_fill_ownership": FillOwnership()}
    nodes = [admission]
    if repaired:
        evaluation = next(n for n in process.body if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == "pending_snapshot")
        nodes = [ast.parse("ready_orders = []").body[0], evaluation, admission]
        ns.update(pending_snapshot=[order], lane_orders_allowed=lambda lane: True,
                  _pending_limit_ready_for_fill=lambda *a, **k: True, price=100,
                  fill_bid=99, fill_ask=101, venue_snapshot={}, recent_market_trades=[], now=1)
    code = compile(ast.Module(body=nodes, type_ignores=[]), "deployed-admission", "exec")
    exec(code, ns)  # Caller A admitted, then descheduled before fill_order.
    assert order["fill_handoff_in_progress"] and "fmg-race" in ns["fill_handoff_trade_ids"]
    exec(code, ns)  # Caller B checks the same pending reference.
    if repaired:
        assert len(handoffs) == 1 and len(price_calls) == 1
        return
    assert len(handoffs) == 2 and len(price_calls) == 2
    assert handoffs[0][0] is handoffs[1][0]

    fill = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "fill_order")
    target = next(n for n in fill.body if isinstance(n, ast.FunctionDef) and n.name == "target_mutator")
    ns.update(order=order, candidate_pos={"trade_id": "fmg-race", "qty": 1},
              _canonicalize_paper_position_snapshot=lambda row: row)
    exec(compile(ast.Module(body=[target], type_ignores=[]), "deployed-fill-target", "exec"), ns)
    durable = {"pending_orders": [copy.deepcopy(order)], "positions": []}
    ns["target_mutator"](durable)
    with pytest.raises(RuntimeError, match="position-open target already exists"):
        ns["target_mutator"](copy.deepcopy(durable))
    assert durable["positions"] == [{"trade_id": "fmg-race", "qty": 1}]
