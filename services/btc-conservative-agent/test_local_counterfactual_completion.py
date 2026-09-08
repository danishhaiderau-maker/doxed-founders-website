import hashlib,json
import pytest
from research import local_counterfactual_completion as module
from test_conservative_shadow_terminal import _inputs,_rebind
from test_declared_shadow_model import contract
from research.conservative_shadow_terminal import evaluate_shadow_terminal


def inputs(tmp_path,monkeypatch,generation=None,shift=0):
    values=_inputs()
    if generation is not None: values['generation']=generation
    values['entry_receipt']['trigger_bucket_ts']+=shift
    values['entry_receipt']['quantity_attempts'][0]['trigger_bucket_ts']+=shift
    for row in values['future_path_rows']: row['bucket_ts']+=shift
    values['required_horizon_end_ts']+=shift
    costs=contract(values['generation'])
    values['cost_model'].update(calculation_mode='DECLARED_EXECUTION_RATE_MODEL_V1',declared_contract=costs,cost_provenance='DECLARED_SIMULATION')
    _rebind(values); terminal=evaluate_shadow_terminal(**values)
    assert terminal['status']=='COMPLETE'
    root=tmp_path/'mirror'; path=root/'v3/ledgers/opportunity.jsonl'; path.parent.mkdir(parents=True)
    raw=json.dumps({'record_id':'opp','epoch_id':values['generation']['epoch_id'],
        'source_revision':values['generation']['source_revision'],'signal_ts':9.+shift,
        'deployed_revision':values['generation']['deployed_revision'],
        'tile_config_signature':values['generation']['tile_config_signature']}).encode()
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
        policy_id='policy',source_segments=refs,clock=lambda:20.+shift,replay_inputs=values)


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
    with pytest.raises(ValueError,match='SEAL_GENERATION_MISMATCH'):
        module.write_completion(**args)


def test_completion_artifact_tamper_rejected(tmp_path,monkeypatch):
    args=inputs(tmp_path,monkeypatch); receipt=module.write_completion(**args)
    path=args['repo_root']/'local-derived/counterfactual-completions'/(receipt['artifact_sha256']+'.json')
    body=json.loads(path.read_text()); body['direction']='SHORT'; path.chmod(0o666); path.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='ARTIFACT_HASH'): module.load_completion(args['repo_root'],receipt['artifact_sha256'])


