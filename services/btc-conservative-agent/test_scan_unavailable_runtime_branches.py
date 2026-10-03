"""Run actual extracted runtime branches without importing the trading runtime."""
import ast
import copy
from contextlib import nullcontext
import math
from pathlib import Path
from types import SimpleNamespace
import time

import pytest


@pytest.mark.parametrize('case,reason', [('price','PRICE_UNAVAILABLE'),
    ('context','CONTEXT_UNAVAILABLE'), ('features','FEATURE_VALIDATION_FAILED')])
@pytest.mark.parametrize('sink_ok', [True, False, 'raises'])
def test_actual_early_rejection_records_both_direction_gap_without_api(case, reason, sink_ok):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in
             {'evaluate_signal_with_ai', '_record_unavailable_scan_coverage'}]
    rows, warnings, api_calls = [], [], []
    def noop(*a, **k):
        pass
    def forbidden(*a, **k):
        api_calls.append(True)
        raise AssertionError('AI API must not run for invalid input')
    def append(path, row, **kw):
        rows.append(row)
        if sink_ok == 'raises':
            raise OSError('sink unavailable')
        return sink_ok
    env = dict(RESEARCH_LANE_CONTINUOUS='continuous', copy=copy,
        state={'price':None if case=='price' else 100}, state_lock=nullcontext(),
        time=SimpleNamespace(time=lambda:123),
        logger=SimpleNamespace(info=noop,error=noop,warning=lambda *a: warnings.append(a)),
        full_pipeline_trace=noop, trace=noop, debug_snapshot=noop,
        enrich_ai_context_upgrade=lambda ctx:ctx, sanitize_ai_inputs=lambda ctx:ctx,
        validate_ai_features=lambda ctx:(False,'invalid feature'),
        _runtime_git_rev_exact=lambda:'a'*40, _collector_v22_epoch_id=lambda:'epoch-fixture',
        _safe_append_jsonl=append, AI_INPUT_LOG_FILE='fixture-only',
        call_deepseek_api=forbidden, SHARED_DIRECTION_PROMPT_ID='fixture',
        build_ai_error_result=lambda error, trade_id:dict(decision='REJECT',win_prob=0,
            approved=False,ai_error=True,trade_id=trade_id),
        log_ai_error_row=noop, log_ai_tranche_outcome=noop, log_pipeline_event=noop)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'bot.py','exec'),env)
    context={} if case=='context' else {'trade_id':'scan-1'}
    result=env['evaluate_signal_with_ai'](context,shadow_only=True)
    assert result['decision']=='REJECT' and result['approved'] is False
    assert api_calls==[] and len(rows)==1
    assert rows[0]['reason_code']==reason
    assert rows[0]['simulated_trade_count']==0
    assert all(r['status']=='UNAVAILABLE' for r in rows[0]['directional_coverage'].values())
    accepted = sink_ok is True
    assert any('COUNTERFACTUAL_COVERAGE_WRITE_FAILED' in str(w) for w in warnings) is (not accepted)
    # Caller-visible evidence is required even if its persistence failed.
    coverage = result['counterfactual_coverage']
    assert coverage['write_status'] == ('ACCEPTED' if accepted else 'FAILED')
    assert coverage['receipt'] == rows[0]


