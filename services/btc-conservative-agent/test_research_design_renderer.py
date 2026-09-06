import shutil
import subprocess

import pytest
from research import research_dashboard as dashboard


@pytest.mark.parametrize('mode', ['success', 'http', 'json'])
def test_actual_research_design_renderer_resolves_all_response_paths(mode):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    function = 'async function loadResearchDesign()' + dashboard.DASHBOARD_HTML.split(
        'async function loadResearchDesign()', 1)[1].split('async function loadEvidenceCoverage()', 1)[0]
    script = """
const assert=require('assert'); const elements={};
global.document={getElementById:id=>elements[id]||(elements[id]={style:{}})};
global.fetch=async()=>({ok:MODE!=='http',status:503,json:async()=>{
 if(MODE==='json') throw new Error('invalid JSON');
 return {status:'CURRENT',entry_baselines:[{baseline_id:'<unsafe>',required_evidence:[]}],
 regime_feature_coverage:{row_count:3,dimensions:[{name:'ATR',observed_rows:2,unknown_rows:1,status:'PARTIAL'}]}};
}});
""".replace('MODE', repr(mode)) + function + """
loadResearchDesign().then(()=>{
 assert(elements['research-design-kpis'].innerHTML.includes('DISABLED'));
 assert(elements['research-design-kpis'].innerHTML.includes('NOT CALCULATED'));
 assert(!elements['research-design-banner'].textContent.includes('cards is not defined'));
 if(MODE==='success') {
  assert(elements['research-baseline-body'].innerHTML.includes('&lt;unsafe&gt;'));
  assert(elements['research-regime-coverage-body'].innerHTML.includes('ATR'));
 } else {
  assert(elements['research-design-banner'].textContent.includes('RESEARCH_DESIGN_LOAD_FAILED'));
  assert(elements['research-baseline-body'].innerHTML.includes('UNKNOWN'));
  assert(elements['research-regime-coverage-body'].innerHTML.includes('UNKNOWN'));
 }
}).catch(e=>{console.error(e);process.exit(1)});
""".replace('MODE', repr(mode))
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
