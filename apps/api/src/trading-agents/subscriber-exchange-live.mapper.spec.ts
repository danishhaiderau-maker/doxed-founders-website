import assert from 'node:assert/strict';
import test from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import { calculateBitfinexSessionPnl } from '../exchanges/bitfinex-api.client';
import { mapSubscriberExchangeLiveBook } from './subscriber-exchange-live.mapper';

test('does not render a protective stop as a pending entry order', () => {
  const book = mapSubscriberExchangeLiveBook({
    orders: [
      {
        id: 1,
        symbol: 'tBTCF0:USTF0',
        amount: 0.031,
        amountOrig: 0.031,
        price: 64_681,
        status: 'ACTIVE',
        orderType: 'STOP',
      },
      {
        id: 2,
        symbol: 'tBTCF0:USTF0',
        amount: -0.031,
        amountOrig: -0.031,
        price: 64_424,
        status: 'ACTIVE',
        orderType: 'LIMIT',
      },
    ],
    position: null,
    participants: [],
  });

  assert.equal(book.pendingOrders.length, 1);
  assert.equal(book.pendingOrders[0]?.tradeId, 'bfx-2');
  assert.equal(book.pendingOrders[0]?.side, 'SHORT');
  assert.equal(book.pendingOrders[0]?.limitPrice, 64_424);
});

test('renders one pending row when Bitfinex rounds the relay quantity', () => {
  const now = new Date('2026-07-23T10:55:53.717Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [
      {
        id: 241025573521,
        symbol: 'tBTCF0:USTF0',
        amount: -0.03038,
        amountOrig: -0.03038,
        price: 65_812.58,
        status: 'ACTIVE',
        orderType: 'LIMIT',
      },
    ],
    position: null,
    participants: [
      {
        status: SignalCycleStatus.PENDING_ENTRY,
        fillPrice: null,
        exitPrice: null,
        pnlUsd: null,
        pnlMarginPct: null,
        limitPrice: 65_812.58,
        qty: 0.0304131628,
        stopLoss: null,
        takeProfit: null,
        terminalReason: null,
        createdAt: now,
        updatedAt: now,
        cycle: {
          tradeId: 'cont-6aec65edb25c',
          status: SignalCycleStatus.PENDING_ENTRY,
          intentEnvelope: { direction: 'SHORT' },
          showcaseExitReason: null,
          createdAt: now,
        },
      },
    ],
  });

  assert.equal(book.pendingOrders.length, 1);
  assert.equal(book.pendingOrders[0]?.tradeId, 'cont-6aec65edb25c');
  assert.equal(book.pendingOrders[0]?.status, 'ACTIVE');
  assert.equal(book.activeSignals[0]?.tradeId, 'cont-6aec65edb25c');
});

test('labels an opted-in late entry continuation separately from a normal pending copy', () => {
  const now = new Date('2026-08-12T07:00:00.000Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [
      {
        id: 241999000001,
        symbol: 'tBTCF0:USTF0',
        amount: -0.031,
        amountOrig: -0.031,
        price: 64_200,
        status: 'ACTIVE',
        orderType: 'LIMIT',
      },
    ],
    position: null,
    participants: [
      {
        status: SignalCycleStatus.PENDING_ENTRY,
        fillPrice: null,
        exitPrice: null,
        pnlUsd: null,
        pnlMarginPct: null,
        limitPrice: 64_200,
        qty: 0.031,
        stopLoss: null,
        takeProfit: null,
        terminalReason: null,
        lateEntryContinuation: true,
        createdAt: now,
        updatedAt: now,
        cycle: {
          tradeId: 'cont-late-entry-visible',
          status: SignalCycleStatus.OPEN,
          intentEnvelope: { direction: 'SHORT' },
          showcaseExitReason: null,
          createdAt: now,
        },
      },
    ],
  });

  assert.equal(book.activeSignals[0]?.outcome, 'LATE_ENTRY_BETTER_ONLY_CONTINUATION');
});

