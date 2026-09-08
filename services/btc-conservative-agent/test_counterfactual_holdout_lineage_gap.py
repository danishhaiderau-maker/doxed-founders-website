"""Supported terminal replay is not prospective lifecycle provenance.

This regression uses the existing supported-entry fixture, not a claim of an
end-to-end live collector or newly computed baseline fill.
"""
import pytest
from test_discovery_scorecard_publication import shadow_inputs,GENERATION
from research.discovery_scorecard_publication import _dynamic_projection
from test_local_dynamic_loader import prepared
from test_dynamic_cohort_adapter import row
from test_local_dynamic_mapping import PROTOCOL
from research.dynamic_cohort_adapter import adapt_dynamic_cohorts
from research.local_dynamic_mapping import build_local_dynamic_mapping
from research.local_dynamic_input import write_local_dynamic_input
from research.local_holdout_producer import produce_local_holdout


@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('verdict',['REJECT','AI_NOT_CALLED'])
def test_supported_shadow_provenance_cannot_be_promoted_to_prospective_lifecycle(tmp_path,direction,verdict):
    baseline,shadow=shadow_inputs(tmp_path/'shadow')
    assert shadow['complete_replay_count']==1
    episode=baseline['episode_receipts'][0]
    terminal=shadow['results'][0]['terminal']
    projected=_dynamic_projection(episode,GENERATION,terminal)
    assert projected['source_lifecycle_identity'] is None
    args,existing=prepared(tmp_path/'mirror-fixture')
    generation=existing['expected_generation']
    # Exercise both mapping strata; never relabel the LONG terminal as SHORT.
    # Terminal proof contributes only its demonstrated absent lineage here.
    candidate=row(generation=generation,direction=direction,raw_ai_decision=verdict,
        ai_evaluated=verdict!='AI_NOT_CALLED',source_lifecycle_identity=projected['source_lifecycle_identity'])
    adapted=adapt_dynamic_cohorts([candidate],expected_generation=generation,
        feature_names=['regime'],protocol=PROTOCOL)
    mapping=build_local_dynamic_mapping(adapted,group_id=adapted['groups'][0]['group_id'],
        expected_generation=generation,protocol=PROTOCOL)
    written=write_local_dynamic_input(**args,rows=mapping['training_episodes'],mapping_payload=mapping)
    result=produce_local_holdout(**args,input_sha256=written['input_sha256'])
    assert result['excluded_counts']['EXACT_SOURCE_LIFECYCLE_IDENTITY_MISSING']==1
    assert result['qualification_allowed'] is False
    assert result['rows']==[]


