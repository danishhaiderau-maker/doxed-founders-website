import importlib.util
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services' / 'btc-conservative-agent'))
from test_raw_generation_cleanup_owner import persisted_authority
from raw_generation_cleanup import RawGenerationCleanupRejected
from raw_generation_cleanup_owner import RawGenerationCleanupOwner

spec = importlib.util.spec_from_file_location('client', Path(__file__).with_name('sealed_generation_cleanup_client.py'))
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def fixture(tmp_path):
    root = tmp_path / 'source'
    source, identity = persisted_authority(root)
    manifest = json.loads(next((root/'v3/raw_generation_manifests').glob('*.json')).read_text())
    ack = json.loads(next((root/'v3/raw_generation_laptop_acks').glob('*.json')).read_text())
    for directory, value in ((root/'v3/raw_generation_manifests', manifest), (root/'v3/raw_generation_laptop_acks', ack)):
        next(directory.glob('*.json')).write_bytes(client.encoded(value) + b'\n')
    return dict(source_root=root, manifest=manifest, ack=ack, identity=identity,
                generation_id='V3:decision:1', receipts=tmp_path/'receipts', endpoint='https://doxed-btc-bot.fly.dev')


def test_default_plan_zero_network_and_zero_receipts(tmp_path):
    args = fixture(tmp_path)
    result = client.execute(**args, post=lambda *_: pytest.fail('network'))
    assert result['freed_bytes'] == 0
    assert not args['receipts'].exists()


def test_missing_ack_or_wrong_identity_refused(tmp_path):
    args = fixture(tmp_path)
    with pytest.raises(RawGenerationCleanupRejected):
        client.execute(**{**args, 'ack': {} })
    with pytest.raises(ValueError, match='EXPECTED_GENERATION'):
        client.execute(**{**args, 'generation_id': 'V3:decision:2'})


def test_active_unsealed_source_refused(tmp_path):
    args = fixture(tmp_path)
    args['manifest']['members'][0]['seal']['sealed_ref']['state'] = 'ACTIVE'
    with pytest.raises(RawGenerationCleanupRejected):
        client.execute(**args)


def test_register_real_server_verifier_and_idempotent_local_receipt(tmp_path):
    args = fixture(tmp_path)
    owner = RawGenerationCleanupOwner(args['source_root'], identity=lambda:args['identity'], leases=lambda _: {})
    calls = []
    def post(url, body):
        calls.append(url)
        return {'ok': True, **owner.persist_authority(body['manifest'], body['acknowledgement'])}
    run = dict(args, action='register', confirmation='REGISTER:V3:decision:1', post=post)
    assert client.execute(**run)['ok'] is True
    assert client.execute(**run)['local_receipt_reused'] is True
    assert len(calls) == 1


def test_live_lease_refusal_does_not_retry(tmp_path):
    args = fixture(tmp_path)
    owner = RawGenerationCleanupOwner(args['source_root'], identity=lambda:args['identity'], leases=lambda _: {'reader':['active']})
    def post(url, body):
        return owner.persist_authority(body['manifest'], body['acknowledgement'])
    run = dict(args, action='register', confirmation='REGISTER:V3:decision:1', post=post)
    with pytest.raises(RawGenerationCleanupRejected): client.execute(**run)
    with pytest.raises(ValueError, match='PRIOR_REQUEST_UNCERTAIN'): client.execute(**run)


def test_no_implicit_quarantine_or_purge(tmp_path):
    args = fixture(tmp_path)
    for action in ('quarantine','purge'):
        with pytest.raises(ValueError, match='CONFIRMATION'):
            client.execute(**args, action=action, post=lambda *_:pytest.fail('network'))
        with pytest.raises(FileNotFoundError):
            client.execute(**args, action=action, confirmation=action.upper()+':V3:decision:1', post=lambda *_:pytest.fail('network'))


@pytest.mark.parametrize('tamper', [None, 'freed_bytes', 'future_unknown_field'])
def test_phases_are_explicit_and_purge_hash_verified(tmp_path, tamper):
    args = fixture(tmp_path)
    proof = client.execute(**args)['job']['proof_sha256']
    calls = []
    def post(url, body):
        calls.append(url)
        if url.endswith('/purge'):
            receipt = {'schema':'raw_generation_purge_receipt_v1', 'state':'PURGED',
                       'generation_id':args['generation_id'], 'freed_bytes':10}
            import hashlib
            receipt['receipt_sha256'] = hashlib.sha256(client.encoded(receipt)).hexdigest()
            receipt['owner_space_observation'] = {'free_bytes_delta':12}
            if tamper: receipt[tamper] = 99
            return {'ok':True, **receipt}
        return {'ok': True, 'generation_id': args['generation_id'],
                'proof_sha256': proof, 'status': 'RAW_GENERATION_AUTHORITY_REGISTERED_SOURCE_RETAINED'
                if url.endswith('/authority') else 'QUARANTINED_SOURCE_RETAINED'}
    client.execute(**args, action='register', confirmation='REGISTER:V3:decision:1', post=post)
    assert len(calls) == 1
    client.execute(**args, action='quarantine', confirmation='QUARANTINE:V3:decision:1', post=post)
    assert len(calls) == 2
    assert client.execute(**args, action='quarantine', confirmation='QUARANTINE:V3:decision:1', post=post)['local_receipt_reused']
    if tamper:
        with pytest.raises(ValueError, match='PURGE_RECEIPT_HASH_UNVERIFIED'):
            client.execute(**args, action='purge', confirmation='PURGE:V3:decision:1', post=post)
    else:
        client.execute(**args, action='purge', confirmation='PURGE:V3:decision:1', post=post)
    assert len(calls) == 3


def test_noncanonical_origin_and_redirect_are_refused(tmp_path):
    args = fixture(tmp_path)
    with pytest.raises(ValueError, match='REFUSED_NON_CANONICAL'):
        client.execute(**{**args, 'endpoint':'https://example.invalid'})
    with pytest.raises(ValueError, match='ADMIN_REDIRECT_REFUSED'):
        client.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.invalid')
