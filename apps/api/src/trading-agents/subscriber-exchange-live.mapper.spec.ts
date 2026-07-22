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
  assert.equal(book.pendingOrders[0]?.side, 'SHORT');
  assert.equal(book.pendingOrders[0]?.limitPrice, 64_424);
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
  assert.equal(
    book.expiredOrders[0]?.reason,
    'EXECUTOR_WATCHDOG_CANCELLED_UNFILLED',
  );
});
