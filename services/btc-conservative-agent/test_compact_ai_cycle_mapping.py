import ast
import copy
import math
from pathlib import Path

import pytest
from cycle_3m_indicators import compute_3m_universe_snapshot


@pytest.fixture
def compact():
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == 'build_shared_direction_prompt_context')
    env = dict(sanitize_ai_inputs=lambda value: value, utc_iso=lambda: 'fixture',
               weak_countertrend_conflict=lambda source, direction: None)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot.py', 'exec'), env)
    return env[node.name]


def test_actual_3m_producer_maps_stoch_without_faking_closed_timestamp(compact):
    origin = 1700000100
    rows = []
    for index in range(200):
        close = 100 + math.sin(index / 5) * 4 + index * .01
        rows.append([(origin + index * 60) * 1000, close, close+1, close-1, close, 3])
    cycle = compute_3m_universe_snapshot(rows, ts=origin + 200*60)
    assert cycle['stoch_rsi_k'] is not None and cycle['stoch_rsi_d'] is not None
    assert 'closed_3m_ts' not in cycle
    before = copy.deepcopy(cycle)
    payload = compact({'cycle_3m_universe': cycle})
    assert payload['raw']['stoch_rsi_k_3m'] == cycle['stoch_rsi_k']
    assert payload['raw']['stoch_rsi_d_3m'] == cycle['stoch_rsi_d']
    assert payload['raw']['closed_3m_ts'] is None
    assert cycle == before


@pytest.mark.parametrize('key', ['cycle_3m_universe', 'exhaustion_3m'])
def test_zero_and_compatibility_fields_are_preserved(compact, key):
    result = compact({key: {'stoch_rsi_k': 0, 'stoch_rsi_d': 0}})['raw']
    assert result['stoch_rsi_k_3m'] == result['stoch_rsi_d_3m'] == 0
    result = compact({key: {'stoch_rsi_k_3m': 0, 'stoch_rsi_d_3m': None,
                           'stoch_rsi_k': 90, 'stoch_rsi_d': 15}})['raw']
    assert result['stoch_rsi_k_3m'] == 0
    assert result['stoch_rsi_d_3m'] == 15


def test_unavailable_producer_remains_unknown(compact):
    cycle = compute_3m_universe_snapshot([], ts=1700000100)
    result = compact({'cycle_3m_universe': cycle})['raw']
    assert result['stoch_rsi_k_3m'] is None
    assert result['stoch_rsi_d_3m'] is None
    assert result['closed_3m_ts'] is None
