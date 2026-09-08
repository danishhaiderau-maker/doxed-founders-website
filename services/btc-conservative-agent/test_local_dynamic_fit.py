import pytest
from research import local_dynamic_fit as fit
from research.local_dynamic_input import write_local_dynamic_input
from research.local_dynamic_mapping import build_local_dynamic_mapping, _hash
from research.dynamic_cohort_adapter import adapt_dynamic_cohorts
from test_local_dynamic_loader import prepared
from test_dynamic_cohort_adapter import row


def options(tmp_path, count=80):
    args, mapping = prepared(tmp_path)
    generation = mapping['expected_generation']
    protocol = dict(outer_folds=3, inner_folds=3, purge_sec=10, embargo_sec=10, minimum_bucket_support=1)
    rows = [row(generation=generation, episode_id=f'e-{i}', opportunity_id=f'o-{i}',
                signal_ts=1000+i*20000, required_end_ts=2000+i*20000,
                pre_entry_features={'regime':{'value':'BULL','observed_ts':999+i*20000}}) for i in range(count)]
    adapted=adapt_dynamic_cohorts(rows,expected_generation=generation,feature_names=['regime'],protocol=protocol)
    mapping=build_local_dynamic_mapping(adapted,group_id=adapted['groups'][0]['group_id'],
                                       expected_generation=generation,protocol=protocol)
    args['config_signature']=_hash(protocol)
    receipt=write_local_dynamic_input(**args,rows=mapping['training_episodes'],mapping_payload=mapping)
    return {**args,'input_sha256':receipt['input_sha256']}


def test_actual_historical_computation_and_repeat_no_refit(tmp_path,monkeypatch):
    args=options(tmp_path)
    result=fit.fit_local_dynamic_input(**args)
    assert result['nested_protocol'] is not None
    assert result['frozen_policy'] is not None
    assert result['qualification_allowed'] is False
    assert result['sealed_holdout_evaluated'] is False
    assert (args['repo_root']/'local-derived/dynamic-fits').exists()
    def forbidden(*a,**k): pytest.fail('repeat retrained')
    monkeypatch.setattr(fit,'train_frozen_dynamic_policy',forbidden)
    monkeypatch.setattr(fit,'nested_purged_walk_forward_dynamic',forbidden)
    assert fit.fit_local_dynamic_input(**args)==result


def test_insufficient_cohort_unknown_and_bounded_work(tmp_path,monkeypatch):
    args=options(tmp_path,count=1)
    result=fit.fit_local_dynamic_input(**args)
    assert result['status']=='UNKNOWN'
    assert result['qualification_allowed'] is False
    args=options(tmp_path/'budget',count=10)
    monkeypatch.setattr(fit,'MAX_WORK',1)
    with pytest.raises(ValueError,match='WORK_BUDGET'):
        fit.fit_local_dynamic_input(**args)
