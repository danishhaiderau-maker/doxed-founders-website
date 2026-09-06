import hashlib
import json

import pytest
import local_research_reset_cli as cli


def fixture(tmp_path, monkeypatch):
    root = tmp_path/'canonical'; root.mkdir()
    monkeypatch.setattr(cli, 'CANONICAL_ROOT', root)
    source = tmp_path/'fly.json'; source.write_bytes(b'{}')
    args = dict(root=root, fly_receipt=source, fly_sha256=hashlib.sha256(b'{}').hexdigest(),
                revision='a'*40, new_epoch='epoch-'+'b'*24, reset_id='c'*24)
    def factory(**kw):
        assert kw['lease'].held
        return lambda root: {'recovery_receipt_bytes': b'original-audit'}
    monkeypatch.setattr(cli, 'make_local_reset_auditor', factory)
    def helper(**kw):
        assert kw['lease'].held
        result = kw['audit_owners'](kw['root'])
        return {'status':'VALIDATED' if kw['validate_only'] else 'COMPLETE'}
    monkeypatch.setattr(cli, 'reset_local_research', helper)
    return args


def test_default_does_not_persist_or_execute(tmp_path, monkeypatch):
    args = fixture(tmp_path, monkeypatch)
    assert cli.run(**args)['status'] == 'VALIDATED'
    assert not (args['root']/'research_reset_receipts').exists()


def test_execute_saves_original_audit_without_overwrite(tmp_path, monkeypatch):
    args = fixture(tmp_path, monkeypatch)
    assert cli.run(**args, execute=True)['status'] == 'COMPLETE'
    audit = args['root']/'research_reset_receipts'/args['reset_id']/'initial-audit.json'
    assert audit.read_bytes() == b'original-audit'
    audit.write_bytes(b'other')
    with pytest.raises(ValueError, match='OVERWRITE_REFUSED'):
        cli.run(**args, execute=True)
    assert audit.read_bytes() == b'other'


def test_noncanonical_root_rejected(tmp_path, monkeypatch):
    args = fixture(tmp_path, monkeypatch)
    args['root'] = tmp_path
    with pytest.raises(ValueError, match='CANONICAL_ROOT'):
        cli.run(**args)


def test_resume_requires_prior_audit_matching_journal(tmp_path, monkeypatch):
    args = fixture(tmp_path, monkeypatch)
    directory = args['root']/'research_reset_receipts'/args['reset_id']
    directory.mkdir(parents=True)
    (directory/'operation.json').write_text(json.dumps({'binding':{'initial_audit_sha256':'0'*64}}))
    (directory/'initial-audit.json').write_bytes(b'original-audit')
    with pytest.raises(ValueError, match='PRIOR_AUDIT_MISMATCH'):
        cli.run(**args)


@pytest.mark.parametrize('crash', [False, True])
def test_real_helper_adapter_cli_execute_and_resume(tmp_path, monkeypatch, crash):
    import local_research_reset as helper
    import local_research_reset_audit as adapter
    from research.canonical_data_store import append_manifest
    root = tmp_path/'canonical'; root.mkdir()
    monkeypatch.setattr(cli, 'CANONICAL_ROOT', root)
    append_manifest(root, dict(dataset_epoch='epoch-old', source_revision='9b588c0b5f79',
        deployed_revision='9b588c0b5f79', tile_config_signature='b'*64, collection_started_at='then',
        collection_observed_at='now', row_count=1, opportunity_count=1, dataset_checksum='d'*64,
        analyzer_status='old', analyzer_completed_at=None, analyzer_schema_version='fixture'))
    target = root/'signal_replay.jsonl'; target.write_text('old')
    new_epoch = 'epoch-'+'b'*24
    fly = {'schema':'bot_destructive_research_reset_v1', 'stage':'COMPLETE',
        'new_epoch_id':new_epoch, 'accounting_preserved':True,
        'boundary_evidence':{'deployed_revision':'a'*40},
        'proof':{'new_epoch_id':new_epoch,'paper_only':True,'live_disarmed':True,
                 'pending_paper_orders':0,'open_paper_positions':0}}
    raw = json.dumps(fly).encode(); source = tmp_path/'fly.json'; source.write_bytes(raw)
    owners = dict(schema='local_reset_os_owner_snapshot_v1', process_ids=[], listeners=[],
        unknown_process_count=0,tasks=[{'name':n,'state':'Disabled'} for n in adapter.TASKS])
    monkeypatch.setattr(cli, 'make_local_reset_auditor',
        lambda **kw:adapter.make_local_reset_auditor(**kw,owner_snapshot_provider=lambda root:owners))
    args = dict(root=root, fly_receipt=source, fly_sha256=hashlib.sha256(raw).hexdigest(),
        revision='a'*40, new_epoch=new_epoch, reset_id='c'*24, execute=True)
    original = helper.execute_research_reset
    if crash:
        def interrupt(**kw):
            if not kw.get('validate_only'):
                raise RuntimeError('injected before raw')
            return original(**kw)
        monkeypatch.setattr(helper, 'execute_research_reset', interrupt)
        with pytest.raises(RuntimeError, match='injected'):
            cli.run(**args)
        assert target.exists()
        monkeypatch.setattr(helper, 'execute_research_reset', original)
    assert cli.run(**args)['stage'] == 'COMPLETE'
    assert not target.exists()
