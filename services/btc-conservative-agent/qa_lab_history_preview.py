"""Loopback-only SYNTHETIC visual QA. Never imports bot or contacts production.

Extracts committed HTML Simulation-history section, its actual JS renderer/load
functions and listener statements, and actual Flask route/identity/auth functions.
The reader module must byte-match the pinned commit. Only fixture data is served.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import types
import sys

from flask import Flask, jsonify, request, make_response

SOURCE = '2f8f1fc2e46068a9934f6350b247b61466f7d1d2'
BOT_PATH = 'services/btc-conservative-agent/bot.py'
READER_PATH = 'services/btc-conservative-agent/research/lab_history.py'


def committed(path):
    return subprocess.run(['git', 'show', SOURCE+':'+path], cwd=Path(__file__).parent,
                          capture_output=True, check=True).stdout


def create_preview(root):
    source = committed(BOT_PATH).decode('utf-8')
    expected_reader = committed(READER_PATH)
    accounting_path = 'services/btc-conservative-agent/research/lab_accounting.py'
    accounting_source = committed(accounting_path)
    accounting = types.ModuleType('research.lab_accounting')
    sys.modules['research.lab_accounting'] = accounting
    exec(compile(accounting_source, accounting_path, 'exec'), accounting.__dict__)
    reader = types.ModuleType('research.lab_history')
    reader.__file__ = READER_PATH
    sys.modules['research.lab_history'] = reader
    exec(compile(expected_reader, READER_PATH, 'exec'), reader.__dict__)
    tree = ast.parse(source)
    constants = {target.id: node.value.value for node in tree.body if isinstance(node, ast.Assign)
                 and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
                 for target in node.targets if isinstance(target, ast.Name)}
    html, js = constants['HTML'], constants['DASHBOARD_JS']
    section = html[html.index('<h2>Simulation history — LAB only</h2>'):html.index('<h2>AI History (Session)</h2>')]
    style = html[html.index('<style>'):html.index('</style>')+len('</style>')]
    functions = js[js.index('    let labHistoryCursor = null;'):js.index('    function displayExitCause(')]
    start = js.index("      document.getElementById('labHistoryLoad')?.addEventListener")
    listeners = js[start:js.index('      const dropdowns = {', start)]
    page = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    page += '<title>SYNTHETIC QA — LAB history</title>'+style+'</head><body>'
    page += '<div style="background:#ffdc60;color:#171717;padding:18px;font-weight:bold;position:sticky;top:0;z-index:99">'
    page += 'SYNTHETIC QA ONLY — NO PRODUCTION DATA OR TRADING CONTROLS</div>'
    page += '<p>Committed UI '+SOURCE[:7]+'. Fixture: 122 rows, 75 positive / 47 negative; simulated gross 1.48 USD (costs unmodeled). '
    page += 'These fixture totals are not production findings. Click Load, then Next. '
    page += 'Some original AI fields intentionally missing; hostile text must render literally.</p>'
    page += section+'<script>'+functions+"document.addEventListener('DOMContentLoaded', () => {"+listeners+'});</script></body></html>'
    root = Path(root)
    fixture = root/'shadow_lane_outcome.jsonl'
    rows = []
    for index in range(122):
        row = {'schema':'shadow_lane_outcome_v1', 'epoch_id':'epoch-SYNTHETIC-qa',
               'collection_epoch_id':'epoch-SYNTHETIC-qa', 'collection_mode':'LAB',
               'research_lane':'CONTINUOUS', 'policy_version':'continuous_shared_direction_gap_v1',
               'study_id':f'SYNTHETIC-study-{index+1:03}', 'direction':'LONG' if index%2 == 0 else 'SHORT',
               'ts':f'2026-09-09T00:{index//60:02}:{index%60:02}Z', 'filled':True,
               'entry_outcome':'FILLED', 'exit_reason':'PROFIT_LOCK_LADDER' if index<75 else 'STOP_LOSS',
               'fill_price':65000+index, 'net_pnl_usd':0.04 if index<75 else -0.14 if index==121 else -0.03,
               'shared_ai_call_id':f'SYNTHETIC-scan-{index+1}', 'prompt_id':'SYNTHETIC-prompt-v1'}
        if index%4:
            row['ai_snapshot']={'decision':'APPROVE' if index%3 else 'REJECT', 'direction':row['direction'],
                'long_score':65 if index%2 == 0 else 25, 'short_score':25 if index%2 == 0 else 65,
                'model_id':'SYNTHETIC-model'}
        rows.append(row)
    rows[0]['study_id'] = '<img src=x onerror=alert("SYNTHETIC")>'
    fixture.write_text(''.join(json.dumps(row)+'\n' for row in rows), encoding='utf-8')
    app = Flask('synthetic-lab-history-only')
    names = {'api_lab_history', '_lab_history_current_identity', '_admin_authed_strict'}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if len(nodes) != len(names):
        raise RuntimeError('PINNED_ROUTE_EXTRACTION_FAILED')
    registrations = [node for node in tree.body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'add_url_rule'
        and node.value.args and isinstance(node.value.args[0], ast.Constant)
        and node.value.args[0].value == '/api/research/lab-history']
    if len(registrations) != 1:
        raise RuntimeError('PINNED_ROUTE_REGISTRATION_MISSING')
    token = 'SYNTHETIC-PREVIEW-ONLY-NOT-A-PRODUCTION-CREDENTIAL'
    env = dict(app=app, jsonify=jsonify, request=request, os=os, _BOT_ADMIN_TOKEN=token,
        _is_local_operator=lambda _:False, _client_ip=lambda:request.remote_addr,
        _research_write_gate=threading.RLock(), _fresh_collection_lock=threading.Lock(),
        _research_report_reset_generation=0, _data_sync_runtime_root=lambda:root,
        _data_sync_identity_cache_lock=threading.Lock(),
        _data_sync_identity_epoch_cache={'collection_epoch_id':'epoch-SYNTHETIC-qa'},
        COMBO_LANE_SPECS={}, RESEARCH_LANE_CONTINUOUS='CONTINUOUS',
        SHADOW_LANE_OUTCOME_FILE=str(fixture), _lab_history_cursor_secret=os.urandom(32))
    exec(compile(ast.Module(body=nodes+registrations, type_ignores=[]), BOT_PATH, 'exec'), env)

    @app.before_request
    def loopback_only():
        if request.remote_addr not in ('127.0.0.1', '::1') or request.host.split(':')[0] not in ('127.0.0.1', 'localhost'):
            return 'Loopback synthetic QA only', 403

    @app.after_request
    def isolate(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        return response

    @app.get('/')
    def preview():
        response = make_response(page)
        response.set_cookie('bot_admin_token', token, httponly=True, samesite='Strict')
        return response
    return app, {'source_revision':SOURCE, 'bot_sha256':hashlib.sha256(source.encode()).hexdigest(),
                 'reader_sha256':hashlib.sha256(expected_reader).hexdigest(),
                 'accounting_sha256':hashlib.sha256(accounting_source).hexdigest(), 'fixture_rows':len(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=9017)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be unprivileged')
    with tempfile.TemporaryDirectory(prefix='lab-history-SYNTHETIC-') as temporary:
        app, receipt = create_preview(temporary)
        print(json.dumps({**receipt, 'url':f'http://127.0.0.1:{args.port}/', 'synthetic_only':True}), flush=True)
        app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
