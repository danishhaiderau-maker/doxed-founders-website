import ast
import copy
from pathlib import Path


def test_actual_fill_live_projection_preserves_terminal_schedule_and_actions():
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    fill = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'fill_order')
    live = next(n for n in fill.body if isinstance(n, ast.FunctionDef) and n.name == 'live_mutator')
    block = next(n for n in live.body if isinstance(n, ast.If) and 'frozen_fill_order' in ast.unparse(n.test))
    schedule = {'authoritative': True, 'terminal_reason': 'FILLED',
                'action_timing_receipts': [{'fill_ts': 100.5, 'filled_qty': 2}]}
    ns = {'copy': copy, 'frozen_fill_order': {'research_chase_schedule': schedule},
          'order': {}, 'signal': {}}
    exec(compile(ast.Module(body=[block], type_ignores=[]), 'actual-fill-projection', 'exec'), ns)
    for key in ('order', 'signal'):
        assert ns[key]['research_chase_schedule'] == schedule
        assert ns[key]['chase_schedule_authoritative'] is True
    ns['signal']['research_chase_schedule']['action_timing_receipts'].clear()
    assert schedule['action_timing_receipts']
    assert ns['order']['research_chase_schedule']['action_timing_receipts']
