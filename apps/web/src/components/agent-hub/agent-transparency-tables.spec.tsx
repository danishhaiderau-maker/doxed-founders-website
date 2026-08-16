import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  AgentTransparencyTables,
  canonicalTradeIdForDisplay,
  displayExitCause,
} from './agent-transparency-tables';

(globalThis as typeof globalThis & { React: typeof React }).React = React;

test('dashboard displays canonical showcase ids for adopted and relinked exchange trades', () => {
  assert.equal(
    canonicalTradeIdForDisplay('relink:unknown:cont-fa8ce196a716:1786346662320'),
    'cont-fa8ce196a716',
  );
  assert.equal(
    canonicalTradeIdForDisplay('adopt:cont-61251a7811df:1786349393891'),
    'cont-61251a7811df',
  );
  assert.equal(canonicalTradeIdForDisplay('cont-42d2923c0cae'), 'cont-42d2923c0cae');
});

test('completed trade exit causes use a readable label without hiding unknown audit codes', () => {
  assert.equal(displayExitCause('PROFIT_LOCK_LADDER'), 'Profit lock (trailing)');
  assert.equal(displayExitCause('THESIS_FAST_CUT'), 'Thesis fast cut');
  assert.equal(displayExitCause('SOURCE_ABSENCE_FALLBACK'), 'Source snapshot absence fallback');
  assert.equal(
    displayExitCause('SOURCE_CONFIRMED_PROFIT_LOCK_LADDER'),
    'Source-confirmed: Profit lock (trailing)',
  );
  assert.equal(displayExitCause('CUSTOM_FUTURE_EXIT'), 'CUSTOM FUTURE EXIT');
  assert.equal(displayExitCause(null), 'Not recorded');
});

test('completed trade tables show the recorded exit cause', () => {
  const html = renderToStaticMarkup(
    React.createElement(AgentTransparencyTables, {
      executionOnly: true,
      liveBook: {
        activeSignals: [], positions: [], pendingOrders: [], expiredOrders: [],
        trades: [{
          time: '2026-08-12 12:00:00 AEST', tradeId: 'cont-exit-cause', direction: 'SHORT',
          entry: 64_000, exit: 63_900, durationMin: 7.2, pnlPct: 4.1, netUsd: 0.82,
          grossUsd: 0.82, tradeFeesUsd: 0, fundingUsd: 0, aiBand: 'EXCHANGE_VERIFIED',
          exitReason: 'PROFIT_LOCK_LADDER',
        }],
      },
    }),
  );
  assert.match(html, /Exit cause/);
  assert.match(html, /Profit lock \(trailing\)/);
});

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
  assert.match(html, /Expired relay orders/);
  assert.match(html, /Blocked signals/);
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

test('labels copy-first exchange evidence without inventing a Showcase fill', () => {
  const html = renderToStaticMarkup(
    React.createElement(AgentTransparencyTables, {
      executionOnly: true,
      liveBook: {
        activeSignals: [], positions: [], pendingOrders: [], expiredOrders: [],
        trades: [{
          time: '2026-08-15 18:50:13 AEST',
          tradeId: 'cont-copy-first', direction: 'SHORT', entry: 63060, exit: 63051,
          durationMin: 1.5, exitReason: 'PROFIT_LOCK', pnlPct: 2.38, netUsd: 0.49,
          grossUsd: 0.49, tradeFeesUsd: 0, fundingUsd: 0,
          sourceFillTime: null,
          exchangeOrderAckTime: '2026-08-15 18:48:03 AEST',
          exchangeFillTime: '2026-08-15 18:48:43 AEST',
          exchangeExitTime: '2026-08-15 18:50:13 AEST',
          lifecycleStatus: 'COPY_FIRST_DIVERGENCE',
          evidenceStatus: 'Exchange-linked lifecycle',
          negativeEvidence: 'MIRROR_DIFF preserved',
        }],
      },
    }),
  );

  assert.match(html, /Source fill \(Melbourne\)/);
  assert.match(html, /Bitfinex fill \(Melbourne\)/);
  assert.match(html, /Copy filled first \/ source fill absent/);
  assert.match(html, /Not recorded/);
  assert.match(html, /MIRROR_DIFF preserved/);
  assert.match(html, /never fabricates a Showcase fill/);
});