test('keeps the late-entry label visible after an unfilled continuation is cancelled', () => {
  const now = new Date('2026-08-12T07:00:00.000Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [
      {
        status: SignalCycleStatus.EXPIRED,
        fillPrice: null,
        exitPrice: null,
        pnlUsd: null,
        pnlMarginPct: null,
        limitPrice: 64_200,
        qty: 0.031,
        stopLoss: null,
        takeProfit: null,
        terminalReason: 'SHOWCASE_CYCLE_CLOSED',
        lateEntryContinuation: true,
        createdAt: now,
        updatedAt: new Date('2026-08-12T07:03:00.000Z'),
        cycle: {
          tradeId: 'cont-late-entry-terminal',
          status: SignalCycleStatus.CLOSED,
          intentEnvelope: { direction: 'SHORT' },
          showcaseExitReason: 'THESIS_FAST_CUT',
          createdAt: now,
        },
      },
    ],
  });

  assert.equal(
    book.expiredOrders[0]?.reason,
    'LATE_ENTRY_BETTER_ONLY_CONTINUATION: SHOWCASE_CYCLE_CLOSED',
  );
});

test('keeps genuinely separate pending orders visible', () => {
  const now = new Date('2026-07-23T10:55:53.717Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [
      {
        id: 1,
        symbol: 'tBTCF0:USTF0',
        amount: -0.03038,
        amountOrig: -0.03038,
        price: 65_812.58,
        status: 'ACTIVE',
        orderType: 'LIMIT',
      },
      {
        id: 2,
        symbol: 'tBTCF0:USTF0',
        amount: -0.03038,
        amountOrig: -0.03038,
        price: 66_100,
        status: 'ACTIVE',
        orderType: 'LIMIT',
      },
    ],
    position: null,
    participants: [
      {
        status: SignalCycleStatus.PENDING_ENTRY,
        fillPrice: null,
        exitPrice: null,
        pnlUsd: null,
        pnlMarginPct: null,
        limitPrice: 66_100,
        qty: 0.03038,
        stopLoss: null,
        takeProfit: null,
        terminalReason: null,
        createdAt: now,
        updatedAt: now,
        cycle: {
          tradeId: 'cont-separate',
          status: SignalCycleStatus.PENDING_ENTRY,
          intentEnvelope: { direction: 'SHORT' },
          showcaseExitReason: null,
          createdAt: now,
        },
      },
    ],
  });

  assert.equal(book.pendingOrders.length, 2);
  assert.equal(book.pendingOrders[0]?.tradeId, 'bfx-1');
  assert.equal(book.pendingOrders[1]?.tradeId, 'cont-separate');
});

test('does not render a relay-only pending row as a Bitfinex order', () => {
  const now = new Date('2026-07-23T10:55:53.717Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [
      {
        status: SignalCycleStatus.PENDING_ENTRY,
        fillPrice: null,
        exitPrice: null,
        pnlUsd: null,
        pnlMarginPct: null,
        limitPrice: 65_812.58,
        qty: 0.0304131628,
        stopLoss: null,
        takeProfit: null,
        terminalReason: null,
        createdAt: now,
        updatedAt: now,
        cycle: {
          tradeId: 'cont-ledger-only',
          status: SignalCycleStatus.PENDING_ENTRY,
          intentEnvelope: { direction: 'SHORT' },
          showcaseExitReason: null,
          createdAt: now,
        },
      },
    ],
  });

  assert.equal(book.pendingOrders.length, 0);
});

test('labels the actual exchange net separately from its virtual tracked lot', () => {
  const now = new Date('2026-07-19T12:51:04Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: {
      symbol: 'tBTCF0:USTF0',
      amount: -0.031,
      basePrice: 64_424,
      pnlUsd: 0.81,
      pnlPct: 0.1,
      direction: 'SHORT',
    },
    markPrice: 64_400,
    participants: [
      {
        status: SignalCycleStatus.OPEN,
        fillPrice: 64_424,
        exitPrice: null,
        pnlUsd: null,
        pnlMarginPct: null,
        limitPrice: 64_424,
        qty: 0.031,
        stopLoss: 64_681,
        takeProfit: null,
        createdAt: now,
        updatedAt: now,
        cycle: {
          tradeId: 'cont-ccf411542b21',
          status: SignalCycleStatus.OPEN,
          intentEnvelope: { direction: 'SHORT' },
          showcaseExitReason: null,
          createdAt: now,
        },
      },
    ],
  });

  assert.deepEqual(
    book.positions.map((position) => position.leg),
    ['Exchange net (actual)', 'Tracked lot cont-ccf41'],
  );
  assert.equal(book.positions[0]?.pnlUsd, 0.81);
  assert.equal(book.positions[1]?.pnlUsd, 0.744);
  assert.equal(book.activeSignals[0]?.tradeId, 'cont-ccf411542b21');
});

