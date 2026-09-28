"""Send reviewed retirement modules in memory; inspect by default, no deploy."""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def remote_command(sources, revision, old, state, current, execute=False):
    if not re.fullmatch('[0-9a-f]{40}', revision or '') or not all(re.fullmatch('[0-9a-f]{64}', v or '') for v in (old, state, current)):
        raise ValueError('EXACT_IDENTITIES_REQUIRED')
    names = ('data_sync_bundle_storage', 'data_sync_bundle_retirement', 'fly_bundle_retire')
    if set(sources) != set(names) or any(not isinstance(v, str) or len(v.encode()) > 64 * 1024 for v in sources.values()):
        raise ValueError('REVIEWED_SOURCE_SET_INVALID')
    args = ['fly_bundle_retire', '--expected-revision', revision, '--old-generation', old,
        '--state-sha', state, '--current-generation', current] + (['--execute'] if execute else [])
    blob = base64.b64encode(gzip.compress(json.dumps({'sources': sources, 'args': args}).encode(), mtime=0)).decode()
    bootstrap = "import sys,types,base64,gzip,json;sys.path.insert(0,'/app');p=json.loads(gzip.decompress(base64.b64decode('" + blob + "')));sys.argv=p['args'];"
    # Load the storage and retirement helpers under their reviewed module names;
    # no file on the running collector is overwritten.
    for name in names[:-1]:
        bootstrap += "m=types.ModuleType('" + name + "');sys.modules['" + name + "']=m;exec(compile(p['sources']['" + name + "'],'<reviewed>','exec'),m.__dict__);"
    bootstrap += "exec(compile(p['sources']['fly_bundle_retire'],'<reviewed-retirement>','exec'),{'__name__':'__main__'});"
    if len(bootstrap.encode()) > 24 * 1024:
        raise ValueError('REMOTE_COMMAND_LIMIT')
    return 'python -c "' + bootstrap + '"'


def main():
    repo = Path(__file__).resolve().parents[1]
    service = repo / 'services/btc-conservative-agent'
    sources = {name: (service / (name + '.py')).read_text() for name in ('data_sync_bundle_storage', 'data_sync_bundle_retirement')}
    sources['fly_bundle_retire'] = Path(__file__).with_name('fly_bundle_retire.py').read_text()
    revision, old, state, current = [os.getenv(k, '') for k in ('RETIRE_EXPECTED_REVISION', 'RETIRE_OLD_GENERATION', 'RETIRE_STATE_SHA', 'RETIRE_CURRENT_GENERATION')]
    mode = os.getenv('RETIRE_EXECUTE', '0')
    if mode not in {'0', '1'}: raise ValueError('EXPLICIT_MODE_REQUIRED')
    command = remote_command(sources, revision, old, state, current, mode == '1')
    listing = subprocess.run(['flyctl', 'machines', 'list', '--app', 'doxed-btc-bot', '--json'], capture_output=True, check=True, timeout=30)
    if len(listing.stdout) > 256 * 1024: raise ValueError('MACHINE_LIST_LIMIT')
    rows = json.loads(listing.stdout)
    active = [v for v in rows if v.get('state') not in {'stopped', 'destroyed'}]
    if len(active) != 1 or active[0].get('state') != 'started' or not re.fullmatch('[0-9a-f]{10,32}', active[0].get('id', '')):
        raise ValueError('ONE_STARTED_OWNER_REQUIRED')
    print(json.dumps({'reviewed_sources': {k: hashlib.sha256(v.encode()).hexdigest() for k,v in sources.items()}, 'execute': mode == '1'}), flush=True)
    result = subprocess.run(['flyctl', 'machine', 'exec', '--app', 'doxed-btc-bot', '--json', '--timeout', '180', active[0]['id'], command], capture_output=True, timeout=190)
    if result.returncode or len(result.stdout) > 65536: raise ValueError('REMOTE_RESULT_UNAVAILABLE_INSPECT_BEFORE_RETRY')
    value = json.loads(result.stdout)
    if isinstance(value, dict) and 'stdout' in value:
        if value.get('exit_code', 0) != 0: raise ValueError('REMOTE_FAILED')
        value = json.loads(value['stdout'])
    if (value.get('schema') != 'fly_bundle_retirement_operation_v1' or value.get('runtime_revision') != revision
            or value.get('old_generation') != old or value.get('current_generation') != current or value.get('state_sha256') != state
            or value.get('status') != ('COMPLETE' if mode == '1' else 'INSPECTED')
            or value.get('raw_source_deleted') is not False or value.get('retirement_performed') is not (mode == '1')):
        raise ValueError('REMOTE_TERMINAL_RECEIPT_INVALID')
    print(json.dumps(value, sort_keys=True))


if __name__ == '__main__': main()
