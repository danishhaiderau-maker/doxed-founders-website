import assert from 'node:assert/strict';
import test from 'node:test';
import { readInstanceScope, readRelayFidelitySessionStart } from './instance-view.mapper';

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
