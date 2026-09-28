from copy import deepcopy
import pytest

from research.dynamic_cohort_adapter import adapt_dynamic_cohorts
from research.local_dynamic_mapping import build_local_dynamic_mapping, _hash
from test_dynamic_cohort_adapter import GENERATION, row

PROTOCOL = dict(outer_folds=5, inner_folds=4, purge_sec=7200, embargo_sec=300, minimum_bucket_support=3)


def fixture(rows=None, protocol=None):
    protocol = PROTOCOL if protocol is None else protocol
    adapted = adapt_dynamic_cohorts(rows or [row()], expected_generation=GENERATION,
                                   feature_names=['regime'], protocol=protocol)
    return adapted


def build(adapted, **changes):
    options = dict(group_id=adapted['groups'][0]['group_id'], expected_generation=GENERATION, protocol=PROTOCOL)
    options.update(changes)
    return build_local_dynamic_mapping(adapted, **options)


def test_actual_adapter_mapping_unknowns_and_missing_collection_times():
    adapted = fixture([row(), row(episode_id='episode-two', opportunity_id='opportunity-two', outcome_state='UNKNOWN',
                                  terminal_complete=False, net_pnl_usd=None)])
    result = build(adapted)
    assert result['schema'] == 'dynamic_policy_analysis_input_v1'
    assert len(result['training_episodes']) == 2
    assert any(r['policy_outcomes'] == {} for r in result['training_episodes'])
    assert all('evidence_collected_at' not in r for r in result['training_episodes'])
    assert result['missing_collection_timestamp_episodes'] == 2
    assert result['historical_diagnostics_allowed'] is True and result['sealed_evaluation_allowed'] is False
    assert result['sealed_holdout_evaluation'] is None and result['sealed_holdout_episodes'] == []
    assert result['selected_group'] == adapted['groups'][0]
    assert result['protocol'] == PROTOCOL
    assert result == build(deepcopy(adapted))


def test_world_and_sizing_groups_never_merged():
    adapted = fixture([row(), row(evidence_world='IDEAL_TOUCH'),
                       row(declared_contract_sha256=None, declared_position_margin_usd=None)])
    assert len(adapted['groups']) == 3
    results = [build(adapted, group_id=g['group_id']) for g in adapted['groups']]
    assert all(len(r['training_episodes']) == 1 for r in results)
    assert len({r['mapping_sha256'] for r in results}) == 3
    with pytest.raises(ValueError, match='AMBIGUOUS_OR_MISSING'):
        build(adapted, group_id=[g['group_id'] for g in adapted['groups']])


def test_protocol_changes_have_new_hash_not_silent_refit():
    original = build(fixture())
    changed = {**PROTOCOL, 'purge_sec':7300}
    with pytest.raises(ValueError, match='PROTOCOL_HASH'):
        build(fixture(), protocol=changed)
    result = build(fixture(protocol=changed), protocol=changed)
    assert result['mapping_sha256'] != original['mapping_sha256']
    assert result['protocol_run_id'] != original['protocol_run_id']


def test_mixed_generation_or_tampering_rejected():
    adapted = fixture()
    with pytest.raises(ValueError, match='GENERATION_MISMATCH'):
        build(adapted, expected_generation={**GENERATION, 'epoch_id':'other'})
    adapted['groups'][0]['episodes'][0]['signal_ts'] += 1
    with pytest.raises(ValueError, match='ADAPTER_CHECKSUM'):
        build(adapted)
    adapted['adapter_sha256'] = _hash({k:v for k,v in adapted.items() if k != 'adapter_sha256'})
    with pytest.raises(ValueError, match='GROUP_CHECKSUM'):
        build(adapted)
