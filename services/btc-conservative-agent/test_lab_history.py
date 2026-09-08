import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

from flask import Flask, jsonify, request
import pytest
from research import lab_history as history

SECRET = b'fixture-not-production'
CURRENT = {'epoch': 'epoch-one', 'reset_generation': 0, 'policies': {'CONTINUOUS': 'policy-one'}}


def row(**kw):
    return dict(schema='shadow_lane_outcome_v1', epoch_id='epoch-one', collection_epoch_id='epoch-one',
        collection_mode='LAB', research_lane='CONTINUOUS', policy_version='policy-one',
        study_id='study', direction='LONG', net_pnl_usd=0.005, filled=True, **kw)


def write(path, rows):
    path.write_bytes(b''.join(json.dumps(value).encode()+b'\n' for value in rows))


def read(path, **kw):
    return history.read_history(path, identity=lambda: CURRENT, secret=SECRET, **kw)


def test_bounded_pages_do_not_claim_totals_or_deduplication(tmp_path):
    path = tmp_path/'rows.jsonl'
    write(path, [row() for _ in range(122)])
    first = read(path)
    second = read(path, cursor=first['next_cursor'])
    assert len(first['rows']) == 100 and len(second['rows']) == 22
    assert first['coverage']['bytes_read'] <= history.MAX_BYTES
    assert not first['coverage']['end_of_pinned_file']
    assert second['coverage']['end_of_pinned_file']
    assert not second['coverage']['whole_file_in_this_page']
    assert first['cohort_totals'] is second['cohort_totals'] is None
    assert first['coverage']['duplicate_reconciliation_performed'] is False
    assert first['qualification_allowed'] is False


def test_filter_epoch_policy_mode_and_never_dump_raw_ai_payload(tmp_path):
    path = tmp_path/'rows.jsonl'
    base = row()
    write(path, [base, {**base, 'epoch_id':'other'}, {**base, 'epoch_id':None},
        {**base, 'policy_version':'old'}, {**base, 'collection_mode':'SHADOW'},
        {**base, 'research_lane':[]}, {**base, 'ai_snapshot':{'private':'not for browser'}}])
    result = read(path, lane='CONTINUOUS')
    assert len(result['rows']) == 2
    assert result['coverage']['excluded'] == {'EPOCH_MISSING_OR_DIFFERENT':2,
        'POLICY_MISSING_OR_DIFFERENT':1, 'NOT_LAB':1, 'LANE_INVALID':1}
    assert 'ai_snapshot' not in result['rows'][1]


def test_only_original_bounded_ai_fields_are_projected(tmp_path):
    path = tmp_path/'rows.jsonl'
    write(path, [{**row(), 'shared_ai_call_id':'scan-a', 'prompt_id':'prompt-a',
        'ai_snapshot':{'decision':'REJECT', 'direction':'SHORT', 'long_score':0,
                       'short_score':77, 'model_id':'model-a', 'reasoning':'private-not-exposed'}}, row()])
    present, absent = read(path)['rows']
    assert present['original_ai']['decision'] == 'REJECT'
    assert present['original_ai']['direction'] == 'SHORT'
    assert present['original_ai']['long_score'] == 0
    assert present['original_ai']['short_score'] == 77
    assert present['original_ai']['model_id'] == 'model-a'
    assert present['shared_ai_call_id'] == 'scan-a'
    assert 'reasoning' not in present['original_ai']
    assert all(value is None for value in absent['original_ai'].values())


def test_malformed_and_incomplete_tail_explicit(tmp_path):
    path = tmp_path/'rows.jsonl'
    path.write_bytes(json.dumps(row()).encode()+b'\nnot json\n{"partial":')
    result = read(path)
    assert len(result['rows']) == 1
    assert result['coverage']['excluded'] == {'MALFORMED_ROW':1}
    assert result['coverage']['incomplete_tail'] is True
    assert result['next_cursor'] is None


