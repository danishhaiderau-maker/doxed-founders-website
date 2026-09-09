import json
import pytest

from research import local_dynamic_input as local
from research.mirror_coherence import MirrorCoherenceError
from test_mirror_coherence import _canonical_fixture, REVISION, NOW


def fixture(tmp_path):
    repo, mirror = _canonical_fixture(tmp_path, deployed_revision=REVISION[:12])
    args = dict(repo_root=repo, data_root=mirror, source_revision=REVISION,
                analyzer_revision='analyzer-a', transformation_signature='transform-a',
                config_signature='config-a', now=NOW)
    row = dict(episode_id='one', dataset_epoch='epoch-current', source_revision=REVISION[:12],
               deployed_revision=REVISION[:12], policy_outcomes={})
    return args, row


def test_roundtrip_immutable_outside_raw_and_bound(tmp_path):
    args, row = fixture(tmp_path)
    result = local.write_local_dynamic_input(**args, rows=[row])
    assert local.write_local_dynamic_input(**args, rows=[row]) == result
    body = local.load_local_dynamic_input(**args, input_sha256=result['input_sha256'])
    assert body['rows'] == [row] and body['qualification_allowed'] is False
    assert body['source_generation']['manifest_entry_hash']
    assert body['source_generation']['dataset_checksum'] == 'a'*64
    assert not list(args['data_root'].rglob('*dynamic*'))
    with pytest.raises(ValueError, match='CONFIG_MISMATCH'):
        local.load_local_dynamic_input(**{**args, 'config_signature': 'changed'}, input_sha256=result['input_sha256'])


@pytest.mark.parametrize('kind', ['duplicate', 'mixed', 'rows', 'bytes'])
def test_input_rejections(tmp_path, kind):
    args, row = fixture(tmp_path)
    rows = [row, row] if kind == 'duplicate' else [{**row, 'source_revision':'wrong'}] if kind == 'mixed' else (
        ({**row, 'episode_id':str(i)} for i in range(1001)) if kind == 'rows' else [{**row, 'huge':'x'*65536}])
    with pytest.raises(ValueError): local.write_local_dynamic_input(**args, rows=rows)
    assert not list(args['repo_root'].rglob('local-derived/**/*.json'))


def test_real_receipt_failed_not_caller_boolean(tmp_path):
    args, row = fixture(tmp_path)
    path = args['data_root']/'.fly-data-sync-loop.heartbeat.json'
    heartbeat = json.loads(path.read_text()); heartbeat['ok'] = False; path.write_text(json.dumps(heartbeat))
    with pytest.raises(MirrorCoherenceError): local.write_local_dynamic_input(**args, rows=[row])


def test_tamper_and_source_generation_change_rejected(tmp_path):
    args, row = fixture(tmp_path)
    result = local.write_local_dynamic_input(**args, rows=[row])
    path = args['repo_root']/'local-derived/dynamic-inputs'/f"{result['input_sha256']}.json"
    path.chmod(0o666)
    body = json.loads(path.read_text()); body['rows'][0]['episode_id'] = 'tampered'
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError, match='CHECKSUM'):
        local.load_local_dynamic_input(**args, input_sha256=result['input_sha256'])


def test_source_changes_during_derivation_no_publication(tmp_path):
    args, row = fixture(tmp_path)
    def rows():
        yield row
        path = args['data_root']/'.fly-sync-state.json'
        state=json.loads(path.read_text()); state['research.db']['size']+=1
        path.write_text(json.dumps(state))
    with pytest.raises(MirrorCoherenceError, match='IDENTITY_CHANGED'):
        local.write_local_dynamic_input(**args, rows=rows())
    assert not (args['repo_root']/'local-derived').exists()


def test_raw_overlap_rejected(tmp_path):
    args, row = fixture(tmp_path)
    with pytest.raises(ValueError, match='RAW_OVERLAP'):
        local.write_local_dynamic_input(**{**args, 'repo_root':args['data_root']}, rows=[row])
