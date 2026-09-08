import ast
import copy
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import pytest
from research_scan_census import wrap_scan_census
from research_v3_store import V3EvidenceStore
from research_v3_bridge import write_pre_ai_scan_opportunity


@pytest.mark.parametrize('hook',['cooldown','pre_ai'])
def test_actual_skip_branch_records_both_sides_without_ai_or_order(tmp_path,monkeypatch,hook):
    tree=ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    helper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_capture_pre_ai_skip_research')
    process=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='process_signal')
    name='trigger_ok' if hook=='cooldown' else 'invoke_ai'
    branch=next(n for n in ast.walk(process) if isinstance(n,ast.If) and isinstance(n.test,ast.UnaryOp)
                and isinstance(n.test.operand,ast.Name) and n.test.operand.id==name)
    function=ast.FunctionDef(name='skip',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg='event')],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[branch],decorator_list=[])
    features={'capture_schema':'measured_feature_capture_v1','captured_at_ts':999.,
        'regime':{'value':'BULL','observed_ts':999.},'bbo':{'bid':99.,'ask':101.,'bid_qty':1.,'ask_qty':1.}}
    from research import runtime_baseline_declaration as declaration
    monkeypatch.setattr(declaration,'build_runtime_baseline_declaration',lambda **k:{'declaration':None,'status':'UNSUPPORTED'})
    noop=lambda *a,**k:None
    namespace={'copy':copy,'time':SimpleNamespace(time=lambda:1000.),'is_research_data_collection':lambda:True,
        'is_valid_feature_set':lambda f:True,'validate_ai_features':lambda ctx:(True,None),
        'build_pure_ai_context':lambda *a:{'signal_ts':998.},'get_aggregated':lambda x:0.,
        'features':features,'ctx':{'signal_ts':998.},'trigger_ok':False,'invoke_ai':False,
        'trigger_reason':'AI_COOLDOWN','ai_gate_reason':'TEST_GATE','edge_score':1.,'research_lane':'AI_SCAN',
        'state':{'debug_state':{}},'state_lock':nullcontext(),'logger':SimpleNamespace(info=noop,warning=lambda s:pytest.fail(s)),
        'increment_pipeline_funnel':noop,'log_no_signal_with_context':noop,'full_pipeline_trace':noop,
        'update_debug_state_always':noop,'_set_lane_pipeline_stage':noop,
        '_capture_runtime_quantity_constraints':lambda **k:{},'BITFINEX_WS_SYMBOL':'BTCUSD',
        '_runtime_git_rev_exact':lambda:'rev','get_trading_fee_rates':lambda:(0.,0.),'FIXED_MARGIN_USDT':.25,
        '_state_leverage':lambda:100,'_collector_v22_epoch_id':lambda:'epoch','_data_sync_runtime_root':lambda:tmp_path,
        'evaluate_signal_with_ai':lambda *a,**k:pytest.fail('AI called'),
        'place_order':lambda *a,**k:pytest.fail('order created')}
    for key in ('ret_1m','ret_5m','velocity','volume','delta','delta_change','imbalance','candle_range','wick_ratio','body_ratio'):
        namespace[key+'_buffer']=[]
    exec(compile(ast.fix_missing_locations(ast.Module(body=[helper,function],type_ignores=[])),'bot.py','exec'),namespace)
    store=V3EvidenceStore(tmp_path,epoch_id='epoch')
    run=wrap_scan_census(namespace['skip'],eligible=lambda e:True,store_factory=lambda:store,
        clock=lambda:1000.,on_failure=lambda c:pytest.fail(c))
    run({})
    rows=[json.loads(line) for line in store.ledger_path('opportunity').read_text().splitlines()]
    assert len(rows)==1 and rows[0]['raw_ai_decision']=='AI_NOT_CALLED'
    assert rows[0]['raw_direction']=='UNKNOWN' and rows[0]['actual_ai_call_id'] is None
    assert not rows[0]['ai_evaluated']
    assert set(rows[0]['baseline_schedule_snapshot']['directional_schedules'])=={'LONG','SHORT'}
    assert not store.ledger_path('order_intent').exists()
    dispositions=[json.loads(line) for line in store.ledger_path('decision').read_text().splitlines()]
    assert dispositions[-1]['observed_lane_verdicts']==['AI_NOT_CALLED']
    assert dispositions[-1]['opportunity_references']


def test_exact_scan_retry_is_idempotent_and_missing_measurement_refused(tmp_path):
    source={'research_scan_id':'scan-census-test-1','signal_ts':1000.,'symbol':'BTCUSD',
        'feature_snapshot_at_signal':{'capture_schema':'measured_feature_capture_v1','captured_at_ts':999.}}
    write_pre_ai_scan_opportunity(source,epoch_id='epoch',data_dir=str(tmp_path))
    second=write_pre_ai_scan_opportunity(source,epoch_id='epoch',data_dir=str(tmp_path))
    assert second['opportunity']['duplicate']
    source['feature_snapshot_at_signal'].pop('capture_schema')
    with pytest.raises(ValueError,match='MEASURED_CONTEXT_MISSING'):
        write_pre_ai_scan_opportunity(source,epoch_id='epoch',data_dir=str(tmp_path))


