"""Normal opportunity projection must retain explicit research assumptions."""
import ast
import copy
from pathlib import Path
from typing import Any, Mapping


def project(*sources):
    source = Path(__file__).with_name("research_v3_bridge.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    names = {"_signal_time_baseline_inputs", "_first"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    env = {"copy": copy, "Mapping": Mapping, "Any": Any}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "research_v3_bridge.py", "exec"), env)
    return env["_signal_time_baseline_inputs"](*sources)


def test_explicit_declaration_survives_without_aliasing():
    declaration = {"schema": "research_baseline_context_declaration_v1", "atr": {"atr_pct": 1}}
    result = project({"research_baseline_context_declaration": declaration})
    assert result["research_baseline_context_declaration"] == declaration
    result["research_baseline_context_declaration"]["atr"]["atr_pct"] = 2
    assert declaration["atr"]["atr_pct"] == 1


def test_missing_declaration_is_not_synthesized():
    assert "research_baseline_context_declaration" not in project({"atr14_pct": 1})


def test_conflicting_declarations_remain_unsupported_even_with_third_source():
    first = {"schema": "research_baseline_context_declaration_v1", "margin_usd": 10}
    second = {**first, "margin_usd": 20}
    result = project(*({"research_baseline_context_declaration": item} for item in (first, second, first)))
    assert result["research_baseline_context_declaration"]["schema"] == "research_baseline_context_conflict_v1"


def test_identical_declarations_are_not_conflicts():
    declaration = {"schema": "research_baseline_context_declaration_v1"}
    assert project(*[{"research_baseline_context_declaration": declaration}] * 2)["research_baseline_context_declaration"] == declaration
