"""Execute the real scope renderer against missing/stale/current receipts."""
import ast
from pathlib import Path
import subprocess

SOURCE = (Path(__file__).parent / 'research' / 'research_dashboard.py').read_text(encoding='utf-8-sig')


def test_scope_renderer_requires_affirmative_source_and_freshness():
    js = 'function policyGridEvidenceScope(d) {' + SOURCE.split('function policyGridEvidenceScope(d) {', 1)[1].split('\nasync function loadCombos()', 1)[0]
    subprocess.run(['node', '-e', "const assert=require('assert');\n" + js + '''
assert.equal(policyGridEvidenceScope({})[0], 'SOURCE UNAVAILABLE');
assert.equal(policyGridEvidenceScope({source_available:false})[0], 'SOURCE UNAVAILABLE');
const base={source_available:true,policy_grid:{source_available:true,epoch_id:'epoch-new'}};
assert.equal(policyGridEvidenceScope(base)[0], 'SAVED POLICY GRID — NOT VERIFIED CURRENT');
base.policy_grid.generation_freshness={current:false,stale:true};
assert.equal(policyGridEvidenceScope(base)[0], 'SAVED POLICY GRID — NOT VERIFIED CURRENT');
base.policy_grid.generation_freshness={current:true,stale:false};
assert.equal(policyGridEvidenceScope(base)[0], 'CURRENT V3.1 POLICY GRID + LEGACY EXECUTED — SEPARATED');
assert(policyGridEvidenceScope(base)[1].includes('does not establish qualification'));
base.policy_grid.epoch_id='UNBOUND';
assert.equal(policyGridEvidenceScope(base)[0], 'SAVED POLICY GRID — NOT VERIFIED CURRENT');
base.policy_grid.epoch_id='epoch-new';base.policy_grid.generation_freshness.stale=true;
assert.equal(policyGridEvidenceScope(base)[0], 'SAVED POLICY GRID — NOT VERIFIED CURRENT');
'''], check=True, capture_output=True, text=True)


def test_navigation_and_refresh_never_start_with_current_claim():
    assert "combos: ['POLICY GRID FRESHNESS UNVERIFIED'" in SOURCE
    loader = SOURCE.split('async function loadCombos() {', 1)[1].split('\nasync function loadSpreadPerf()', 1)[0]
    assert loader.index("setEvidenceScope('combos', ...EVIDENCE_SCOPES.combos)") < loader.index('await fetch')
    assert "setEvidenceScope('combos', ...policyGridEvidenceScope(d))" in loader


def test_policy_payload_exposes_actual_source_freshness_on_both_paths():
    fn = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.FunctionDef) and n.name == '_current_policy_grid_rows')
    returns = [n.value for n in ast.walk(fn) if isinstance(n,ast.Return) and isinstance(n.value,ast.Dict)]
    assert len(returns) == 2
    for result in returns:
        fields = {k.value: v for k,v in zip(result.keys,result.values) if isinstance(k,ast.Constant)}
        assert ast.unparse(fields['generation_freshness']) == "source.get('generation_freshness') or {}"


def test_exit_scope_missing_stale_and_current():
    js = 'function exitEvidenceScope(payload) {' + SOURCE.split('function exitEvidenceScope(payload) {', 1)[1].split('\nfunction executionPanelSource(', 1)[0]
    subprocess.run(['node', '-e', "const assert=require('assert');\n" + js + '''
assert.equal(exitEvidenceScope({source_available:false,empty_reason:'ATOMIC_GENERATION_UNAVAILABLE'})[0], 'SOURCE UNAVAILABLE');
const p={source_available:true,generation_identity:{epoch_id:'epoch-new'}};
assert.equal(exitEvidenceScope(p)[0], 'SAVED EXIT EVIDENCE — NOT VERIFIED CURRENT');
p.generation_freshness={current:true,stale:false};
assert.equal(exitEvidenceScope(p)[0], 'CURRENT EXECUTED PAPER + SHADOW/LAB — SEPARATED');
p.generation_freshness.current=false;
assert.equal(exitEvidenceScope(p)[0], 'SAVED EXIT EVIDENCE — NOT VERIFIED CURRENT');
p.generation_freshness.current=true;p.generation_identity.epoch_id='UNBOUND';
assert.equal(exitEvidenceScope(p)[0], 'SAVED EXIT EVIDENCE — NOT VERIFIED CURRENT');
'''], check=True, capture_output=True, text=True)
    for section, loader in [('exit-combos','loadExitCombos'),('exit-reason-leak','loadExitReasonLeak')]:
        assert f"'{section}': ['EXIT EVIDENCE FRESHNESS UNVERIFIED'" in SOURCE
        body = SOURCE.split(f'async function {loader}() {{',1)[1]
        assert body.index(f"setEvidenceScope('{section}'") < body.index('await fetch')
