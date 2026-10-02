import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize('paper_only,live,expected', [(True, False, False), (False, False, True), (False, True, False)])
def test_actual_close_direct_writer_guard(paper_only, live, expected):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    close = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'close_position')
    guard = next(n for n in close.body if isinstance(n, ast.If) and any(
        isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == 'dual_write_paper_close'
        for call in ast.walk(n)))
    ns = {'COMBO_LANE_SPECS': {'family': {'paper_only': paper_only}},
          'pos': {'research_lane': 'family', 'bitfinex_live_entry': live}}
    assert eval(compile(ast.Expression(guard.test), 'actual-close-guard', 'eval'), ns) is expected
