import assert from 'node:assert/strict';
import test from 'node:test';
import type { TradingAgentDashboardState } from '@dcf/utils';
import {
  resolveInitialAgentDesk,
  selectLiveExecutionBook,
  shouldShowShowcaseReference,
} from './agent-live-execution-view';

type LiveBook = TradingAgentDashboardState['liveBook'];

const baseBook = (): LiveBook => ({
  activeSignals: [],
  positions: [],
  pendingOrders: [],
  expiredOrders: [],
  trades: [],
});

test('live customers open on Bitfinex even when an old showcase preference exists', () => {
  assert.equal(
    resolveInitialAgentDesk({
      storedDesk: 'showcase',
      isLiveSession: true,
      relaySimActive: false,
    }),
    'live',
  );
});

test('an active relay simulation remains the selected desk', () => {
  assert.equal(
    resolveInitialAgentDesk({
      storedDesk: 'live',
      isLiveSession: true,
      relaySimActive: true,
    }),
    'relay-sim',
  );
});

test('live desk keeps one real net position and removes virtual lot representations', () => {
  const book = baseBook();
  book.positions = [
    {
      leg: 'Exchange net (actual)',
      side: 'SHORT',
      qty: 0.03,
      entry: 65_000,
      current: 64_990,
      stopLoss: 66_000,
      takeProfit: 0,
      pnlUsd: 0.3,
    },
    {
      leg: 'Tracked lot cont-one',
      side: 'SHORT',
      qty: 0.02,
      entry: 65_000,
      current: 64_990,
      stopLoss: 66_000,
      takeProfit: 0,
      pnlUsd: 0.2,
    },
    {
      leg: 'Tracked lot type-b',
      side: 'SHORT',
      qty: 0.01,
      entry: 65_000,
      current: 64_990,
      stopLoss: 66_000,
      takeProfit: 0,
      pnlUsd: 0.1,
    },
  ];

  const selected = selectLiveExecutionBook(book);
  assert.equal(selected.positions.length, 1);
  assert.equal(selected.positions[0]?.leg, 'Exchange net (actual)');
});

test('live desk deduplicates identified orders and trades without merging distinct exchange IDs', () => {
  const book = baseBook();
  book.pendingOrders = [
    {
      tradeId: 'bfx-101',
      ageMin: 1,
      side: 'SHORT',
      status: 'ACTIVE',
      qty: 0.03,
      limitPrice: 65_100,
      signalPrice: 65_100,
    },
    {
      tradeId: 'bfx-101',
      ageMin: 1,
      side: 'SHORT',
      status: 'ACTIVE',
      qty: 0.03,
      limitPrice: 65_100,
      signalPrice: 65_100,
    },
    {
      tradeId: 'bfx-102',
      ageMin: 1,
      side: 'SHORT',
      status: 'ACTIVE',
      qty: 0.03,
      limitPrice: 65_100,
      signalPrice: 65_100,
    },
  ];
  book.trades = [
    {
      time: '2026-07-24 17:00:00 AEST',
      tradeId: 'cont-one',
      direction: 'SHORT',
      entry: 65_100,
      exit: 65_000,
      durationMin: 5,
      pnlPct: 1,
      netUsd: 1,
      grossUsd: 1,
      tradeFeesUsd: 0,
      fundingUsd: 0,
      aiBand: 'LIVE',
    },
    {
      time: '2026-07-24 17:00:00 AEST',
      tradeId: 'cont-one',
      direction: 'SHORT',
      entry: 65_100,
      exit: 65_000,
      durationMin: 5,
      pnlPct: 1,
      netUsd: 1,
      grossUsd: 1,
      tradeFeesUsd: 0,
      fundingUsd: 0,
      aiBand: 'LIVE',
    },
  ];

  const selected = selectLiveExecutionBook(book);
  assert.deepEqual(
    selected.pendingOrders.map((order) => order.tradeId),
    ['bfx-101', 'bfx-102'],
  );
  assert.equal(selected.trades.length, 1);
});

test('showcase reference data is never rendered on the live desk', () => {
  assert.equal(shouldShowShowcaseReference('live'), false);
  assert.equal(shouldShowShowcaseReference('showcase'), false);
  assert.equal(shouldShowShowcaseReference('relay-sim'), true);
});
