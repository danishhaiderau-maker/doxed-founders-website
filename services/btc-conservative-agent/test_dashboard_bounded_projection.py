import ast
from pathlib import Path
import pytest
from dashboard_bounded_projection import project_fields


def actual():
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if
             isinstance(node, ast.FunctionDef) and node.name == "_dashboard_signal_ref_lite"
             or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_DASHBOARD_ACTIVE_SIGNAL_KEYS" for target in node.targets)]
    ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "actual-dashboard-projector", "exec"), ns)
    return ns["_dashboard_signal_ref_lite"]


def test_actual_preserves_normal_fields_and_detaches_nested_values():
    source = {"trade_id": "a", "status": "PENDING", "qty": 2, "final_direction": "LONG",
              "timing": {"fill_ts": 1}, "research_chase_schedule": {"authoritative": True, "intervals": [{"limit_price": 100}]},
              "chase_schedule_authoritative": True, "private_unneeded": "excluded"}
    result = actual()(source)
    assert result == {key: value for key, value in source.items() if key != "private_unneeded"}
    source["timing"]["fill_ts"] = 9
    source["research_chase_schedule"]["intervals"][0]["limit_price"] = 8
    assert result["timing"]["fill_ts"] == 1
    assert result["research_chase_schedule"]["intervals"][0]["limit_price"] == 100


def test_actual_large_schedule_is_explicitly_unavailable_without_traversal():
    class Bomb:
        def __deepcopy__(self, memo):
            pytest.fail("custom deepcopy executed")
    source = {"trade_id": "a", "status": "PENDING", "chase_schedule_authoritative": True,
              "research_chase_schedule": {"intervals": [Bomb()] * 1000000}}
    result = actual()(source)
    assert result["trade_id"] == "a"
    assert result["research_chase_schedule"] is None
    assert result["chase_schedule_authoritative"] is False
    assert result["dashboard_projection"]["unavailable_fields"]["research_chase_schedule"] == "CONTAINER_LIMIT"


def test_cycle_custom_object_and_deep_tree_are_bounded():
    cycle = {}
    cycle["self"] = cycle
    class Bomb(dict):
        def items(self):
            pytest.fail("custom mapping dispatch")
        def __deepcopy__(self, memo):
            pytest.fail("custom deepcopy dispatch")
    result = project_fields({"cycle": cycle, "custom": Bomb()}, ("cycle", "custom"))
    assert result["cycle"] is None and result["custom"] is None
    assert result["dashboard_projection"]["unavailable_fields"] == {"custom": "NON_JSON_TYPE", "cycle": "DEPTH_LIMIT"}


def test_total_budget_prevents_many_individually_small_nested_fields():
    result = project_fields({str(i): list(range(10)) for i in range(20)}, [str(i) for i in range(20)], node_limit=20)
    assert result["dashboard_projection"]["status"] == "PARTIAL"
