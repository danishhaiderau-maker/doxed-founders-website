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


@pytest.mark.parametrize('authority',['component','real_mirror','late_seal'])
def test_genuine_successful_prospective_artifact(tmp_path,monkeypatch,authority):
    from test_local_dynamic_loader import prepared
    from test_dynamic_cohort_adapter import row
    from research.dynamic_cohort_adapter import adapt_dynamic_cohorts
    from research.local_dynamic_mapping import build_local_dynamic_mapping,_hash
    from research.local_dynamic_input import write_local_dynamic_input
    from research.local_dynamic_fit import fit_local_dynamic_input
    from research.local_dynamic_seal import seal_historical_fit
    from research_entry_baselines import materialize_signal_time_baseline_schedules
    from research.conservative_limit_fill import _normalise_schedule
    real_check,real_source=module._check,module._source
    opts,mapping=prepared(tmp_path/'training'); generation=mapping['expected_generation']
    args=inputs(tmp_path/'proof',monkeypatch,generation=generation,shift=2000000)
    original=json.loads((args['data_root']/'v3/ledgers/opportunity.jsonl').read_bytes())
    original['feature_snapshot_at_signal']={'capture_schema':'measured_feature_capture_v1',
        'captured_at_ts':2000010.,'regime':'BULL'}
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
    if authority=='real_mirror':
        import shutil
        from research.canonical_data_store import append_manifest
        from test_declared_shadow_model import contract
        shutil.copytree(args['data_root']/'v3',opts['data_root']/'v3',dirs_exist_ok=True)
        state={}
        for path in (opts['data_root']/'v3').rglob('*'):
            if path.is_file():
                data=path.read_bytes()
                state[path.relative_to(opts['data_root']).as_posix()]={'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                    'inode':path.stat().st_ino,'mtime_ns':path.stat().st_mtime_ns}
        (opts['data_root']/'.fly-sync-state.json').write_text(json.dumps(state))
        manifest=json.loads((opts['data_root']/'canonical_dataset_current.json').read_text())
        fields={k:v for k,v in manifest.items() if k not in ('entry_hash','previous_entry_hash','recorded_at','schema')}
        fields['dataset_checksum']=_hash({'revision':generation['source_revision'],'epoch':generation['epoch_id'],'files':state})
        promoted=append_manifest(opts['data_root'],fields)
        generation={**generation,'manifest_entry_hash':promoted['entry_hash']}
        args['replay_inputs']['generation']=generation
        args['cost_contract']=contract(generation)
        args['replay_inputs']['cost_model']['declared_contract']=args['cost_contract']
        _rebind(args['replay_inputs'])
        args['terminal']=evaluate_shadow_terminal(**args['replay_inputs'])
        assert args['terminal']['status']=='COMPLETE'
        args.update(data_root=opts['data_root'],source_revision=opts['source_revision'],now=opts['now'])
        monkeypatch.setattr(module,'_check',real_check)
        monkeypatch.setattr(module,'_source',real_source)
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
    if authority=='late_seal':
        original['signal_ts']=1999999.
        raw=json.dumps(original).encode()
        (args['data_root']/'v3/ledgers/opportunity.jsonl').write_bytes(raw)
        args['opportunity_ref'].update(row_length=len(raw),row_sha256=hashlib.sha256(raw).hexdigest())
        with pytest.raises(ValueError,match='COUNTERFACTUAL_MODEL_NOT_AVAILABLE'):
            module.write_completion(**args)
        return
    result=module.write_completion(**args)
    assert result['scope']=='SEALED_POLICY_REPLAY_PROOF_NOT_QUALIFIED'
    assert result['qualification_allowed'] is False
    artifact=module.load_completion(args['repo_root'],result['artifact_sha256'])
    assert artifact['entry_semantic_replay_verified'] is True
    assert artifact['verification_blockers']==[]
    from research.holdout_counterfactual_provenance import verify_counterfactual_provenance
    identity={'epoch_id':generation['epoch_id'],'source_episode_id':'original','opportunity_id':'opp',
        'policy_id':'policy','policy_signature':args['terminal']['policy_signature'],'direction':'LONG',
        'seal_request_id':args['seal_request_id']}
    consumer_args={k:args[k] for k in ('repo_root','data_root','source_revision')}
    consumer_args.update(artifact_sha256=result['artifact_sha256'],expected_identity=identity,
        clock=lambda:2000030.,now=args.get('now'))
    if authority=='component':
        import research.counterfactual_source_membership as membership
        monkeypatch.setattr(membership,'verify_membership',lambda *a,**kw:None)
    before_files=set((args['repo_root']/'local-derived/counterfactual-completions').iterdir())
    proof=verify_counterfactual_provenance(**consumer_args)
    assert proof['counterfactual_identity']==identity
    assert proof['entry_semantic_replay_verified'] and proof['terminal_semantic_replay_verified']
    assert proof['causal_provenance']['signal_ts']==2000010.
    assert proof['qualification_allowed'] is False
    assert proof['evidence_collected_at']==2000030.
    assert 'source_lifecycle_identity' not in proof
    assert set((args['repo_root']/'local-derived/counterfactual-completions').iterdir())==before_files
    from research.mirror_generation_lease import MirrorGenerationLease
    held=MirrorGenerationLease(args['data_root'],owner='test-caller').acquire(timeout_seconds=0)
    try:
        assert verify_counterfactual_provenance(**consumer_args,held_lease=held)['counterfactual_identity']==identity
        assert held.held
        with pytest.raises(ValueError,match='EXPECTED_IDENTITY'):
            verify_counterfactual_provenance(**{**consumer_args,'expected_identity':{}},held_lease=held)
        assert held.held
    finally: held.release()
    with pytest.raises(ValueError,match='HELD_LEASE_INVALID'):
        verify_counterfactual_provenance(**consumer_args,held_lease=held)
    with pytest.raises(ValueError,match='EXPECTED_IDENTITY'):
        verify_counterfactual_provenance(**{**consumer_args,'expected_identity':{**identity,'direction':'SHORT'}})
    import copy
    earlier=copy.deepcopy(artifact)
    earlier['verified_at']=args['terminal']['required_horizon_end_ts']
    earlier_digest=_hash(earlier)
    (args['repo_root']/'local-derived/counterfactual-completions'/(earlier_digest+'.json')).write_text(json.dumps(earlier),encoding='utf-8')
    earlier_proof=verify_counterfactual_provenance(**{**consumer_args,'artifact_sha256':earlier_digest})
    assert earlier_proof['evidence_collected_at']==2000030.
    assert earlier_proof['qualification_eligible_at']==2000030.
    if authority=='real_mirror':
        from research.local_holdout_producer import produce_local_holdout
        from research.local_dynamic_evaluation import evaluate_local_frozen_holdout
        import research.holdout_counterfactual_provenance as verifier
        # Deterministic actual verification clock, not artifact-asserted time.
        actual_verify=verifier.verify_counterfactual_provenance
        import research.local_holdout_producer as producer
        verification_clock=[2110001.]
        monkeypatch.setattr(producer,'verify_counterfactual_provenance',
            lambda **kw:actual_verify(**kw,clock=lambda:verification_clock[0]))
        prospective=row(generation=generation,episode_id='original',opportunity_id='opp',policy_id='policy',
            policy_signature=args['terminal']['policy_signature'],cost_model_id=args['terminal']['cost_model_id'],
            simulation_model=args['terminal']['simulation_model'],declared_contract_sha256=_hash(args['cost_contract']),
            signal_ts=2000010.,required_end_ts=args['terminal']['required_horizon_end_ts'],
            pre_entry_features={'regime':{'value':'BULL','observed_ts':2000010.}},
            outcome_state=args['entry']['final_classification'],net_pnl_usd=args['terminal']['net_pnl_usd'],
            counterfactual_identity=identity,replay_proof_sha256=result['artifact_sha256'])
        adapted=adapt_dynamic_cohorts([prospective],expected_generation=generation,feature_names=['regime'],protocol=protocol)
        future_mapping=build_local_dynamic_mapping(adapted,group_id=adapted['groups'][0]['group_id'],expected_generation=generation,protocol=protocol)
        future_opts={k:v for k,v in opts.items() if k!='input_sha256'}
        receipt=write_local_dynamic_input(**future_opts,rows=future_mapping['training_episodes'],mapping_payload=future_mapping)
        future_opts['input_sha256']=receipt['input_sha256']
        holdout=produce_local_holdout(**future_opts)
        assert len(holdout['rows'])==1,holdout['excluded_counts']
        assert holdout['rows'][0]['collection_provenance_by_policy']['policy']['completion']['terminal']==artifact['terminal']
        evaluated=evaluate_local_frozen_holdout(**future_opts,seal_request_id=args['seal_request_id'],clock=lambda:2110002.)
        assert evaluated['comparison']['episodes_scored']==1
        assert evaluated['qualification_allowed'] is False
        verification_clock[0]=2110010.
        assert produce_local_holdout(**future_opts)['artifact_sha256']==holdout['artifact_sha256']
        assert evaluate_local_frozen_holdout(**future_opts,seal_request_id=args['seal_request_id'],clock=lambda:2110011.)==evaluated
        for field,value in [('net_pnl_usd',999.),('cost_model_id','wrong-cost')]:
            badrow={**prospective,field:value}
            adapted_bad=adapt_dynamic_cohorts([badrow],expected_generation=generation,feature_names=['regime'],protocol=protocol)
            badmap=build_local_dynamic_mapping(adapted_bad,group_id=adapted_bad['groups'][0]['group_id'],expected_generation=generation,protocol=protocol)
            badopts={k:v for k,v in future_opts.items() if k!='input_sha256'}
            badreceipt=write_local_dynamic_input(**badopts,rows=badmap['training_episodes'],mapping_payload=badmap)
            rejected=produce_local_holdout(**badopts,input_sha256=badreceipt['input_sha256'])
            assert rejected['rows']==[]
            assert rejected['excluded_counts']=={'COUNTERFACTUAL_OUTCOME_COMPLETION_MISMATCH':1}
    for mutation in ('historical','missing_replay','forged_fill','forged_terminal','wrong_source'):
        changed=copy.deepcopy(artifact)
        if mutation=='historical': changed['scope']='HISTORICAL_REPLAY_ONLY'
        elif mutation=='missing_replay': changed.pop('replay_inputs')
        elif mutation=='forged_fill': changed['entry']['fill_price']=90.
        elif mutation=='forged_terminal': changed['terminal']['net_pnl_usd']=99999.
        else: changed['source']['revision']='wrong'
        digest=_hash(changed)
        (args['repo_root']/'local-derived/counterfactual-completions'/(digest+'.json')).write_text(json.dumps(changed),encoding='utf-8')
        with pytest.raises(ValueError):
            verify_counterfactual_provenance(**{**consumer_args,'artifact_sha256':digest})
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
