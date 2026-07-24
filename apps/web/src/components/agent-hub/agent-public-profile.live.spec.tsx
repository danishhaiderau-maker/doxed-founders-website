import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

(globalThis as typeof globalThis & { React: typeof React }).React = React;

const count = (text: string, needle: string) => text.split(needle).length - 1;

test('signed-in live desk shows each Bitfinex entity once and no showcase panels', async () => {
  const { AgentPublicProfile } = await import('./agent-public-profile');
  const liveBook = {
    activeSignals: [
      {
        time: '2026-07-24 17:00:00 AEST',
        direction: 'SHORT',
        confidence: 80,
        regime: 'trend',
        strategy: 'copy',
        trigger: 'relay',
        pullRequiredPct: 0,
        signalPrice: 65_100,
        maxPullPct: 0,
        outcome: 'OPEN',
        fillPrice: 65_100,
        exitReason: null,
      },
    ],
    positions: [
      {
        leg: 'Exchange net (actual)',
        side: 'SHORT',
        qty: 0.03123,
        entry: 65_100,
        current: 65_000,
        stopLoss: 66_000,
        takeProfit: 0,
        pnlUsd: 3.12,
      },
      {
        leg: 'Tracked lot virtual-1',
        side: 'SHORT',
        qty: 0.03123,
        entry: 65_100,
        current: 65_000,
        stopLoss: 66_000,
        takeProfit: 0,
        pnlUsd: 3.12,
      },
    ],
    pendingOrders: [
      {
        tradeId: 'bfx-order-101',
        ageMin: 1,
        side: 'SHORT',
        status: 'ACTIVE',
        qty: 0.03123,
        limitPrice: 65_100,
        signalPrice: 65_100,
      },
      {
        tradeId: 'bfx-order-101',
        ageMin: 1,
        side: 'SHORT',
        status: 'ACTIVE',
        qty: 0.03123,
        limitPrice: 65_100,
        signalPrice: 65_100,
      },
    ],
    expiredOrders: [],
    trades: [
      {
        time: '2026-07-24 17:01:00 AEST',
        tradeId: 'bfx-close-201',
        direction: '—',
        entry: 0,
        exit: 0,
        durationMin: 1,
        pnlPct: 0,
        netUsd: 2.25,
        grossUsd: 2.25,
        tradeFeesUsd: 0,
        fundingUsd: 0,
        aiBand: 'EXCHANGE',
      },
      {
        time: '2026-07-24 17:01:00 AEST',
        tradeId: 'bfx-close-201',
        direction: '—',
        entry: 0,
        exit: 0,
        durationMin: 1,
        pnlPct: 0,
        netUsd: 2.25,
        grossUsd: 2.25,
        tradeFeesUsd: 0,
        fundingUsd: 0,
        aiBand: 'EXCHANGE',
      },
    ],
  };
  const dashboard = {
    currentAction: 'MONITORING',
    currentPrice: 65_000,
    openTrades: [],
    pendingOrders: [],
    recentTrades: [],
    marketStructure: '',
    aiReasoning: '',
    riskStatus: 'NORMAL',
    fundingStatus: '',
    dataSource: '',
    wsHealth: '',
    dataQuality: '',
    pnl: { daily: 0, total: 0 },
    leverage: 100,
    aiDecision: 'WAIT',
    noTradeReason: '',
    currentEdge: 0,
    requiredEdge: 1,
    currentThinking: { conclusion: 'Waiting', market: 'trend' },
    liveBook,
  };
  const agent = {
    id: 'agent-1',
    slug: 'conservative-btc',
    name: 'Conservative BTC Agent',
    status: 'ACTIVE',
    balanceUsd: 261.64,
    equityUsd: 264.76,
    sessionPnlUsd: 2.25,
    unrealizedPnlUsd: 3.12,
    dailyPnlUsd: 2.25,
    netReturnPct: 0.85,
    tradeCount: 1,
    winRatePct: 100,
    costDdollarWeek: 2_000,
    userSessionStartedAt: '2026-07-24T06:00:00.000Z',
    exchangeBalanceUsd: 261.64,
    openPositionSide: 'SHORT',
  };

  const html = renderToStaticMarkup(
    React.createElement(AgentPublicProfile, {
      slug: 'conservative-btc',
      agent,
      dashboard,
      activity: [],
      allAgents: [],
      following: true,
      hired: true,
      signedIn: true,
      isAdmin: false,
      botConnected: true,
      executionPaused: false,
      publicStatus: 'ONLINE',
      instanceStatus: 'ACTIVE',
      instanceMode: 'live',
      exchangeProvider: 'bitfinex',
      exchangeLabel: 'Bitfinex',
      exchangeConnected: true,
      viewScope: 'user',
      showcaseNote: 'showcase-only-note',
      showcaseFlash: {
        tone: 'live-testing',
        title: 'showcase-only-flash',
        summary: 'showcase-only-flash-body',
      },
      showcaseAgent: agent,
      showcaseLiveBook: dashboard.liveBook,
      exchangeLiveBook: liveBook,
      relaySimLiveBook: null,
      copyRelaySim: null,
      showcaseActivity: [],
      userActivity: [],
    } as never),
  );

  for (const forbidden of [
    'From last Fresh Collection wipeout',
    'Admin research update',
    'Global showcase bot',
    'Drift vs showcase',
    'Behind showcase',
    'Ahead of showcase',
    'Last relay signal',
    'Tracked lot',
    'Showcase bot feed',
    'showcase-only-note',
  ]) {
    assert.equal(html.includes(forbidden), false, forbidden);
  }
  assert.equal(count(html, 'bfx-order-101'), 1);
  assert.equal(count(html, 'bfx-close-201'), 1);
  assert.equal(count(html, 'Exchange net (actual)'), 1);
  assert.match(html, /Your Bitfinex live session/);
  assert.match(html, /Completed trades/);
});
