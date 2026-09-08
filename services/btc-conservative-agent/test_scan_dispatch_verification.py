import json
import pytest
from research_scan_census import ScanCensus
from research_v3_store import V3EvidenceStore
from research import local_scan_reconciliation as module


@pytest.mark.parametrize('stage,status',[('OTHER','ENQUEUED'),('SCAN_FANOUT_ADMISSION','COMPLETE')])
def test_raw_admission_role_and_status_required(monkeypatch,stage,status):
    from research import scan_dispatch_verification as verifier
    identity=dict(scan_id='scan-census-test',research_lane='LANE',policy_signature='policy',
        job_key='LANE:scan-census-test',payload_sha256='a'*64)
    plan={'record_id':'plan','decision_stage':'SCAN_FANOUT_PLAN',**identity}
    parent={'decision_stage':'SCAN_ADMISSION','scan_id':identity['scan_id']}
    admission={'plan_record_id':'plan','decision_stage':stage,'admission_status':status,**identity}
    monkeypatch.setattr(verifier,'_verify_page_ref',lambda root,ref,*a:ref)
    with pytest.raises(ValueError,match='ADMISSION_CONFLICT'):
        verifier.verify_dispatch(None,plan,[admission],[],{},'config',[parent])


@pytest.mark.parametrize('resolution',['AWAITING','NO_ORDER','ORDER_SUBMITTED',None])
def test_actual_durable_plan_child_join(tmp_path,monkeypatch,resolution):
    root=tmp_path/'data'; store=V3EvidenceStore(root,epoch_id='epoch')
    census=ScanCensus(store,clock=lambda:1000.,boot_id='boot'); scan=census.admit()
    opts=dict(lane='CONTINUOUS',policy_signature='policy',job_key='CONTINUOUS:'+scan,payload_sha256='a'*64)
    plan=census.fanout(scan,**opts)
    census.fanout(scan,**opts,admitted=True); census.finish(scan,refs=[])
    store.append('opportunity',{'record_id':'opp','shared_ai_call_id':scan})
    if resolution:
        store.append('lifecycle',{'record_id':'child','shared_ai_call_id':scan,
            'research_lane':'CONTINUOUS','policy_signature':'policy','resolution_scope':'LANE_ENTRY',
            'entry_resolution':resolution,'entry_resolution_terminal':resolution!='AWAITING',
            'research_fanout_plan_reference':plan})
    source={'epoch':'epoch','revision':store._identity_binding()['source_revision']}
    monkeypatch.setattr(module,'_check',lambda *a,**k:source)
    monkeypatch.setattr(module,'_source',lambda token:token)
    args=dict(repo_root=tmp_path/'repo',data_root=root,source_revision=source['revision'],
        config_signature=store._identity_binding()['tile_config_signature'])
    result=module.reconcile_scans(**args)
    dispatch=result['observed_dispatch_page'][0]
    assert dispatch['admission_status']=='ENQUEUED'
    assert dispatch['entry_resolution']==(resolution or 'UNKNOWN')
    assert dispatch['trade_completed'] is None and not dispatch['qualification_eligible']
    if resolution:
        path=store.ledger_path('lifecycle'); parked=path.with_suffix('.parked')
        before=path.stat(); path.rename(parked)
        assert module.reconcile_scans(**args)['observed_dispatch_page'][0]['entry_resolution']=='UNKNOWN'
        parked.rename(path)
        after=path.stat()
        assert (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns)
        assert module.reconcile_scans(**args)['observed_dispatch_page'][0]['entry_resolution']==resolution
    receipt=store._record_receipt_path('decision',plan['plan_record_id'])
    data=json.loads(receipt.read_text()); data['row_sha256']='0'*64; receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='RECEIPT_MISMATCH'): module.reconcile_scans(**args)