@pytest.mark.parametrize('verdict',['REJECT','AI_NOT_CALLED'])
def test_actual_directional_fill_to_terminal_to_scorecard(tmp_path,monkeypatch,verdict):
    import copy,hashlib,json
    from test_both_direction_baseline_capture import fresh
    from research.entry_baseline_replay import materialize_same_opportunity_replay
    from test_conservative_shadow_report import _fixture
    from research.conservative_shadow_report import build_conservative_shadow_report,build_composite_policy_identity
    from research.policy_evidence_schema import stable_hash,canonical_json
    from test_entry_baseline_replay import _segment_object,_row
    from research.discovery_scorecard_publication import build_discovery_scorecard_publication
    import test_discovery_scorecard_publication as fixtures
    args,existing=prepared(tmp_path/'mirror'); generation=existing['expected_generation']
    monkeypatch.setattr(fixtures,'GENERATION',generation)
    root,status,_=fixtures.inputs(tmp_path/'publication',rows=[])
    source=fresh('UNKNOWN'); source.update(source_revision=generation['source_revision'],
        deployed_revision=generation['deployed_revision'],dataset_epoch=generation['epoch_id'],epoch_id=generation['epoch_id'],
        tile_config_signature=generation['tile_config_signature'],config_signature='source-config',
        market='spot',pre_entry_features={'regime':{'value':'BULL','observed_ts':99.}},bucket_definition_signature='buckets',
        raw_ai_decision=verdict,ai_evaluated=verdict!='AI_NOT_CALLED')
    from research_entry_baselines import materialize_signal_time_baseline_schedules
    source['baseline_schedule_snapshot']=materialize_signal_time_baseline_schedules(source)
    baseline=materialize_same_opportunity_replay([source],generation=generation)
    _,candidates,artifact,model=_fixture(tmp_path/'model')
    artifact['evaluation_generation']=generation
    artifact['artifact_identity']={'epoch_id':generation['epoch_id'],'source_revision':generation['source_revision'],
        'analyzer_generation_revision':generation['analyzer_revision'],'tile_config_signature':generation['tile_config_signature']}
    artifact['artifact_verified_identity_fields']=sorted(artifact['artifact_identity'])
    contexts=[]
    for episode in baseline['episode_receipts']:
        entry=next(r for r in episode['results'] if r['baseline_id']=='MARKET_ENTRY_AT_SIGNAL')
        assert entry['supported'] and entry['outcome_state']=='FULL_FILL',entry
        episode['results']=[entry]
        rows=[_row(ts,bid=100,ask=101) for ts in (100,101,102,103)]
        digest,relative=_segment_object(root,rows)
        episode['market_evidence_provenance']=[{'status':'VERIFIED','sha256':digest,'relative_path':relative,'segment_record_id':'s1'}]
        context=copy.deepcopy(model['contexts'][0])
        from test_declared_shadow_model import contract
        context.update(calculation_mode='DECLARED_EXECUTION_RATE_MODEL_V1',declared_contract=contract(generation),cost_provenance='DECLARED_SIMULATION')
        context.update(episode_id=episode['episode_id'],opportunity_id=episode['opportunity_id'],baseline_id=entry['baseline_id'],
            composite_policy_signature=build_composite_policy_identity(entry,candidates[0])[1]['composite_policy_signature'],
            margin_usd=entry['conservative_receipt']['fill_price']*entry['conservative_receipt']['filled_qty']/context['leverage'],
            required_horizon_end_ts=103)
        contexts.append(context)
    model.update(generation=generation,contexts=contexts)
    model['signature']=stable_hash('conservative-shadow-research-model',{k:v for k,v in model.items() if k!='signature'})
    shadow=build_conservative_shadow_report(root,expected_generation=generation,baseline_report=baseline,
        policy_candidates=candidates,policy_artifact_receipt=artifact,research_model=model)
    assert shadow['complete_replay_count']==2,shadow['reason_counts']
    scorecard=build_discovery_scorecard_publication(root,expected_generation=generation,evaluator_status=status,
        baseline_report=baseline,shadow_terminal_report=shadow,dynamic_feature_names=['regime'],dynamic_protocol=PROTOCOL)
    assert baseline['same_opportunity_count']==1 and baseline['directional_episode_count']==2
    assert scorecard['input_counts'].get('shadow_terminal_rows_added')==2,scorecard
    assert scorecard['dynamic_cohorts']['groups'],scorecard['dynamic_cohorts']['rejections']
    for group in scorecard['dynamic_cohorts']['groups']:
        mapping=build_local_dynamic_mapping(scorecard['dynamic_cohorts'],group_id=group['group_id'],expected_generation=generation,protocol=PROTOCOL)
        written=write_local_dynamic_input(**args,rows=mapping['training_episodes'],mapping_payload=mapping)
        holdout=produce_local_holdout(**args,input_sha256=written['input_sha256'])
        assert holdout['excluded_counts']['EXACT_SOURCE_LIFECYCLE_IDENTITY_MISSING']==1
        assert holdout['rows']==[] and holdout['qualification_allowed'] is False
    envelope=scorecard['dynamic_cohorts']
    for kind in ('metadata','inner','rehashed_groups','rehashed_generation'):
        changed=copy.deepcopy(envelope)
        if kind=='metadata': changed['status']='tampered'
        elif kind=='inner': changed['adapter_payload']['counts']['supported_outcomes']=999
        elif kind=='rehashed_groups': changed['groups']=[]
        else: changed['expected_generation']={}
        if kind.startswith('rehashed'):
            changed['publication_sha256']=hashlib.sha256(canonical_json({k:v for k,v in changed.items() if k!='publication_sha256'}).encode()).hexdigest()
        with pytest.raises(ValueError,match='PUBLICATION'):
            build_local_dynamic_mapping(changed,group_id=group['group_id'],expected_generation=generation,protocol=PROTOCOL)


@pytest.mark.parametrize('defect',['bool','string','duplicate','missing_side'])
def test_directional_count_contract_rejects_invalid_or_incomplete_pairs(tmp_path,defect):
    from test_discovery_scorecard_publication import inputs,GENERATION
    from research.discovery_scorecard_publication import build_discovery_scorecard_publication
    root,status,baseline=inputs(tmp_path,rows=[])
    baseline.update(same_opportunity_count=1,directional_episode_count=2,
        independent_sample_basis='SOURCE_OPPORTUNITY_NOT_DIRECTIONAL_VARIANTS',
        episode_receipts=[{'opportunity_id':'o','source_episode_id':'e','episode_id':side,
            'direction':side,'directional_coverage':'BOTH_SIDES_CAPTURED'} for side in ('LONG','SHORT')])
    if defect=='bool': baseline['directional_episode_count']=True
    if defect=='string': baseline['same_opportunity_count']='1'
    if defect=='duplicate': baseline['episode_receipts'][1]=dict(baseline['episode_receipts'][0])
    if defect=='missing_side':
        baseline['episode_receipts'].pop(); baseline['directional_episode_count']=1
    result=build_discovery_scorecard_publication(root,expected_generation=GENERATION,evaluator_status=status,baseline_report=baseline)
    assert result['blockers']==['BASELINE_ROW_COUNT_MISMATCH']
