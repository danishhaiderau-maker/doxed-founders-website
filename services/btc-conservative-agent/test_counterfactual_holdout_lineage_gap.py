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
    assert not result.get('qualification_eligible',False)
