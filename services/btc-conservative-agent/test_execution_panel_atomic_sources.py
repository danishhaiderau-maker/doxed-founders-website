import json
import pytest
from research import research_dashboard as d


@pytest.mark.parametrize('endpoint,name', [('/api/chase-policy-lab','chase_policy_lab_report.json'),('/api/missed-opportunity-proof','missed_opportunity_proof_report.json'),('/api/chase-threshold','chase_threshold_report.json'),('/api/exit-combos','exit_combinations_report.json')])
@pytest.mark.parametrize('published',[False,True])
def test_current_execution_panels_refuse_loose_history(tmp_path,monkeypatch,endpoint,name,published):
    monkeypatch.setattr(d,'ROOT',tmp_path)
    monkeypatch.setattr(d,'DATA_ROOT',tmp_path)
    monkeypatch.setattr(d,'_API_RESPONSE_CACHE',{})
    (tmp_path/name).write_text(json.dumps({'all_schedule_count':118,'generated_at':'old'}))
    if published:
        root=tmp_path/d.PUBLISHED_REPORTS_DIR; root.mkdir()
        (root/name).write_text('{}')
        (root/d.REPORT_MANIFEST_FILE).write_text(json.dumps({'reports':[{'file':name}],
            'generation_revision':'revision','fresh_epoch':{'epoch_id':'epoch-new'}}))
    payload=d.app.test_client().get(endpoint).get_json()
    assert payload['source_available'] is published
    assert payload.get('all_schedule_count') != 118
    if published: assert payload['generation_identity']['epoch_id']=='epoch-new'
    else: assert 'UNAVAILABLE' in payload['empty_reason']


def test_ui_stops_before_default_counts_when_source_missing():
    page=d.DASHBOARD_HTML
    for section,loader in [('chase-threshold','loadChaseThreshold'),('exit-combos','loadExitCombos')]:
        start=page.index('async function '+loader+'()')
        end=page.index('\nasync function ',start+20)
        code=page[start:end]
        assert "if (!executionPanelSource('"+section in code
        assert 'return;' in code


@pytest.mark.parametrize('lab_present',[True,False])
def test_lab_and_proof_independent_loader(lab_present):
    import shutil,subprocess
    page=d.DASHBOARD_HTML
    start=page.index('async function loadChasePolicyLab()')
    end=page.index('\nasync function ',start+20)
    lab={'source_available':lab_present,'all_schedule_count':7,'generation_identity':{'epoch_id':'lab'}}
    proof={'source_available':not lab_present,'proof_count':9,'generation_identity':{'epoch_id':'proof'}}
    script='const elements={}; const document={getElementById:id=>elements[id]||=( {innerHTML:"",textContent:""})};'
    script+='const compressedScheduleQualification=x=>x,fmtPct=x=>x,fmtExecutionUsd=x=>x,missedProofTouchLabel=x=>x;'
    script+='const fetch=async url=>({json:async()=>url.includes("chase-policy-lab")?'+json.dumps(lab)+':'+json.dumps(proof)+'});'
    script+=page[start:end]+'loadChasePolicyLab().then(()=>console.log(JSON.stringify(elements)));'
    result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    elements=json.loads(result.stdout)
    lab_html=elements['chase-policy-kpis']['innerHTML']
    proof_html=elements['missed-proof-kpis']['innerHTML']
    if lab_present:
        assert '>7<' in lab_html and 'UNAVAILABLE' in proof_html
        assert 'lab' in elements['chase-policy-lab-note']['textContent']
    else:
        assert 'UNAVAILABLE' in lab_html and '>9<' in proof_html
        assert 'proof' in proof_html
