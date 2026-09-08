from copy import deepcopy
import pytest
import dynamic_policy_analyzer as analyzer
from research.local_dynamic_input import write_local_dynamic_input
from research.local_dynamic_mapping import build_local_dynamic_mapping, _hash
from research.dynamic_cohort_adapter import adapt_dynamic_cohorts
from research.mirror_coherence import assert_mirror_coherent
from test_local_dynamic_input import fixture
from test_dynamic_cohort_adapter import row, GENERATION
from test_local_dynamic_mapping import PROTOCOL


def prepared(tmp_path):
    args, unused = fixture(tmp_path)
    token = assert_mirror_coherent(repo_root=args['repo_root'], data_root=args['data_root'],
        expected_revision=args['source_revision'], now=args['now'], require_canonical_manifest=True)
    generation = {**GENERATION, 'manifest_entry_hash':token.manifest_entry_hash, 'epoch_id':token.epoch,
        'source_revision':token.revision, 'deployed_revision':token.deployed_revision,
        'analyzer_revision':args['analyzer_revision']}
    adapted = adapt_dynamic_cohorts([row(generation=generation)], expected_generation=generation,
                                    feature_names=['regime'], protocol=PROTOCOL)
    mapping = build_local_dynamic_mapping(adapted, group_id=adapted['groups'][0]['group_id'],
                                          expected_generation=generation, protocol=PROTOCOL)
    args['config_signature'] = _hash(PROTOCOL)
    return args, mapping


def test_full_mapping_roundtrip_and_no_fit(tmp_path, monkeypatch):
    args, mapping = prepared(tmp_path)
    receipt = write_local_dynamic_input(**args, rows=mapping['training_episodes'], mapping_payload=mapping)
    def forbidden(*a, **k): pytest.fail('report loading must not refit')
    monkeypatch.setattr(analyzer, 'train_frozen_dynamic_policy', forbidden)
    monkeypatch.setattr(analyzer, 'orchestrate_dynamic_policy_analysis', forbidden)
    loaded, proof = analyzer.load_verified_local_dynamic_mapping(**args, input_sha256=receipt['input_sha256'])
    assert loaded == mapping and proof['input_sha256'] == receipt['input_sha256']
    result = analyzer.build_local_dynamic_policy_analysis_report(**args, input_sha256=receipt['input_sha256'])
    assert result['local_input_status'] == 'INPUT_VERIFIED_NOT_ANALYZED'
    assert result['fit_performed'] is False and result['status'] == 'UNKNOWN'


def test_mapping_conflicts_fail_before_write(tmp_path):
    args, mapping = prepared(tmp_path)
    bad = deepcopy(mapping); bad['protocol']['purge_sec'] += 1
    with pytest.raises(ValueError, match='CHECKSUM'):
        write_local_dynamic_input(**args, rows=mapping['training_episodes'], mapping_payload=bad)
    bad['mapping_sha256'] = _hash({k:v for k,v in bad.items() if k != 'mapping_sha256'})
    with pytest.raises(ValueError, match='CONTENT_MISMATCH'):
        write_local_dynamic_input(**args, rows=mapping['training_episodes'], mapping_payload=bad)
