import json
import threading
import pytest
from research_scan_census import ScanCensus,wrap_scan_census,observe_opportunity
from research_v3_store import V3EvidenceStore
from research_v3_bridge import dual_write_lane_decision


def records(store):
    return [json.loads(line) for line in store.ledger_path('decision').read_text().splitlines()]


@pytest.mark.parametrize('verdict',['REJECT','NO_TRADE'])
def test_real_store_both_direction_reference_with_rejected_scan(tmp_path,verdict):
    store=V3EvidenceStore(tmp_path,epoch_id='epoch')
    def scan(event):
        return dual_write_lane_decision({'trade_id':event['research_scan_id'],
            'shared_ai_call_id':event['research_scan_id'],'shared_ai_call_ts_epoch':1000.,
            'symbol':'BTCUSD','raw_direction':'NO_TRADE','feature_snapshot_at_signal':{}},
            lane='CONTINUOUS',policy_decision=verdict,execution_disposition='AI_REJECTED_NO_ORDER',
            exact_reason='AI_REJECT',epoch_id='epoch',data_dir=str(tmp_path),
            lane_policy={'policy_id':'CONTINUOUS','paper_only':True})
    run=wrap_scan_census(scan,eligible=lambda e:True,store_factory=lambda:store,clock=lambda:1001.,on_failure=lambda c:pytest.fail(c))
    run({})
    rows=records(store); admission=next(r for r in rows if r.get('decision_stage')=='SCAN_ADMISSION')
    terminal=next(r for r in rows if r.get('decision_stage')=='SCAN_DISPOSITION')
    assert terminal['scan_id']==admission['scan_id']
    assert terminal['disposition']=='OPPORTUNITY_RECORDED'
    assert terminal['observed_lane_verdicts']==[verdict]
    assert set(terminal['opportunity_references'][0]['directions'])=={'LONG','SHORT'}
    assert all(terminal['opportunity_references'][0]['directions'].values())
    assert not terminal['qualification_eligible']


def test_early_return_is_admitted_not_fabricated_no_fill(tmp_path):
    store=V3EvidenceStore(tmp_path,epoch_id='epoch')
    run=wrap_scan_census(lambda e:None,eligible=lambda e:True,store_factory=lambda:store,clock=lambda:10.,on_failure=lambda c:pytest.fail(c))
    assert run({}) is None
    terminal=records(store)[-1]
    assert terminal['disposition']=='NO_OPPORTUNITY_UNKNOWN'
    assert 'entry_outcome' not in terminal


def test_write_failure_only_blocks_new_scans_and_preserves_original_exception(tmp_path,monkeypatch):
    store=V3EvidenceStore(tmp_path,epoch_id='epoch'); calls=[]; failures=[]
    monkeypatch.setattr(store,'append',lambda *a,**k:{'blocked':True})
    run=wrap_scan_census(lambda e:calls.append(e),eligible=lambda e:e.get('fresh',False),
        store_factory=lambda:store,clock=lambda:10.,on_failure=failures.append)
    run({'management':True})
    assert len(calls)==1
    assert run({'fresh':True})['exact_reason']=='SCAN_CENSUS_ADMISSION_FAILED'
    assert len(calls)==1 and failures==['SCAN_CENSUS_ADMISSION_FAILED']


def test_restart_sequence_and_unfinished_scans_remain_explicit(tmp_path):
    store=V3EvidenceStore(tmp_path,epoch_id='epoch')
    first=ScanCensus(store,clock=lambda:10.,boot_id='first'); a=first.admit()
    second=ScanCensus(store,clock=lambda:20.,boot_id='second'); b=second.admit()
    rows=records(store)
    gap=next(r for r in rows if r.get('continuity')=='RESTART_GAP_UNKNOWN')
    assert gap['unresolved_scan_ids']==[a]
    assert b.endswith('-2') and not gap['continuous_market_coverage']


def test_uncertain_committed_admission_replays_without_duplicate_or_reusing_sequence(tmp_path,monkeypatch):
    store=V3EvidenceStore(tmp_path,epoch_id='epoch'); append=store.append; failed=[]
    def interrupted(ledger,row):
        result=append(ledger,row)
        if row.get('decision_stage')=='SCAN_ADMISSION' and not failed:
            failed.append(True); raise OSError('injected after durable commit')
        return result
    monkeypatch.setattr(store,'append',interrupted)
    with pytest.raises(OSError): ScanCensus(store,clock=lambda:10.,boot_id='first').admit()
    scan=ScanCensus(store,clock=lambda:20.,boot_id='second').admit()
    admissions=[r for r in records(store) if r['decision_stage']=='SCAN_ADMISSION']
    assert [r['scan_sequence'] for r in admissions]==[1,2]
    assert scan==admissions[-1]['scan_id']


