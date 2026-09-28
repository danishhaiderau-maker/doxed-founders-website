"""A clean epoch without closed trades is valid analyzer input."""

import ast
from pathlib import Path


ENGINE = Path(__file__).with_name("analyzer_research_engine_v62.py")
SOURCE = ENGINE.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(ENGINE))
FUNCTION = next(
    node
    for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name == "_paper_trade_index"
)


def test_missing_trade_csv_is_an_empty_terminal_cohort(tmp_path):
    calls = []
    namespace = {
        "os": __import__("os"),
        "TRADES_FILE": str(tmp_path / "trades_3factor.csv"),
        "robust_read_csv": lambda *_args: calls.append(_args),
    }
    module = ast.Module(body=[FUNCTION], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(ENGINE), "exec"), namespace)

    assert namespace["_paper_trade_index"]() == {}
    assert calls == []
