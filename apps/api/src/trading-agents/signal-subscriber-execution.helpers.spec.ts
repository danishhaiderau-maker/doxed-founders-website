import assert from 'node:assert/strict';
import test from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import {
  canonicalPendingIntentCycles,
  buildRelayExecutorHealth,
  capRelayLimitAtShowcaseFill,
  flatSignedFastPathPreflight,
  sameDirectionPendingSignedFastPathPreflight,
  readPersistedRelayExecutorHealth,
  isBenignShowcaseEntryWait,
  isRecoveredShowcaseOutageError,
  isCycleFreshForRelayArm,
  marketCloseReductionConfirmed,
  mergedDirectionCompatible,
  pendingEntryMayOwnExchangePosition,
  pendingCopyShowcaseDisposition,
  relayEntryOrderIsCompletelyUnfilled,
  relayWatchdogShouldRestart,
  resolveMissedShowcaseFill,
  untrackedActiveOrderIds,
  readFreshSignedShowcaseExactLimit,
  readSignedShowcaseClose,
  relayArmTimestampMs,
  reportableMirrorDiffsForRelayMode,
  shouldPersistLotMetaRepair,
  shouldClearShowcaseStatusError,
} from './signal-subscriber-execution.service';

test('orphan adoption defers to a same-direction pending entry that may own the fill', () => {
  assert.equal(
    pendingEntryMayOwnExchangePosition('SHORT', [
      { direction: 'SHORT', qty: 0.03033 },
    ]),
    true,
  );
  assert.equal(
    pendingEntryMayOwnExchangePosition('SHORT', [
      { direction: 'LONG', qty: 0.03033 },
    ]),
    false,
  );
  assert.equal(
    pendingEntryMayOwnExchangePosition('SHORT', [
      { direction: 'SHORT', qty: 0.000001 },
      { direction: 'SHORT' },
    ]),
    false,
  );
});

test('market close is recorded only after the exchange position is reduced', () => {
  assert.equal(
    marketCloseReductionConfirmed({
      direction: 'SHORT',
      beforeQty: 0.03033,
      closeQty: 0.03033,
      afterAmount: 0,
    }),
    true,
  );
  assert.equal(
    marketCloseReductionConfirmed({
      direction: 'SHORT',
      beforeQty: 0.06066,
      closeQty: 0.03033,
      afterAmount: -0.03033,
    }),
    true,
  );
  assert.equal(
    marketCloseReductionConfirmed({
      direction: 'SHORT',
      beforeQty: 0.03033,
      closeQty: 0.03033,
      afterAmount: -0.03033,
    }),
    false,
  );
  assert.equal(
    marketCloseReductionConfirmed({
      direction: 'SHORT',
      beforeQty: 0.03033,
      closeQty: 0.03033,
      afterAmount: 0.03033,
    }),
    false,
  );
});

test('watchdog cleanup cancels only exchange orders with zero reported fill', () => {
  assert.equal(
    relayEntryOrderIsCompletelyUnfilled({ amountOrig: -0.031, amount: -0.031 }),
    true,
  );
  assert.equal(
    relayEntryOrderIsCompletelyUnfilled({ amountOrig: -0.031, amount: -0.0308 }),
    false,
  );
  assert.equal(
    relayEntryOrderIsCompletelyUnfilled({ amountOrig: 0.031, amount: 0 }),
    false,
  );
});

test('historical orphan recovery ignores orders attributed to current live lots', () => {
  assert.deepEqual(
    untrackedActiveOrderIds(
      [101, 102, 103],
      [
        { bitfinexOrderId: 101 },
        { bitfinexOrderId: 102, stopOrderId: 103 },
      ],
    ),
    [],
  );
  assert.deepEqual(
    untrackedActiveOrderIds([101, 999], [{ bitfinexOrderId: 101 }]),
    [999],
  );
});

