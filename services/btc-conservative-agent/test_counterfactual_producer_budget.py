import copy
import json
import pytest
from research import local_holdout_producer as module


def fixture(tmp_path,monkeypatch):
    root=tmp_path/'mirror'; root.mkdir()
    source={'epoch':'epoch'}; episodes=[]
    for i in range(40):
        identity={'epoch_id':'epoch','source_episode_id':str(i),'opportunity_id':str(i),
            'policy_id':'p','policy_signature':'a'*64,'direction':'LONG','seal_request_id':'b'*64}
        episodes.append({'dataset_epoch':'epoch','source_episode_id':str(i),'opportunity_id':str(i),
            'direction':'LONG','signal_ts':1,'required_end_ts':2,'pre_entry_features':{'regime':{'value':'BULL','observed_ts':1}},
            'policy_outcomes':{'p':{'counterfactual_identity':identity,'replay_proof_sha256':'c'*64,
                'outcome_state':'FULL_FILL','net_pnl_usd':1}}})
    mapping={'expected_generation':{'epoch_id':'epoch'},'candidates':[{'policy_id':'p','policy_signature':'a'*64}],
        'selected_group':{'cost_model_id':'cost','simulation_model':'sim','sizing':{'contract_sha256':'d'*64}},
        'training_episodes':episodes}
    calls=[]; clock=[100]
    def verify(**kw):
        assert kw['held_lease'].held
        calls.append(kw['expected_identity']); original=episodes[int(kw['expected_identity']['source_episode_id'])]
        return {'schema':'verified_counterfactual_collection_provenance_v1','counterfactual_identity':kw['expected_identity'],
            'replay_proof_sha256':'c'*64,'entry_semantic_replay_verified':True,'terminal_semantic_replay_verified':True,
            'causal_provenance':{k:original[k] for k in ('source_episode_id','opportunity_id','signal_ts','pre_entry_features')},
            'completion':{'entry':{'final_classification':'FULL_FILL'},'terminal':{'net_pnl_usd':1,'status':'COMPLETE',
                'cost_model_id':'cost','simulation_model':'sim','declared_contract_sha256':'d'*64,'required_horizon_end_ts':2}},
            'evidence_collected_at':clock[0],'qualification_eligible_at':clock[0]}
    monkeypatch.setattr(module,'load_verified_local_dynamic_mapping',lambda **kw:(mapping,{'source_generation':copy.deepcopy(source)}))
    monkeypatch.setattr(module,'_check',lambda *a,**kw:source)
    monkeypatch.setattr(module,'_source',lambda value:value)
    monkeypatch.setattr(module,'verify_counterfactual_provenance',verify)
    return {'repo_root':tmp_path/'repo','data_root':root,'source_revision':'rev'},calls,clock,source


def test_bounded_new_proofs_resume_and_retry_preserves_original_hash(tmp_path,monkeypatch):
    args,calls,clock,source=fixture(tmp_path,monkeypatch)
    first=module.produce_local_holdout(**args)
    assert first['status']=='IN_PROGRESS' and first['new_proofs']==32 and first['pending_proofs']==8
    assert not list((args['repo_root']/'local-derived/holdout-inputs').glob('*.json'))
    clock[0]=200
    final=module.produce_local_holdout(**args)
    assert len(final['rows'])==40
    assert len(calls)==72
    clock[0]=300
    assert module.produce_local_holdout(**args)==final
    source['extra_generation_binding']='changed'
    changed=module.produce_local_holdout(**args)
    assert changed['status']=='IN_PROGRESS' and changed['new_proofs']==32


@pytest.mark.parametrize('fault',['corrupt_time','foreign_receipt'])
def test_corrupt_fixed_producer_receipt_fails_closed(tmp_path,monkeypatch,fault):
    args,*_=fixture(tmp_path,monkeypatch)
    module.produce_local_holdout(**args)
    path=next(p for p in (args['repo_root']/'local-derived/holdout-progress').glob('*/*.json') if not p.name.startswith('revalidation'))
    value=json.loads(path.read_text())
    if fault=='corrupt_time': value['proof']['evidence_collected_at']=1
    else:
        value['job']='different-caller-job'
        value['sha256']=module.hashlib.sha256(module._encoded({k:v for k,v in value.items() if k!='sha256'})).hexdigest()
    path.chmod(0o666); path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='PRODUCER_RECEIPT_INVALID'):
        module.produce_local_holdout(**args)


def test_exception_details_are_allowlisted():
    assert module._counterfactual_reason(ValueError('COUNTERFACTUAL_MODEL_NOT_AVAILABLE'))=='COUNTERFACTUAL_MODEL_NOT_AVAILABLE'
    assert module._counterfactual_reason(ValueError('COUNTERFACTUAL_MODEL_NOT_AVAILABLE secret=abc'))=='COUNTERFACTUAL_PROVENANCE_UNVERIFIED'
    assert module._counterfactual_reason(OSError('secret=abc'))=='COUNTERFACTUAL_PROVENANCE_UNVERIFIED'


@pytest.mark.parametrize('failure',['exception','mismatch'])
def test_failed_first_page_cannot_starve_later_valid_proofs(tmp_path,monkeypatch,failure):
    args,calls,clock,source=fixture(tmp_path,monkeypatch)
    verify=module.verify_counterfactual_provenance
    broken=[True]
    def sometimes(**kw):
        if broken[0] and int(kw['expected_identity']['source_episode_id'])<32:
            if failure=='exception': raise ValueError('COUNTERFACTUAL_MODEL_NOT_AVAILABLE')
            result=verify(**kw); result['completion']['terminal']['net_pnl_usd']=99
            return result
        return verify(**kw)
    monkeypatch.setattr(module,'verify_counterfactual_provenance',sometimes)
    first=module.produce_local_holdout(**args)
    assert first['status']=='IN_PROGRESS' and first['new_proofs']==32
    final=module.produce_local_holdout(**args)
    assert len(final['rows'])==8
    assert sum(final['excluded_counts'].values())==32
    # A failed attempt is not permanent negative authority. Repaired source
    # proof appends a separate immutable verified receipt on the next run.
    broken[0]=False; clock[0]=200
    recovered=module.produce_local_holdout(**args)
    assert len(recovered['rows'])==40 and recovered['excluded_counts']=={}
    assert len(list((args['repo_root']/'local-derived/holdout-progress').glob('*/*.verified.json')))==32
