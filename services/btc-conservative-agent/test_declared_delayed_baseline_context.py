from copy import deepcopy
import pytest
import research.baseline_execution_context as module
from research.latency_schedule_replay import replay_delayed_entry, TREATMENT
from research.entry_baseline_replay import materialize_v3_opportunity_replay
from test_declared_directional_context_integration import dataset


@pytest.mark.parametrize('value',[None,[],False,{'entry_receipt':[]}])
def test_malformed_delayed_receipt_is_unknown(value):
    result=module.build_declared_delayed_baseline_context(timing_declaration={},tape=[],
        delayed_replay_receipt=value,entry_evidence={},entry_binding={},capture={})
    assert result['status']=='UNKNOWN'


@pytest.mark.parametrize('delay',[0,2])
def test_zero_delay_pinned_context_and_replay_tamper(tmp_path,monkeypatch,delay):
    import test_declared_directional_context_integration as fixture_module
    original_row=fixture_module._row
    def changing_row(ts,**kwargs):
        if ts>=102:
            kwargs.update(bid=106,ask=106.1)
        if ts>=103:
            kwargs.update(bid=107,ask=107.1)
        return original_row(ts,**kwargs)
    monkeypatch.setattr(fixture_module,'_row',changing_row)
    producer=fixture_module.materialize_signal_time_baseline_schedules
    def capture_timing(opportunity):
        schedules=producer(opportunity)
        opportunity['research_timing_declarations']=[dict(
            schema='declared_submission_timing_v1',evidence_basis='DECLARED_SIMULATION',
            provenance='FIXTURE',declared_at_ts=100,source_capture_signature=capture['capture_signature'],
            delay_sec=delay,ordering_treatment=TREATMENT)
            for capture in schedules['directional_schedules'].values()]
        return schedules
    monkeypatch.setattr(fixture_module,'materialize_signal_time_baseline_schedules',capture_timing)
    generation,manifest=dataset(tmp_path)
    captured=[]
    original=module.build_declared_directional_baseline_context
    def intercept(**kwargs):
        captured.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(module,'build_declared_directional_baseline_context',intercept)
    baseline_report=materialize_v3_opportunity_replay(tmp_path,generation=generation,canonical_manifest=manifest)
    args=next(value for value in captured if original(**value)['status']=='SUPPORTED'
        and (delay==0 or (value['capture']['direction']=='SHORT' and
             value['capture']['schedules'][value['baseline']['baseline_id']]['schedule'][0]['end_ts']>102)))
    # Verified source envelopes are reused, never substituted with caller-only tape.
    segment,_=module._verified(args['coverage_evidence'],args['pinned_sources'])
    tape=segment['rows']
    inputs=module.declared_directional_baseline_inputs(args['capture'],args['baseline'])
    timing=dict(schema='declared_submission_timing_v1',evidence_basis='DECLARED_SIMULATION',
        provenance='FIXTURE',declared_at_ts=100,source_capture_signature=args['capture']['capture_signature'],
        delay_sec=delay,ordering_treatment=TREATMENT)
    replay=replay_delayed_entry(schedule=args['capture']['schedules'][args['baseline']['baseline_id']]['schedule'],
        tape=tape,delay_sec=delay,ordering_treatment=TREATMENT,direction=args['capture']['direction'],
        requested_qty=inputs['requested_qty'],quantity_constraints=inputs['signed_quantity_constraints'],symbol='BTC')
    common={k:v for k,v in args.items() if k!='entry_receipt'}
    assert replay['status']=='ENTRY_REPLAY_SUPPORTED',replay
    common.update(timing_declaration=timing,tape=tape,delayed_replay_receipt=replay,
        entry_evidence=args['coverage_evidence'],entry_binding=args['coverage_binding'])
    result=module.build_declared_delayed_baseline_context(**common)
    assert result['status']=='SUPPORTED',result
    assert result['context']['timing_basis']=='DECLARED_DELAYED_SUBMISSION_REPLAY'
    assert result['context']['qualification_eligible'] is False
    opportunity,_=module._verified(args['opportunity_binding'],args['pinned_sources'])
    assert timing in opportunity['research_timing_declarations']
    model_fields=('schema','delay_sec','ordering_treatment','evidence_basis')
    model_hashes={module._sha({key:t[key] for key in model_fields})
                  for t in opportunity['research_timing_declarations']}
    assert model_hashes=={result['context']['timing_model_sha256']}
    assert module._sha({key:({**timing,'delay_sec':delay+1})[key] for key in model_fields}) not in model_hashes
    from research.declared_shadow_model import _baseline_context
    _baseline_context({'execution_model_context':result['context'],
                       'conservative_receipt':{**replay['entry_receipt'],'symbol':'BTC'}},generation)
    from research.policy_evidence_schema import stable_hash
    tampered={k:v for k,v in result['context'].items() if k!='signature'}
    tampered['timing_declaration_sha256']='0'*64
    tampered['signature']=stable_hash('baseline-execution-model-context',tampered)
    with pytest.raises(ValueError,match='BASELINE_DELAYED_REPLAY_BINDING_INVALID'):
        _baseline_context({'execution_model_context':tampered,
                           'conservative_receipt':{**replay['entry_receipt'],'symbol':'BTC'}},generation)
    altered=deepcopy(replay); altered['replay_input_sha256']='0'*64
    assert module.build_declared_delayed_baseline_context(**{**common,'delayed_replay_receipt':altered})['status']=='UNKNOWN'
    assert module.build_declared_delayed_baseline_context(**{**common,'tape':[]})['status']=='UNKNOWN'
    assert module.build_declared_delayed_baseline_context(**{**common,'timing_declaration':{**timing,'declared_at_ts':101}})['status']=='UNKNOWN'
    assert module.build_declared_delayed_baseline_context(**{**common,'timing_declaration':{**timing,'declared_at_ts':99}})['reason_codes']==['DELAYED_CONTEXT_TIMING_SOURCE_UNBOUND']
    from test_conservative_shadow_report import _fixture
    from test_declared_shadow_model import contract, sign
    from research.conservative_shadow_report import build_conservative_shadow_report
    model=sign({**contract(generation),'funding':{'treatment':'CONSTANT_ENTRY_NOTIONAL_RATE','rate_per_hour':.001}})
    _,candidates,artifact,_=_fixture(tmp_path/'policies',model=False)
    artifact.update(evaluation_generation=generation,artifact_identity={
        'epoch_id':generation['epoch_id'],'source_revision':generation['source_revision'],
        'analyzer_generation_revision':generation['analyzer_revision'],
        'tile_config_signature':generation['tile_config_signature']})
    selected_episode=next(e for e in baseline_report['episode_receipts'] if e['episode_id']==args['identity']['episode_id'])
    selected_entry=next(e for e in selected_episode['results'] if e['baseline_id']==args['baseline']['baseline_id'])
    original_report=build_conservative_shadow_report(tmp_path,expected_generation=generation,
        baseline_report=baseline_report,policy_candidates=candidates,policy_artifact_receipt=artifact,
        research_model=model)
    selected_entry.update(conservative_receipt={**replay['entry_receipt'],'symbol':'BTC'},
        execution_model_context=result['context'],outcome_state=replay['entry_receipt']['final_classification'])
    report=build_conservative_shadow_report(tmp_path,expected_generation=generation,
        baseline_report=baseline_report,policy_candidates=candidates,policy_artifact_receipt=artifact,
        research_model=model)
    terminals=[r for r in report['results'] if r.get('episode_id')==args['identity']['episode_id']
               and r.get('baseline_id')==args['baseline']['baseline_id']]
    assert terminals and terminals[0]['status']=='COMPLETE',terminals
    terminal=terminals[0]['terminal']
    assert ':DECLARED_DELAYED_SUBMISSION:' in terminal['simulation_model']
    assert terminal['entry_complete_ts']>=100
    if delay:
        old=next(r['terminal'] for r in original_report['results']
                 if r.get('episode_id')==args['identity']['episode_id']
                 and r.get('baseline_id')==args['baseline']['baseline_id'])
        assert terminal['entry_complete_ts']>old['entry_complete_ts']
        assert terminal['replay_start_ts']>old['replay_start_ts']
        assert terminal['economics_evidence_basis']=='DECLARED_SIMULATION'
        assert terminal['trading_fees_usd']!=old['trading_fees_usd']
