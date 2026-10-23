import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  buildExchangeMirror,
  EXECUTOR_DECISION_EVENT,
  intentDecisionSourceId,
} from './signal-subscriber-execution.service';
import { buildExecutorDecisionReports, summarizeCopyStatus } from './live-copy-fly-reporter';
import { flyViewMirror, flyViewTransition } from './trading-agents.service';

const T0 = 1_760_000_000_000;

test('Start/Stop wakes the isolated worker (not the no-op local wakeNow)', () => {
  const src = readFileSync(join(__dirname, 'trading-agent-instances.service.ts'), 'utf8');
  assert.match(src, /requestExecutorWake\('USER_RESUME'\)/);
  assert.match(src, /requestExecutorWake\('USER_PAUSE'\)/);
  assert.doesNotMatch(src, /execution\.wakeNow\('USER_(RESUME|PAUSE)'\)/);
});

test('executor decisions become INTENT_* reports even without a participant', () => {
  const events = [
    { id: 'e1', participantId: null, eventType: EXECUTOR_DECISION_EVENT, createdAt: new Date(T0), platformReceivedAt: null,
      payload: { decision: 'RECEIVED', reason: null, account: 'acct-cmq6cfwv', decided_at: new Date(T0).toISOString() } },
    { id: 'e2', participantId: null, eventType: EXECUTOR_DECISION_EVENT, createdAt: new Date(T0 + 50), platformReceivedAt: null,
      payload: { decision: 'SKIPPED', reason: 'instance-status=PAUSED', account: 'acct-cmq6cfwv' } },
    { id: 'e3', participantId: 'p', eventType: 'FILLED', createdAt: new Date(T0 + 60), platformReceivedAt: null, payload: {} },
  ];
  const out = buildExecutorDecisionReports({
    cycle: { id: 'c1', tradeId: 'gs1-a', intentEnvelope: {} }, correlationId: 'gs1-a', lane: 'GS01', events, nowMs: T0 + 100,
  });
  assert.deepEqual(out.map((r) => r.type), ['INTENT_RECEIVED', 'INTENT_SKIPPED']);
  assert.equal((out[1] as any).decision.reason, 'instance-status=PAUSED');
  assert.equal(out[1].account, 'acct-cmq6cfwv');
  assert.equal(summarizeCopyStatus(out, T0 + 100).gap_count, 0);
});

test('decision source id is stable and bounded', () => {
  const a = intentDecisionSourceId('cmq6cfwv4001', 'gs1-a', 'SKIPPED', 'instance-status=PAUSED');
  assert.equal(a, intentDecisionSourceId('cmq6cfwv4001', 'gs1-a', 'SKIPPED', 'instance-status=PAUSED'));
  assert.ok(intentDecisionSourceId('x', 't'.repeat(300), 'SKIPPED', 'r'.repeat(300)).length <= 190);
});

test('exchange mirror: stops, orphans when flat, balance', () => {
  const flat = buildExchangeMirror({
    orders: [{ id: 246228885722, symbol: 'tBTCF0:USTF0', amount: -0.0003, amountOrig: -0.0003, price: 82817, status: 'ACTIVE', orderType: 'STOP', flags: 1024 }],
    position: null, balance: 19.5, readStartedAt: T0, readAt: T0 + 120,
  });
  assert.equal(flat.flat, true);
  assert.deepEqual(flat.orphans, ['246228885722']);
  assert.equal((flat.stops as any[])[0].reduce_only, true);
  const open = buildExchangeMirror({
    orders: [], position: { symbol: 'tBTCF0:USTF0', amount: 0.0003, basePrice: 83085, pnlUsd: 0.01, direction: 'LONG' },
    balance: null, readStartedAt: T0, readAt: T0,
  });
  assert.equal(open.flat, false);
  assert.equal((open.positions as any[])[0].stops, 0);
  assert.deepEqual(open.orphans, []);
});

test('AMEND4 E5: mirror positions carry live liq_price + liq_read_at (null when unreadable)', () => {
  const pos = { symbol: 'tBTCF0:USTF0', amount: 0.0003, basePrice: 67000, pnlUsd: -0.01, direction: 'LONG' as const };
  const withLiq = buildExchangeMirror({ orders: [], position: { ...pos, liqPrice: 60250.5 }, balance: null, readStartedAt: T0, readAt: T0 + 50 });
  const p = (withLiq.positions as any[])[0];
  assert.equal(p.liq_price, 60250.5);
  assert.equal(p.liq_read_at, new Date(T0 + 50).toISOString());
  for (const liqPrice of [null, undefined, 0]) {
    const m = buildExchangeMirror({ orders: [], position: { ...pos, liqPrice }, balance: null, readStartedAt: T0, readAt: T0 });
    assert.equal((m.positions as any[])[0].liq_price, null);
    assert.equal((m.positions as any[])[0].liq_read_at, null);
  }
});

test('fly-view projections: transition (who/why/when) and mirror read age', () => {
  const t = flyViewTransition({ at: '2026-10-11T06:22:00Z', actor: 'SYSTEM', action: 'SAFETY_PAUSED', reason: 'POSITION_MISMATCH', userId: 'secret' });
  assert.deepEqual(Object.keys(t ?? {}).includes('userId'), false);
  assert.equal(t?.reason, 'POSITION_MISMATCH');
  const m = flyViewMirror({ read_at: new Date(T0).toISOString(), ok: true }, T0 + 4_000);
  assert.equal(m?.read_age_s, 4);
  assert.equal(flyViewMirror(null, T0), null);
});

test('reconcile defers (does not pause) when an entry participant was created during the tick', () => {
  const src = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const i = src.indexOf('Stale ledger snapshot');
  const j = src.indexOf('RECONCILE ALERT: exchange');
  assert.ok(i > 0 && j > i, 'stale-snapshot deferral must run before the mismatch pause');
  assert.match(src, /id: \{ notIn: knownIds \}/);
});

import { flyViewListedReason } from './trading-agents.service';
test('fly-view lists disarmed instances with no transition (known_disarmed)', () => {
  assert.equal(flyViewListedReason({ relayArmedAt: null, relayLastTransition: null }), 'known_disarmed');
  assert.equal(flyViewListedReason({ relayArmedAt: '2026-10-11T00:00:00Z' }), 'armed_at');
  assert.equal(flyViewListedReason({ relayLastTransition: { action: 'PAUSED' } }), 'last_transition');
});