test('expired relay orders table computes age from expired-created timestamps, not server ageMin alone', () => {
  // Defect 2: a row whose server-supplied ageMin is missing/0 but whose
  // timestamps clearly span 12 minutes must show 12, not 0. The displayed age
  // must always agree with the displayed Created / Expired columns.
  const html = renderToStaticMarkup(
    React.createElement(AgentTransparencyTables, {
      executionOnly: true,
      liveBook: {
        activeSignals: [],
        positions: [],
        pendingOrders: [],
        expiredOrders: [
          {
            time: '2026-07-31 16:30:00 AEST',
            createdTime: '2026-07-31 16:00:00 AEST',
            expiredTime: '2026-07-31 16:12:00 AEST',
            direction: 'LONG',
            limitPrice: 63_900,
            ageMin: 0, // legacy snapshot — server forgot to populate
            reason: 'SIGNAL_TTL_EXPIRED',
            confidence: 0,
            mode: 'LIVE_COPY',
          },
        ],
        trades: [],
      },
    }),
  );

  // Row appears in the genuine "Expired relay orders" table (limit price > 0).
  assert.match(html, /Expired relay orders/);
  assert.match(html, /SIGNAL_TTL_EXPIRED/);
  // Age column shows 12, derived from (16:12 - 16:00), not the stale 0.
  // Anchor to the cell that immediately follows the limit-price cell so we
  // don't accidentally match a 0 inside a date or price.
  const ageCellMatch = /63,900<\/td><td[^>]*>(\d+)<\/td><td[^>]*>SIGNAL_TTL_EXPIRED/.exec(html);
  assert.ok(ageCellMatch, 'could not locate the expired-row age cell');
  assert.equal(ageCellMatch![1], '12');
});

test('blocked signals (limitPrice 0 / SPREAD_BUCKET_BLOCKED) render in a separate table, not as expired orders', () => {
  // Defect 3: a row representing a signal that was blocked before any exchange
  // order existed must NOT appear under "Expired relay orders" — it belongs in
  // the dedicated "Blocked signals" table, labelled with the block reason.
  const html = renderToStaticMarkup(
    React.createElement(AgentTransparencyTables, {
      executionOnly: true,
      liveBook: {
        activeSignals: [],
        positions: [],
        pendingOrders: [],
        expiredOrders: [
          {
            time: '2026-07-31 17:00:00 AEST',
            createdTime: '2026-07-31 16:59:59 AEST',
            expiredTime: '2026-07-31 17:00:00 AEST',
            direction: 'LONG',
            limitPrice: 0, // no order was ever created
            ageMin: 0,
            reason: 'SPREAD_BUCKET_BLOCKED',
            confidence: 0,
            mode: 'LIVE_COPY',
          },
          {
            time: '2026-07-31 17:10:00 AEST',
            createdTime: '2026-07-31 16:40:00 AEST',
            expiredTime: '2026-07-31 17:10:00 AEST',
            direction: 'LONG',
            limitPrice: 63_950,
            ageMin: 30,
            reason: 'SIGNAL_TTL_EXPIRED',
            confidence: 0,
            mode: 'LIVE_COPY',
          },
        ],
        trades: [],
      },
    }),
  );

  // Both tables exist with their distinct labels.
  assert.match(html, /Expired relay orders/);
  assert.match(html, /Blocked signals/);
  // The blocked-signals table clearly states no order was created.
  assert.match(html, /no Bitfinex order ever existed/i);
  assert.match(html, /Block reason/);

  // The genuine TTL expiry (limitPrice > 0) appears under Expired orders, with
  // its age computed from timestamps (30 minutes between 16:40 and 17:10).
  assert.match(html, /SIGNAL_TTL_EXPIRED/);
  assert.match(html, /63,950/);
  assert.match(html, /2026-07-31 17:10:00 AEST/);

  // The blocked signal appears once, under Blocked signals, with its block
  // reason — and is NOT mislabelled as an expired order. The cell text marks
  // it as having no real order so it cannot be confused with an expired
  // exchange order that had a limit price.
  const blockedMatches = html.split('SPREAD_BUCKET_BLOCKED').length - 1;
  assert.equal(blockedMatches, 1);
  assert.match(html, />No order</);

  // The blocked signal's price cell is "No order", never a numeric price —
  // this is the visible signal that distinguishes it from a real expired order.
  const blockedRowHasFakePrice = /Blocked signals[\s\S]*?No order[\s\S]*?SPREAD_BUCKET_BLOCKED/.test(html);
  assert.equal(blockedRowHasFakePrice, true);
});