@pytest.mark.parametrize('change', ['append', 'replace', 'reset', 'epoch', 'policy', 'tamper'])
def test_cursor_fails_closed_across_source_identity_changes(tmp_path, change):
    path = tmp_path/'rows.jsonl'; write(path, [row()]*101)
    cursor = read(path)['next_cursor']
    current = {**CURRENT}
    if change == 'append':
        with path.open('ab') as stream: stream.write(b'{}\n')
    elif change == 'replace':
        replacement = tmp_path/'replacement'; replacement.write_bytes(path.read_bytes()); os.replace(replacement,path)
    elif change == 'reset': current['reset_generation'] = 1
    elif change == 'epoch': current['epoch'] = 'epoch-two'
    elif change == 'policy': current['policies'] = {'CONTINUOUS':'new'}
    else: cursor += 'x'
    with pytest.raises(history.HistoryUnavailable, match='CURSOR_'):
        history.read_history(path, cursor=cursor, identity=lambda:current, secret=SECRET)


def test_large_row_bounded_read_and_mid_read_identity_change(tmp_path, monkeypatch):
    path = tmp_path/'rows.jsonl'; path.write_bytes(b'x'*(history.MAX_BYTES*2))
    with pytest.raises(history.HistoryUnavailable, match='BYTE_BUDGET'):
        read(path)
    write(path, [row()])
    states = iter([CURRENT, {**CURRENT, 'reset_generation':1}])
    with pytest.raises(history.HistoryUnavailable, match='SOURCE_GENERATION_CHANGED'):
        history.read_history(path, identity=lambda:next(states), secret=SECRET)


