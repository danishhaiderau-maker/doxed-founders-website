import assert from 'node:assert/strict';
import test from 'node:test';
import {
  CONSERVATIVE_BTC_LIVE_RELAY_POLICY,
  hasActiveLiveRelayConsent,
  shouldDryRunIntentMirror,
} from '../relay-execution-policy';

const consent = {
  relayExecutionMode: 'LIVE',
  relayPolicyVersion: CONSERVATIVE_BTC_LIVE_RELAY_POLICY,
  realTradingConfirmedAt: '2026-07-18T00:00:00.000Z',
};

test('relay is dry-run without explicit user consent', () => {
  assert.equal(shouldDryRunIntentMirror(undefined, {}), true);
  assert.equal(shouldDryRunIntentMirror('0', {}), true);
});

test('current consent enables execution when ops has not forced dry-run', () => {
  assert.equal(hasActiveLiveRelayConsent(consent), true);
  assert.equal(shouldDryRunIntentMirror(undefined, consent), false);
  assert.equal(shouldDryRunIntentMirror('0', consent), false);
});

test('ops dry-run override always wins', () => {
  assert.equal(shouldDryRunIntentMirror('1', consent), true);
  assert.equal(shouldDryRunIntentMirror('true', consent), true);
});

test('stale policy or paused state fails closed', () => {
  assert.equal(
    shouldDryRunIntentMirror(undefined, { ...consent, relayPolicyVersion: 'old' }),
    true,
  );
  assert.equal(
    shouldDryRunIntentMirror(undefined, { ...consent, relayExecutionMode: 'PAUSED' }),
    true,
  );
});
