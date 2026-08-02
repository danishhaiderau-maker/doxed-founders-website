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

test('executionOnly live copy surfaces session-scoped active signals and expired orders without showcase AI columns', () => {
  const html = renderToStaticMarkup(
    React.createElement(AgentTransparencyTables, {
      executionOnly: true,
      liveBook: {
        activeSignals: [
          {
            tradeId: 'cont-8f1a0939a7e6',
            time: '2026-07-31 17:00:00 AEST',
            direction: 'SHORT',
            confidence: 0,
            regime: 'trend',
            strategy: 'copy',
            trigger: 'relay',
            pullRequiredPct: 0,
            signalPrice: 64_306,
            maxPullPct: 0,
            outcome: 'PENDING_ENTRY',
            fillPrice: null,
            exitReason: null,
          },
        ],
        positions: [
          {
            leg: 'Exchange net (actual)',
            side: 'SHORT',
            qty: 0.03103,
            entry: 64_306,
            current: 64_250,
            stopLoss: 65_000,
            takeProfit: 0,
            pnlUsd: 1.74,
          },
        ],
        pendingOrders: [
          {
            tradeId: 'cont-8f1a0939a7e6',
            ageMin: 4,
            side: 'SHORT',
            status: 'ACTIVE',
            qty: 0.03103,
            limitPrice: 64_306,
            signalPrice: 64_306,
          },
        ],
        expiredOrders: [
          {
            time: '2026-07-31 16:30:00 AEST',
            createdTime: '2026-07-31 16:00:00 AEST',
            expiredTime: '2026-07-31 16:30:00 AEST',
            direction: 'LONG',
            limitPrice: 63_900,
            ageMin: 30,
            reason: 'PRICE_MOVED_AWAY',
            confidence: 0,
            mode: 'LIVE_COPY',
          },
        ],
        trades: [],
      },
    }),
  );

  // New live-copy sections are present and scoped to the session.
  assert.match(html, /Active relay signals/);
  assert.match(html, /Expired \/ blocked relay signals/);
  assert.match(html, /scoped to your session only/);
  // The session signal and the expired cycle appear.
  assert.match(html, /64,306/);
  assert.match(html, /PENDING_ENTRY/);
  assert.match(html, /PRICE_MOVED_AWAY/);
  // The same traceable trade id links the live intent and real Bitfinex order.
  assert.equal(html.split('cont-8f1a0939a7e6').length - 1, 2);
  // Showcase-only AI columns are NOT rendered in executionOnly mode.
  assert.equal(html.includes('Raw AI gap'), false);
  assert.equal(html.includes('Gap bucket'), false);
  assert.equal(html.includes('Chase now'), false);
  // The original three live-copy sections are preserved.
  assert.match(html, /Open Bitfinex position/);
  assert.match(html, /Resting Bitfinex order/);
  assert.match(html, /Completed trades/);
});
