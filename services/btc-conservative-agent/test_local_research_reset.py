import hashlib
import json
import time

import pytest
import local_research_reset as module
from research.mirror_generation_lease import MirrorGenerationLease
from research_exact_deletion import ResearchDeletionRejected
from research_reset_inventory import PROOF_SCHEMA


def arguments(tmp_path):
    fly = {'schema': 'bot_destructive_research_reset_v1', 'stage': 'COMPLETE',
           'new_epoch_id': 'epoch-new', 'proof': {'new_epoch_id': 'epoch-new'},
           'boundary_evidence': {'deployed_revision': 'a'*40}, 'accounting_preserved': True}
    raw = json.dumps(fly).encode()
    recovery = b'fixture audited owner receipt'
    proof = dict(schema=PROOF_SCHEMA, runtime_root=str(tmp_path), retired_epoch_id='epoch-old',
        new_epoch_id='epoch-new', source_revision='a'*40,
        recovery_receipt_sha256=hashlib.sha256(recovery).hexdigest(), writers_quiesced=True,
        paper_only=True, live_disarmed=True, epoch_retired=True, pending_paper_orders=0,
        open_paper_positions=0, pending_wal_records=0, pending_recovery_records=0)
    def audit(root):
        return dict(checked_at=time.time(), proof=proof, recovery_receipt_bytes=recovery,
                    retired_manifest_entry_hash='f'*64,
                    retired_identity={'epoch_id': 'epoch-old', 'source_revision': proof['source_revision'],
                                      'deployed_revision': 'b'*40, 'tile_config_signature': 'c'*64},
                    recovery_states={'owners': 'RECONCILED'})
    return dict(root=tmp_path, lease=MirrorGenerationLease(tmp_path), fly_receipt_bytes=raw,
        fly_receipt_sha256=hashlib.sha256(raw).hexdigest(), expected_new_epoch='epoch-new',
        expected_revision='a'*40, audit_owners=audit,
        journal_path=tmp_path/'research_reset_receipts'/'test'/'operation.json')


def test_unheld_lease_rejected(tmp_path):
    with pytest.raises(ResearchDeletionRejected, match='MATCHING_MIRROR'):
        module.reset_local_research(**arguments(tmp_path))


@pytest.mark.parametrize('case',['large','oversize','hash'])
def test_fly_completion_large_bounded_receipt(tmp_path,case):
    args=arguments(tmp_path)
    args['fly_receipt_bytes']+=b' '*(64*1024**2 if case=='oversize' else 17*1024**2)
    args['fly_receipt_sha256']=hashlib.sha256(args['fly_receipt_bytes']).hexdigest()
    if case=='hash': args['fly_receipt_sha256']='0'*64
    with args['lease']:
        if case=='large':
            assert module.reset_local_research(**args)['status']=='VALIDATED'
        else:
            with pytest.raises(ResearchDeletionRejected): module.reset_local_research(**args)


def test_default_validation_does_not_delete(tmp_path):
    args = arguments(tmp_path)
    payload = tmp_path/'signal_replay.jsonl'
    payload.write_text('old')
    with args['lease']:
        result = module.reset_local_research(**args)
    assert result['status'] == 'VALIDATED'
    assert payload.read_text() == 'old'
    assert not args['journal_path'].exists()


