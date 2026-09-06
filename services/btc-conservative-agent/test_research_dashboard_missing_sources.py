"""Missing reports must never masquerade as measured empty research."""
import json
import shutil
import subprocess

import pytest

from research import research_dashboard as dashboard


def render_loader(name, payload, ok=True):
    page = dashboard.DASHBOARD_HTML
    helper = page[page.index('function missingResearchSource()'):page.index('async function loadFindings()')]
    start = page.index('async function ' + name + '()')
    end = page.index('async function ', start + 15)
    script = helper + page[start:end] + """
const elements = {};
const document = {getElementById: id => elements[id] ||= {innerHTML:'', textContent:''}};
const chaseLaneQuery = () => '';
const fmtExecutionUsd = x => String(x), fmtPct = x => String(x), fmtAdxBucket = x => String(x);
""" + f"const fetch = async () => ({{ok:{json.dumps(ok)}, json:async () => ({json.dumps(payload)})}});\n"
    script += f"{name}().then(() => console.log(JSON.stringify(elements)));"
    result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('loader,payload,target', [
    ('loadFindings', {'source_available': False}, 'findings-list'),
    ('loadFeatures', {'source_available': False}, 'feat-body'),
    ('loadRegime', {}, 'regime-body'),
    ('loadChase', {'source_available': {'totals':False,'executed':False,'shadow':False}}, 'chase-body'),
    ('loadCombos', {'source_available':False,'legacy_executed_combos':{'source_available':False}}, 'policy-grid-body'),
])
def test_unavailable_is_unknown_not_measured_zero(loader, payload, target):
    elements = render_loader(loader, payload)
    assert 'SOURCE UNAVAILABLE' in elements[target]['innerHTML']
    assert 'do not start a duplicate' in elements[target]['innerHTML']
    if loader == 'loadCombos':
        assert 'No profitable' not in elements[target]['innerHTML']
        assert elements['policy-grid-kpis']['innerHTML'] == ''
    if loader == 'loadChase':
        assert 'UNAVAILABLE' in elements['chase-kpis']['innerHTML']
        assert '0/0' not in elements['chase-kpis']['innerHTML']


@pytest.mark.parametrize('loader,payload,target,expected', [
    ('loadFindings', {'source_available':True,'findings':[]}, 'findings-list', 'no findings'),
    ('loadFindings', {'source_available':True,'findings':['Saved historical evidence']}, 'findings-list', 'Saved historical evidence'),
    ('loadFeatures', {'source_available':True,'features':[]}, 'feat-body', 'no eligible feature'),
    ('loadFeatures', {'source_available':True,'features':[{'feature':'ADX','n':4}]}, 'feat-body', 'ADX'),
    ('loadRegime', {'regimes':[]}, 'regime-body', 'no regime cells'),
    ('loadChase', {'source_available':{'totals':True,'executed':True,'shadow':True},'totals':{'total_fills':0}}, 'chase-kpis', '0/0'),
    ('loadCombos', {'source_available':True}, 'policy-grid-body', 'No profitable conservative'),
])
def test_saved_empty_and_populated_evidence_remains_visible(loader, payload, target, expected):
    assert expected in render_loader(loader, payload)[target]['innerHTML']


def test_stale_policy_source_preserves_warning_and_saved_rows():
    elements = render_loader('loadCombos', {
        'source_available': True,
        'policy_grid': {'warning': 'STALE SAVED GENERATION', 'policy_rows': [
            {'policy_id': 'saved-policy', 'policy_family': 'saved-family'}]},
    })
    assert elements['policy-grid-note']['textContent'] == 'STALE SAVED GENERATION'
    assert 'saved-policy' in elements['policy-grid-body']['innerHTML']


def test_partial_chase_sources_keep_measured_totals_but_not_missing_shadow():
    elements = render_loader('loadChase', {
        'source_available': {'totals': True, 'executed': True, 'shadow': False},
        'totals': {'total_fills': 7, 'chase_assisted_fills': 2},
        'executed_buckets': [{'bucket': '1', 'trades': 7}],
    })
    assert '2/7' in elements['chase-kpis']['innerHTML']
    assert '<td>7</td>' in elements['chase-body']['innerHTML']
    assert 'SOURCE UNAVAILABLE' in elements['chase-shadow-body']['innerHTML']


@pytest.mark.parametrize('present', [False, True])
def test_api_source_flags_do_not_depend_on_nonempty_result_rows(monkeypatch, present):
    monkeypatch.setattr(dashboard, '_API_RESPONSE_CACHE', {})
    report = {'generated_at':'saved', 'features':[], 'key_findings':[]} if present else {}
    monkeypatch.setattr(dashboard, '_read_json', lambda *args, **kwargs: report)
    monkeypatch.setattr(dashboard, '_read_report', lambda *args, **kwargs: report)
    monkeypatch.setattr(dashboard, '_integrity_payload', lambda: {})
    monkeypatch.setattr(dashboard, '_current_policy_grid_rows', lambda **kwargs: {})
    monkeypatch.setattr(dashboard, '_safe_policy_v3_dashboard_source', lambda: {'report': report})
    client = dashboard.app.test_client()
    for path in ('/api/findings', '/api/features', '/api/combos'):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json['source_available'] is present
    chase = client.get('/api/chase').json
    assert chase['source_available'] == dict.fromkeys(('totals','executed','shadow'), present)
    if not present:
        assert 'empty_reason' not in client.get('/api/features').json
