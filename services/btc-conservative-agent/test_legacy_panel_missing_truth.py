import ast
from pathlib import Path
import subprocess

SOURCE = (Path(__file__).parent/'research/research_dashboard.py').read_text(encoding='utf-8-sig')


def test_payloads_distinguish_missing_from_explicit_empty():
    names = {'_legacy_list_report_available','_spread_performance_payload','_ladder_sim_payload'}
    nodes = [n for n in ast.parse(SOURCE).body if isinstance(n,ast.FunctionDef) and n.name in names]
    report = {}
    env = {'_read_report':lambda *a:report, '_combo_row_known':lambda row:True,
           '_current_generation_identity':lambda:{}, '_integrity_payload':lambda:{},
           '_nonqualifying_scope':lambda *a:{'qualification_eligible':False}}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'research_dashboard.py','exec'),env)
    for name in ('_spread_performance_payload','_ladder_sim_payload'):
        result=env[name]()
        assert result['source_available'] is False
        assert 'UNAVAILABLE' in result['empty_reason']
    report={'top':[], 'profiles':[], 'actual_trades':0, 'replays_matched_executed':0, 'raw_replays_available':0}
    spread=env['_spread_performance_payload']()
    ladder=env['_ladder_sim_payload']()
    assert spread['source_available'] is True and spread['total_combos']==0
    assert ladder['source_available'] is True and ladder['actual_trades']==0


def test_real_loaders_display_unavailable_and_keep_measured_zero():
    spread='async function loadSpreadPerf() {'+SOURCE.split('async function loadSpreadPerf() {',1)[1].split('\nfunction exitEvidenceScope',1)[0]
    ladder='async function loadLadderSim() {'+SOURCE.split('async function loadLadderSim() {',1)[1].split('\nasync function loadPathwayAudit',1)[0]
    js='''
const assert=require('assert'); const els={};let payload={source_available:false};
const document={getElementById:id=>els[id]||(els[id]={style:{},innerHTML:'',textContent:''})};
const fetch=async()=>({json:async()=>payload});
const fmtExecutionUsd=x=>x??'UNAVAILABLE', fmtPct=x=>x??'UNAVAILABLE';
'''+spread+ladder+'''
(async()=>{
await loadSpreadPerf();await loadLadderSim();
assert(els['spread-perf-kpis'].innerHTML.includes('UNAVAILABLE'));
assert(els['ladder-sim-kpis'].innerHTML.includes('UNAVAILABLE'));
payload={source_available:true,buckets:[],total_combos:0,profiles:[],actual_trades:0,replays_matched_executed:0,raw_replays_available:0};
await loadSpreadPerf();await loadLadderSim();
assert(els['spread-perf-kpis'].innerHTML.includes('>0<'));
assert(els['ladder-sim-kpis'].innerHTML.includes('>0<'));
assert(!els['spread-perf-body'].innerHTML.includes('current cohort'));
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run(['node','-e',js],check=True,capture_output=True,text=True)