def test_retry_after_metadata_retirement_before_raw_receipt(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    payload = tmp_path/'signal_replay.jsonl'
    payload.write_text('old')
    original = module.execute_research_reset
    def fail_raw(**kw):
        if not kw.get('validate_only'):
            raise RuntimeError('before raw receipt')
        return original(**kw)
    monkeypatch.setattr(module, 'execute_research_reset', fail_raw)
    with args['lease']:
        with pytest.raises(RuntimeError, match='before raw receipt'):
            module.reset_local_research(**args, validate_only=False)
        assert json.loads(args['journal_path'].read_text())['stage'] == 'METADATA_RETIRED'
        monkeypatch.setattr(module, 'execute_research_reset', original)
        assert module.reset_local_research(**args, validate_only=False)['stage'] == 'COMPLETE'
        assert module.reset_local_research(**args, validate_only=False)['stage'] == 'COMPLETE'
    assert not payload.exists()


def test_bad_fly_pin_rejected(tmp_path):
    args = arguments(tmp_path)
    args['fly_receipt_sha256'] = '0'*64
    with args['lease'], pytest.raises(ResearchDeletionRejected, match='HASH_MISMATCH'):
        module.reset_local_research(**args)


@pytest.mark.parametrize('mode', ['accurate', 'missing', 'mismatch'])
def test_retired_source_is_distinct_from_new_deployment(tmp_path, mode):
    args = arguments(tmp_path)
    original = args['audit_owners']
    def audit(root):
        result = original(root)
        result['proof']['source_revision'] = 'd'*40
        result['retired_identity']['source_revision'] = 'd'*40
        if mode == 'missing':
            result.pop('retired_identity')
        elif mode == 'mismatch':
            result['retired_identity']['source_revision'] = 'e'*40
        return result
    args['audit_owners'] = audit
    with args['lease']:
        if mode != 'accurate':
            with pytest.raises(ResearchDeletionRejected):
                module.reset_local_research(**args)
        else:
            result = module.reset_local_research(**args)
            assert result['binding']['source_revision'] == 'a'*40
            assert result['binding']['retired_identity']['source_revision'] == 'd'*40


@pytest.mark.parametrize('missing_hash', [False, True])
def test_recorded_legacy_short_revision_not_fabricated(tmp_path, missing_hash):
    args = arguments(tmp_path)
    original = args['audit_owners']
    def audit(root):
        result = original(root)
        result['proof']['source_revision'] = '9b588c0b5f79'
        result['retired_identity'].update(source_revision='9b588c0b5f79',
                                          deployed_revision='9b588c0b5f79')
        result['retired_manifest_entry_hash'] = 'cf4994008fcaa76cfb2082de9b52ec648f1099b2cd0bd141a9e5ec10878d9fc4'
        if missing_hash:
            result.pop('retired_manifest_entry_hash')
        return result
    args['audit_owners'] = audit
    with args['lease']:
        if missing_hash:
            with pytest.raises(ResearchDeletionRejected):
                module.reset_local_research(**args)
        else:
            result = module.reset_local_research(**args)
            assert result['binding']['retired_identity']['source_revision'] == '9b588c0b5f79'
            assert result['binding']['retired_manifest_entry_hash'].startswith('cf499400')


def test_metadata_success_before_joint_save_can_resume(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    (tmp_path/'signal_replay.jsonl').write_text('old')
    original = module.retire_canonical_generation
    def crash_after_retirement(**kw):
        original(**kw)
        raise RuntimeError('after metadata complete')
    monkeypatch.setattr(module, 'retire_canonical_generation', crash_after_retirement)
    with args['lease']:
        with pytest.raises(RuntimeError, match='after metadata'):
            module.reset_local_research(**args, validate_only=False)
        assert json.loads(args['journal_path'].read_text())['stage'] == 'VALIDATED'
        monkeypatch.setattr(module, 'retire_canonical_generation', original)
        assert module.reset_local_research(**args, validate_only=False)['stage'] == 'COMPLETE'


@pytest.mark.parametrize('target', ['marker', 'metadata', 'raw'])
def test_completed_receipt_tampering_is_rejected(tmp_path, target):
    args = arguments(tmp_path)
    (tmp_path/'signal_replay.jsonl').write_text('old')
    with args['lease']:
        module.reset_local_research(**args, validate_only=False)
        if target == 'marker':
            path = tmp_path/module.RETIRED_MARKER
            value = json.loads(path.read_text())
            value['new_epoch_id'] = 'epoch-wrong'
        elif target == 'metadata':
            path = args['journal_path'].parent/'metadata-deletion.json'
            value = json.loads(path.read_text())
            value['expected_sha256_by_path'] = {'wrong': '0'*64}
        else:
            path = args['journal_path'].parent/'raw-deletion.json'
            value = json.loads(path.read_text())
            value['receipt_path'] = str(tmp_path/'wrong.json')
        path.write_text(json.dumps(value))
        with pytest.raises(ResearchDeletionRejected):
            module.reset_local_research(**args, validate_only=False)


def test_raw_success_before_final_joint_save_recovers(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    (tmp_path/'signal_replay.jsonl').write_text('old')
    original = module.execute_research_reset
    def crash_after_raw(**kw):
        result = original(**kw)
        if not kw.get('validate_only'):
            raise RuntimeError('after raw complete')
        return result
    monkeypatch.setattr(module, 'execute_research_reset', crash_after_raw)
    with args['lease']:
        with pytest.raises(RuntimeError, match='after raw complete'):
            module.reset_local_research(**args, validate_only=False)
        assert json.loads(args['journal_path'].read_text())['stage'] == 'METADATA_RETIRED'
        # No raw replanning or repeated deletion is permitted on this recovery.
        monkeypatch.setattr(module, 'execute_research_reset', lambda **kw: pytest.fail('repeated raw work'))
        assert module.reset_local_research(**args, validate_only=False)['stage'] == 'COMPLETE'


@pytest.mark.parametrize('change', ['partial', 'proof'])
def test_raw_recovery_rejects_partial_or_wrong_proof(tmp_path, monkeypatch, change):
    args = arguments(tmp_path)
    (tmp_path/'signal_replay.jsonl').write_text('old')
    with args['lease']:
        module.reset_local_research(**args, validate_only=False)
        journal = json.loads(args['journal_path'].read_text())
        journal['stage'] = 'METADATA_RETIRED'
        if change == 'proof':
            journal['raw_proof_sha256'] = '0'*64
        args['journal_path'].write_text(json.dumps(journal))
        if change == 'partial':
            path = args['journal_path'].parent/'raw-deletion.json'
            raw = json.loads(path.read_text())
            raw['status'] = 'PARTIAL'
            path.write_text(json.dumps(raw))
        with pytest.raises(ResearchDeletionRejected):
            module.reset_local_research(**args, validate_only=False)


def test_same_proof_different_inventory_receipt_rejected(tmp_path):
    from research_exact_deletion import delete_exact_research_files
    args = arguments(tmp_path)
    (tmp_path/'signal_replay.jsonl').write_text('old')
    with args['lease']:
        module.reset_local_research(**args, validate_only=False)
        path = args['journal_path'].parent/'raw-deletion.json'
        original = json.loads(path.read_text())
        # Construct a genuinely self-consistent alternative receipt with the
        # same context/proof and canonical receipt path, but another target.
        path.unlink()
        path.with_name(path.name + '.progress.jsonl').unlink()
        substitute = tmp_path/'alternate.jsonl'
        substitute.write_text('other')
        digest = hashlib.sha256(substitute.read_bytes()).hexdigest()
        delete_exact_research_files(root=tmp_path, targets=[substitute], allowed_paths=[substitute],
            receipt_path=path, quiescent=True, recovery_states={'owners':'RECONCILED'},
            expected_sha256_by_path={str(substitute):digest}, receipt_context=original['context'])
        with pytest.raises(ResearchDeletionRejected, match='RAW_TARGET_BINDING_MISMATCH'):
            module.reset_local_research(**args, validate_only=False)


def test_new_target_before_raw_execution_rejected_without_unlink(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    target = tmp_path/'signal_replay.jsonl'
    target.write_text('old')
    added = tmp_path/'signal_snapshot.jsonl'
    original = module.execute_research_reset
    def add_target(**kw):
        if not kw.get('validate_only'):
            added.write_text('new target')
        return original(**kw)
    monkeypatch.setattr(module, 'execute_research_reset', add_target)
    with args['lease'], pytest.raises(ResearchDeletionRejected, match='EXPECTED_TARGET_MAP_CHANGED'):
        module.reset_local_research(**args, validate_only=False)
    assert target.exists() and added.exists()
    assert not (args['journal_path'].parent/'raw-deletion.json').exists()
