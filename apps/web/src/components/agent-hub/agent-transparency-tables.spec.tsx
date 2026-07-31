import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { AgentTransparencyTables } from './agent-transparency-tables';

(globalThis as typeof globalThis & { React: typeof React }).React = React;

test('active signal table separates raw gap, execution bucket, and virtual chase from real orders', () => {
  const html = renderToStaticMarkup(
    React.createElement(AgentTransparencyTables, {
      liveBook: {
        activeSignals: [
          {
            tradeId: 'cont-gap-30',
            time: '2026-07-31 12:00:00 AEST',
            direction: 'LONG',
            confidence: 0,
            rawScoreGap: 30,
            gapBucket: 3,
            chaseCount: 2,
            selectedChaseBuckets: [3, 4],
            entryLimitPrice: null,
            waitingReason: 'WAITING_FOR_CHASE_3',
            regime: 'RANGE',
            strategy: 'CONTINUOUS',
            trigger: 'VIRTUAL_CHASE',
            pullRequiredPct: 0,
            signalPrice: 64_000,
            maxPullPct: 0,
            outcome: 'APPROVE_PENDING',
            fillPrice: null,
            exitReason: null,
          },
        ],
        positions: [],
        pendingOrders: [],
        expiredOrders: [],
        trades: [],
      },
    }),
  );

  assert.match(html, /Raw AI gap/);
  assert.match(html, /Gap bucket/);
  assert.match(html, /30\/100/);
  assert.match(html, /WAITING_FOR_CHASE_3/);
  assert.match(html, /No real resting limit order/);
  assert.equal(html.includes('Conf'), false);
  assert.equal(html.includes('Pull req'), false);
  assert.equal(html.includes('Max pull'), false);
});
