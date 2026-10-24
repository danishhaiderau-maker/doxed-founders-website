import { test } from 'node:test';
import assert from 'node:assert/strict';
import { liveFencesExclusion, withFenceExclusion } from './live-fences-exclusion';

const NOW = Date.parse('2026-10-24T09:00:00Z');
const base = (): any => ({ status: 'PAUSED', relayArmedAt: null, expiresAt: new Date('2026-10-01T00:00:00Z'),
  relayExchangeMirror: { ok: false, error: 'credentials-missing' } });

test('acct-cmqdk8n9 excluded only when all four criteria hold', () => {
  const fe = liveFencesExclusion('acct-cmqdk8n9', base(), NOW)!;
  assert.equal(fe.excluded, true); assert.equal(fe.read_ok, true);
});
test('other accounts never get exclusion evidence', () => {
  assert.equal(liveFencesExclusion('acct-aaaaaaaa', base(), NOW), null);
});
const flips: [string, (r: any) => void][] = [
  ['hire_expired', (r) => { r.expiresAt = new Date(NOW + 86400000); }],
  ['hire_expired (no expiry)', (r) => { r.expiresAt = null; }],
  ['no_credentials', (r) => { r.relayExchangeMirror = { ok: true, live_fences: { ok: true } }; }],
  ['paused', (r) => { r.status = 'ACTIVE'; }],
  ['cannot_arm', (r) => { r.relayArmedAt = '2026-10-24T08:00:00Z'; }],
];
for (const [n, flip] of flips) {
  test(`flip ${n} => not excluded (UNVERIFIED)`, () => {
    const r = base(); flip(r);
    assert.equal(liveFencesExclusion('acct-cmqdk8n9', r, NOW)!.excluded, false);
  });
}
test('unreadable read => read_ok false, not excluded', () => {
  const r = base(); r.expiresAt = 'garbage';
  const fe = liveFencesExclusion('acct-cmqdk8n9', r, NOW)!;
  assert.equal(fe.read_ok, false); assert.equal(fe.excluded, false);
});
test('withFenceExclusion logs each exclusion with reason and DB values', () => {
  const calls: string[] = [];
  const m = withFenceExclusion({ warn: (s) => calls.push(s) }, 'acct-cmqdk8n9', base(), { ok: false, error: 'credentials-missing' }, NOW)!;
  assert.equal((m.fence_exclusion as any).excluded, true);
  assert.match(calls[0], /HIRE_EXPIRED\+NO_CREDENTIALS\+PAUSED\+CANNOT_ARM/);
  assert.match(calls[0], /"status":"PAUSED"/);
});