test('session P&L uses one exchange accounting basis without virtual-lot double counting', () => {
  assert.equal(
    calculateBitfinexSessionPnl({
      realizedPnlUsd: -3.91,
      unrealizedPnlUsd: -0.47,
      tradingFeesUsd: 0.2,
      fundingFeesUsd: 0.01,
    }),
    -4.59,
  );
});

test('does not invent trade direction from a Bitfinex ledger win or loss', () => {
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [],
    ledgerCloses: [
      {
        ledgerId: '12345',
        closedAt: new Date('2026-07-23T10:55:53.717Z'),
        pnlUsd: 2.25,
        description: 'Position closed',
      },
    ],
  });

  assert.equal(book.trades.length, 1);
  assert.equal(book.trades[0]?.direction, '—');
  assert.equal(book.trades[0]?.netUsd, 2.25);
});

test('renders only exchange-ledger closes and keeps distinct close ids inside two minutes', () => {
  const closedAt = new Date('2026-07-23T10:55:53.717Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [
      {
        status: SignalCycleStatus.CLOSED,
        fillPrice: 65_000,
        exitPrice: 64_900,
        pnlUsd: 3,
        pnlMarginPct: 1,
        limitPrice: 65_000,
        qty: 0.03,
        stopLoss: null,
        takeProfit: null,
        terminalReason: null,
        createdAt: new Date(closedAt.getTime() - 60_000),
        updatedAt: closedAt,
        cycle: {
          tradeId: 'virtual-close-must-not-render',
          status: SignalCycleStatus.CLOSED,
          intentEnvelope: { direction: 'SHORT' },
          showcaseExitReason: null,
          createdAt: new Date(closedAt.getTime() - 60_000),
        },
      },
    ],
    ledgerCloses: [
      {
        ledgerId: '2001',
        closedAt,
        pnlUsd: 2.25,
        description: 'Position closed',
      },
      {
        ledgerId: '2002',
        closedAt: new Date(closedAt.getTime() + 30_000),
        pnlUsd: -0.75,
        description: 'Position closed',
      },
    ],
  });

  assert.deepEqual(
    book.trades.map((trade) => trade.tradeId).sort(),
    ['bfx-2001', 'bfx-2002'],
  );
});

test('renders one exchange-proven participant close when Bitfinex close ledger is empty', () => {
  const createdAt = new Date('2026-07-25T09:07:47.530Z');
  const closedAt = new Date('2026-07-25T09:36:18.000Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [
      {
        status: SignalCycleStatus.CLOSED,
        fillPrice: 63_918,
        exitPrice: 63_969,
        pnlUsd: -1.6,
        pnlMarginPct: -7.98,
        limitPrice: 63_917.9,
        qty: 0.03129,
        stopLoss: 64_173.672,
        takeProfit: null,
        terminalReason: 'SHOWCASE_MIRROR',
        exchangeProven: true,
        createdAt,
        updatedAt: closedAt,
        cycle: {
          tradeId: 'tbhv1-49cb75b66c23',
          status: SignalCycleStatus.CLOSED,
          intentEnvelope: { direction: 'SHORT' },
          showcaseExitReason: 'SHOWCASE_UNREACHABLE_OPEN_LOT',
          createdAt,
        },
      },
    ],
    ledgerCloses: [],
  });

  assert.equal(book.trades.length, 1);
  assert.equal(book.trades[0]?.tradeId, 'tbhv1-49cb75b66c23');
  assert.equal(book.trades[0]?.direction, 'SHORT');
  assert.equal(book.trades[0]?.entry, 63_918);
  assert.equal(book.trades[0]?.exit, 63_969);
  assert.equal(book.trades[0]?.netUsd, -1.6);
});

