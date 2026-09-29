"""A paper order must be filled by exactly one thread.

state_monitor_loop and position_manager both run process_pending_orders.  The
fill handoff releases trade_lock before the OPEN lifecycle commit, so a second
touch pass used to re-fill the same order and crash on the duplicate commit
("position-open target already exists"), safety-pausing paper.
"""
import ast
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
BOT = HERE / "bot.py"
SOURCE = BOT.read_text(encoding="utf-8")
WORKFLOW = (HERE.parents[1] / ".github" / "workflows" / "fly-bot-deploy.yml").read_text(encoding="utf-8")


def _functions(*names, namespace=None, **values):
    namespace = values if namespace is None else namespace
    tree = ast.parse(SOURCE)
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(selected) == len(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT), "exec"), namespace)
    return namespace


def _body(name):
    start = SOURCE.index(f"\ndef {name}(")
    end = SOURCE.index("\ndef ", start + 1)
    return SOURCE[start:end]


def _chase6_namespace(order, fill_calls, reenter):
    ns = {
        "time": __import__("time"),
        "trade_lock": threading.RLock(),
        "pending_orders": [order],
        "fill_handoff_trade_ids": set(),
        "trades_map": {},
        "is_virtual_chase_entry_lane": lambda lane: True,
        "resolve_sim_fill_price": lambda o: 100.0,
        "_normalize_order_side_to_dir": lambda side: "LONG",
        "VIRTUAL_CHASE_LANE_CHASE6_WAIT_SEC": 60,
        "_virtual_chase_fill_phase": lambda *a, **k: "P6",
        "_record_virtual_chase_execution_metrics": lambda *a, **k: None,
        "fmt": str,
        "logger": type("L", (), {"info": staticmethod(lambda *a, **k: None)})(),
    }

    def fill_order(o):
        fill_calls.append(o["trade_id"])
        if reenter:
            ns["process_virtual_chase_chase6_market_conversions"](100.0)
        o["status"] = "FILLED"
        ns["pending_orders"].remove(o)

    ns["fill_order"] = fill_order
    return _functions("process_virtual_chase_chase6_market_conversions",
                      "_release_unfilled_fill_handoff", namespace=ns)


def _chase6_order():
    return {"trade_id": "fvc-1", "status": "PENDING", "research_lane": "L",
            "virtual_chase_6_wait_until": 1.0, "limit_price": 99.0}


def test_chase6_conversion_is_single_flight_during_handoff():
    order, calls = _chase6_order(), []
    ns = _chase6_namespace(order, calls, reenter=True)
    ns["process_virtual_chase_chase6_market_conversions"](100.0)
    assert calls == ["fvc-1"]
    assert order["status"] == "FILLED"


def test_failed_fill_attempt_releases_claim_for_retry():
    order, calls = _chase6_order(), []
    ns = _chase6_namespace(order, calls, reenter=False)

    def failing_fill(o):
        calls.append(o["trade_id"])
        raise RuntimeError("lifecycle commit failed")

    ns["fill_order"] = failing_fill
    for _ in range(2):
        with pytest.raises(RuntimeError, match="lifecycle commit failed"):
            ns["process_virtual_chase_chase6_market_conversions"](100.0)
        assert "fvc-1" not in ns["fill_handoff_trade_ids"]
        assert "fill_handoff_in_progress" not in order
    assert calls == ["fvc-1", "fvc-1"]


def test_release_keeps_claim_once_order_left_pending_book():
    order = {"trade_id": "t-1", "status": "FILLED", "fill_handoff_in_progress": True}
    ns = _functions("_release_unfilled_fill_handoff", trade_lock=threading.RLock(),
                    pending_orders=[], fill_handoff_trade_ids={"t-1"})
    ns["_release_unfilled_fill_handoff"](order)
    assert ns["fill_handoff_trade_ids"] == {"t-1"}

    pending = {"trade_id": "t-2", "status": "PENDING", "fill_handoff_in_progress": True}
    ns["pending_orders"].append(pending)
    ns["fill_handoff_trade_ids"].add("t-2")
    ns["_release_unfilled_fill_handoff"](pending)
    assert "t-2" not in ns["fill_handoff_trade_ids"]
    assert "fill_handoff_in_progress" not in pending


def test_touch_pass_skips_orders_already_in_fill_handoff():
    body = _body("process_pending_orders")
    recheck = body.index("if not tid or tid in fill_handoff_trade_ids or any(")
    claim = body.index('fill_handoff_trade_ids.add(order["trade_id"])')
    assert body.rindex("with trade_lock:", 0, recheck) > body.index("for order in ready_orders:")
    assert recheck < claim
    loop = body[body.rindex("    for order, fill_signal in fills:"):]
    assert loop.index("fill_order(order)") < loop.index("finally:") < loop.index(
        "_release_unfilled_fill_handoff(order)")


def test_maintenance_treats_already_cancelled_reconcile_as_no_mutation():
    start = WORKFLOW.index('reconciled = mutate_json("/api/reconcile/phantom-cancel"')
    loop = WORKFLOW[start:WORKFLOW.index("maintenance boundary did not become flat", start)]
    skip = loop.index('if reconciled.get("already_cancelled") is True and generation is None:')
    assert loop.index('if reconciled.get("ok") is not True:') < skip
    assert skip < loop.index('raise SystemExit("maintenance reconciliation generation is missing")')
    assert "continue" in loop[skip:loop.index("_legacy_exact_revision_bootstrap", skip)]


def _maintenance_step():
    start = WORKFLOW.index("- name: Enter durable authenticated paper maintenance boundary")
    return WORKFLOW[start:WORKFLOW.index("maintenance boundary did not become flat", start)]


def test_maintenance_timeout_is_unconfirmed_not_fatal():
    step = _maintenance_step()
    helper = step[step.index("def mutate_json("):step.index("def require_legacy_bootstrap_status(")]
    assert "timeout=90" in helper
    assert "if not transient(exc):" in helper and "raise" in helper
    assert "return None" in helper
    assert 'mutate_json("/api/orders/cancel"' in step
    assert 'request_json("/api/orders/cancel"' not in step
    for marker in ("if cancelled is None:", "if reconciled is None:"):
        block = step[step.index(marker):step.index("continue", step.index(marker))]
        assert "unconfirmed_trade_ids.add(trade_id)" in block
        assert "unconfirmed_floor = max(" in block


def test_flat_after_unconfirmed_mutation_requires_newer_generation():
    step = _maintenance_step()
    flat = step[step.index("if not orders and not positions:"):step.index("for trade_id in orders:")]
    guard = flat.index('exposure.get("money_state_generation") <= unconfirmed_floor')
    assert guard < flat.index('print(f"Durable maintenance boundary is flat')
    assert "continue" in flat[guard:flat.index('print(f"Durable maintenance boundary is flat')]
    not_found = step[step.index('if exc.code == 404 and exposure.get("_legacy_exact_revision_bootstrap") is not True:'):]
    assert not_found.index("continue") < not_found.index("if exc.code != 404:") < not_found.index("raise")


def test_unconfirmed_cancel_resamples_exposure_before_more_mutations():
    step = _maintenance_step()
    unconfirmed = step[step.index("if cancelled is None:"):]
    assert unconfirmed.index("round_unconfirmed = True") < unconfirmed.index("break") < unconfirmed.index('cancelled.get("status") == "not_found"')
    resample = step.index("if round_unconfirmed:")
    assert step.index("round_unconfirmed = False") < resample < step.index("for trade_id in positions:")
    assert "continue" in step[resample:step.index("for trade_id in positions:")]