test('relay executor health fails closed while starting, stale, or stuck', () => {
  const base = {
    nowMs: 100_000,
    running: false,
    tickStartedAtMs: 0,
    lastTickCompletedAtMs: 0,
    lastTickDurationMs: 0,
    currentInstanceId: null,
    currentStage: null,
    timeoutMs: 60_000,
    healthMaxAgeMs: 15_000,
    timeoutCount: 0,
  };
  assert.equal(buildRelayExecutorHealth(base).status, 'STARTING');
  assert.equal(buildRelayExecutorHealth(base).healthy, false);
  assert.equal(
    buildRelayExecutorHealth({ ...base, lastTickCompletedAtMs: 99_000 }).healthy,
    true,
  );
  assert.equal(
    buildRelayExecutorHealth({ ...base, lastTickCompletedAtMs: 80_000 }).healthy,
    false,
  );
  const stuck = buildRelayExecutorHealth({
    ...base,
    running: true,
    tickStartedAtMs: 30_000,
    lastTickCompletedAtMs: 29_000,
    currentInstanceId: 'instance-1',
    currentStage: 'PROCESS_LIVE_INSTANCE',
  });
  assert.equal(stuck.status, 'STUCK');
  assert.equal(stuck.healthy, false);
});

test('persisted executor health accepts only fresh isolated-worker evidence', () => {
  const now = Date.parse('2026-07-21T11:00:00.000Z');
  const dashboardState = {
    lastTickAt: '2026-07-21T10:59:58.000Z',
    relayExecutor: {
      healthy: true,
      status: 'IDLE',
      running: false,
      observedAt: '2026-07-21T10:59:58.000Z',
      serviceRole: 'executor-worker',
      executionEnabled: true,
      ownerId: 'service:replica',
    },
  };
  const healthy = readPersistedRelayExecutorHealth(dashboardState, now);
  assert.equal(healthy.healthy, true);
  assert.equal(healthy.heartbeatAgeMs, 2_000);

  assert.equal(
    readPersistedRelayExecutorHealth({
      ...dashboardState,
      relayExecutor: { ...dashboardState.relayExecutor, serviceRole: 'public-api' },
    }, now).healthy,
    false,
  );
  assert.equal(
    readPersistedRelayExecutorHealth({
      lastTickAt: '2026-07-21T10:58:00.000Z',
      relayExecutor: {
        ...dashboardState.relayExecutor,
        observedAt: '2026-07-21T10:58:00.000Z',
      },
    }, now).healthy,
    false,
  );
});

test('watchdog keeps the API online when no live Bitfinex relay is active', () => {
  assert.equal(relayWatchdogShouldRestart(0), false);
  assert.equal(relayWatchdogShouldRestart(1), true);
  assert.equal(relayWatchdogShouldRestart(null), true);
});

test('live relay accepts only cycles created after the latest explicit Start', () => {
  const dashboardState = { relayArmedAt: '2026-07-20T01:02:03.000Z' };
  assert.equal(relayArmTimestampMs(dashboardState), Date.parse(dashboardState.relayArmedAt));
  assert.equal(
    isCycleFreshForRelayArm(dashboardState, new Date('2026-07-20T01:02:02.999Z')),
    false,
  );
  assert.equal(
    isCycleFreshForRelayArm(dashboardState, new Date('2026-07-20T01:02:03.000Z')),
    false,
  );
  assert.equal(
    isCycleFreshForRelayArm(dashboardState, new Date('2026-07-20T01:02:03.001Z')),
    true,
  );
});

test('relay arming falls back to legacy confirmation and fails closed when missing', () => {
  assert.equal(
    isCycleFreshForRelayArm(
      { realTradingConfirmedAt: '2026-07-20T01:00:00.000Z' },
      new Date('2026-07-20T01:00:01.000Z'),
    ),
    true,
  );
  assert.equal(isCycleFreshForRelayArm({}, new Date()), false);
  assert.equal(isCycleFreshForRelayArm({ relayArmedAt: 'invalid' }, new Date()), false);
});

test('merged-position tick gate allows same direction and rejects an opposing pair', () => {
  assert.equal(mergedDirectionCompatible(null, 'LONG'), true);
  assert.equal(mergedDirectionCompatible('LONG', 'LONG'), true);
  assert.equal(mergedDirectionCompatible('SHORT', 'SHORT'), true);
  assert.equal(mergedDirectionCompatible('LONG', 'SHORT'), false);
  assert.equal(mergedDirectionCompatible('SHORT', 'LONG'), false);
});

test('paused relay suppresses expected source-only mirror gaps but keeps exposure risks', () => {
  const diffs = [
    { type: 'SHOWCASE_ORDER_NOT_MIRRORED', tradeId: 'cont-new' },
    { type: 'SHOWCASE_POSITION_NOT_MIRRORED', tradeId: 'cont-filled' },
    { type: 'COPY_ORDER_NO_SHOWCASE', tradeId: 'cont-orphan-order' },
    { type: 'COPY_POSITION_NO_SHOWCASE', tradeId: 'cont-orphan-position' },
  ];
  assert.deepEqual(reportableMirrorDiffsForRelayMode(diffs, false), diffs.slice(2));
  assert.deepEqual(reportableMirrorDiffsForRelayMode(diffs, true), diffs);
});

