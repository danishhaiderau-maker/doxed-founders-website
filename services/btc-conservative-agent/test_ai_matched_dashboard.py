import ast
import json
from pathlib import Path
import subprocess

SOURCE = Path('research/research_dashboard.py').read_text(encoding='utf-8-sig')


def test_actual_payload_uses_atomic_report_and_rejects_stale():
    fn=next(n for n in ast.parse(SOURCE).body if isinstance(n,ast.FunctionDef) and n.name=='_ai_payload')
    current=[True]
    comparison={'status':'DESCRIPTIVE_ONLY','groups':[{'supported_rows':1}]}
    calls=[]
    def loader(name):
        calls.append(name)
        return {'generation':{'source_revision':'s','analyzer_revision':'a','epoch_id':'e'},
                'ai_verdict_coverage':{'matched_selection_comparison':comparison}}, {'manifest':{
                    'source_data_revision':'s','generation_revision':'a','fresh_epoch':{'epoch_id':'e'}}}
    ns=dict(_read_json=lambda *a:{}, _spread_performance_payload=lambda:{},
        _declared_atomic_generation_report=loader,
        _generation_freshness_meta=lambda manifest:{'current':current[0]})
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-dashboard','exec'),ns)
    loaded=ns['_ai_payload']()['matched_selection_comparison']
    assert loaded['groups']==comparison['groups']
    assert loaded['binding_diagnostic']=='CURRENT_GENERATION_ONLY_NO_CHECKSUM_BINDING'
    current[0]=False
    blocked=ns['_ai_payload']()['matched_selection_comparison']
    assert blocked['status']=='UNKNOWN' and not blocked['groups']
    assert calls==['discovery_cohort_scorecard_report.json']*4
    current[0]=True
    ns['_declared_atomic_generation_report']=lambda name:(None, {'manifest':{'id':'current'}})
    unavailable=ns['_ai_payload']()['matched_selection_comparison']
    assert unavailable['status']=='UNKNOWN' and unavailable['groups']==[]
    assert unavailable['blockers']==['MATCHED_AI_REPORT_UNAVAILABLE']
    counter=[0]
    def racing(name):
        report,binding=loader(name)
        counter[0]+=1
        binding['manifest']['generation_revision']=str(counter[0])
        return report,binding
    ns['_declared_atomic_generation_report']=racing
    assert ns['_ai_payload']()['matched_selection_comparison']['blockers']==['MATCHED_AI_GENERATION_BINDING_FAILED']


def test_actual_renderer_populated_unknown_and_safe_text():
    js=SOURCE.split('function renderMatchedAI(c) {',1)[1].split('\nasync function loadAI()',1)[0]
    js='function renderMatchedAI(c) {'+js
    fixture={'status':'DESCRIPTIVE_ONLY','independent_episode_n':3,'matched_rows':4,'excluded_rows':2,
        'groups':[{'dimensions':{'policy_id':'<script>bad</script>','evidence_world':'CONSERVATIVE_BBO'},
        'independent_episode_n':3,'supported_rows':4,'rejected_positive_outcomes':1,
        'rejected_negative_outcomes':2,'filter_minus_unfiltered_usd':-1.25}]}
    code="""
const assert=require('assert');
function node(){return {children:[],textContent:'',appendChild(x){this.children.push(x)},replaceChildren(){this.children=[]}}}
const elements={'ai-matched-status':node(),'ai-matched-body':node()};
const document={getElementById:id=>elements[id],createElement:node};
"""+js+'\nrenderMatchedAI('+json.dumps(fixture)+");\n"+"""
assert(elements['ai-matched-status'].textContent.includes('excluded 2'));
assert.equal(elements['ai-matched-body'].children[0].children[4].textContent,'-1.2500');
assert(elements['ai-matched-body'].children[0].children[0].textContent.includes('<script>bad</script>'));
renderMatchedAI({status:'UNKNOWN',groups:[]});
assert(elements['ai-matched-body'].children[0].children[0].textContent.includes('UNKNOWN'));
"""
    subprocess.run(['node','-e',code],check=True,capture_output=True,text=True)
