import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from research_scan_census import ScanCensus, wrap_scan_census
from research_v3_store import V3EvidenceStore


def rows(store):
    return [json.loads(x) for x in store.ledger_path('decision').read_text().splitlines()]


@pytest.mark.parametrize('accepted',[True,False])
def test_actual_enqueue_has_prior_durable_plan_and_ambiguous_false(tmp_path,accepted):
    store=V3EvidenceStore(tmp_path,epoch_id='epoch')
    source=ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    func=next(x for x in source.body if isinstance(x,ast.FunctionDef) and x.name=='_enqueue_combo_lane_execution')
    calls=[]
    def submit(key,payload,**kwargs):
        assert rows(store)[-1]['decision_stage']=='SCAN_FANOUT_PLAN'
        calls.append(key)
        return accepted
    ns=dict(copy=copy,time=SimpleNamespace(time=lambda:20.),
        _shared_ai_call_id=lambda ai_result,ctx:ctx['research_scan_id'],
        _v3_lane_policy_material=lambda lane:{'policy_signature':'policy-sig'},
        _get_combo_lane_execution_worker=lambda lane:SimpleNamespace(submit=submit),
        logger=SimpleNamespace(warning=lambda *a:None))
    exec(compile(ast.Module(body=[func],type_ignores=[]),'<enqueue>','exec'),ns)
    def body(event):
        return ns[func.name](event,{},1.,{},'CONTINUOUS','trigger')
    run=wrap_scan_census(body,eligible=lambda e:True,store_factory=lambda:store,
                        clock=lambda:10.,on_failure=lambda x:pytest.fail(x))
    assert run({}) is accepted
    result=rows(store)
    receipt=next(r for r in result if r.get('decision_stage')=='SCAN_FANOUT_ADMISSION')
    assert receipt['admission_status']==('ENQUEUED' if accepted else 'ADMISSION_UNKNOWN')
    assert receipt['completion_status']=='UNKNOWN' and len(calls)==1


def test_pending_plan_replay_and_restart_unresolved(tmp_path,monkeypatch):
    store=V3EvidenceStore(tmp_path,epoch_id='epoch'); census=ScanCensus(store,clock=lambda:10.,boot_id='boot1')
    scan=census.admit(); append=store.append
    def fail(ledger,row):
        if row.get('decision_stage')=='SCAN_FANOUT_PLAN': raise OSError('crash')
        return append(ledger,row)
    monkeypatch.setattr(store,'append',fail)
    args=dict(lane='CONTINUOUS',policy_signature='sig',job_key='CONTINUOUS:'+scan,payload_sha256='a'*64)
    with pytest.raises(OSError): census.fanout(scan,**args)
    monkeypatch.setattr(store,'append',append)
    restored=ScanCensus(store,clock=lambda:11.,boot_id='boot2')
    restored.admit()
    restored.fanout(scan,**args)
    assert len([r for r in rows(store) if r.get('decision_stage')=='SCAN_FANOUT_PLAN'])==1
    assert scan in next(r for r in rows(store) if r.get('record_id')=='scan-census-boot:boot2')['unresolved_scan_ids']
    assert not any(r.get('decision_stage')=='SCAN_FANOUT_ADMISSION' for r in rows(store))
    with pytest.raises(ValueError,match='JOB_CONFLICT'):
        restored.fanout(scan,**{**args,'payload_sha256':'b'*64})