test('active merged book suppresses only the expected opposing showcase leg', () => {
  const diffs = [
    { type: 'SHOWCASE_ORDER_NOT_MIRRORED', tradeId: 'short', showcaseDir: 'SHORT' },
    { type: 'SHOWCASE_ORDER_NOT_MIRRORED', tradeId: 'long', showcaseDir: 'LONG' },
    { type: 'COPY_ORDER_NO_SHOWCASE', tradeId: 'owned-long' },
  ];
  assert.deepEqual(reportableMirrorDiffsForRelayMode(diffs, true, 'LONG'), diffs.slice(1));
});

test('showcase fill makes a still-pending copy fail closed even while relay is paused', () => {
  const bot = {
    orders: [],
    positions: [{ trade_id: 'cont-filled' }],
  } as never;
  assert.equal(
    pendingCopyShowcaseDisposition(bot, 'cont-filled'),
    'MISSED_SHOWCASE_FILL',
  );
  assert.equal(pendingCopyShowcaseDisposition(null, 'cont-filled'), 'SOURCE_UNAVAILABLE');
});

test('a still-resting showcase limit remains eligible for exact-price chase', () => {
  const bot = {
    orders: [
      {
        trade_id: 'cont-pending',
        status: 'PENDING',
        limit_price: 66_000,
      },
    ],
    positions: [],
  } as never;
  assert.equal(
    pendingCopyShowcaseDisposition(bot, 'cont-pending'),
    'SHOWCASE_PENDING',
  );
});

test('full cancel-race fill is recorded and the managed order is not cancelled', async () => {
  let cancelCalls = 0;
  const result = await resolveMissedShowcaseFill({
    managedOrderId: 101,
    detectFill: async () => ({ source: 'POSITION_DELTA', qty: 0.03 }),
    recordFill: async () => true,
    cancelManagedOrder: async () => {
      cancelCalls += 1;
      return { gone: true, attempts: 1 };
    },
  });
  assert.deepEqual(result, { outcome: 'FILL_RECORDED' });
  assert.equal(cancelCalls, 0);
});

test('partial cancel-race fill is recorded before its resting remainder is resolved', async () => {
  let seenSource = '';
  const result = await resolveMissedShowcaseFill({
    managedOrderId: 102,
    detectFill: async () => ({ source: 'ORDER_PARTIAL', qty: 0.01 }),
    recordFill: async (fill) => {
      seenSource = fill.source;
      return true;
    },
    cancelManagedOrder: async () => {
      throw new Error('must not bypass partial-fill reconciliation');
    },
  });
  assert.equal(seenSource, 'ORDER_PARTIAL');
  assert.deepEqual(result, { outcome: 'FILL_RECORDED' });
});

test('fill arriving during cancellation is reconciled before the participant can expire', async () => {
  let checks = 0;
  let recordedQty = 0;
  const result = await resolveMissedShowcaseFill({
    managedOrderId: 106,
    detectFill: async () => {
      checks += 1;
      return checks === 1 ? null : { source: 'POSITION_DELTA', qty: 0.03 };
    },
    recordFill: async (fill) => {
      recordedQty = fill.qty;
      return true;
    },
    cancelManagedOrder: async () => ({ gone: true, reason: 'CANCELLED', attempts: 1 }),
  });
  assert.equal(checks, 2);
  assert.equal(recordedQty, 0.03);
  assert.deepEqual(result, { outcome: 'FILL_RECORDED' });
});

test('failed partial-fill remainder cancellation leaves the participant pending', async () => {
  const result = await resolveMissedShowcaseFill({
    managedOrderId: 103,
    detectFill: async () => ({ source: 'ORDER_PARTIAL' }),
    recordFill: async () => false,
    cancelManagedOrder: async () => {
      throw new Error('outer resolver must not perform a second cancellation');
    },
  });
  assert.deepEqual(result, { outcome: 'PENDING_RETRY_AFTER_FILL' });
});