test('preserves canonical identity and explicit clocks when a cash-ledger close is present', () => {
  const closedAt = new Date('2026-08-15T08:50:13.000Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [{
      status: SignalCycleStatus.CLOSED,
      fillPrice: 63_060,
      exitPrice: 63_051,
      pnlUsd: 0.48,
      pnlMarginPct: 2.38,
      qty: 0.03172,
      terminalReason: 'SHOWCASE_MIRROR',
      exchangeProven: true,
      createdAt: new Date('2026-08-15T08:48:00.000Z'),
      updatedAt: closedAt,
      sourceSignalAt: new Date('2026-08-15T08:47:59.000Z'),
      sourceFillAt: null,
      exchangeOrderAckAt: new Date('2026-08-15T08:48:03.000Z'),
      exchangeFillAt: new Date('2026-08-15T08:48:43.000Z'),
      exchangeExitAt: closedAt,
      negativeEvidence: 'MIRROR_DIFF preserved',
      cycle: {
        tradeId: 'cont-copy-first',
        status: SignalCycleStatus.CLOSED,
        intentEnvelope: { direction: 'SHORT' },
        showcaseExitReason: 'PROFIT_LOCK',
        createdAt: new Date('2026-08-15T08:48:00.000Z'),
      },
    }],
    ledgerCloses: [{
      ledgerId: '241972152606',
      closedAt,
      pnlUsd: 0.49,
      description: 'Position closed',
    }],
  });

  assert.equal(book.trades.length, 1);
  assert.equal(book.trades[0]?.tradeId, 'cont-copy-first');
  assert.equal(book.trades[0]?.netUsd, 0.49);
  assert.equal(book.trades[0]?.sourceFillTime, null);
  assert.equal(book.trades[0]?.lifecycleStatus, 'COPY_FIRST_DIVERGENCE');
  assert.match(book.trades[0]?.exchangeFillTime ?? '', /18:48:43/);
  assert.equal(book.trades[0]?.negativeEvidence, 'MIRROR_DIFF preserved');
});

test('measures a completed live-copy trade from exchange fill to first close', () => {
  const createdAt = new Date('2026-08-11T00:09:05.569Z');
  const filledAt = new Date('2026-08-11T00:21:57.590Z');
  const closedAt = new Date('2026-08-11T02:22:07.512Z');
  // A later duplicate reconciliation event updated the participant after the
  // real close. Neither pre-fill chase time nor that bookkeeping tail belongs
  // in the exchange holding duration.
  const updatedAt = new Date('2026-08-11T02:22:10.690Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [
      {
        status: SignalCycleStatus.CLOSED,
        fillPrice: 64_025,
        exitPrice: 64_085,
        pnlUsd: -1.87,
        pnlMarginPct: -9.37,
        qty: 0.03122,
        terminalReason: 'SHOWCASE_MIRROR',
        exchangeProven: true,
        createdAt,
        filledAt,
        closedAt,
        updatedAt,
        cycle: {
          tradeId: 'cont-d05a25f5874c',
          status: SignalCycleStatus.CLOSED,
          intentEnvelope: { direction: 'SHORT' },
          showcaseExitReason: 'TIME_EXIT',
          createdAt,
        },
      },
    ],
    ledgerCloses: [],
  });

  assert.equal(book.trades[0]?.durationMin, 120.2);
  assert.equal(book.trades[0]?.time, '2026-08-11 12:22:07 AEST');
});

test('renders Neon CLOSED live-copy rows when Bitfinex close ledger is empty', () => {
  const createdAt = new Date('2026-08-07T16:53:00.000Z');
  const closedAt = new Date('2026-08-07T16:58:00.000Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [
      {
        status: SignalCycleStatus.CLOSED,
        fillPrice: 64_100,
        exitPrice: 64_050,
        pnlUsd: -1.42,
        pnlMarginPct: -7.1,
        limitPrice: 64_100,
        qty: 0.0308,
        terminalReason: 'SHOWCASE_MIRROR',
        exchangeProven: false,
        createdAt,
        updatedAt: closedAt,
        cycle: {
          tradeId: 'cont-211f46765b49',
          status: SignalCycleStatus.CLOSED,
          intentEnvelope: { direction: 'SHORT' },
          showcaseExitReason: null,
          createdAt,
        },
      },
    ],
    ledgerCloses: [],
  });

  assert.equal(book.trades.length, 1);
  assert.equal(book.trades[0]?.tradeId, 'cont-211f46765b49');
  assert.equal(book.trades[0]?.netUsd, -1.42);
  assert.equal(book.trades[0]?.aiBand, 'NEON_CLOSED');
});