@pytest.mark.parametrize('reason',['no_census','not_collecting','invalid_features'])
def test_skip_capture_bypasses_ineligible_or_invalid_inputs(reason):
    tree=ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    helper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_capture_pre_ai_skip_research')
    namespace={'is_research_data_collection':lambda:reason!='not_collecting',
        'is_valid_feature_set':lambda f:False,
        'build_pure_ai_context':lambda *a:pytest.fail('built context for invalid scan')}
    exec(compile(ast.Module(body=[helper],type_ignores=[]),'bot.py','exec'),namespace)
    event={} if reason=='no_census' else {'research_scan_id':'scan-census-test'}
    assert namespace['_capture_pre_ai_skip_research'](event,{},'PRE_AI_GATE') is None


@pytest.mark.parametrize('captured',[None,True,float('nan'),float('inf'),1001.])
def test_capture_timestamp_missing_or_future_cannot_be_backdated(tmp_path,captured):
    source={'research_scan_id':'scan-census-test-1','signal_ts':1000.,'symbol':'BTCUSD',
        'feature_snapshot_at_signal':{'capture_schema':'measured_feature_capture_v1','captured_at_ts':captured}}
    with pytest.raises(ValueError,match='CAPTURE_TIME_INVALID_OR_FUTURE'):
        write_pre_ai_scan_opportunity(source,epoch_id='epoch',data_dir=str(tmp_path))


@pytest.mark.parametrize('failed_ledger',['pre_entry_features','opportunity'])
def test_ambiguous_write_response_never_claims_durability(tmp_path,monkeypatch,failed_ledger):
    append=V3EvidenceStore.append
    monkeypatch.setattr(V3EvidenceStore,'append',lambda self,ledger,row:
        {} if ledger==failed_ledger else append(self,ledger,row))
    source={'research_scan_id':'scan-census-test-1','signal_ts':1000.,'symbol':'BTCUSD',
        'feature_snapshot_at_signal':{'capture_schema':'measured_feature_capture_v1','captured_at_ts':999.}}
    with pytest.raises(ValueError,match='NOT_DURABLE'):
        write_pre_ai_scan_opportunity(source,epoch_id='epoch',data_dir=str(tmp_path))


def test_real_store_pre_ai_opportunity_reaches_both_direction_replay_unknown(tmp_path):
    from research.entry_baseline_replay import materialize_v3_opportunity_replay
    source={'research_scan_id':'scan-census-replay-1','signal_ts':1000.,'symbol':'BTCUSD',
        'feature_snapshot_at_signal':{'capture_schema':'measured_feature_capture_v1','captured_at_ts':999.}}
    write_pre_ai_scan_opportunity(source,epoch_id='epoch',data_dir=str(tmp_path))
    store=V3EvidenceStore(tmp_path,epoch_id='epoch')
    original=store.ledger_path('opportunity').read_bytes()
    report=materialize_v3_opportunity_replay(tmp_path)
    assert report['same_opportunity_count']==1
    assert report['directional_episode_count']==2
    assert {r['direction'] for r in report['episode_receipts']}=={'LONG','SHORT'}
    assert all(r['original_ai_direction']=='UNKNOWN' for r in report['episode_receipts'])
    assert all(r['raw_ai_decision']=='AI_NOT_CALLED' and r['ai_evaluated'] is False
        and r['research_scan_id']==source['research_scan_id'] for r in report['episode_receipts'])
    for receipt in report['episode_receipts']:
        assert receipt['results']
        assert all(r['outcome_state']=='UNKNOWN' and r['supported'] is False for r in receipt['results'])
        assert all(r['rejection_codes'] for r in receipt['results'])
    assert store.ledger_path('opportunity').read_bytes()==original
    assert json.loads(original)['raw_ai_decision']=='AI_NOT_CALLED'
    assert not store.ledger_path('order_intent').exists()


@pytest.mark.parametrize('verdict,evaluated', [('AI_NOT_CALLED',False),('REJECT',True),('NO_TRADE',True),(None,None)])
def test_replay_preserves_explicit_ai_treatment_without_inference(tmp_path,verdict,evaluated):
    from research.entry_baseline_replay import materialize_v3_opportunity_replay
    source={'research_scan_id':'scan-census-projection-1','signal_ts':1000.,'symbol':'BTCUSD',
        'feature_snapshot_at_signal':{'capture_schema':'measured_feature_capture_v1','captured_at_ts':999.}}
    write_pre_ai_scan_opportunity(source,epoch_id='epoch',data_dir=str(tmp_path))
    store=V3EvidenceStore(tmp_path,epoch_id='epoch')
    row=json.loads(store.ledger_path('opportunity').read_bytes())
    # Synthetic alternate historical source; no verdict inferred from outcome or direction.
    for key in ('raw_ai_decision','ai_evaluated','research_scan_id'):
        row.pop(key,None)
    if verdict is not None:
        row.update(raw_ai_decision=verdict,ai_evaluated=evaluated,research_scan_id='explicit-scan')
    store.ledger_path('opportunity').write_text(json.dumps(row)+'\n',encoding='utf-8')
    report=materialize_v3_opportunity_replay(tmp_path)
    assert len(report['episode_receipts'])==2
    for receipt in report['episode_receipts']:
        assert receipt['raw_ai_decision']==verdict
        assert receipt['ai_evaluated'] is evaluated
        assert receipt['research_scan_id']==('explicit-scan' if verdict is not None else None)
        assert all(r['outcome_state']=='UNKNOWN' for r in receipt['results'])
