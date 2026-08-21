import ast
from pathlib import Path


BOT = Path(__file__).with_name("bot.py")


def _function_node(name: str) -> ast.FunctionDef:
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_ai_input_logging_has_no_retired_edge_runtime_dependency():
    node = _function_node("log_ai_input_full")
    referenced_names = {
        child.id for child in ast.walk(node) if isinstance(child, ast.Name)
    }

    assert "_edge_candle_bucket" not in referenced_names


def test_ai_input_candle_bucket_is_a_descriptive_fifteen_minute_bucket():
    node = _function_node("log_ai_input_full")
    source = ast.unparse(node)

    assert "int(time.time() // (15 * 60))" in source
