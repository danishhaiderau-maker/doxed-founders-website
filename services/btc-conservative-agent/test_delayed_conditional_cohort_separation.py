import pytest
from research.entry_baseline_replay import delayed_variant_cohorts


@pytest.mark.parametrize('delayed', [[], [{'baseline_id': 'delayed-only'}]])
def test_delayed_cohort_cannot_inherit_undelayed_conditional_results(delayed):
    report = {'episode_receipts': [{
        'episode_id': 'e', 'results': [{'baseline_id': 'ordinary-undelayed'}],
        'conditional_results': [{'baseline_id': 'conditional-undelayed'}],
        'delayed_variants': [{'timing_model_sha256': 'a'*64, 'results': [],
                              'conditional_results': delayed}]}]}
    cohort = delayed_variant_cohorts(report)['a'*64]
    assert cohort['episode_receipts'][0]['conditional_results'] == delayed
    assert cohort['episode_receipts'][0]['results'] == []
    assert report['episode_receipts'][0]['conditional_results'][0]['baseline_id'] == 'conditional-undelayed'


def test_invalid_conditional_variant_shape_rejected():
    with pytest.raises(ValueError, match='DELAYED_VARIANT_SHAPE_INVALID'):
        delayed_variant_cohorts({'episode_receipts': [{'delayed_variants': [
            {'timing_model_sha256': 'a'*64, 'results': [], 'conditional_results': 'invalid'}]}]})


def test_cohort_counts_and_hash_are_rebound_to_selected_variants():
    from research_v3_contract import canonical_hash
    episodes = []
    for direction in ['LONG', 'SHORT']:
        episodes.append({'episode_id': direction, 'opportunity_id': 'same',
            'results': [], 'conditional_results': [], 'delayed_variants': [
                {'timing_model_sha256': 'b'*64, 'results': [], 'conditional_results': [
                    {'baseline_id': 'b', 'outcome_state': 'FULL_FILL' if direction == 'LONG' else 'UNKNOWN'}]}]})
    source = {'baseline_ids': ['b'], 'report_id': 'old', 'episode_receipts': episodes,
              'summaries': {'b': {'full_fills': 999}}, 'conditional_summaries': {}}
    cohort = delayed_variant_cohorts(source)['b'*64]
    assert cohort['same_opportunity_count'] == 1
    assert cohort['directional_episode_count'] == 2
    assert cohort['summaries']['b']['full_fills'] == 0
    summary = cohort['conditional_summaries']['b']
    assert summary['opportunities'] == 1
    assert summary['directional_evaluations'] == 2
    assert summary['full_fills'] == summary['unknown'] == 1
    assert summary['qualification_eligible'] is False
    assert cohort['report_id'] == canonical_hash('entry-baseline-replay',
        {key: value for key, value in cohort.items() if key != 'report_id'})
    assert source['report_id'] == 'old' and source['summaries']['b']['full_fills'] == 999