@pytest.mark.parametrize('boundary',['after_write','before_journal_commit'])
def test_disposition_commit_active_removal_crash_retry_exact_original_bytes(tmp_path,monkeypatch,boundary):
    store=V3EvidenceStore(tmp_path,epoch_id='epoch')
    census=ScanCensus(store,clock=lambda:10.,boot_id='first'); scan=census.admit()
    if boundary=='after_write':
        original=census._write
        def fail(state,row):
            original(state,row)
            if row.get('decision_stage')=='SCAN_DISPOSITION': raise OSError('injected terminal boundary')
        monkeypatch.setattr(census,'_write',fail)
    else:
        original=census._save
        def fail(state):
            if state.get('pending') is None and scan not in state['active']:
                raise OSError('injected terminal boundary')
            original(state)
        monkeypatch.setattr(census,'_save',fail)
    with pytest.raises(OSError): census.finish(scan,refs=[],verdicts=['REJECT'])
    before=store.ledger_path('decision').read_bytes()
    recovered=ScanCensus(store,clock=lambda:20.,boot_id='second')
    recovered.finish(scan,refs=[],verdicts=['REJECT'])
    assert store.ledger_path('decision').read_bytes()==before
    assert scan not in recovered._load()['active']
    assert recovered._load()['pending'] is None
    with pytest.raises(ValueError,match='PAYLOAD_CONFLICT'):
        recovered.finish(scan,refs=[],verdicts=['ACCEPT'])


def test_finally_write_failure_does_not_mask_exception(tmp_path,monkeypatch):
    store=V3EvidenceStore(tmp_path,epoch_id='epoch')
    def fail(*a,**k): raise RuntimeError('journal failed')
    monkeypatch.setattr(ScanCensus,'finish',fail)
    def original(event): raise KeyError('original')
    run=wrap_scan_census(original,eligible=lambda e:True,store_factory=lambda:store,
        clock=lambda:10.,on_failure=fail)
    with pytest.raises(KeyError,match='original'): run({})


def test_async_context_not_silently_inherited(tmp_path):
    store=V3EvidenceStore(tmp_path,epoch_id='epoch')
    def scan(event):
        thread=threading.Thread(target=lambda:observe_opportunity(store,{'record_id':'absent'}))
        thread.start(); thread.join()
    run=wrap_scan_census(scan,eligible=lambda e:True,store_factory=lambda:store,
        clock=lambda:10.,on_failure=lambda c:pytest.fail(c))
    run({})
    assert records(store)[-1]['async_fanout_coverage']=='UNPROVEN'
    assert records(store)[-1]['disposition']=='NO_OPPORTUNITY_UNKNOWN'


def test_actual_bot_wrapper_admits_before_manual_pause_filter(tmp_path):
    import ast,copy,time
    from pathlib import Path
    from types import SimpleNamespace
    tree=ast.parse(Path('bot.py').read_text(encoding='utf-8'))
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='process_signal')
    assignment=next(n for n in tree.body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='process_signal' for t in n.targets))
    from research_scan_census import canonical_scan_store
    paused=[]
    namespace={'copy':copy,'time':time,'manual_admin_pause_active':lambda:True,
        'is_research_data_collection':lambda:False,'is_patient_chase_lane':lambda lane:False,
        '_manual_pause_block_entry':lambda *a:paused.append(True),'RESEARCH_LANE_AI_SCAN':'AI_SCAN',
        'is_ai_scan_lane':lambda lane:lane=='AI_SCAN','wrap_scan_census':wrap_scan_census,
        'canonical_scan_store':canonical_scan_store,'_data_sync_runtime_root':lambda:tmp_path,
        '_collector_v22_epoch_id':lambda:'epoch','logger':SimpleNamespace(error=lambda c:pytest.fail(c))}
    exec(compile(ast.Module(body=[function,assignment],type_ignores=[]),'bot.py','exec'),namespace)
    namespace['process_signal']({'research_lane':'AI_SCAN'})
    assert paused==[True]
    rows=records(V3EvidenceStore(tmp_path,epoch_id='epoch'))
    assert [r['decision_stage'] for r in rows]==['SCAN_CENSUS_BOOT','SCAN_ADMISSION','SCAN_DISPOSITION']
