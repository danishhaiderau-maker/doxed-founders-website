import json
import shutil
import subprocess
import pytest
from research import research_dashboard as dashboard

@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(dashboard, '_API_RESPONSE_CACHE', {})


@pytest.mark.parametrize('report', [{}, {'schema':'current_generation_report_unavailable_v1','report_unavailable':True}])
def test_missing_grid_endpoint_and_renderer(monkeypatch, report):
    monkeypatch.setattr(dashboard,'_safe_policy_v3_dashboard_source',lambda:dict(report=report,screen={},blockers=['CURRENT_POLICY_REPORT_MISSING']))
    monkeypatch.setattr(dashboard,'_read_report',lambda *args,**kwargs:{})
    payload=dashboard.app.test_client().get('/api/combos').get_json()
    assert payload['source_available'] is False and payload['total_combos'] is None
    grid=payload['policy_grid']
    assert grid['source_available'] is False and grid['status']=='SOURCE_UNAVAILABLE'
    assert grid['rows_available'] is None and grid['policy_search_statistics'] is None
    node=shutil.which('node')
    if not node: pytest.skip('Node unavailable')
    fn='async function loadCombos('+dashboard.DASHBOARD_HTML.split('async function loadCombos(',1)[1].split('async function loadSpreadPerf',1)[0]
    script="const assert=require('assert');const elements={};global.document={getElementById:id=>elements[id]||(elements[id]={})};function missingResearchSource(){return 'SOURCE UNAVAILABLE';}global.fetch=async()=>({json:async()=>("+json.dumps(payload)+")});"+fn+"""
loadCombos().then(()=>{
 assert.equal(elements['policy-grid-kpis'].innerHTML,'');
 assert.equal(elements['policy-grid-note'].textContent,'SOURCE UNAVAILABLE');
 assert(!elements['policy-grid-body'].innerHTML.includes('No profitable'));
 assert(!elements['diagnostic-policy-grid-body'].innerHTML.includes('No positive'));
 assert(elements['policy-grid-body'].innerHTML.includes('SOURCE UNAVAILABLE'));
}).catch(error=>{console.error(error);process.exit(1)});
"""
    result=subprocess.run([node,'-e',script],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr


def test_real_empty_report_preserves_measured_zero(monkeypatch):
    monkeypatch.setattr(dashboard,'_safe_policy_v3_dashboard_source',lambda:dict(report={'schema':'safe_policy_genome_v3'},screen={},blockers=[],epoch_id='epoch',qualified=False))
    monkeypatch.setattr(dashboard,'_read_report',lambda *args,**kwargs:{})
    payload=dashboard.app.test_client().get('/api/combos').get_json()
    assert payload['source_available'] is True and payload['total_combos']==0
    assert payload['policy_grid']['policy_search_statistics']['profitable_conservative_rows_displayed']==0
