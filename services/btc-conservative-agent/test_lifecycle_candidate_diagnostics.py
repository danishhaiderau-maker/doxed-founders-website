from lifecycle_pipeline_runtime import _candidate_diagnostics
from test_lifecycle_pipeline_runtime import _runtime


def test_bounded_projection_does_not_copy_payload_or_paths():
    row = {'identity': {'episode_id': 'episode-1', 'secret': 'PRIVATE'},
           'stage': 'ENTRY_RESOLVED_NO_ORDER', 'blockers': ['SAFE_CODE'] * 20,
           'raw_record': {'secret': 'PRIVATE'}, 'path': '/private/path'}
    result = _candidate_diagnostics([row] * 100)
    assert len(result['candidates']) == 8 and result['truncated']
    assert len(result['candidates'][0]['blockers']) == 8
    assert 'PRIVATE' not in str(result) and '/private' not in str(result)
    assert set(result['candidates'][0]) == {'identity', 'stage', 'blockers'}
    row['identity']['episode_id'] = 'changed'
    row['blockers'].clear()
    assert result['candidates'][0]['identity']['episode_id'] == 'episode-1'
    assert len(result['candidates'][0]['blockers']) == 8


def test_oversized_or_untrusted_values_are_unavailable_not_false_ids():
    class Unsafe:
        def __str__(self):
            raise AssertionError('must not stringify')
    result = _candidate_diagnostics([{'identity': {'episode_id': 'x' * 161},
                                      'stage': Unsafe(), 'blockers': ['/path', Unsafe()]}])
    assert result['truncated']
    assert result['candidates'][0]['identity']['episode_id'] is None
    assert result['candidates'][0]['stage'] is None
    assert result['candidates'][0]['blockers'] == []


def test_real_success_summary_retains_candidate_identity(tmp_path):
    runtime = _runtime(tmp_path)
    runtime._record_success({'pipeline': {'results': [
        {'identity': {'episode_id': 'episode-1'}, 'stage': 'QUALIFICATION_INCOMPLETE',
         'blockers': ['ENTRY_SCHEDULE_NOT_TERMINAL'], 'raw_record': 'PRIVATE'}]}})
    summary = runtime.status()['last_result']
    assert summary['recent_candidate_diagnostics']['candidates'][0]['identity']['episode_id'] == 'episode-1'
    assert 'PRIVATE' not in str(summary)
