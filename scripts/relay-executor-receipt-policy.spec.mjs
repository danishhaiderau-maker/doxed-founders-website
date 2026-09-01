import assert from 'node:assert/strict';
import test from 'node:test';
import { relayExecutorReceiptAccepted } from './relay-executor-receipt-policy.mjs';

const base = { serviceRole: 'executor-worker', healthy: true, executionEnabled: true,
  timeoutCount: 0, ownerId: 'owner' };

test('accepts a fresh RUNNING executor receipt', () => {
  assert.equal(relayExecutorReceiptAccepted({ health: { ...base, status: 'RUNNING' }, fresh: true }), true);
});

test('accepts stale receipt only with explicit and independently proven quiescence', () => {
  const health = { ...base, status: 'IDLE', terminalState: 'QUIESCENT', paused: true, flatExposure: true };
  assert.equal(relayExecutorReceiptAccepted({ health, fresh: false, instancePaused: true, flatExposure: true }), true);
  assert.equal(relayExecutorReceiptAccepted({ health, fresh: false, instancePaused: true, flatExposure: false }), false);
  assert.equal(relayExecutorReceiptAccepted({ health: { ...health, status: 'STARTING' }, fresh: false, instancePaused: true, flatExposure: true }), false);
});
