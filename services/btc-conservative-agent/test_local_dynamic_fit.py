import pytest
import json
import subprocess
import sys
from datetime import datetime, timezone
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


def test_result_tamper_rejected(tmp_path):
    args=options(tmp_path)
    fit.fit_local_dynamic_input(**args)
    path=next((args['repo_root']/'local-derived/dynamic-fits').glob('*/historical-result.json'))
    path.chmod(0o666)
    data=json.loads(path.read_text()); data['status']='fake'; path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='CHECKSUM'): fit.fit_local_dynamic_input(**args)


def test_source_changed_before_publication(tmp_path,monkeypatch):
    args=options(tmp_path)
    original=fit.load_verified_local_dynamic_mapping
    calls=[0]
    def load(**kwargs):
        mapping, receipt=original(**kwargs); calls[0]+=1
        if calls[0]==2: receipt={**receipt,'input_sha256':'changed'}
        return mapping,receipt
    monkeypatch.setattr(fit,'load_verified_local_dynamic_mapping',load)
    with pytest.raises(ValueError,match='SOURCE_CHANGED'): fit.fit_local_dynamic_input(**args)
    assert not list((args['repo_root']/'local-derived/dynamic-fits').glob('*/historical-result.json'))


def test_interrupted_after_model_rerun_is_deterministic(tmp_path,monkeypatch):
    args=options(tmp_path)
    original=fit._write_once
    def fail(*a,**k): raise OSError('injected result write failure')
    monkeypatch.setattr(fit,'_write_once',fail)
    with pytest.raises(OSError): fit.fit_local_dynamic_input(**args)
    path=next((args['repo_root']/'local-derived/dynamic-fits').glob('*/frozen-policy.json'))
    before=path.read_bytes()
    monkeypatch.setattr(fit,'_write_once',original)
    result=fit.fit_local_dynamic_input(**args)
    assert path.read_bytes()==before
    assert result['frozen_policy']==json.loads(before)


def test_dependency_bytes_change_identity(tmp_path):
    (tmp_path/'research').mkdir()
    (tmp_path/'research/local_dynamic_fit.py').write_text('import research_dynamic_entry_policy\n')
    (tmp_path/'research_dynamic_entry_policy.py').write_text('from research_entry_baselines import X\n')
    dependency=tmp_path/'research_entry_baselines.py'; dependency.write_text('X=1\n')
    before=fit._code_hash(tmp_path)
    dependency.write_text('X=2\n')
    assert fit._code_hash(tmp_path)!=before


def test_actual_cli_summary_only(tmp_path):
    args=options(tmp_path)
    heartbeat=args['data_root']/'.fly-data-sync-loop.heartbeat.json'
    data=json.loads(heartbeat.read_text()); data['syncedAt']=datetime.now(timezone.utc).isoformat()
    heartbeat.write_text(json.dumps(data))
    command=[sys.executable,'-m','research.local_dynamic_fit']
    for key,value in args.items():
        if key!='now': command.extend(['--'+key.replace('_','-'),str(value)])
    process=subprocess.run(command,capture_output=True,text=True,timeout=20)
    assert process.returncode==0,process.stdout+process.stderr
    summary=json.loads(process.stdout)
    assert summary['qualification_allowed'] is False
    assert 'training_episodes' not in summary and str(tmp_path) not in process.stdout
