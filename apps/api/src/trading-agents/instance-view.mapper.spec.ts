import assert from 'node:assert/strict';
import test from 'node:test';
import { readRelayFidelitySessionStart } from './instance-view.mapper';

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
