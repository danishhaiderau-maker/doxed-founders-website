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
    # Current concrete blocker: publication mistakes independent opportunities
    # for directional receipt count. Do not rewrite either honest denominator.
    assert baseline['same_opportunity_count']==1 and baseline['directional_episode_count']==2
    assert scorecard['blockers']==['BASELINE_ROW_COUNT_MISMATCH']
    assert scorecard['input_counts']['declared_baseline_episode_receipts']==1
    assert scorecard['input_counts']['valid_baseline_episode_receipts']==2
