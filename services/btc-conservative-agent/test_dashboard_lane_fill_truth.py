"""Dashboard tile fill percentages must agree with auditable counts."""

import ast
from pathlib import Path


SOURCE_PATH = Path(__file__).with_name("bot.py")


def _load_helper():
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_truthful_approve_to_fill_pct"
    )
    module = ast.Module(body=[helper], type_ignores=[])
    namespace = {}
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace["_truthful_approve_to_fill_pct"]


def test_count_truth_overrides_stale_cached_zero():
    fill_pct = _load_helper()
    assert fill_pct(12, 12, 0.0) == 100.0
    assert fill_pct(10, 4, 99.0) == 40.0


def test_reported_value_is_only_fallback_without_approvals():
    fill_pct = _load_helper()
    assert fill_pct(0, 0, 12.5) == 12.5
    assert fill_pct(0, 0, None) == 0.0
