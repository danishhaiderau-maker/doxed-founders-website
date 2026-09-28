import assert from 'node:assert/strict';
import test from 'node:test';
import {
  activeLiveRelayArmForSessionReset,
  buildFreshInstanceDashboardState,
  applyInstanceDashboardPatch,
  readInstanceScope,
  readRelayFidelitySessionStart,
} from './instance-view.mapper';

test('paused instance status dominates stale live relay arm telemetry', () => {
  const next = applyInstanceDashboardPatch(
    'PAUSED',
    {
      relayExecutionMode: 'LIVE',
      relayArmedAt: '2026-08-11T00:07:21.756Z',
      realTradingConfirmedAt: '2026-08-11T00:07:21.756Z',
      mirrorDiff: { ticks: 1 },
    },
    { copyRelayReconcile: { openLots: 0 } },
  );
  assert.equal(next.relayExecutionMode, 'PAUSED');
  assert.equal(next.relayArmedAt, null);
  assert.equal(next.realTradingConfirmedAt, null);
  assert.deepEqual(next.mirrorDiff, { ticks: 1 });
  assert.deepEqual(next.copyRelayReconcile, { openLots: 0 });
});

test('active instance telemetry preserves its current live arm epoch', () => {
  const armedAt = '2026-08-11T00:07:21.756Z';
  const next = applyInstanceDashboardPatch(
    'ACTIVE',
    { relayExecutionMode: 'LIVE', relayArmedAt: armedAt, realTradingConfirmedAt: armedAt },
    { lastTickAt: '2026-08-11T02:43:27.278Z' },
  );
  assert.equal(next.relayExecutionMode, 'LIVE');
  assert.equal(next.relayArmedAt, armedAt);
  assert.equal(next.realTradingConfirmedAt, armedAt);
});

test('fresh collection preserves an explicit ACTIVE live arm but never invents one', () => {
  const armedAt = '2026-08-14T00:10:00.000Z';
  const preserved = activeLiveRelayArmForSessionReset('ACTIVE', {
    relayExecutionMode: 'LIVE',
    relayArmedAt: armedAt,
    realTradingConfirmedAt: armedAt,
    liveDeskSessionStartedAt: '2026-08-13T23:00:00.000Z',
    relayEntryPolicy: 'NEXT_FRESH_ONLY',
    relayPolicyVersion: 'two_lane_explicit_v6',
    relayLastTransition: { action: 'STARTED' },
  });
  assert.equal(preserved.relayExecutionMode, 'LIVE');
  assert.equal(preserved.relayArmedAt, armedAt);
  assert.equal(preserved.realTradingConfirmedAt, armedAt);
  assert.equal(preserved.liveDeskSessionStartedAt, '2026-08-13T23:00:00.000Z');
  assert.deepEqual(preserved.relayLastTransition, { action: 'STARTED' });

  assert.deepEqual(
    activeLiveRelayArmForSessionReset('ACTIVE', { relayExecutionMode: 'LIVE' }),
    {},
  );
  assert.deepEqual(
    activeLiveRelayArmForSessionReset('PAUSED', {
      relayExecutionMode: 'LIVE',
      relayArmedAt: armedAt,
    }),
    { relayExecutionMode: 'PAUSED', relayArmedAt: null, realTradingConfirmedAt: null },
  );
});

test('session reset preserves PAUSED disarm metadata in the replacement dashboard', () => {
  for (const prior of [
    {},
    { relayExecutionMode: null },
    { relayExecutionMode: 'PAUSED', relayArmedAt: null, realTradingConfirmedAt: null },
    { relayExecutionMode: 'LIVE', relayArmedAt: '2026-09-06T00:00:00Z', realTradingConfirmedAt: '2026-09-06T00:00:00Z' },
  ]) {
    const original = { ...prior };
    const replacement = {
      ...buildFreshInstanceDashboardState('live', 500),
      ...activeLiveRelayArmForSessionReset('PAUSED', prior),
    };
    assert.equal(replacement.relayExecutionMode, 'PAUSED');
    assert.equal(replacement.relayArmedAt, null);
    assert.equal(replacement.realTradingConfirmedAt, null);
    assert.equal(replacement.startingBalanceUsd, 500);
    assert.deepEqual(prior, original);
  }
});

test('reset never manufactures a live arm from active status or unknown status', () => {
  for (const status of ['ACTIVE', 'STOPPED', '']) {
    assert.deepEqual(activeLiveRelayArmForSessionReset(status, {}), {});
    assert.deepEqual(activeLiveRelayArmForSessionReset(status, { relayExecutionMode: 'PAUSED' }), {});
  }
});

test('live fidelity has no epoch before explicit NEXT_FRESH_ONLY arm', () => {
  const start = readRelayFidelitySessionStart({
    instanceMode: 'live',
    dashboardState: {
      sessionStartedAt: '2026-07-20T00:00:00.000Z',
      relayArmedAt: null,
      realTradingConfirmedAt: null,
    },
    copyRelaySim: {
      active: false,
      startedAt: '2026-07-20T01:00:00.000Z',
    },
    userSessionStartedAt: '2026-07-20T00:00:00.000Z',
  });

  assert.equal(start, null);
});

test('live fidelity begins at the current explicit relay arm', () => {
  const start = readRelayFidelitySessionStart({
    instanceMode: 'live',
    dashboardState: {
      relayArmedAt: '2026-07-29T14:30:00.000Z',
      realTradingConfirmedAt: '2026-07-20T00:00:00.000Z',
    },
  });

  assert.equal(start?.toISOString(), '2026-07-29T14:30:00.000Z');
});

test('live desk session prefers arm timestamps over collection sessionStartedAt', () => {
  const scope = readInstanceScope({
    id: 'inst-1',
    activatedAt: new Date('2026-08-06T00:00:00.000Z'),
    hiredAt: new Date('2026-08-01T00:00:00.000Z'),
    exchangeProvider: 'bitfinex',
    dashboardState: {
      sessionStartedAt: '2026-08-06T00:00:00.000Z',
      relayArmedAt: null,
      liveDeskSessionStartedAt: '2026-08-08T02:00:00.000Z',
    },
  });
  assert.equal(scope.sessionStartedAt.toISOString(), '2026-08-08T02:00:00.000Z');
  assert.equal(scope.instanceMode, 'live');
});

test('paper copy fidelity keeps its active sim epoch', () => {
  const start = readRelayFidelitySessionStart({
    instanceMode: 'copy',
    dashboardState: {},
    copyRelaySim: {
      active: true,
      startedAt: '2026-07-28T03:00:00.000Z',
    },
    userSessionStartedAt: '2026-07-20T00:00:00.000Z',
  });

  assert.equal(start?.toISOString(), '2026-07-28T03:00:00.000Z');
});
