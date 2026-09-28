import json
import shutil
import subprocess
import pytest
from research import research_dashboard as dashboard
from research_entry_baselines import ENTRY_BASELINE_REGISTRY


@pytest.mark.parametrize('present', [False, True])
def test_api_to_actual_loader_preserves_unknown_and_zero(monkeypatch, present):
    monkeypatch.setattr(dashboard,'_API_RESPONSE_CACHE',{})
    baseline=ENTRY_BASELINE_REGISTRY['baselines'][0]['baseline_id']
    summary=dict(opportunities=0,full_fills=0,partial_fills=0,no_fills=0,unknown=0)
    coverage=dict(schema='phase7_regime_feature_coverage_v1',row_count=0,dimensions=[dict(name='ADX',observed_rows=0,unknown_rows=None)])
    def report(name):
        if not present: return None, {'reason':'REPORT_NOT_IN_CURRENT_GENERATION'}
        value=dict(same_opportunity_count=0,summaries={baseline:summary}) if name=='entry_baseline_replay_report.json' else dict(conservative_evaluator=dict(regime_feature_coverage=coverage))
        return value, {'manifest':{'generation_id':'test'}}
    monkeypatch.setattr(dashboard,'_declared_atomic_generation_report',report)
    monkeypatch.setattr(dashboard,'_generation_freshness_meta',lambda *_:{'current':present})
    payload=dashboard.app.test_client().get('/api/research-design').get_json()
    assert len(payload['entry_baselines'])==11
    assert payload['entry_baseline_replay']['same_opportunity_count']==(0 if present else None)
    assert payload['regime_feature_coverage']['row_count']==(0 if present else None)
    assert payload['entry_baselines'][0]['replay_summary']['full_fills']==(0 if present else None)
    assert payload['entry_baselines'][1]['replay_summary']['full_fills'] is None
    node=shutil.which('node')
    if not node: pytest.skip('Node unavailable')
    fn='async function loadResearchDesign('+dashboard.DASHBOARD_HTML.split('async function loadResearchDesign(',1)[1].split('async function loadEvidenceCoverage',1)[0]
    js="const assert=require('assert'),nodes={};global.document={getElementById:id=>nodes[id]||(nodes[id]={style:{}})};global.fetch=async()=>({ok:true,json:async()=>("+json.dumps(payload)+")});"+fn+"""
loadResearchDesign().then(()=>{
const k=nodes['research-design-kpis'].innerHTML,rows=nodes['research-baseline-body'].innerHTML;
assert.equal((rows.match(/<tr>/g)||[]).length,11);
assert(rows.includes('N UNKNOWN · full UNKNOWN'));
assert(!nodes['research-design-banner'].textContent.includes('LOAD_FAILED'));
"""+f"assert(k.includes('Same-opportunity replay N</small><div>{'0' if present else 'UNKNOWN'}</div>'));"+"""
}).catch(e=>{console.error(e);process.exit(1)});
"""
    result=subprocess.run([node,'-e',js],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
