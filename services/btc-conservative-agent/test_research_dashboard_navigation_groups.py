import json
import shutil
import subprocess
from research import research_dashboard as dashboard
from test_research_dashboard_missing_sources import render_loader


def test_navigation_preserves_all_sections_and_remaps_saved_sections():
    expected = set('summary findings regime lanes ai chase chase-policy-lab chase-threshold chase-delay combos spread-perf exit-combos exit-reason-leak ladder-sim exits genome research-design evidence-coverage edge explorer archives download runtime-incidents pathway-audit horizon'.split())
    groups=dashboard.REPORT_NAV_GROUPS
    ids=[item[0] for _,_,items in groups for item in items]
    assert len(ids)==25 and set(ids)==expected
    for sid in ids:
        assert f'id="sec-{sid}"' in dashboard.DASHBOARD_HTML
    page=dashboard.DASHBOARD_HTML
    start=page.index('function sectionGroup(')
    end=page.index('\nfunction ',start+10)
    script='const NAV_GROUPS='+json.dumps([dict(id=gid,items=items) for gid,_,items in groups])+';'+page[start:end]
    script+='console.log(JSON.stringify('+json.dumps(ids)+'.map(sectionGroup)));'
    result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==[gid for gid,_,items in groups for _ in items]
    assert dict((sid,label) for _,_,items in groups for sid,label,_ in items)['ai']=='Historical AI Calibration'


def test_registry_count_does_not_claim_collection():
    result=render_loader('loadDecisionReadiness',{'source_available':False,
                      'deployed_policy_collection':{'policy_count':5}})
    cards=result['decision-readiness']['innerHTML']
    assert 'Registered policies (not collection proof)' in cards
    assert 'Deployed policies collecting' not in cards
    assert 'Registry identities (not collection or qualification proof)' in result['decision-readiness-provenance']['textContent']
