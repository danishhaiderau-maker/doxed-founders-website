import hashlib
import json

import pytest
import lifecycle_index_recovery as recovery
import lifecycle_pipeline
from test_lifecycle_index_recovery import _source


def setup_reset(root):
    source = _source(root, rows=4)
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(root)
    _source(root, rows=1)
    proof = dict(schema='research_reset_boundary_proof_v1', runtime_root=str(root),
                 new_epoch_id='new', retired_epoch_id='old', source_revision='revision',
                 recovery_receipt_sha256='a'*64, writers_quiesced=True, paper_only=True,
                 live_disarmed=True, epoch_retired=True, pending_paper_orders=0,
                 open_paper_positions=0, pending_wal_records=0, pending_recovery_records=0)
    operation = dict(schema='bot_destructive_research_reset_v1', stage='COMPLETE',
                     accounting_preserved=True, new_epoch_id='new', proof=proof,
                     deleted=[str(source)], deletion={'deletion_receipt': {
                         'root':str(root), 'status':'COMPLETE', 'deleted':[str(source)],
                         'context':{'proof_sha256':hashlib.sha256(json.dumps(proof,sort_keys=True,separators=(',', ':')).encode()).hexdigest()}}})
    path = root/'research_reset_receipts'/'reset'/'operation.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(operation))
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), source


def test_reset_rebuild_requires_bound_proof_and_epoch(tmp_path):
    path, digest, source = setup_reset(tmp_path)
    original = source.read_bytes()
    with pytest.raises(ValueError, match='SOURCE_LEDGER_TRUNCATED'):
        lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    for i in range(60):
        result = recovery.recover_reset_index(tmp_path, 'SOURCE_LEDGER_TRUNCATED:opportunity.jsonl',
                    operation_path=path, operation_sha256=digest, current_epoch_id='new')
        if result['complete']: break
    assert result['complete']
    assert source.read_bytes() == original
    assert recovery.resume_rotated_index_recovery(tmp_path,current_epoch_id='new')['complete']
    with pytest.raises(ValueError, match='RESET_PROOF_INVALID'):
        recovery.resume_rotated_index_recovery(tmp_path,current_epoch_id='wrong')


@pytest.mark.parametrize('change', ['hash', 'epoch', 'deletion', 'stage'])
def test_invalid_reset_receipt_does_not_touch_index(tmp_path, change):
    path, digest, source = setup_reset(tmp_path)
    index = tmp_path/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3'
    before = index.read_bytes()
    if change in ('deletion','stage'):
        payload=json.loads(path.read_text())
        if change=='deletion': payload['deleted']=[]
        else: payload['stage']='PARTIAL'
        path.write_text(json.dumps(payload)); digest=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        recovery.recover_reset_index(tmp_path,'SOURCE_LEDGER_TRUNCATED:opportunity.jsonl',
                    operation_path=path,operation_sha256='0'*64 if change=='hash' else digest,
                    current_epoch_id='wrong' if change=='epoch' else 'new')
    assert index.read_bytes()==before


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}', b'{"nested":{"x":1,"x":2}}'])
def test_reset_receipt_strict_json(raw):
    with pytest.raises(ValueError):
        recovery._strict_reset_json(raw)


def test_reset_receipt_size_bounded_before_read(tmp_path, monkeypatch):
    path, digest, source = setup_reset(tmp_path)
    monkeypatch.setattr(recovery, 'MAX_RESET_RECEIPT_BYTES', 10)
    with pytest.raises(ValueError, match='TOO_LARGE'):
        recovery.recover_reset_index(tmp_path,'SOURCE_LEDGER_TRUNCATED:opportunity.jsonl',
                    operation_path=path,operation_sha256=digest,current_epoch_id='new')


def test_completed_resume_is_compact_after_live_index_advances(tmp_path, monkeypatch):
    path,digest,source=setup_reset(tmp_path)
    for _ in range(60):
        result=recovery.recover_reset_index(tmp_path,'SOURCE_LEDGER_TRUNCATED:opportunity.jsonl',
                    operation_path=path,operation_sha256=digest,current_epoch_id='new')
        if result['complete']: break
    assert result['complete']
    with source.open('ab') as stream:
        stream.write(b'{"record_id":"new","epoch_id":"new","episode_id":"new"}\n')
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path,current_epoch_id='new')
    def prohibited(*a,**kw): raise AssertionError('bulk verification on COMPLETE')
    monkeypatch.setattr(recovery,'_reset_binding',prohibited)
    monkeypatch.setattr(recovery,'_verify_quarantine',prohibited)
    monkeypatch.setattr(recovery,'_sha',prohibited)
    for _ in range(2):
        assert recovery.resume_rotated_index_recovery(tmp_path,current_epoch_id='new')['complete']
    with pytest.raises(ValueError,match='RESET_PROOF_INVALID'):
        recovery.resume_rotated_index_recovery(tmp_path,current_epoch_id='wrong')
    statepath=tmp_path/'v3'/'lifecycle_bundle_index'/'recovery-state.json'
    state=json.loads(statepath.read_text())
    completion=statepath.parent/'recovery-quarantine'/recovery._directory_id(state)/'completion.json'
    completion.write_bytes(completion.read_bytes()+b' ')
    with pytest.raises(ValueError,match='COMPLETION_TAMPERED'):
        recovery.resume_rotated_index_recovery(tmp_path,current_epoch_id='new')
    statepath.write_text('{}')
    with pytest.raises(ValueError,match='STATE_TAMPERED'):
        recovery.resume_rotated_index_recovery(tmp_path,current_epoch_id='new')
