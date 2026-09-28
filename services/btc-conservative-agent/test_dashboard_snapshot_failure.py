"""Execute the actual dashboard refresh JavaScript against failure responses."""
import json
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('case,expected', [
    ('warming', 'SNAPSHOT WARMING'),
    ('http', 'SNAPSHOT API HTTP 500'),
    ('network', 'SNAPSHOT NETWORK FAILURE'),
    ('timeout', 'SNAPSHOT TIMEOUT'),
    ('parse', 'SNAPSHOT RESPONSE INVALID'),
    ('shape', 'SNAPSHOT RESPONSE INVALID'),
    ('api', 'SNAPSHOT API FAILURE'),
    ('render', 'DASHBOARD RENDER FAILURE'),
])
def test_actual_refresh_distinguishes_snapshot_failures(case, expected):
    source = Path(__file__).with_name('bot.py').read_text(encoding='utf-8')
    start = source.index('    let refreshInFlight = false;')
    end = source.index("    document.addEventListener('DOMContentLoaded'", start)
    script = source[start:end]
    harness = r'''
const elements = {};
const document = {getElementById: id => elements[id] ||= {style:{}}};
const formatMelbourneNow = () => 'now';
const safeText = () => {throw new Error('render defect');};
let calls = 0;
const fetch = async () => {
  calls++;
  if (testCase === 'network') throw new TypeError('network');
  if (testCase === 'timeout') {const e = new Error('timeout'); e.name='AbortError'; throw e;}
  return {
    ok: !['warming','http'].includes(testCase),
    status: testCase === 'warming' ? 503 : testCase === 'http' ? 500 : 200,
    headers: {get: () => testCase === 'warming' ? 'warming' : null},
    json: async () => {
      if (testCase === 'parse') throw new SyntaxError('bad JSON');
      if (testCase === 'shape') return null;
      if (testCase === 'api') return {api_state_error:'internal failure'};
      return {};
    }
  };
};
'''
    tail = r'''
(async () => {
  await refresh();
  console.log(JSON.stringify({elements, calls, refreshInFlight}));
})();
'''
    result = subprocess.run(['node', '-'], input='const testCase=' + json.dumps(case) + ';\n' + harness + script + tail,
                            capture_output=True, text=True, encoding='utf-8', timeout=10, check=True)
    receipt = json.loads(result.stdout)
    assert receipt['calls'] == 1
    assert receipt['refreshInFlight'] is False
    assert receipt['elements']['refreshStatus']['innerText'].startswith(expected)
    banner = receipt['elements']['serverBanner']['textContent']
    assert expected in banner
    assert 'does not establish that the bot is offline' in banner
    assert 'start_bot' not in banner and '15minu_bot' not in banner
    assert 'current snapshot unavailable' in receipt['elements']['dataSource']['textContent']
