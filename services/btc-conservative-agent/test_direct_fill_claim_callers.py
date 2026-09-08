import ast
import copy
from pathlib import Path
import threading
from types import SimpleNamespace
import pytest
from paper_fill_ownership import FillOwnership


@pytest.mark.parametrize("caller", ["instant", "conversion"])
@pytest.mark.parametrize("preclaimed", [False, True])
def test_actual_direct_caller_claims_before_mutation_and_releases_on_failure(caller, preclaimed):
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    owner, lock = FillOwnership(), threading.RLock()
    order = {"trade_id": "a", "status": "PENDING", "limit_price": 90, "entry_type": "SIM_LIMIT",
             "research_lane": "PAPER", "virtual_chase_6_wait_until": 1}
    before = copy.deepcopy(order)
    prior = owner.claim(order, lock) if preclaimed else None
    calls = []
    def fill(row, *, _fill_claim):
        owner.verify(row, _fill_claim, lock)
        calls.append("fill")
        raise OSError("injected commit failure")
    def resolve(row):
        assert owner.claims["a"]["order"] is row
        calls.append("resolve")
        return 100
    ns = {"_paper_fill_ownership": owner, "trade_lock": lock, "order": order, "price": 100,
          "fill_order": fill, "resolve_sim_fill_price": resolve, "can_instant": True,
          "time": SimpleNamespace(time=lambda: 100), "pending_orders": [order], "trades_map": {},
          "is_virtual_chase_entry_lane": lambda lane: True, "_normalize_order_side_to_dir": lambda x: "LONG",
          "logger": SimpleNamespace(info=lambda *a: None), "fmt": str}
    if caller == "instant":
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                    and n.test.id == "can_instant" and "fill_claim" in ast.unparse(n))
        nodes = [node]
    else:
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "process_virtual_chase_chase6_market_conversions")
        nodes = [node, ast.parse("process_virtual_chase_chase6_market_conversions(100)").body[0]]
    compiled = compile(ast.Module(body=nodes, type_ignores=[]), "actual-direct-caller", "exec")
    if preclaimed:
        exec(compiled, ns)
        assert order == before and calls == [] and owner.claims["a"] is prior
        owner.release(prior, lock)
    else:
        with pytest.raises(OSError, match="commit failure"):
            exec(compiled, ns)
        assert owner.claims == {} and calls[-1] == "fill"