test('zero-fill missed showcase entry cancels only its managed order then expires', async () => {
  const cancelled: number[] = [];
  const unrelatedManualOrderId = 999;
  const result = await resolveMissedShowcaseFill({
    managedOrderId: 104,
    detectFill: async () => null,
    recordFill: async () => {
      throw new Error('no fill must be recorded');
    },
    cancelManagedOrder: async (orderId) => {
      cancelled.push(orderId);
      return { gone: true, reason: 'CANCELLED', attempts: 1 };
    },
  });
  assert.deepEqual(cancelled, [104]);
  assert.equal(cancelled.includes(unrelatedManualOrderId), false);
  assert.deepEqual(result, {
    outcome: 'EXPIRE_UNFILLED',
    cancelReason: 'CANCELLED',
    cancelAttempts: 1,
  });
});

test('failed zero-fill cancellation stays pending with a blocking outcome', async () => {
  const result = await resolveMissedShowcaseFill({
    managedOrderId: 105,
    detectFill: async () => null,
    recordFill: async () => false,
    cancelManagedOrder: async () => ({
      gone: false,
      reason: 'STILL_LIVE',
      attempts: 3,
    }),
  });
  assert.deepEqual(result, {
    outcome: 'PENDING_CANCEL_FAILED',
    cancelReason: 'STILL_LIVE',
    cancelAttempts: 3,
  });
});

test('exchange verification failure before cancellation cannot expire or cancel the order', async () => {
  let cancelCalls = 0;
  const result = await resolveMissedShowcaseFill({
    managedOrderId: 107,
    detectFill: async () => {
      throw new Error('exchange timeout');
    },
    recordFill: async () => false,
    cancelManagedOrder: async () => {
      cancelCalls += 1;
      return { gone: true, attempts: 1 };
    },
  });
  assert.equal(cancelCalls, 0);
  assert.deepEqual(result, {
    outcome: 'PENDING_FILL_CHECK_FAILED',
    phase: 'BEFORE_CANCEL',
    error: 'exchange timeout',
  });
});

test('exchange verification failure after cancellation leaves an auditable pending retry', async () => {
  let checks = 0;
  const result = await resolveMissedShowcaseFill({
    managedOrderId: 108,
    detectFill: async () => {
      checks += 1;
      if (checks === 2) throw new Error('position history unavailable');
      return null;
    },
    recordFill: async () => false,
    cancelManagedOrder: async () => ({ gone: true, attempts: 1 }),
  });
  assert.deepEqual(result, {
    outcome: 'PENDING_FILL_CHECK_FAILED',
    phase: 'AFTER_CANCEL',
    error: 'position history unavailable',
  });
});

test('signed flat fast path requires an armed and exchange-proven flat hire', () => {
  const safe = {
    status: 'ACTIVE' as const,
    simActive: false,
    hireExpired: false,
    relayArmed: true,
    virtualOpenOrPending: 0,
    exchangeActiveOrders: 0,
    exchangePositionQty: 0,
  };
  assert.equal(flatSignedFastPathPreflight(safe), true);
  assert.equal(flatSignedFastPathPreflight({ ...safe, status: 'PAUSED' }), false);
  assert.equal(flatSignedFastPathPreflight({ ...safe, relayArmed: false }), false);
  assert.equal(flatSignedFastPathPreflight({ ...safe, virtualOpenOrPending: 1 }), false);
  assert.equal(flatSignedFastPathPreflight({ ...safe, exchangeActiveOrders: 1 }), false);
  assert.equal(flatSignedFastPathPreflight({ ...safe, exchangePositionQty: 0.000001 }), false);
  assert.equal(flatSignedFastPathPreflight({ ...safe, exchangePositionQty: 0.01 }), false);
});

test('signed pending fast path allows only fully attributed same-direction resting orders', () => {
  const safe = {
    status: 'ACTIVE' as const,
    simActive: false,
    hireExpired: false,
    relayArmed: true,
    exchangePositionQty: 0,
    candidateDirection: 'SHORT' as const,
    maxConcurrent: 3,
    virtualLots: [
      {
        status: SignalCycleStatus.PENDING_ENTRY,
        direction: 'SHORT' as const,
        bitfinexOrderId: 101,
      },
    ],
    exchangeActiveOrderIds: [101],
  };
  assert.equal(sameDirectionPendingSignedFastPathPreflight(safe), true);
  assert.equal(
    sameDirectionPendingSignedFastPathPreflight({
      ...safe,
      candidateDirection: 'LONG',
    }),
    false,
  );
  assert.equal(
    sameDirectionPendingSignedFastPathPreflight({
      ...safe,
      exchangeActiveOrderIds: [101, 999],
    }),
    false,
  );
  assert.equal(
    sameDirectionPendingSignedFastPathPreflight({
      ...safe,
      virtualLots: [{ ...safe.virtualLots[0], status: SignalCycleStatus.OPEN }],
    }),
    false,
  );
  assert.equal(
    sameDirectionPendingSignedFastPathPreflight({
      ...safe,
      exchangePositionQty: -0.03,
    }),
    false,
  );
  assert.equal(
    sameDirectionPendingSignedFastPathPreflight({
      ...safe,
      maxConcurrent: 1,
    }),
    false,
  );
});