def route_app(tmp_path):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    app = Flask('actual-lab-history-route')
    names = {'api_lab_history', '_lab_history_current_identity'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    registrations = [n for n in tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'add_url_rule'
        and n.value.args and isinstance(n.value.args[0], ast.Constant)
        and n.value.args[0].value == '/api/research/lab-history']
    assert len(registrations) == 1
    env = dict(app=app, jsonify=jsonify, request=request, os=os,
        _admin_authed_strict=lambda: request.headers.get('X-Test-Owner') == 'yes',
        _research_write_gate=threading.RLock(), _fresh_collection_lock=threading.Lock(),
        _research_report_reset_generation=0, _data_sync_runtime_root=lambda:tmp_path,
        _data_sync_identity_cache_lock=threading.Lock(), _data_sync_identity_epoch_cache={'collection_epoch_id':'epoch-one'},
        _collector_v22_epoch_id=lambda: pytest.fail('request must not infer an epoch'),
        COMBO_LANE_SPECS={}, RESEARCH_LANE_CONTINUOUS='CONTINUOUS',
        SHADOW_LANE_OUTCOME_FILE=str(tmp_path/'rows.jsonl'), _lab_history_cursor_secret=SECRET)
    exec(compile(ast.Module(body=nodes+registrations, type_ignores=[]), 'bot.py', 'exec'), env)
    return app, env


def test_actual_flask_route_authorization_rows_reset_and_no_mutation(tmp_path):
    app, env = route_app(tmp_path)
    path = tmp_path/'rows.jsonl'
    write(path, [{**row(), 'policy_version':'continuous_shared_direction_gap_v1'}])
    original = path.read_bytes()
    client = app.test_client()
    assert client.get('/api/research/lab-history').status_code == 401
    response = client.get('/api/research/lab-history?lane=CONTINUOUS', headers={'X-Test-Owner':'yes'})
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.json['rows'][0]['study_id'] == 'study'
    assert client.get('/api/research/lab-history?lane=INVALID', headers={'X-Test-Owner':'yes'}).status_code == 409
    marker = tmp_path/'research_reset_receipts'; marker.mkdir(); (marker/'ACTIVE_RESET.json').write_text('{}')
    response = client.get('/api/research/lab-history', headers={'X-Test-Owner':'yes'})
    assert response.status_code == 409 and response.json['reason_code'] == 'RESEARCH_RESET_ACTIVE'
    assert path.read_bytes() == original


def test_actual_route_missing_explicit_epoch_never_synthesizes_one(tmp_path):
    app, env = route_app(tmp_path)
    env['_data_sync_identity_epoch_cache']['collection_epoch_id'] = None
    response = app.test_client().get('/api/research/lab-history', headers={'X-Test-Owner':'yes'})
    assert response.status_code == 409
    assert response.json['reason_code'] == 'CURRENT_EPOCH_UNAVAILABLE'
    assert list(tmp_path.iterdir()) == []


def test_actual_renderer_uses_text_nodes_for_hostile_row_values():
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    script = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'DASHBOARD_JS' for t in n.targets))
    renderer = script[script.index('    function renderLabHistoryPage('):script.index('    async function loadLabHistory(')]
    node = shutil.which('node')
    assert node, 'Node required for executable UI escaping test'
    harness = r'''
let labHistoryCursor = null;
function element() { return { children: [], options: [], replaceChildren(){this.children=[];},
 appendChild(child){this.children.push(child);}, set innerHTML(_){throw Error('unsafe HTML');} }; }
const elements = {labHistoryRows: element(), labHistoryStatus:element(), labHistoryLane:element()};
const document = {getElementById:id=>elements[id], createElement:element};
'''+renderer+r'''
const attack = '<img src=x onerror=alert(1)>';
renderLabHistoryPage({epoch_id:attack, rows:[{study_id:attack, research_lane:attack, policy_version:attack,
 exit_reason:attack}], coverage:{returned_rows:1,records_scanned:1,bytes_read:1,excluded:{}},available_lanes:[attack]});
if(elements.labHistoryRows.children[0].children[1].textContent !== attack) throw Error('text not preserved');
if(elements.labHistoryLane.children[0].textContent !== attack) throw Error('unsafe option');
if(!elements.labHistoryStatus.textContent.includes('page only')) throw Error('coverage missing');
'''
    completed = subprocess.run([node, '-e', harness], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr


def test_actual_loader_pagination_errors_and_single_flight():
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    script = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'DASHBOARD_JS' for t in n.targets))
    loader = script[script.index('    async function loadLabHistory('):script.index('    function displayExitCause(')]
    node = shutil.which('node')
    assert node, 'Node required for executable loader test'
    harness = r'''
let labHistoryCursor = 'signed-page-two', labHistoryBusy = false;
let resolveFetch, calls = [], rendered = 0;
const elements = Object.fromEntries(['labHistoryLoad','labHistoryNext','labHistoryLane','labHistoryRows','labHistoryStatus']
  .map(id => [id, {disabled:false, value:'CONTINUOUS', cleared:false, replaceChildren(){this.cleared=true;}}]));
const document = {getElementById:id=>elements[id]};
let fetch = (url, options) => {calls.push({url, options}); return new Promise(resolve=>{resolveFetch=resolve;});};
function renderLabHistoryPage(page) {rendered++; labHistoryCursor=page.next_cursor;}
'''+loader+r'''
(async()=>{
  const pending = loadLabHistory(true);
  if(!elements.labHistoryLoad.disabled || !elements.labHistoryLane.disabled) throw Error('controls not locked');
  await loadLabHistory(true);
  if(calls.length!==1) throw Error('duplicate request');
  if(!calls[0].url.includes('cursor=signed-page-two')) throw Error('cursor omitted');
  if(calls[0].options.credentials!=='same-origin' || calls[0].options.cache!=='no-store') throw Error('unsafe fetch');
  resolveFetch({ok:true,json:async()=>({status:'AVAILABLE',next_cursor:null})}); await pending;
  if(rendered!==1 || elements.labHistoryLoad.disabled || !elements.labHistoryNext.disabled) throw Error('terminal controls');
  labHistoryCursor='stale';
  const failing=loadLabHistory(false);
  if(calls[1].url.includes('cursor=')) throw Error('reload retained cursor');
  resolveFetch({ok:false,json:async()=>({reason_code:'CURSOR_GENERATION_CHANGED'})}); await failing;
  if(labHistoryCursor!==null || !elements.labHistoryRows.cleared || !elements.labHistoryNext.disabled) throw Error('stale rows retained');
  if(!elements.labHistoryStatus.textContent.includes('CURSOR_GENERATION_CHANGED')) throw Error('failure hidden');
  if(elements.labHistoryLane.disabled || elements.labHistoryLoad.disabled || labHistoryBusy) throw Error('retry locked');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    completed = subprocess.run([node, '-e', harness], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
