import test from 'node:test';
import assert from 'node:assert/strict';
import {
  protectionSweepDecision, protectiveStopsFor, validateTerminalCloseAuthority, PROTECTION_SWEEP_GRACE_MS,
} from './signal-subscriber-execution.service';

const now = 1_000_000;
test('1497: flat or inside the 3 s entry-ack grace -> no action', () => {
  assert.equal(protectionSweepDecision({ stopIds: [], positionAmount: 0, nowMs: now }).action, 'NONE');
  assert.equal(protectionSweepDecision({ stopIds: [], positionAmount: 0.0003, entryAckAtMs: now - PROTECTION_SWEEP_GRACE_MS + 1, nowMs: now }).action, 'GRACE');
});
test('1497: zero stops -> emergency stop; one -> OK', () => {
  assert.equal(protectionSweepDecision({ stopIds: [], positionAmount: 0.0003, entryAckAtMs: now - 4000, nowMs: now }).action, 'EMERGENCY_STOP');
  assert.equal(protectionSweepDecision({ stopIds: [5], positionAmount: -0.0003, nowMs: now }).action, 'OK');
});
test('1497: two stops -> keep confirmed working one, cancel the newer', () => {
  const d = protectionSweepDecision({ stopIds: [10, 12], positionAmount: 0.0003, keepId: 10, nowMs: now });
  assert.equal(d.action, 'CANCEL_EXTRAS');
  assert.equal(d.keepId, 10);
  assert.deepEqual(d.cancelIds, [12]);
  const d2 = protectionSweepDecision({ stopIds: [10, 12, 11], positionAmount: 0.0003, keepId: 99, nowMs: now });
  assert.equal(d2.keepId, 10);
  assert.deepEqual(d2.cancelIds, [12, 11]);
});
test('1497: only reduce-only closing-side stops count', () => {
  const o = (id: number, amount: number, flags: number, orderType = 'STOP') =>
    ({ id, amount, flags, orderType, symbol: 'tBTCF0:USTF0' } as never);
  const st = protectiveStopsFor([o(1, -0.0003, 1024), o(2, 0.0003, 1024), o(3, -0.0003, 0), o(4, -0.0003, 1024, 'LIMIT')], 'LONG');
  assert.deepEqual(st.map((x: { id: number }) => x.id), [1]);
});
test('1497: sweeper flatten authority requires 0 stops + failed emergency stop', () => {
  const a = (ev: Record<string, unknown>) => ({ kind: 'PROTECTION_SWEEPER_EMERGENCY' as const, canonicalTradeId: 'dsl-1-1', lifecycleGeneration: 'g', evidence: ev });
  assert.equal(validateTerminalCloseAuthority(a({ schema: 'protection_sweeper_v1', stop_count: 0, emergency_stop_failed: true, position_side: 'LONG' }), now), true);
  assert.equal(validateTerminalCloseAuthority(a({ schema: 'protection_sweeper_v1', stop_count: 1, emergency_stop_failed: true, position_side: 'LONG' }), now), false);
  assert.equal(validateTerminalCloseAuthority(a({ schema: 'protection_sweeper_v1', stop_count: 0, emergency_stop_failed: false, position_side: 'LONG' }), now), false);
});
