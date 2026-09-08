import hashlib,json
import pytest
from research import local_counterfactual_completion as module
from test_conservative_shadow_terminal import _inputs,_rebind
from test_declared_shadow_model import contract
from research.conservative_shadow_terminal import evaluate_shadow_terminal


def inputs(tmp_path,monkeypatch):
    values=_inputs(); costs=contract(values['generation'])
    values['cost_model'].update(calculation_mode='DECLARED_EXECUTION_RATE_MODEL_V1',declared_contract=costs,cost_provenance='DECLARED_SIMULATION')
    _rebind(values); terminal=evaluate_shadow_terminal(**values)
    assert terminal['status']=='COMPLETE'
    root=tmp_path/'mirror'; path=root/'v3/ledgers/opportunity.jsonl'; path.parent.mkdir(parents=True)
    raw=json.dumps({'record_id':'opp','epoch_id':values['generation']['epoch_id'],
        'source_revision':values['generation']['source_revision'],'signal_ts':9.}).encode()
    path.write_bytes(raw)
    refs=[]
    for payload in values['source_segment_payloads']:
        digest=hashlib.sha256(payload).hexdigest(); target=root/'v3/market_segments'/digest[:2]/(digest+'.json')
        target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(payload); refs.append({'sha256':digest})
    source={'epoch':values['generation']['epoch_id'],'revision':values['generation']['source_revision'],
        'deployed_revision':values['generation']['deployed_revision']}
    monkeypatch.setattr(module,'_check',lambda *a,**k:source); monkeypatch.setattr(module,'_source',lambda v:v)
    return dict(repo_root=tmp_path/'repo',data_root=root,source_revision=source['revision'],
        opportunity_ref={'record_id':'opp','byte_offset':0,'row_length':len(raw),'row_sha256':hashlib.sha256(raw).hexdigest()},
        entry=values['entry_receipt'],terminal=terminal,path_rows=values['future_path_rows'],cost_contract=costs,
        policy_id='policy',source_segments=refs,clock=lambda:20.)


def test_actual_terminal_immutable_historical_proof(tmp_path,monkeypatch):
    args=inputs(tmp_path,monkeypatch)
    raw=(args['data_root']/'v3/ledgers/opportunity.jsonl').read_bytes()
    result=module.write_completion(**args)
    assert result['scope']=='HISTORICAL_REPLAY_ONLY' and result['qualification_allowed'] is False
    assert module.write_completion(**args)==result
    value=module.load_completion(args['repo_root'],result['artifact_sha256'])
    assert value['verified_at']==20.
    assert (args['data_root']/'v3/ledgers/opportunity.jsonl').read_bytes()==raw


@pytest.mark.parametrize('kind',['quantity','time','direction','policy','path','missing'])
def test_invalid_proof_never_written(tmp_path,monkeypatch,kind):
    args=inputs(tmp_path,monkeypatch)
    if kind=='quantity': args['entry']['filled_qty']=float('nan')
    elif kind=='time': args['clock']=lambda:float('inf')
    elif kind=='direction': args['entry']['direction']='SHORT'
    elif kind=='policy': args['terminal']['policy_signature']='wrong'
    elif kind=='path': args['path_rows'][0]['bid']=999.
    else: args['source_segments']=[]
    with pytest.raises(ValueError): module.write_completion(**args)
    assert not (args['repo_root']/'local-derived/counterfactual-completions').exists()


def test_genuine_seal_created_after_signal_cannot_make_replay_prospective(tmp_path,monkeypatch):
    from test_local_dynamic_fit import options
    from research.local_dynamic_fit import fit_local_dynamic_input
    from research.local_dynamic_seal import seal_historical_fit
    from research.local_dynamic_mapping import _hash
    opts=options(tmp_path/'fit'); fit_local_dynamic_input(**opts)
    sealed=seal_historical_fit(**opts,holdout_start_ts=2000100.,holdout_end_ts=2100000.,
        holdout_maturity_delay_sec=10000.,clock=lambda:2000000.)
    args=inputs(tmp_path/'proof',monkeypatch); args['repo_root']=opts['repo_root']
    args['seal_request_id']=_hash(sealed['binding'])
    with pytest.raises(ValueError,match='MODEL_NOT_AVAILABLE'):
        module.write_completion(**args)


def test_completion_artifact_tamper_rejected(tmp_path,monkeypatch):
    args=inputs(tmp_path,monkeypatch); receipt=module.write_completion(**args)
    path=args['repo_root']/'local-derived/counterfactual-completions'/(receipt['artifact_sha256']+'.json')
    body=json.loads(path.read_text()); body['direction']='SHORT'; path.chmod(0o666); path.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='ARTIFACT_HASH'): module.load_completion(args['repo_root'],receipt['artifact_sha256'])
