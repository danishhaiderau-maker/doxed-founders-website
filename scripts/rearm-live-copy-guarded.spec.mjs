import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

test('guarded rearm stamps the same live relay policy required by execution', () => {
  const policySource = readFileSync(
    join(root, 'packages/utils/src/relay-execution-policy.ts'),
    'utf8',
  );
  const rearmSource = readFileSync(join(root, 'scripts/rearm-live-copy-guarded.mjs'), 'utf8');
  const policy = policySource.match(/CONSERVATIVE_BTC_LIVE_RELAY_POLICY\s*=\s*'([^']+)'/)?.[1];
  const rearmPolicy = rearmSource.match(/CONSERVATIVE_BTC_LIVE_RELAY_POLICY\s*=\s*'([^']+)'/)?.[1];
  assert.ok(policy);
  assert.equal(rearmPolicy, policy);
  assert.match(rearmSource, /relayPolicyVersion:\s*CONSERVATIVE_BTC_LIVE_RELAY_POLICY/);
});
