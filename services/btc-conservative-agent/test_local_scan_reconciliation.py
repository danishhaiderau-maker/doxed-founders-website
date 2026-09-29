import json
import pytest
from research import local_scan_reconciliation as module
from research_scan_census import ScanCensus
from research_v3_store import V3EvidenceStore
from research_v3_bridge import write_pre_ai_scan_opportunity


def fixture(tmp_path,monkeypatch):
    root=tmp_path/'mirror'; store=V3EvidenceStore(root,epoch_id='epoch')
    census=ScanCensus(store,clock=lambda:1000.,boot_id='boot')
    scan=census.admit(); census.finish(scan,refs=[])
    write_pre_ai_scan_opportunity({'research_scan_id':scan,'signal_ts':1000.,'symbol':'BTCUSD',
        'feature_snapshot_at_signal':{'capture_schema':'measured_feature_capture_v1','captured_at_ts':999.}},
        epoch_id='epoch',data_dir=str(root))
    row=json.loads(store.ledger_path('opportunity').read_bytes())
    source={'epoch':'epoch','revision':row['source_revision']}
    monkeypatch.setattr(module,'_check',lambda *a,**k:source)
    monkeypatch.setattr(module,'_source',lambda t:dict(t))
    args=dict(repo_root=tmp_path/'repo',data_root=root,source_revision=source['revision'],
        config_signature=row['tile_config_signature'])
    return store,args


def test_late_child_restart_and_unchanged_disposition(tmp_path,monkeypatch):
    store,args=fixture(tmp_path,monkeypatch)
    before=store.ledger_path('decision').read_bytes()
    result=module.reconcile_scans(**args)
    assert result['index_caught_up'] and result['observed_joined_opportunity_rows']==1
    assert not result['exhaustive_fanout'] and not result['qualification_eligible']
    assert module.reconcile_scans(**args)==result
    assert store.ledger_path('decision').read_bytes()==before


def test_receipt_tamper_rejected(tmp_path,monkeypatch):
    store,args=fixture(tmp_path,monkeypatch)
    row=json.loads(store.ledger_path('opportunity').read_bytes())
    receipt=store._record_receipt_path('opportunity',row['record_id'])
    body=json.loads(receipt.read_bytes()); body['row_sha256']='0'*64
    receipt.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='RECEIPT_MISMATCH'): module.reconcile_scans(**args)


def test_duplicate_raw_record_cannot_inflate_observed_children(tmp_path,monkeypatch):
    store,args=fixture(tmp_path,monkeypatch)
    path=store.ledger_path('opportunity'); raw=path.read_bytes(); path.write_bytes(raw+raw)
    with pytest.raises(ValueError,match='RECEIPT_MISMATCH'): module.reconcile_scans(**args)


def test_provenance_conflict_rejected(tmp_path,monkeypatch):
    store,args=fixture(tmp_path,monkeypatch)
    args['config_signature']='wrong'
    with pytest.raises(ValueError,match='BINDING_CONFLICT'): module.reconcile_scans(**args)


def test_bounded_cursor_converges_and_replacement_invalidates(tmp_path,monkeypatch):
    store,args=fixture(tmp_path,monkeypatch)
    # Bound permits one largest real record, but not the entire source.
    budget=max(len(line) for ledger in ('decision','opportunity')
        for line in store.ledger_path(ledger).read_bytes().splitlines(keepends=True))
    outputs=[]
    for _ in range(8):
        result=module.reconcile_scans(**args,max_bytes=budget); outputs.append(result)
        if result['index_caught_up']: break
    assert result['index_caught_up'] and result['observed_joined_opportunity_rows']==1
    assert any(not r['index_caught_up'] for r in outputs)
    path=store.ledger_path('opportunity')
    replacement=path.with_suffix('.replacement'); replacement.write_bytes(b'')
    replacement.replace(path)
    result=module.reconcile_scans(**args)
    assert result['index_caught_up'] and result['observed_joined_opportunity_rows']==0


def test_actual_coherence_and_lease_reject_failed_heartbeat(tmp_path):
    from test_mirror_coherence import _canonical_fixture,REVISION,NOW
    from research.mirror_coherence import MirrorCoherenceError
    repo,mirror=_canonical_fixture(tmp_path,deployed_revision=REVISION[:12])
    ledger=mirror/'v3/ledgers'; ledger.mkdir(parents=True,exist_ok=True)
    for name in ('decision','opportunity'): (ledger/(name+'.jsonl')).write_bytes(b'')
    args=dict(repo_root=repo,data_root=mirror,source_revision=REVISION,config_signature='config',now=NOW)
    report=module.reconcile_scans(**args)
    assert report['index_caught_up'] and not report['exhaustive_fanout']
    heartbeat=mirror/'.fly-data-sync-loop.heartbeat.json'
    data=json.loads(heartbeat.read_text()); data['ok']=False; heartbeat.write_text(json.dumps(data))
    with pytest.raises(MirrorCoherenceError): module.reconcile_scans(**args)


def test_cached_reference_tamper_rejected_without_source_stat_change(tmp_path,monkeypatch):
    import sqlite3
    store,args=fixture(tmp_path,monkeypatch)
    module.reconcile_scans(**args)
    path=args['repo_root']/'local-derived/scan-reconciliation/index.sqlite'
    with sqlite3.connect(path) as db:
        ref=json.loads(db.execute("SELECT reference FROM refs WHERE ledger='opportunity'").fetchone()[0])
        ref['directions']['LONG']='tampered'
        db.execute("UPDATE refs SET reference=? WHERE ledger='opportunity'",(json.dumps(ref),))
    with pytest.raises(ValueError,match='CACHED_REFERENCE_INVALID'): module.reconcile_scans(**args)


def test_final_page_has_no_cursor_or_truncation(tmp_path,monkeypatch):
    store,args=fixture(tmp_path,monkeypatch)
    result=module.reconcile_scans(**args)
    assert result['next_reference_cursor'] is None and not result['reference_sample_truncated']
    after=result['sample_original_references'][0]['record_id']
    result=module.reconcile_scans(**args,reference_after=after)
    assert result['sample_original_references']==[] and result['next_reference_cursor'] is None
    assert not result['reference_sample_truncated']


def test_active_mirror_writer_blocks_reconciliation_without_publication(tmp_path,monkeypatch):
    from research.mirror_generation_lease import MirrorGenerationLease, MirrorGenerationLeaseTimeout
    store,args=fixture(tmp_path,monkeypatch)
    writer=MirrorGenerationLease(args['data_root'],owner='test-sync-writer')
    writer.acquire(timeout_seconds=0)
    try:
        with pytest.raises(MirrorGenerationLeaseTimeout):
            module.reconcile_scans(**args)
        assert not (args['repo_root']/'local-derived/scan-reconciliation/current.json').exists()
    finally:
        writer.release()
    assert module.reconcile_scans(**args)['index_caught_up']


def test_failed_reconciliation_releases_os_lease(tmp_path,monkeypatch):
    from research.mirror_generation_lease import MirrorGenerationLease
    store,args=fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='BINDING_CONFLICT'):
        module.reconcile_scans(**dict(args,config_signature='wrong'))
    writer=MirrorGenerationLease(args['data_root'],owner='test-sync-after-failure')
    writer.acquire(timeout_seconds=0)
    try:
        assert writer.held
    finally:
        writer.release()