@pytest.mark.parametrize(
    'high,low,expected,observed_high,observed_low',
    [
        (None, 90, ['SR_SWING_HIGH_MISSING'], None, 90),
        (110, None, ['SR_SWING_LOW_MISSING'], 110, None),
        (None, None, ['SR_SWING_HIGH_MISSING', 'SR_SWING_LOW_MISSING'], None, None),
        (False, 90, ['SR_SWING_HIGH_INVALID'], None, 90),
        ('110', 90, ['SR_SWING_HIGH_INVALID'], None, 90),
        (float('nan'), 90, ['SR_SWING_HIGH_INVALID'], None, 90),
        (float('inf'), 90, ['SR_SWING_HIGH_INVALID'], None, 90),
        (-110, 90, ['SR_SWING_HIGH_INVALID'], -110, 90),
        (110, False, ['SR_SWING_LOW_INVALID'], 110, None),
        (110, -90, ['SR_SWING_LOW_INVALID'], 110, -90),
        (90, 110, ['SR_SWING_RANGE_INVALID'], 90, 110),
        (110, 110, ['SR_SWING_RANGE_INVALID'], 110, 110),
        (110, 90, [], 110, 90),
    ],
)
def test_actual_pure_context_failure_emits_bounded_no_call_receipt(
    high, low, expected, observed_high, observed_low,
):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    names = {
        '_finite_positive_sr_value',
        '_sr_swing_pair_ready',
        '_sr_swing_prerequisite_failures',
        '_bounded_ctx_failure_scalar',
        '_ctx_fail_prerequisite_detail',
        'build_pure_ai_context',
        '_record_ctx_fail_unavailable_coverage',
        '_build_pure_ai_context_with_evidence',
    }
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    rows, ai_calls, order_calls = [], [], []

    def noop(*args, **kwargs):
        pass

    def forbidden_ai(*args, **kwargs):
        ai_calls.append(True)

    def forbidden_order(*args, **kwargs):
        order_calls.append(True)

    def append(path, row, **kwargs):
        rows.append(copy.deepcopy(row))
        return True

    def sanitize_features(value):
        return {key: 0.0 if item is None else item for key, item in value.items()}

    numeric = [1.0] * 10
    sr = {'swing_high': high, 'swing_low': low, 'sr_state': 'RANGE', 'ts': '2026-09-14T00:00:00Z'}
    snapshot = {
        'price': 100,
        'support_resistance': sr,
        'ema_status': {'ema9': 101, 'ema21': 100, 'ema200': 99},
        'regime': 'RANGE',
        'data_quality': 1.0,
    }
    env = {
        'math': math,
        'time': time,
        'state': {'last_edge': 0},
        'latest_candles': [object()] * 200,
        'price_buffer': numeric,
        'volume_buffer': numeric,
        'delta_buffer': numeric,
        'delta_change_buffer': numeric,
        'imbalance_buffer': numeric,
        'candle_range_buffer': numeric,
        'wick_ratio_buffer': numeric,
        'body_ratio_buffer': numeric,
        'velocity_buffer': numeric,
        'logger': SimpleNamespace(warning=noop),
        'nz': lambda value, default=None: None if value is None else float(value),
        'sanitize_features': sanitize_features,
        'get_aggregated': lambda value: value[-1] if value else None,
        'compute_volume_ratio': lambda: 1.0,
        'get_edge_threshold': lambda: 0.0,
        'get_funding_snapshot_for_ai': lambda: {},
        'get_market_context_for_ai': lambda: {},
        'enrich_ai_context_upgrade': lambda value: value,
        '_stamp_3m_exhaustion_for_ai': lambda value: value,
        'sanitize_ai_inputs': lambda value: value,
        '_runtime_git_rev_exact': lambda: 'a' * 40,
        '_collector_v22_epoch_id': lambda: 'epoch-fixture',
        '_runtime_readiness_components': lambda now: {
            'system_ready': True,
            'structural_prerequisites_ready': True,
            'sr_ready': True,
            'buffers_ready': True,
            'candle_ready': True,
            'ema_ready': True,
            'ohlcv_ready': True,
            'readiness_reasons': [],
        },
        '_safe_append_jsonl': append,
        'AI_INPUT_LOG_FILE': 'fixture-only',
        'call_deepseek_api': forbidden_ai,
        'execute_simulated_order': forbidden_order,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'bot.py', 'exec'), env)
    buffers = {
        'ret_1m': numeric,
        'ret_5m': numeric,
        'velocity': numeric,
        'volume': numeric,
        'delta': numeric,
        'delta_change': numeric,
        'imbalance': numeric,
        'range': numeric,
        'wick_ratio': numeric,
        'body_ratio': numeric,
    }
    ctx, coverage = env['_build_pure_ai_context_with_evidence'](
        snapshot, buffers, {'trade_id': 'scan-ctx-1', 'shared_ai_call_id': 'scan-ctx-1'}
    )

    assert ai_calls == [] and order_calls == []
    if expected:
        assert ctx is None and coverage['write_status'] == 'ACCEPTED'
        assert len(rows) == 1
        receipt = rows[0]
        assert receipt['ai_evaluated'] is False
        assert receipt['reason_code'] == 'CONTEXT_UNAVAILABLE'
        assert receipt['prerequisite_failures'] == expected
        assert receipt['sr_swing_high'] == observed_high
        assert receipt['sr_swing_low'] == observed_low
        assert receipt['sr_state'] == 'RANGE'
        assert receipt['sr_timestamp'] == '2026-09-14T00:00:00Z'
        assert set(receipt['directional_coverage']) == {'LONG', 'SHORT'}
        assert all(side['status'] == 'UNAVAILABLE' for side in receipt['directional_coverage'].values())
        assert receipt['simulated_trade_count'] == 0
        assert receipt['qualification_eligible'] is False
        assert receipt['candle_count'] == 200
        assert all(value == 10 for value in receipt['buffer_counts'].values())
        assert receipt['readiness']['system_ready'] is True
        assert receipt['readiness']['sr_ready'] is True
    else:
        assert ctx is not None
        assert coverage is None
        assert rows == []


def test_ctx_failure_receipt_scalar_projection_is_bounded():
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    names = {
        '_finite_positive_sr_value',
        '_sr_swing_prerequisite_failures',
        '_bounded_ctx_failure_scalar',
        '_ctx_fail_prerequisite_detail',
    }
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    env = {'math': math}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'bot.py', 'exec'), env)

    scalar = env['_ctx_fail_prerequisite_detail'](
        {'recent_high': 0, 'recent_low': 0},
        {'support_resistance': {
            'swing_high': {'not': 'scalar'},
            'swing_low': [90],
            'sr_state': 'R' * 1000,
            'ts': {'not': 'scalar'},
        }},
    )
    assert scalar['sr_swing_high'] is None
    assert scalar['sr_swing_low'] is None
    assert scalar['sr_state'] == 'R' * 64
    assert scalar['sr_timestamp'] is None

    finite = env['_ctx_fail_prerequisite_detail'](
        {'recent_high': 0, 'recent_low': 0},
        {'support_resistance': {
            'swing_high': float('nan'),
            'swing_low': float('inf'),
            'sr_state': 'RANGE',
            'ts': '2026-09-14T00:00:00Z',
        }},
    )
    assert finite['sr_swing_high'] is None
    assert finite['sr_swing_low'] is None
    assert finite['sr_state'] == 'RANGE'
    assert finite['sr_timestamp'] == '2026-09-14T00:00:00Z'
