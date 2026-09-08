import json
import pytest
from test_local_dynamic_fit import options
from research import local_dynamic_fit as fit
from research import local_dynamic_fit_loader as loader


def test_actual_fit_read_never_trains_and_no_invented_time(tmp_path,monkeypatch):
    args=options(tmp_path)
    result=fit.fit_local_dynamic_input(**args)
    def forbidden(*a,**k): pytest.fail('loader trained')
    monkeypatch.setattr(fit,'fit_local_dynamic_input',forbidden)
    monkeypatch.setattr(fit,'train_frozen_dynamic_policy',forbidden)
    monkeypatch.setattr(fit,'nested_purged_walk_forward_dynamic',forbidden)
    loaded=loader.load_verified_historical_fit(**args)
    assert loaded['result']==result
    assert loaded['frozen_policy']==result['frozen_policy']
    assert loaded['training_completed_at'] is None
    assert not loaded['qualification_allowed']


@pytest.mark.parametrize('filename',['historical-result.json','frozen-policy.json'])
def test_tampered_persisted_artifacts_rejected(tmp_path,filename):
    args=options(tmp_path); fit.fit_local_dynamic_input(**args)
    path=next((args['repo_root']/'local-derived/dynamic-fits').glob('*/'+filename))
    path.chmod(0o666)
    data=json.loads(path.read_text()); data['tampered']=True
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='CHECKSUM|MISMATCH'):
        loader.load_verified_historical_fit(**args)


def test_source_change_during_read_rejected(tmp_path,monkeypatch):
    args=options(tmp_path); fit.fit_local_dynamic_input(**args)
    original=loader.load_verified_local_dynamic_mapping
    calls=[0]
    def changed(**kwargs):
        mapping,receipt=original(**kwargs); calls[0]+=1
        if calls[0]==2: receipt={**receipt,'input_sha256':'changed'}
        return mapping,receipt
    monkeypatch.setattr(loader,'load_verified_local_dynamic_mapping',changed)
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):
        loader.load_verified_historical_fit(**args)


def test_missing_fit_does_not_create_or_compute(tmp_path):
    args=options(tmp_path)
    with pytest.raises(FileNotFoundError): loader.load_verified_historical_fit(**args)
    assert not (args['repo_root']/'local-derived/dynamic-fits').exists()
