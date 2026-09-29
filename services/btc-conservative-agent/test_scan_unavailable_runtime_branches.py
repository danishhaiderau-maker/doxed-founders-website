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


@pytest.mark.parametrize('high,low,reason', [
    (None, 90, 'SR_SWING_HIGH_MISSING'),
    (110, None, 'SR_SWING_LOW_MISSING'),
    (float('nan'), 90, 'SR_SWING_HIGH_INVALID'),
    (110, False, 'SR_SWING_LOW_INVALID'),
    (90, 110, 'SR_SWING_RANGE_INVALID'),
])
def test_pure_context_failure_records_truthful_both_side_unavailable(
    high, low, reason,
):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    names = {'_finite_positive_sr_value', '_sr_swing_pair_ready',
        '_sr_swing_prerequisite_failures', '_bounded_ctx_failure_scalar',
        '_ctx_fail_prerequisite_detail', 'build_pure_ai_context',
        '_record_ctx_fail_unavailable_coverage',
        '_build_pure_ai_context_with_evidence'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    rows, external_calls = [], []
    numeric = [1.0] * 10
    def append(_path, row, **_kwargs):
        rows.append(copy.deepcopy(row)); return True
    def external(*_args, **_kwargs):
        external_calls.append(True); raise AssertionError('no AI/order call is permitted')
    env = dict(math=math, time=time, state={'last_edge':0}, latest_candles=[object()]*200,
        price_buffer=numeric, volume_buffer=numeric, delta_buffer=numeric,
        delta_change_buffer=numeric,
        imbalance_buffer=numeric, candle_range_buffer=numeric,
        wick_ratio_buffer=numeric, body_ratio_buffer=numeric, velocity_buffer=numeric,
        logger=SimpleNamespace(warning=lambda *a:None), nz=lambda v,default=None:v,
        sanitize_features=lambda v:v, get_aggregated=lambda v:v[-1],
        compute_volume_ratio=lambda:1.0, get_edge_threshold=lambda:0.0,
        get_funding_snapshot_for_ai=lambda:{}, get_market_context_for_ai=lambda:{},
        enrich_ai_context_upgrade=lambda v:v, _stamp_3m_exhaustion_for_ai=lambda v:v,
        sanitize_ai_inputs=lambda v:v, _runtime_git_rev_exact=lambda:'a'*40,
        _collector_v22_epoch_id=lambda:'epoch-fixture',
        _runtime_readiness_components=lambda now:{'system_ready':True,'sr_ready':False},
        _safe_append_jsonl=append, AI_INPUT_LOG_FILE='fixture-only',
        call_deepseek_api=external, execute_simulated_order=external)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'bot.py','exec'),env)
    snapshot = {'price':100, 'support_resistance':{
        'swing_high':high, 'swing_low':low, 'sr_state':'RANGE',
        'ts':'2026-09-20T00:00:00Z'},
        'ema_status':{'ema9':101,'ema21':100,'ema200':99}, 'regime':'RANGE'}
    ctx, coverage = env['_build_pure_ai_context_with_evidence'](
        snapshot, {'ret_1m':numeric,'ret_5m':numeric},
        {'trade_id':'scan-ctx-1','shared_ai_call_id':'scan-ctx-1'})
    assert ctx is None and coverage['write_status'] == 'ACCEPTED'
    assert external_calls == [] and len(rows) == 1
    receipt = rows[0]
    assert receipt['prerequisite_failures'] == [reason]
    assert receipt['ai_evaluated'] is False
    assert receipt['simulated_trade_count'] == 0
    assert receipt['qualification_eligible'] is False
    assert set(receipt['directional_coverage']) == {'LONG', 'SHORT'}
    assert all(v['status'] == 'UNAVAILABLE'
        for v in receipt['directional_coverage'].values())


def test_valid_structural_context_keeps_existing_path_and_writes_no_gap():
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    names = {'_finite_positive_sr_value', '_sr_swing_pair_ready',
        '_sr_swing_prerequisite_failures', '_bounded_ctx_failure_scalar',
        '_ctx_fail_prerequisite_detail', 'build_pure_ai_context',
        '_record_ctx_fail_unavailable_coverage',
        '_build_pure_ai_context_with_evidence'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    rows, numeric = [], [1.0] * 10
    env = dict(math=math,time=time,state={'last_edge':0},latest_candles=[],
        price_buffer=numeric,volume_buffer=numeric,delta_buffer=numeric,
        delta_change_buffer=numeric,
        imbalance_buffer=numeric,candle_range_buffer=numeric,wick_ratio_buffer=numeric,
        body_ratio_buffer=numeric,velocity_buffer=numeric,
        logger=SimpleNamespace(warning=lambda *a:None),nz=lambda v,default=None:v,
        sanitize_features=lambda v:v,get_aggregated=lambda v:v[-1],
        compute_volume_ratio=lambda:1.0,get_edge_threshold=lambda:0.0,
        get_funding_snapshot_for_ai=lambda:{},get_market_context_for_ai=lambda:{},
        enrich_ai_context_upgrade=lambda v:v,_stamp_3m_exhaustion_for_ai=lambda v:v,
        sanitize_ai_inputs=lambda v:v,_runtime_git_rev_exact=lambda:'a'*40,
        _collector_v22_epoch_id=lambda:'epoch-fixture',_runtime_readiness_components=lambda now:{},
        _safe_append_jsonl=lambda *a,**k:rows.append(a[1]) or True,AI_INPUT_LOG_FILE='x')
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'bot.py','exec'),env)
    ctx, coverage = env['_build_pure_ai_context_with_evidence'](
        {'price':100,'support_resistance':{'swing_high':110,'swing_low':90},
         'ema_status':{'ema9':101,'ema21':100,'ema200':99}},
        {'ret_1m':numeric,'ret_5m':numeric}, {'trade_id':'valid'})
    assert ctx['recent_high'] == 110 and ctx['recent_low'] == 90
    assert coverage is None and rows == []