test('showcase fill cap permits improvement but never a worse copied entry', () => {
  assert.equal(capRelayLimitAtShowcaseFill('LONG', 64_010, 64_000), 64_000);
  assert.equal(capRelayLimitAtShowcaseFill('LONG', 63_900, 64_000), 63_900);
  assert.equal(capRelayLimitAtShowcaseFill('SHORT', 63_990, 64_000), 64_000);
  assert.equal(capRelayLimitAtShowcaseFill('SHORT', 64_100, 64_000), 64_100);
});

test('repairs a legacy catch-up lot that has qty but no direction', () => {
  assert.equal(
    shouldPersistLotMetaRepair({ qty: 0.03104 }, { qty: 0.03104, direction: 'SHORT' }),
    true,
  );
});

test('repairs missing quantity when direction is already durable', () => {
  assert.equal(
    shouldPersistLotMetaRepair({ direction: 'LONG' }, { qty: 0.02, direction: 'LONG' }),
    true,
  );
});

test('does not append duplicate repair events for complete metadata', () => {
  assert.equal(
    shouldPersistLotMetaRepair(
      { qty: 0.02, direction: 'LONG' },
      { qty: 0.02, direction: 'LONG' },
    ),
    false,
  );
});

for (const message of [
  'Showcase trade is not present in the current canonical book.',
  'Waiting for the showcase to publish its exact resting limit.',
  'Showcase filled before copy entry; market catch-up is prohibited.',
  'Showcase bot has not placed a limit yet (virtual defer / chase bucket) — relay waiting.',
  'Waiting for showcase limit.',
]) {
  test(`classifies expected strategy wait text as non-error: ${message}`, () => {
    assert.equal(isBenignShowcaseEntryWait(message), true);
  });
}

test('recognizes only historical F1 outage text for recovery clearing', () => {
  assert.equal(
    isRecoveredShowcaseOutageError(
      'Showcase unreachable for 62s — live copy in safe mode: no new entries (F1); open lots will be closed past 120s (F2).',
    ),
    true,
  );
  assert.equal(
    isRecoveredShowcaseOutageError('RECONCILE ALERT: exchange 0.03 BTC ≠ ledger 0 BTC'),
    false,
  );
  assert.equal(isRecoveredShowcaseOutageError(null), false);
});

test('clears a persisted F1 warning after restart only when no outage is tracked', () => {
  const message =
    'Showcase unreachable for 61s — live copy in safe mode: no new entries (F1).';
  assert.equal(
    shouldClearShowcaseStatusError({
      message,
      hadTrackedOutage: false,
      recoveredNow: false,
    }),
    true,
  );
  assert.equal(
    shouldClearShowcaseStatusError({
      message,
      hadTrackedOutage: true,
      recoveredNow: false,
    }),
    false,
  );
  assert.equal(
    shouldClearShowcaseStatusError({
      message,
      hadTrackedOutage: true,
      recoveredNow: true,
    }),
    true,
  );
});

for (const message of [
  'Showcase bridge unavailable — exact-copy entry blocked.',
  'RECONCILE ALERT: exchange 0.03 BTC ≠ ledger 0 BTC',
  'CANCEL_FAILED_ORDER_STILL_LIVE',
  null,
]) {
  test(`preserves operational and safety errors: ${message}`, () => {
    assert.equal(isBenignShowcaseEntryWait(message), false);
  });
}

