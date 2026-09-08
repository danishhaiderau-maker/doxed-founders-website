import pytest
from test_dynamic_cohort_adapter import row,adapt,GENERATION


@pytest.mark.parametrize('fault',['direction','missing','paper','hash'])
def test_malformed_counterfactual_projection_never_becomes_paper(fault):
    identity={'epoch_id':GENERATION['epoch_id'],'source_episode_id':'episode-one',
        'opportunity_id':'opportunity-one','policy_id':'ENTRY_PLUS_EXIT_A','policy_signature':'a'*64,
        'direction':'LONG','seal_request_id':'b'*64}
    source=row(counterfactual_identity=identity,replay_proof_sha256='c'*64)
    if fault=='direction': identity['direction']='SHORT'
    elif fault=='missing': identity.pop('opportunity_id')
    elif fault=='paper': source['source_lifecycle_identity']={'collection_epoch_id':GENERATION['epoch_id'],
        'episode_id':'episode-one','policy_signature':'a'*64,'research_lane':'paper'}
    else: source['replay_proof_sha256']='bad'
    result=adapt([source])
    assert result['rejections']['COUNTERFACTUAL_IDENTITY_INVALID']==1
    assert result['counts']['supported_outcomes']==0
