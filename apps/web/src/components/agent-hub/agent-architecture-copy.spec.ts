import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (name: string) => readFileSync(new URL(name, import.meta.url), 'utf8');

test('Agent Hub describes Fly as sole strategy owner and desktop as mirror/analyzer only', () => {
  const control = read('./agent-admin-showcase-control.tsx');
  const analyzer = read('./agent-analyzer-panel.tsx');
  const api = read('../../lib/api.ts');

  assert.match(control, /Fly\.io is the sole AI, strategy, and trading owner/);
  assert.match(control, /Analyzer mirror/);
  assert.match(control, /Fly uploaded research mirror/);
  assert.match(control, /path: '\/cmd\/start-mirror'/);
  assert.match(control, /path: '\/cmd\/reset-mirror'/);
  assert.doesNotMatch(control, /Optional local control :7810/);
  assert.doesNotMatch(control, /Desktop Fly proxy/);
  assert.match(analyzer, /Fly\.io is the sole AI, strategy, and trading owner/);
  assert.doesNotMatch(control, /Cloudflare|bot\.doxxedcrypto\.digital/);
  assert.doesNotMatch(api, /trading-agents\/conservative-btc\/fly-control/);

  for (const stale of [
    'Home PC command center',
    'signed webhooks + Railway snapshots',
    'the separate Fly.io bot is not the canonical showcase',
  ]) {
    assert.equal(control.includes(stale) || analyzer.includes(stale), false, stale);
  }
});

test('stale Fly evidence is rendered as degraded rather than online', () => {
  const status = read('./agent-public-status.tsx');
  const page = read('../../app/agent-hub/[slug]/page.client.tsx');

  assert.match(status, /degraded:/);
  assert.match(page, /status: 'degraded'/);
  assert.match(page, /showing last verified state/);
});