def test_genuine_successful_prospective_artifact(tmp_path,monkeypatch):
    from test_local_dynamic_loader import prepared
    from test_dynamic_cohort_adapter import row
    from research.dynamic_cohort_adapter import adapt_dynamic_cohorts
    from research.local_dynamic_mapping import build_local_dynamic_mapping,_hash
    from research.local_dynamic_input import write_local_dynamic_input
    from research.local_dynamic_fit import fit_local_dynamic_input
    from research.local_dynamic_seal import seal_historical_fit
    from research_entry_baselines import materialize_signal_time_baseline_schedules
    from research.conservative_limit_fill import _normalise_schedule
    opts,mapping=prepared(tmp_path/'training'); generation=mapping['expected_generation']
    args=inputs(tmp_path/'proof',monkeypatch,generation=generation,shift=2000000)
    original=json.loads((args['data_root']/'v3/ledgers/opportunity.jsonl').read_bytes())
    original.update(signal_ts=2000010.,expiry_ts=2001810.,requested_qty=.4,requested_remaining_qty=.4,
        signed_quantity_constraints=args['entry']['quantity_constraints'],latency_sec=0,fees_usd=0,
        slippage_model='DECLARED_LIMIT',authoritative_parent_expiry=True,direction='UNKNOWN',
        dataset_epoch=generation['epoch_id'])
    original.update(episode_id='original',opportunity_id='opp',raw_direction='UNKNOWN',symbol='BTCUSD',
        signal_time_bbo={'bid':100.,'ask':100.,'bid_qty':1.,'ask_qty':1.,'source_ts':2000010.,'observed_at_ts':2000010.},signal_price=100.)
    original['baseline_schedule_snapshot']=materialize_signal_time_baseline_schedules(original)
    capture=original['baseline_schedule_snapshot']['directional_schedules']['LONG']
    schedule=capture['schedules']['MARKET_ENTRY_AT_SIGNAL']['schedule']
    from test_entry_baseline_replay import _row,_segment_object
    from research.entry_baseline_replay import materialize_same_opportunity_replay
    entry_rows=[{**_row(2000010,bid=100,ask=100),'symbol':'BTCUSD'}]
    digest,_=_segment_object(args['data_root'],entry_rows)
    args['entry_source_segments']=[{'sha256':digest}]
    baseline=materialize_same_opportunity_replay([{**original,'market_microstructure_rows':entry_rows}],generation=generation)
    entry=next(r['conservative_receipt'] for e in baseline['episode_receipts'] if e['direction']=='LONG'
        for r in e['results'] if r['baseline_id']=='MARKET_ENTRY_AT_SIGNAL')
    assert entry and entry['supported']
    args['entry']=entry; args['replay_inputs']['entry_receipt']=entry
    from research_entry_baselines import ENTRY_BASELINE_REGISTRY
    from research.conservative_shadow_report import build_composite_policy_identity
    from research_v3_contract import canonical_hash
    spec=next(s for s in ENTRY_BASELINE_REGISTRY['baselines'] if s['baseline_id']=='MARKET_ENTRY_AT_SIGNAL')
    exit_spec=args['replay_inputs']['policy_spec']
    args['exit_candidate']={'policy_id':'policy','policy_signature':canonical_hash('v3-policy',exit_spec),'policy_spec':exit_spec}
    composite_spec,composite=build_composite_policy_identity({'baseline_id':spec['baseline_id'],'baseline_spec':spec,'policy_signature':spec['policy_signature'],'conservative_receipt':args['entry']},args['exit_candidate'])
    args['replay_inputs']['policy_spec']=composite_spec
    args['replay_inputs']['policy_signature']=composite['composite_policy_signature']
    _rebind(args['replay_inputs']); args['terminal']=evaluate_shadow_terminal(**args['replay_inputs'])
    raw=json.dumps(original).encode(); (args['data_root']/'v3/ledgers/opportunity.jsonl').write_bytes(raw)
    args['opportunity_ref'].update(row_length=len(raw),row_sha256=hashlib.sha256(raw).hexdigest())
    args['baseline_reference']={'baseline_id':'MARKET_ENTRY_AT_SIGNAL','capture_signature':capture['capture_signature'],
        'opportunity_id':'opp','source_episode_id':'original'}
    protocol=dict(outer_folds=3,inner_folds=3,purge_sec=10,embargo_sec=10,minimum_bucket_support=1)
    rows=[row(generation=generation,episode_id=f'e-{i}',opportunity_id=f'o-{i}',policy_id='policy',
        policy_signature=args['terminal']['policy_signature'],cost_model_id=args['terminal']['cost_model_id'],
        simulation_model=args['terminal']['simulation_model'],declared_contract_sha256=_hash(args['cost_contract']),
        signal_ts=1000+i*20000,required_end_ts=2000+i*20000,
        pre_entry_features={'regime':{'value':'BULL','observed_ts':999+i*20000}}) for i in range(80)]
    adapted=adapt_dynamic_cohorts(rows,expected_generation=generation,feature_names=['regime'],protocol=protocol)
    mapping=build_local_dynamic_mapping(adapted,group_id=adapted['groups'][0]['group_id'],expected_generation=generation,protocol=protocol)
    opts['config_signature']=_hash(protocol)
    receipt=write_local_dynamic_input(**opts,rows=mapping['training_episodes'],mapping_payload=mapping)
    opts['input_sha256']=receipt['input_sha256']; fit_local_dynamic_input(**opts)
    sealed=seal_historical_fit(**opts,holdout_start_ts=2000001.,holdout_end_ts=2100000.,
        holdout_maturity_delay_sec=10000.,clock=lambda:2000000.)
    args.update(repo_root=opts['repo_root'],seal_request_id=_hash(sealed['binding']))
    result=module.write_completion(**args)
    assert result['scope']=='SEALED_POLICY_REPLAY_PROOF_NOT_QUALIFIED'
    assert result['qualification_allowed'] is False
    artifact=module.load_completion(args['repo_root'],result['artifact_sha256'])
    assert artifact['entry_semantic_replay_verified'] is True
    assert artifact['verification_blockers']==[]
    import copy
    for kind in ('opportunity','schedule','direction','forged_terminal'):
        altered=copy.deepcopy(args)
        if kind=='opportunity': altered['baseline_reference']['opportunity_id']='other'
        elif kind=='schedule': altered['entry']['schedule_sha256']='0'*64
        elif kind=='direction': altered['entry']['direction']='SHORT'
        else:
            altered['terminal']['net_pnl_usd']=99999.
            altered['terminal']['receipt_sha256']=_hash({k:v for k,v in altered['terminal'].items() if k!='receipt_sha256'})
        with pytest.raises(ValueError): module.write_completion(**altered)
    for kind in ('favorable_fill','pre_signal_fill','opposite_same_price'):
        altered=copy.deepcopy(args)
        if kind=='favorable_fill':
            altered['entry']['fill_price']=90.
        elif kind=='pre_signal_fill':
            altered['entry']['trigger_bucket_ts']=2000009.
        else:
            altered['entry']['direction']='SHORT'
        _rebind(altered['replay_inputs'])
        altered['terminal']=evaluate_shadow_terminal(**altered['replay_inputs'])
        with pytest.raises(ValueError): module.write_completion(**altered)