test('labels a source-absence fallback instead of presenting it as a confirmed Showcase exit', () => {
  const closedAt = new Date('2026-08-15T05:00:00.000Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [{
      status: SignalCycleStatus.CLOSED,
      fillPrice: 64_100,
      exitPrice: 64_080,
      pnlUsd: -0.62,
      pnlMarginPct: -3.1,
      qty: 0.031,
      terminalReason: 'SHOWCASE_MIRROR',
      exitProvenance: 'SHOWCASE_POSITION_ABSENT',
      exchangeProven: true,
      createdAt: new Date('2026-08-15T04:50:00.000Z'),
      updatedAt: closedAt,
      cycle: {
        tradeId: 'source-absence-fallback',
        status: SignalCycleStatus.CLOSED,
        intentEnvelope: { direction: 'SHORT' },
        showcaseExitReason: 'THESIS_FAST_CUT',
        createdAt: new Date('2026-08-15T04:50:00.000Z'),
      },
    }],
    ledgerCloses: [],
  });

  assert.equal(book.trades[0]?.exitReason, 'SOURCE_ABSENCE_FALLBACK');
});

test('labels a signed Showcase-close mirror as source-confirmed', () => {
  const closedAt = new Date('2026-08-15T05:00:00.000Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [{
      status: SignalCycleStatus.CLOSED,
      fillPrice: 64_100,
      exitPrice: 64_080,
      pnlUsd: -0.62,
      pnlMarginPct: -3.1,
      qty: 0.031,
      terminalReason: 'SHOWCASE_MIRROR',
      exitProvenance: 'SHOWCASE_CLOSED_WEBHOOK',
      exchangeProven: true,
      createdAt: new Date('2026-08-15T04:50:00.000Z'),
      updatedAt: closedAt,
      cycle: {
        tradeId: 'source-confirmed-exit',
        status: SignalCycleStatus.CLOSED,
        intentEnvelope: { direction: 'SHORT' },
        showcaseExitReason: 'PROFIT_LOCK_LADDER',
        createdAt: new Date('2026-08-15T04:50:00.000Z'),
      },
    }],
    ledgerCloses: [],
  });

  assert.equal(book.trades[0]?.exitReason, 'SOURCE_CONFIRMED_PROFIT_LOCK_LADDER');
});

test('does not render a virtual-only participant close as a Bitfinex completed trade', () => {
  const now = new Date('2026-07-25T09:36:18.000Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [
      {
        status: SignalCycleStatus.CLOSED,
        fillPrice: 63_918,
        exitPrice: 63_969,
        pnlUsd: -1.6,
        pnlMarginPct: -7.98,
        limitPrice: 63_917.9,
        qty: 0.03129,
        terminalReason: 'PAPER_EXIT',
        exchangeProven: false,
        createdAt: new Date(now.getTime() - 60_000),
        updatedAt: now,
        cycle: {
          tradeId: 'paper-close',
          status: SignalCycleStatus.CLOSED,
          intentEnvelope: { direction: 'SHORT' },
          showcaseExitReason: null,
          createdAt: new Date(now.getTime() - 60_000),
        },
      },
    ],
    ledgerCloses: [],
  });

  assert.equal(book.trades.length, 0);
});

test('expired copy rows expose creation, cancellation, and terminal reason separately', () => {
  const createdAt = new Date('2026-07-22T12:36:19.000Z');
  const expiredAt = new Date('2026-07-22T12:38:42.000Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [
      {
        status: SignalCycleStatus.EXPIRED,
        fillPrice: null,
        exitPrice: null,
        pnlUsd: 0,
        pnlMarginPct: null,
        limitPrice: 65_892.83,
        qty: 0.03035,
        terminalReason: 'EXECUTOR_WATCHDOG_CANCELLED_UNFILLED',
        createdAt,
        updatedAt: expiredAt,
        cycle: {
          tradeId: 'cont-c9a9f6dd243d',
          status: SignalCycleStatus.PENDING_ENTRY,
          intentEnvelope: { direction: 'SHORT' },
          showcaseExitReason: null,
          createdAt,
        },
      },
    ],
  });

  assert.equal(book.expiredOrders[0]?.createdTime, '2026-07-22 22:36:19 AEST');
  assert.equal(book.expiredOrders[0]?.expiredTime, '2026-07-22 22:38:42 AEST');
  assert.equal(book.expiredOrders[0]?.ageMin, 2);
  assert.equal(
    book.expiredOrders[0]?.reason,
    'EXECUTOR_WATCHDOG_CANCELLED_UNFILLED',
  );
});
