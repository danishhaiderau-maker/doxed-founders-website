import hashlib
import json

import pytest
import local_research_reset_audit as module
from research.mirror_generation_lease import MirrorGenerationLease
from research_exact_deletion import ResearchDeletionRejected


def setup(tmp_path, monkeypatch):
    fly = {'schema':'bot_destructive_research_reset_v1', 'stage':'COMPLETE',
           'new_epoch_id':'epoch-new', 'accounting_preserved':True,
           'boundary_evidence':{'deployed_revision':'a'*40},
           'proof':{'new_epoch_id':'epoch-new','paper_only':True,'live_disarmed':True,
                    'pending_paper_orders':0,'open_paper_positions':0}}
    raw = json.dumps(fly).encode()
    identity = dict(dataset_epoch='epoch-old', source_revision='9b588c0b5f79',
                    deployed_revision='9b588c0b5f79', tile_config_signature='b'*64, entry_hash='c'*64)
    monkeypatch.setattr(module, 'current_analyzer_dataset_identity', lambda root:identity)
    owners = dict(schema='local_reset_os_owner_snapshot_v1', process_ids=[], listeners=[],
                  unknown_process_count=0, tasks=[{'name':name,'state':'Disabled'} for name in module.TASKS])
    args = dict(lease=MirrorGenerationLease(tmp_path), fly_receipt_bytes=raw,
                fly_receipt_sha256=hashlib.sha256(raw).hexdigest(), expected_new_epoch='epoch-new',
                expected_revision='a'*40, owner_snapshot_provider=lambda root:owners)
    return args, owners


def test_real_empty_recovery_and_wal_absence(tmp_path, monkeypatch):
    args, owners = setup(tmp_path, monkeypatch)
    with args['lease']:
        result = module.make_local_reset_auditor(**args)(tmp_path)
    assert result['proof']['source_revision'] == '9b588c0b5f79'
    assert result['recovery_states']['emergency_wal'] == 'NOT_PRESENT'
    assert result['proof']['pending_wal_records'] == 0
    assert hashlib.sha256(result['recovery_receipt_bytes']).hexdigest() == result['proof']['recovery_receipt_sha256']


@pytest.mark.parametrize('field,value', [('process_ids',[42]), ('listeners',[{'port':9001}]),
                                      ('unknown_process_count',1), ('tasks',[])])
def test_owner_uncertainty_rejected(tmp_path, monkeypatch, field, value):
    args, owners = setup(tmp_path, monkeypatch)
    owners[field] = value
    with args['lease'], pytest.raises(ResearchDeletionRejected, match='OWNERS_NOT'):
        module.make_local_reset_auditor(**args)(tmp_path)


def test_bad_wal_is_not_absence(tmp_path, monkeypatch):
    args, _ = setup(tmp_path, monkeypatch)
    (tmp_path/'v3'/'emergency_evidence_wal_v2').mkdir(parents=True)
    with args['lease'], pytest.raises((OSError, RuntimeError)):
        module.make_local_reset_auditor(**args)(tmp_path)


def test_missing_fly_exposure_is_not_zero(tmp_path, monkeypatch):
    args, _ = setup(tmp_path, monkeypatch)
    value = json.loads(args['fly_receipt_bytes'])
    value['proof'].pop('open_paper_positions')
    args['fly_receipt_bytes'] = json.dumps(value).encode()
    args['fly_receipt_sha256'] = hashlib.sha256(args['fly_receipt_bytes']).hexdigest()
    with pytest.raises(ResearchDeletionRejected, match='SAFETY_UNPROVEN'):
        module.make_local_reset_auditor(**args)


@pytest.mark.parametrize('journal_padding', [0, 70_000, 16 * 1024**2])
def test_real_manifest_retirement_fresh_adapter_resume(tmp_path, monkeypatch, journal_padding):
    from research.canonical_data_store import append_manifest, current_analyzer_dataset_identity
    import local_research_reset as helper
    args, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'current_analyzer_dataset_identity', current_analyzer_dataset_identity)
    append_manifest(tmp_path, dict(dataset_epoch='epoch-old', source_revision='9b588c0b5f79',
        deployed_revision='9b588c0b5f79', tile_config_signature='b'*64, collection_started_at='then',
        collection_observed_at='now', row_count=1, opportunity_count=1, dataset_checksum='d'*64,
        analyzer_status='old', analyzer_completed_at=None, analyzer_schema_version='fixture'))
    (tmp_path/'signal_replay.jsonl').write_text('old')
    journal = tmp_path/'research_reset_receipts'/'integration'/'operation.json'
    args['journal_path'] = journal
    original = helper.execute_research_reset
    def fail(**kw):
        if not kw.get('validate_only'):
            raise RuntimeError('raw not started')
        return original(**kw)
    with args['lease']:
        auditor = module.make_local_reset_auditor(**args)
        prior = auditor(tmp_path)['recovery_receipt_bytes']
        common = dict(root=tmp_path, lease=args['lease'], fly_receipt_bytes=args['fly_receipt_bytes'],
            fly_receipt_sha256=args['fly_receipt_sha256'], expected_new_epoch=args['expected_new_epoch'],
            expected_revision=args['expected_revision'], journal_path=journal, validate_only=False)
        monkeypatch.setattr(helper, 'execute_research_reset', fail)
        with pytest.raises(RuntimeError, match='raw not started'):
            helper.reset_local_research(**common, audit_owners=auditor)
        monkeypatch.setattr(helper, 'execute_research_reset', original)
        if journal_padding:
            # Valid extra JSON whitespace exercises the receipt byte bound without
            # modifying any of the real journal's authenticated semantic bindings.
            journal.write_bytes(journal.read_bytes() + b' ' * journal_padding)
            assert journal.stat().st_size > 65536
        restarted = module.make_local_reset_auditor(**args, prior_audit_bytes=prior,
                          prior_audit_sha256=hashlib.sha256(prior).hexdigest())
        if journal_padding >= 16 * 1024**2:
            with pytest.raises(ResearchDeletionRejected, match='RESUME_METADATA_LIMIT'):
                helper.reset_local_research(**common, audit_owners=restarted)
            assert (tmp_path/'signal_replay.jsonl').exists()
            assert not (journal.parent/'raw-deletion.json').exists()
            return
        assert helper.reset_local_research(**common, audit_owners=restarted)['stage'] == 'COMPLETE'
