import pytest
from test_local_dynamic_fit import options
from research import local_dynamic_fit as fit
from research.local_dynamic_seal import seal_historical_fit


def test_actual_seal_future_immutable_and_no_training(tmp_path,monkeypatch):
    args=options(tmp_path)
    fit.fit_local_dynamic_input(**args)
    def forbidden(*a,**k): pytest.fail('seal retrained')
    monkeypatch.setattr(fit,'train_frozen_dynamic_policy',forbidden)
    monkeypatch.setattr(fit,'fit_local_dynamic_input',forbidden)
    now=2_000_000
    result=seal_historical_fit(**args,holdout_start_ts=now+100,clock=lambda:now)
    assert result['verified_fit_available_at']==now
    assert result['seal']['sealed_at']<result['seal']['holdout_start_ts']
    assert result['training_time_basis'].startswith('CONSERVATIVE_VERIFIED_AVAILABILITY')
    assert not result['qualification_allowed']
    assert seal_historical_fit(**args,holdout_start_ts=now+100,clock=lambda:now+200)==result


def test_past_boundary_and_incomplete_fit_rejected(tmp_path):
    args=options(tmp_path)
    fit.fit_local_dynamic_input(**args)
    with pytest.raises(ValueError,match='NOT_FUTURE'):
        seal_historical_fit(**args,holdout_start_ts=2_000_000,clock=lambda:2_000_000)
    args=options(tmp_path/'small',count=1)
    fit.fit_local_dynamic_input(**args)
    with pytest.raises(ValueError,match='MODEL_REQUIRED'):
        seal_historical_fit(**args,holdout_start_ts=2_000_000,clock=lambda:1_900_000)


def test_tampered_fit_rejected_before_seal(tmp_path):
    args=options(tmp_path); fit.fit_local_dynamic_input(**args)
    path=next((args['repo_root']/'local-derived/dynamic-fits').glob('*/historical-result.json'))
    path.chmod(0o666); path.write_text('{}')
    with pytest.raises(ValueError,match='CHECKSUM'):
        seal_historical_fit(**args,holdout_start_ts=2_000_000,clock=lambda:1_900_000)
    assert not (args['repo_root']/'local-derived/dynamic-seals').exists()


def test_interrupted_wrapper_retry_retains_single_seal(tmp_path,monkeypatch):
    from research import local_dynamic_seal as module
    args=options(tmp_path); fit.fit_local_dynamic_input(**args)
    original=module._write_once
    def fail_result(path,value):
        if value.get('schema')=='local_dynamic_prospective_seal_v1': raise OSError('injected')
        return original(path,value)
    monkeypatch.setattr(module,'_write_once',fail_result)
    with pytest.raises(OSError):
        seal_historical_fit(**args,holdout_start_ts=2_000_100,clock=lambda:2_000_000)
    monkeypatch.setattr(module,'_write_once',original)
    result=seal_historical_fit(**args,holdout_start_ts=2_000_100,clock=lambda:2_000_010)
    assert result['verified_fit_available_at']==2_000_000
    assert len(list((args['repo_root']/'local-derived/dynamic-seals/sealed_holdout/seals').glob('*.json')))==1


def test_seal_consumption_recognizes_frozen_dynamic_policy(tmp_path):
    from research_v3_sealed_holdout import consume_seal
    from research_dynamic_entry_policy import evaluate_frozen_dynamic_policy
    from dynamic_policy_analyzer import load_verified_local_dynamic_mapping
    args=options(tmp_path); result=fit.fit_local_dynamic_input(**args)
    sealed=seal_historical_fit(**args,holdout_start_ts=2_000_100,clock=lambda:2_000_000)
    model=result['frozen_policy']; seal=sealed['seal']
    assert {'policy_id':model['policy_id'],'policy_signature':model['content_sha256']} in seal['policy_candidates']
    mapping,_=load_verified_local_dynamic_mapping(**args)
    row=dict(mapping['training_episodes'][-1])
    row.update(episode_id='future-test-only',signal_ts=2_000_200,required_end_ts=2_000_300,
               evidence_collected_at=2_000_400,cohort_signature=seal['cohort_signature'])
    receipt=consume_seal(args['repo_root']/'local-derived/dynamic-seals',seal_id=seal['seal_id'],
        policy_candidates=seal['policy_candidates'],holdout_episodes=[row],evaluation_started_at=2_000_500)
    assert receipt['passed']
    evaluated=evaluate_frozen_dynamic_policy(model,[row],evaluation_mode='SEALED_HOLDOUT',
        sealed_holdout_evaluation=receipt)
    assert evaluated['sealed_holdout_evaluation_verified']


@pytest.mark.parametrize('boundary',[float('nan'),float('inf'),float('-inf')])
def test_nonfinite_future_boundary_rejected(tmp_path,boundary):
    with pytest.raises(ValueError,match='INVALID_HOLDOUT_BOUNDARY'):
        seal_historical_fit(holdout_start_ts=boundary)
