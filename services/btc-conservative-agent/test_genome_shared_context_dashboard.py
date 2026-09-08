import json
from pathlib import Path


def test_actual_genome_route_coverage_truth(tmp_path, monkeypatch):
    monkeypatch.delenv('BTC_AGENT_DATA_DIR', raising=False)
    from research import research_dashboard as d
    monkeypatch.setattr(d, 'ROOT', tmp_path)
    monkeypatch.setattr(d, 'DATA_ROOT', tmp_path)
    freshness = {'current': True, 'generation_epoch_id': 'epoch-a'}
    monkeypatch.setattr(d, '_generation_freshness_meta', lambda: freshness)
    report = {'schema': 'safe_policy_genome_v3', 'epoch_id': 'epoch-a',
              'shared_context_coverage': {'schema': 'shared_context_coverage_v1',
                'epoch_id': 'epoch-a', 'eligible_lanes': 100,
                'cohort_evaluated_lanes': 70, 'cohort_bound_lanes': 60,
                'cohort_pending_lanes': 30, 'page_lanes': 10, 'bound_lanes': 4,
                'cohort_evaluation_complete': False, 'truncated': True,
                'lanes': ['PRIVATE_DETAIL'] * 1000}}
    path = tmp_path / d.SAFE_POLICY_GENOME_V3_REPORT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    def get():
        path.write_text(json.dumps(report), encoding='utf-8')
        response = d.app.test_client().get('/api/genome')
        assert response.status_code == 200
        return response.get_json()['shared_context_coverage']
    value = get()
    assert value['cohort_bound_lanes'] == 60 and value['bound_lanes'] == 4
    assert value['page_scope'] == 'CURRENT_PAGE_ONLY'
    assert value['qualification_authority'] is False and 'lanes' not in value
    for bad in (None, -1, True, '12', 1.5):
        report['shared_context_coverage']['cohort_bound_lanes'] = bad
        assert get()['cohort_bound_lanes'] is None
    report['shared_context_coverage']['cohort_evaluation_complete'] = 'true'
    assert get()['cohort_evaluation_complete'] is None
    freshness['current'] = False
    assert 'eligible_lanes' not in get()
    freshness['current'] = True
    freshness['generation_epoch_id'] = 'other'
    assert get()['status'] == 'EPOCH_IDENTITY_UNAVAILABLE_OR_MISMATCH'
    freshness['generation_epoch_id'] = 'epoch-a'
    report['shared_context_coverage']['epoch_id'] = 'other'
    assert 'cohort_bound_lanes' not in get()
    del report['shared_context_coverage']
    assert get()['status'] == 'UNAVAILABLE_OR_STALE'


def test_renderer_uses_textcontent_and_separate_scopes():
    source = (Path(__file__).parent / 'research/research_dashboard.py').read_text(encoding='utf-8')
    fragment = source.split('const coverage = d.shared_context_coverage', 1)[1].split("if (d.collector_generation", 1)[0]
    assert '.textContent =' in fragment and 'innerHTML' not in fragment
    assert 'Cumulative cohort' in fragment and 'Current page only' in fragment
    assert "'UNKNOWN'" in fragment and 'not a completed trade' in fragment
    assert 'Number.isSafeInteger' in fragment
