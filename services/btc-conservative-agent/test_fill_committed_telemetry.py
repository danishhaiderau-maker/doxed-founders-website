import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize('failure', [False, True])
def test_actual_fill_commit_precedes_all_fill_telemetry(monkeypatch, failure):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'fill_order')
    start = next(i for i, n in enumerate(fn.body) if isinstance(n, ast.Expr)
                 and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)
                 and n.value.func.id == '_commit_paper_lifecycle_transition')
    end = next(i for i, n in enumerate(fn.body[start:], start) if isinstance(n, ast.If)
               and isinstance(n.test, ast.Name) and n.test.id == 'fill_lane')
    # Reject an additional pre-commit call, not just the selected block.
    before = ast.unparse(ast.Module(body=fn.body[:start], type_ignores=[]))
    assert 'funnel_on_fill(' not in before
    events = []
    result = {}
    def commit(*args, **kwargs):
        if failure:
            raise OSError('durable commit failed')
        result['pos'] = {'trade_id': 'test'}
        events.append('commit')
    monkeypatch.setitem(sys.modules, 'execution_funnel', SimpleNamespace(
        funnel_on_fill=lambda *a: events.append('receipt')))
    ns = dict(_commit_paper_lifecycle_transition=commit, order={'trade_id': 'test'},
              position_opened_relay_ts='t', fill_px=100, signal={},
              target_mutator=lambda x: None, live_mutator=lambda: None,
              position_close_lock=None, transition_result=result,
              increment_pipeline_funnel=lambda key: events.append(key),
              logger=SimpleNamespace(exception=lambda *a: events.append('error')))
    code = compile(ast.Module(body=fn.body[start:end], type_ignores=[]), 'actual-fill-commit', 'exec')
    if failure:
        with pytest.raises(OSError):
            exec(code, ns)
        assert events == []
    else:
        exec(code, ns)
        assert events == ['commit', 'FILLED', 'receipt']
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == 'increment_pipeline_funnel'
             and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == 'FILLED']
    assert len(calls) == 1