test('ignores stale intents and selects only trades resting in the canonical order book', () => {
  const stale = { tradeId: 'cont-stale', createdAt: new Date('2026-07-19T12:00:00Z') };
  const live = { tradeId: 'cont-live', createdAt: new Date('2026-07-19T12:05:00Z') };
  const result = canonicalPendingIntentCycles([stale, live], {
    orders: [
      {
        trade_id: 'cont-live',
        status: 'PENDING',
        limit_price: 64_500,
      },
    ],
  });
  assert.deepEqual(result, [live]);
});

test('rejects terminal or price-less canonical orders', () => {
  const cycles = [
    { tradeId: 'cont-filled', createdAt: new Date('2026-07-19T12:00:00Z') },
    { tradeId: 'cont-no-price', createdAt: new Date('2026-07-19T12:01:00Z') },
  ];
  const result = canonicalPendingIntentCycles(cycles, {
    orders: [
      { trade_id: 'cont-filled', status: 'FILLED', limit_price: 64_500 },
      { trade_id: 'cont-no-price', status: 'PENDING', limit_price: 0 },
    ],
  });
  assert.deepEqual(result, []);
});

test('preserves canonical book order when more than one live intent is ready', () => {
  const firstCreated = { tradeId: 'cont-a', createdAt: new Date('2026-07-19T12:00:00Z') };
  const secondCreated = { tradeId: 'cont-b', createdAt: new Date('2026-07-19T12:01:00Z') };
  const result = canonicalPendingIntentCycles([firstCreated, secondCreated], {
    orders: [
      { trade_id: 'cont-b', status: 'ORDERED', limit_price: 64_600 },
      { trade_id: 'cont-a', status: 'PENDING', limit_price: 64_500 },
    ],
  });
  assert.deepEqual(result, [secondCreated, firstCreated]);
});

test('accepts a fresh HMAC-verified exact showcase resting limit', () => {
  const now = Date.parse('2026-07-20T01:02:03.000Z');
  assert.deepEqual(
    readFreshSignedShowcaseExactLimit(
      'cont-fast',
      {
        schema: 'dcf-signal-intent/v1',
        action: 'ENTER',
        direction: 'SHORT',
        entry: { exact_limit_price: 64_555.25 },
        context: {
          signed_showcase_event: true,
          showcase_event: 'ORDER_PLACED',
          showcase_event_at: '2026-07-20T01:02:01.750Z',
          platform_received_at: '2026-07-20T01:02:02.250Z',
        },
      },
      now,
    ),
    {
      tradeId: 'cont-fast',
      direction: 'SHORT',
      limitPrice: 64_555.25,
      receivedAtMs: Date.parse('2026-07-20T01:02:02.250Z'),
      sourceEventAtMs: Date.parse('2026-07-20T01:02:01.750Z'),
    },
  );
});

test('rejects unsigned, stale, and non-resting exact-limit events', () => {
  const now = Date.parse('2026-07-20T01:02:30.000Z');
  const base = {
    schema: 'dcf-signal-intent/v1',
    action: 'ENTER',
    direction: 'LONG',
    entry: { exact_limit_price: 64_500 },
    context: {
      signed_showcase_event: true,
      showcase_event: 'ORDER_PLACED',
      platform_received_at: '2026-07-20T01:02:29.000Z',
    },
  };
  assert.equal(
    readFreshSignedShowcaseExactLimit('cont-unsigned', {
      ...base,
      context: { ...base.context, signed_showcase_event: false },
    }, now),
    null,
  );
  assert.equal(
    readFreshSignedShowcaseExactLimit('cont-stale', {
      ...base,
      context: {
        ...base.context,
        platform_received_at: '2026-07-20T01:02:00.000Z',
      },
    }, now),
    null,
  );
  assert.equal(
    readFreshSignedShowcaseExactLimit('cont-approve', {
      ...base,
      context: { ...base.context, showcase_event: 'APPROVE_PENDING' },
    }, now),
    null,
  );
});

test('reads a signed showcase close without another canonical-state fetch', () => {
  assert.deepEqual(
    readSignedShowcaseClose({
      context: {
        signed_showcase_event: true,
        showcase_event: 'POSITION_CLOSED',
        showcase_exit_price: 64_444.25,
        showcase_exit_reason: 'PROFIT_LOCK_LADDER',
      },
    }),
    {
      exitPrice: 64_444.25,
      exitReason: 'PROFIT_LOCK_LADDER',
    },
  );
  assert.equal(
    readSignedShowcaseClose({
      context: {
        signed_showcase_event: false,
        showcase_event: 'POSITION_CLOSED',
      },
    }),
    null,
  );
});
