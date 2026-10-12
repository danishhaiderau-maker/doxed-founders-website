"""LIVE-FIX-1512: the deploy maintenance boundary must block DISLOC/DISLOC-FOI paper entries."""
import ast, pathlib

SRC = (pathlib.Path(__file__).parent / "bot.py").read_text(encoding="utf-8")


def _func(name):
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(name)


def test_disloc_enabled_fn_honours_pause():
    seg = ast.get_source_segment(SRC, _func("_disloc_bind_runtime"))
    assert "_disloc_paper_paused()" in seg


def test_paused_predicate_reads_pipeline_pause_flags():
    fn = _func("_disloc_paper_paused")
    ns = {"state": {}}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "x", "exec"), ns)
    assert ns["_disloc_paper_paused"]() is False
    ns["state"].update(manual_admin_pause=True, pause_intent="DEPLOY_MAINTENANCE")
    assert ns["_disloc_paper_paused"]() is True
    ns["state"].clear(); ns["state"]["execution_paused"] = True
    assert ns["_disloc_paper_paused"]() is True
