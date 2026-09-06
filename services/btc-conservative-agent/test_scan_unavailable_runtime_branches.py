"""Run actual extracted runtime branches without importing the trading runtime."""
import ast
import copy
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

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
