"""Execute the real dashboard reset JS with fake DOM/HTTP; never touch real data."""
import ast
import json
from pathlib import Path
import shutil
import subprocess
import unittest


NODE_HARNESS = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
let raw = ''; process.stdin.on('data', c => raw += c);
process.stdin.on('end', async () => {
  const {code, scenario} = JSON.parse(raw);
  const id = 'abcdef0123456789abcdef0123456789';
  const nodes = {};
  const calls = [];
  let writes = 0, polls = 0;
  const requestBodies = [];
  const complete = {
    operation_id:id, request_id:id, protocol:'local_research_reset_protocol_v1',
    scope_version:'laptop_research_scope_v1', status:'COMPLETE',
    fly_mutation_requested:false, remote_http_writes:0,
    deletion_reconciled:true, exact_hash_reconciliation:true,
    completion_receipt_path:'fixture/completion.json', completion_receipt_sha256:'a'.repeat(64),
    sync_state:'BLOCKED_PENDING_VERIFIED_IMPORT', deleted_file_count:4, deleted_bytes:1024,
    retained_file_count:2, retained_bytes:128, retained_inventory_sha256:'b'.repeat(64),
  };
  const context = {
    document:{getElementById: key => nodes[key] ||= {innerText:'', disabled:false}},
    confirm: () => scenario !== 'cancel',
    crypto:{randomUUID: () => id}, AbortSignal:{timeout: () => null},
    setTimeout: callback => callback(),
    fetch:async (url, options) => {
      calls.push({url, options});
      assert.ok(url.startsWith('http://127.0.0.1:7810/api/local-research-reset/v1/'));
      assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error');
      assert.equal(options.headers['X-Local-Reset-Capability'], 'k'.repeat(32));
      let body;
      if (scenario === 'auth') return {ok:false, status:401, json:async()=>({})};
      if (url.endsWith('/capability')) {
        body={protocol:'local_research_reset_protocol_v1', scope_version:'laptop_research_scope_v1',
          scope:'LAPTOP_RESEARCH_ONLY',fly_mutation_supported:false,current_local_generation:'fixture-epoch',reset_enabled:true};
        if (scenario === 'wrong_scope') body.scope='FLY';
        if (scenario === 'disabled') body.reset_enabled=false;
      } else if (url.endsWith('/requests')) {
        writes++; assert.equal(options.method,'POST');
        requestBodies.push(options.body);
        const request=JSON.parse(options.body);
        assert.equal(request.confirmation,'DELETE LAPTOP RESEARCH ONLY');
        assert.equal(request.expected_local_generation,'fixture-epoch');
        assert.equal(request.request_id,id);
        if (scenario === 'lost_post') throw new Error('sensitive request error');
        body={status:'QUEUED',operation_id:id,request_id:id};
      } else {
        polls++; body={...complete};
        if (scenario === 'bad_proof') body.exact_hash_reconciliation=false;
        if (scenario === 'bad_retained') delete body.retained_inventory_sha256;
        if (scenario === 'wrong_operation') body.operation_id='0'.repeat(32);
        if (scenario === 'remote_write') body.remote_http_writes=1;
        if (scenario === 'blocked') body.status='BLOCKED';
        if (['blocked_error','retry_preflight','unsafe_retry'].includes(scenario) && writes === 1) {
          Object.assign(body,{status:'BLOCKED',error:'LOCAL_RESET_ACTIVE_OWNER'});
          if (scenario !== 'blocked_error') Object.assign(body,{
            retryable_preflight:true,mutation_started:false,fence_persisted:false,
            deleted_file_count:0,deleted_bytes:0});
          if (scenario === 'unsafe_retry') body.fence_persisted=true;
        }
        if (scenario === 'partial') body.status='PARTIAL';
        if (scenario === 'queued' && polls === 1) body.status='QUEUED';
      }
      return {ok:true,status:200,json:async()=>body};
    },
  };
  vm.createContext(context);
  vm.runInContext('let freshCollectionInFlight=false;\n'+code,context);
  vm.runInContext("localResetCapabilityPrompt=async()=> 'k'.repeat(32)",context);
  await context.toggleFreshCollection();
  let label=nodes.freshCollectionLabel?.innerText;
  if (scenario==='lost_post') {
    assert.equal(label,'NOT VERIFIED'); await context.toggleFreshCollection();
    assert.equal(writes,1); label=nodes.freshCollectionLabel.innerText;
  }
  if (['retry_preflight','unsafe_retry','blocked_error'].includes(scenario)) {
    assert.equal(label,'BLOCKED'); assert.equal(writes,1);
    await context.toggleFreshCollection(); label=nodes.freshCollectionLabel.innerText;
    if (scenario==='retry_preflight') {
      assert.equal(writes,2); assert.equal(requestBodies[0],requestBodies[1]);
      assert.equal(label,'COMPLETE');
    } else { assert.equal(writes,1); assert.equal(label,'BLOCKED'); }
  }
  if (['complete','queued','lost_post','retry_preflight'].includes(scenario)) assert.equal(label,'COMPLETE');
  else if (scenario==='cancel') assert.equal(writes,0);
  else if (scenario==='disabled') { assert.equal(writes,0); assert.equal(label,'UNAVAILABLE'); }
  else if (['auth','wrong_scope'].includes(scenario)) { assert.equal(writes,0); assert.equal(label,'NOT VERIFIED'); }
  else if (['blocked','partial','blocked_error','unsafe_retry'].includes(scenario)) assert.equal(label,scenario==='partial'?'PARTIAL':'BLOCKED');
  else assert.equal(label,'NOT VERIFIED');
  assert.equal(nodes.freshCollectionBtn.disabled,false);
  assert.ok(!JSON.stringify(nodes).includes('sensitive request error'));
  console.log(JSON.stringify({scenario,writes,polls,label}));
}).on('error', error=>{console.error(error);process.exitCode=1;});
"""


class LocalResetDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
        cls.html = next(ast.literal_eval(n.value) for n in tree.body
                        if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'HTML' for t in n.targets))
        cls.javascript = next(ast.literal_eval(n.value) for n in tree.body
                        if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'DASHBOARD_JS' for t in n.targets))
        start = cls.javascript.index('// Laptop-only controller contract.')
        end = cls.javascript.index('let wipeFlyOnlyInFlight', start)
        cls.code = cls.javascript[start:end]

    def test_scope_and_secret_rendering_contract(self):
        self.assertIn('Fresh Collection — Laptop Only', self.html)
        self.assertIn('type="password" autocomplete="off"', self.html)
        self.assertNotIn('/api/fresh_epoch_reset', self.code)
        self.assertNotIn('localStorage', self.code)
        self.assertNotIn('sessionStorage', self.code)
        refresh = self.javascript[self.javascript.index('async function refresh('):]
        self.assertNotIn("freshLabel.innerText", refresh)

    @unittest.skipUnless(shutil.which('node'), 'Node needed for real JS execution')
    def test_real_js_contract_with_mock_http(self):
        for scenario in ('complete','queued','lost_post','cancel','auth','wrong_scope',
                         'bad_proof','bad_retained','wrong_operation','remote_write','blocked','partial',
                         'blocked_error','retry_preflight','unsafe_retry','disabled'):
            with self.subTest(scenario=scenario):
                result = subprocess.run(['node','--unhandled-rejections=strict','-e',NODE_HARNESS],
                    input=json.dumps({'code':self.code,'scenario':scenario}), text=True,
                    capture_output=True, timeout=15)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__ == '__main__':
    unittest.main()
