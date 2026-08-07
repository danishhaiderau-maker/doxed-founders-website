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

  const adminShowcaseHtml = renderToStaticMarkup(
    React.createElement(AgentPublicProfile, {
      slug: 'conservative-btc',
      agent,
      dashboard,
      activity: [],
      allAgents: [],
      following: true,
      hired: true,
      signedIn: true,
      isAdmin: true,
      botConnected: true,
      executionPaused: false,
      publicStatus: 'online',
      instanceStatus: 'ACTIVE',
      instanceMode: 'copy',
      exchangeProvider: null,
      exchangeLabel: null,
      exchangeConnected: false,
      viewScope: 'showcase',
      showcaseAgent: agent,
      showcaseLiveBook: dashboard.liveBook,
      exchangeLiveBook: null,
      relaySimLiveBook: null,
      copyRelaySim: null,
      showcaseActivity: [],
      userActivity: [],
    } as never),
  );

  assert.match(adminShowcaseHtml, /Showcase live/);
  assert.match(adminShowcaseHtml, /Admin showcase \(observe only\)/);
  assert.equal(adminShowcaseHtml.includes('Legacy DDollar paper'), false);
});

test('showcase reports a healthy REST fallback and renders canonical AI decisions', async () => {
  const { AgentPublicProfile } = await import('./agent-public-profile');
  const liveBook = {
    activeSignals: [],
    positions: [],
    pendingOrders: [],
    expiredOrders: [],
    trades: [],
  };
  const dashboard = {
    currentAction: 'WAITING',
    currentPrice: 64_200,
    regime: 'BULL',
    support: 64_000,
    resistance: 64_500,
    distanceToResistancePct: 0.47,
    distanceToSupportPct: 0.31,
    currentPosition: 'NONE',
    aiDecision: 'APPROVE',
    aiWinProbability: 0,
    currentEdge: 3.4,
    requiredEdge: 3,
    noTradeReason: '',
    currentThinking: {
      market: 'BULL',
      support: 64_000,
      resistance: 64_500,
      distanceToResistancePct: 0.47,
      distanceToSupportPct: 0.31,
      conclusion: 'LONG',
    },
    transparency: {
      currentEdge: 3.4,
      requiredEdge: 3,
      currentState: 'READY',
      reason: '',
    },
    openTrades: [],
    pendingOrders: [],
    recentTrades: [],
    marketStructure: 'BULL_ALIGNED',
    aiReasoning: 'Shared direction approved LONG.',
    latestAiVerdict: {
      decision: 'APPROVE',
      direction: 'LONG',
      longScore: 65,
      shortScore: 35,
      rawScoreGap: 30,
      gapBucket: 3,
      winProbability: 0,
      reason: 'Shared direction approved LONG.',
      comment: 'Shared direction approved LONG.',
      blockReason: null,
      edgeScore: 3.4,
      requiredEdge: 3,
      marketRegime: 'BULL',
      updatedAt: '2026-07-30T03:35:00.000Z',
    },
    pendingApproval: {
      tradeId: 'cont-gap-30',
      status: 'APPROVE_PENDING',
      direction: 'LONG',
      reason: 'Waiting for selected chase bucket 3',
      rawScoreGap: 30,
      gapBucket: 3,
      chaseCount: 2,
      selectedChaseBuckets: [3, 4],
      exactLimitPrice: null,
      entryLimitPolicy: 'micro_sr_structural_limit_v1',
      updatedAt: '2026-07-30T03:35:00.000Z',
    },
    riskStatus: 'NORMAL',
    fundingStatus: '',
    dataSource: 'bitfinex_rest',
    wsHealth: 'REST_FALLBACK',
    dataQuality: '100%',
    pnl: { daily: 0, total: 0 },
    leverage: 100,
    liveBook,
    stateIntegrity: {
      snapshot_seq: 177,
      snapshot_ts: '2026-07-30T03:36:23.000Z',
      snapshot_age_sec: 2,
      bot_version: 'v15-typeb-opportunity-v2',
      exchange: 'bitfinex',
      symbol: 'tBTCF0:USTF0',
      ws_connected: false,
      ws_status: 'REST_FALLBACK',
      ws_connected_sec_ago: null,
      rest_healthy: true,
      price_age_sec: 3,
      book_age_sec: null,
      orders_synced: true,
      positions_synced: true,
      trades_synced: true,
      last_fill_sec_ago: null,
      execution_paused: false,
      live_armed: false,
      bitfinex_live_enabled: false,
      genome_recorder: 'ACTIVE',
      research_db: true,
    },
    dataWindowHours: 82.1,
    botUptimeHours: 12.3,
    snapshotSource: 'railway_cache',
  };
  const agent = {
    id: 'agent-1',
    slug: 'conservative-btc',
    name: 'Conservative BTC Agent',
    status: 'ACTIVE',
    balanceUsd: 500,
    equityUsd: 500,
    sessionPnlUsd: 0,
    unrealizedPnlUsd: 0,
    dailyPnlUsd: 0,
    netReturnPct: 0,
    tradeCount: 0,
    winRatePct: 0,
    costDdollarWeek: 2_000,
  };
  const aiDecision = {
    id: 'ai-scan-1',
    type: 'AI_APPROVED',
    title: 'AI Approved',
    reason: 'Shared direction approved LONG.',
    outcome: 'LONG',
    profitPct: null,
    edgeScore: 3.4,
    edgeRequired: 3,
    marketRegime: 'BULL',
    shareText: null,
    createdAt: '2026-07-30T03:35:00.000Z',
  };

  const html = renderToStaticMarkup(
    React.createElement(AgentPublicProfile, {
      slug: 'conservative-btc',
      agent,
      dashboard,
      activity: [aiDecision],
      allAgents: [],
      following: true,
      hired: false,
      signedIn: true,
      isAdmin: true,
      botConnected: true,
      executionPaused: false,
      publicStatus: 'online',
      viewScope: 'showcase',
      showcaseAgent: agent,
      showcaseLiveBook: liveBook,
      exchangeLiveBook: null,
      relaySimLiveBook: null,
      copyRelaySim: null,
      showcaseActivity: [aiDecision],
      userActivity: [],
    } as never),
  );

  assert.match(html, /Fly bot online · REST price fallback/);
  assert.match(html, /AI Approved/);
  assert.match(html, /Shared direction approved LONG/);
  assert.match(html, /Latest reasoning/);
  assert.match(html, /Fly\.io is the sole AI, strategy, and trading owner/);
  assert.equal(html.includes('Home PC command center'), false);
  assert.equal(html.includes('30% confidence'), false);
  assert.equal(html.includes('Last 5 AI decisions in detail'), false);
  assert.equal(html.includes('Latest direction evaluation'), false);
  assert.equal(html.includes('Performance · 30D'), false);
  assert.equal(html.includes('Trade journey'), false);
});
