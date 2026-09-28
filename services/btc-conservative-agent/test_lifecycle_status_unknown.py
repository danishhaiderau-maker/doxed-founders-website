import ast
from pathlib import Path
from types import SimpleNamespace


def test_status_timeout_unknown_and_bootstrap_blocked():
    source = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    names = {"_lifecycle_pipeline_runtime_status", "_data_sync_receipt_bootstrap_gate"}
    nodes = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
    def timeout():
        raise TimeoutError("unavailable")
    scope = {"_LIFECYCLE_PIPELINE_RUNTIME": SimpleNamespace(status=timeout)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot.py", "exec"), scope)
    status = scope["_lifecycle_pipeline_runtime_status"]()
    assert status["available"] is False
    assert all(status[key] is None for key in ("owner", "running", "active"))
    gate = scope["_data_sync_receipt_bootstrap_gate"]()
    assert gate["blocked"] is True and gate["complete"] is False
    assert gate["required"] is True
