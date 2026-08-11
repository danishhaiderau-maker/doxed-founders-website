import assert from 'node:assert/strict';
import test from 'node:test';
import {
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
