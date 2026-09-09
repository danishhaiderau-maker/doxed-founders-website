import ast
import copy
import hashlib
import json
import threading
import time
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import Mock
import pytest
from research.lab_accounting import LabOutcomeReconciler,completed_legacy_lab

IDENTITY={'epoch':'epoch','policies':{'CONTINUOUS':{'version':'v','signature':'sig'}}}


def row(i,**extra):
    return {'schema':'shadow_lane_outcome_v1','study_id':str(i),'trade_id':str(i),
        'epoch_id':'epoch','collection_epoch_id':'epoch','research_lane':'CONTINUOUS',
        'policy_version':'v','policy_signature':'sig','collection_mode':'LAB',
        'filled':True,'exit_reason':'TIME_EXIT','net_pnl_usd':.1,'direction':'SHORT',**extra}


def write(path,rows): path.write_bytes(b''.join((json.dumps(r)+'\n').encode() for r in rows))


def test_bounded_complete_counts_exclude_truncation_conflicts_and_old_policy(tmp_path):
    path=tmp_path/'outcomes.jsonl'
    rows=[row(i) for i in range(105)]+[row('truncated',exit_reason='BUFFER_TRUNCATED'),
        row('mark',exit_reason='MARK_TO_MARKET'),row('old',epoch_id='old'),
        row('unsigned',policy_signature=None),row('conflict'),row('conflict',net_pnl_usd=-1),row(0)]
    write(path,rows); original=path.read_bytes(); reader=LabOutcomeReconciler()
    first=reader.advance(path,IDENTITY)['CONTINUOUS']
    assert first['lab_accounting_status']=='BUILDING' and first['lab_closes'] is None
    final=reader.advance(path,IDENTITY)['CONTINUOUS']
    assert final['lab_accounting_status']=='CURRENT' and final['lab_closes']==105
    assert final['lab_wins']==105 and final['lab_losses']==0
    assert final['lab_incomplete_outcomes']==2 and final['lab_conflicting_studies']==1
    assert final['lab_net_after_costs_usd'] is None and final['lab_costs_status']=='UNMODELED'
    assert path.read_bytes()==original


def test_file_generation_change_resets_and_partial_line_boundary_resumes(tmp_path):
    path=tmp_path/'outcomes.jsonl'; write(path,[row(i) for i in range(6)])
    reader=LabOutcomeReconciler(); size=len((json.dumps(row(0))+'\n').encode())
    assert reader.advance(path,IDENTITY,max_bytes=size+3)['CONTINUOUS']['lab_accounting_status']=='BUILDING'
    write(path,[row('new',net_pnl_usd=-.1)])
    assert reader.advance(path,IDENTITY)['CONTINUOUS']['lab_losses']==1


def function(name,env):
    path=Path(__file__).with_name('bot.py'); tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),env)
    return env[name]


@pytest.mark.parametrize('reason,expected',[('BUFFER_TRUNCATED',0),('MARK_TO_MARKET',0),('TIME_EXIT',1)])
def test_actual_finalizer_keeps_raw_outcome_but_not_false_completed_close(reason,expected):
    written=[]; ledger=Mock(); closed=Mock()
    env={'is_patient_chase_lane':lambda _:False,'simulate_replay_outcome':lambda _:row('id',exit_reason=reason),
        'hashlib':hashlib,'utc_iso':lambda *a:'2026-09-09T00:00:00Z','datetime':datetime,'timezone':timezone,
        'time':time,'copy':copy,'EXECUTION_FIX_VERSION':'build','ANALYZER_SYNC_ID':'a',
        'SHADOW_LANE_OUTCOME_FILE':'unused','_safe_append_jsonl':lambda p,r,**kw:written.append(r),
        'logger':Mock(),'update_lane_lab_pnl_ledger':ledger,'close_replay_buffer':closed}
    function('finalize_shadow_lane_collecting',env)('id',{'research_lane':'CONTINUOUS','collection_mode':'LAB'})
    assert len(written)==1 and ledger.call_count==expected and closed.call_count==1
    assert written[0]['economics_basis']=='LEGACY_GROSS_BEFORE_COSTS'
    assert written[0]['net_after_costs_usd'] is None
    from research.lab_history import _project
    projected=_project(written[0])
    assert projected['completed_strategy_exit'] is bool(expected)
    assert projected['gross_before_costs_usd']==written[0]['net_pnl_usd']
    assert projected['net_after_costs_usd'] is None and projected['costs_status']=='UNMODELED'


def test_actual_loader_includes_continuous_exact_identity_and_summary_unknown(tmp_path):
    path=tmp_path/'outcomes.jsonl'; write(path,[row(1)])
    env={'_lab_reconcile_reader':None,'_lab_reconcile_lock':threading.Lock(),
        'COMBO_EXECUTION_LANES':(),'RESEARCH_LANE_CONTINUOUS':'CONTINUOUS','Path':Path,
        'SHADOW_LANE_OUTCOME_FILE':str(path),'_lab_history_current_identity':lambda:{'epoch':'epoch','policies':{'CONTINUOUS':'v'}},
        '_shadow_policy_identity':lambda **kw:{'collection_epoch_id':'epoch','policy_signature':'sig'},
        'get_exit_config_for_lane':lambda _: {},'invert_signal_active':lambda:False}
    metrics=function('_load_reconciled_lab_outcome_metrics',env)()['CONTINUOUS']
    assert metrics['lab_closes']==1
    summary=function('_session_stats_from_lane_metrics',{'_truthful_approve_to_fill_pct':lambda *a:None})
    result=summary(metrics)
    assert 'gross before costs' in result['lab_summary_line'] and 'costs unknown' in result['lab_summary_line']
    result=summary({'lab_accounting_status':'BUILDING','lab_closes':99,'lab_net_pnl':99})
    assert result['lab_closes'] is None and result['lab_net_pnl'] is None
    assert 'totals unavailable' in result['lab_summary_line']
