import assert from 'node:assert/strict';
import test from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import {
  btcToSats,
  effectiveExchangeQtyBtc,
  rawExchangeQtyBtc,
  relayPositionDeltaSats,
} from '@dcf/utils';
import {
  BITFINEX_POSITION_CLOSE_FLAG,
  BITFINEX_REDUCE_ONLY_FLAG,
  BITFINEX_SAFE_CLOSE_FLAGS,
} from '../exchanges/bitfinex-api.client';
import {
  SignalSubscriberExecutionService,
  canonicalPendingIntentCycles,
  buildRelayExecutorHealth,
  capRelayLimitAtShowcaseFill,
  flatSignedFastPathPreflight,
  sameDirectionPendingSignedFastPathPreflight,
  readPersistedRelayExecutorHealth,
  isBenignShowcaseEntryWait,
  isRecoveredShowcaseOutageError,
  isCycleFreshForRelayArm,
  exchangeOrderFilledQtySats,
  marketCloseReductionConfirmed,
  mergedDirectionCompatible,
  pendingEntryMayOwnExchangePosition,
  pendingFillReconcileDecision,
  showcasePositionAbsenceActionable,
  pendingCopyShowcaseDisposition,
  missedShowcaseFillWithinSettlementGrace,
  relayEntryOrderIsCompletelyUnfilled,
  relayLotExitTarget,
  relayWatchdogShouldRestart,
  resolveMissedShowcaseFill,
  untrackedActiveOrderIds,
  readFreshSignedShowcaseExactLimit,
  readSignedShowcaseClose,
  relayArmTimestampMs,
  reportableMirrorDiffsForRelayMode,
  shouldPersistLotMetaRepair,
  shouldRetryImmediateFlatReconcile,
  shouldClearShowcaseStatusError,
  sourceEntityCreatedAtMs,
  advanceLiveFidelityGuard,
  isLiveFidelityGuardEnabled,
  isFreshCanonicalFidelityBotState,
  isFreshExactFidelityReconcile,
  liveRelayFidelityObservation,
  LIVE_FIDELITY_GUARD_THRESHOLD_PCT,
  resolveShowcaseRelinkForRealFill,
  SHOWCASE_RELINK_PRICE_BAND_PCT,
  SHOWCASE_RELINK_TIME_WINDOW_MS,
  resolveShowcaseMirrorTradeIdFromInputs,
} from './signal-subscriber-execution.service';

test('live fidelity guard kill-switch defaults on and accepts explicit off values', () => {
  assert.equal(isLiveFidelityGuardEnabled(undefined), true);
  assert.equal(isLiveFidelityGuardEnabled(''), true);
  assert.equal(isLiveFidelityGuardEnabled('true'), true);
  assert.equal(isLiveFidelityGuardEnabled('1'), true);
  assert.equal(isLiveFidelityGuardEnabled('false'), false);
  assert.equal(isLiveFidelityGuardEnabled('0'), false);
  assert.equal(isLiveFidelityGuardEnabled('OFF'), false);
  assert.equal(isLiveFidelityGuardEnabled(' no '), false);
});

test('live fidelity guard needs three fresh low observations spanning at least 90 seconds', () => {
  const relayArmedAt = '2026-07-31T00:00:00.000Z';
  const start = Date.parse(relayArmedAt);
  const first = advanceLiveFidelityGuard({
    previous: null,
    nowMs: start,
    activeLive: true,
    relayArmedAt,
    evidenceFresh: true,
    scorePct: 40,
    comparisonCount: 5,
  });
  assert.equal(first.shouldTrip, false);
  assert.equal(first.state.status, 'LOW_PENDING');
  assert.equal(first.state.lowObservationCount, 1);
  assert.equal(first.state.breachStartedAt, relayArmedAt);

  const duplicate = advanceLiveFidelityGuard({
    previous: first.state,
    nowMs: start + 10_000,
    activeLive: true,
    relayArmedAt,
    evidenceFresh: true,
    scorePct: 20,
    comparisonCount: 5,
  });
  assert.equal(duplicate.observationAccepted, false);
  assert.equal(duplicate.state.lowObservationCount, 1);

  const second = advanceLiveFidelityGuard({
    previous: first.state,
    nowMs: start + 45_000,
    activeLive: true,
    relayArmedAt,
    evidenceFresh: true,
    scorePct: 50,
    comparisonCount: 6,
  });
  assert.equal(second.shouldTrip, false);
  assert.equal(second.state.lowObservationCount, 2);

  const third = advanceLiveFidelityGuard({
    previous: second.state,
    nowMs: start + 90_000,
    activeLive: true,
    relayArmedAt,
    evidenceFresh: true,
    scorePct: 59.99,
    comparisonCount: 7,
  });
  assert.equal(third.shouldTrip, true);
  assert.equal(third.state.status, 'TRIPPED');
  assert.equal(third.state.lowObservationCount, 3);
  assert.equal(third.state.thresholdPct, LIVE_FIDELITY_GUARD_THRESHOLD_PCT);
});

test('live fidelity guard resets on recovery, inactivity, stale evidence, empty data, and a new arm epoch', () => {
  const relayArmedAt = '2026-07-31T00:00:00.000Z';
  const start = Date.parse(relayArmedAt);
  const low = advanceLiveFidelityGuard({
    previous: null,
    nowMs: start,
    activeLive: true,
    relayArmedAt,
    evidenceFresh: true,
    scorePct: 10,
    comparisonCount: 1,
  }).state;

  const recovered = advanceLiveFidelityGuard({
    previous: low,
    nowMs: start + 30_000,
    activeLive: true,
    relayArmedAt,
    evidenceFresh: true,
    scorePct: 60,
    comparisonCount: 1,
  });
  assert.equal(recovered.state.status, 'HEALTHY');
  assert.equal(recovered.state.lowObservationCount, 0);

  const stale = advanceLiveFidelityGuard({
    previous: low,
    nowMs: start + 30_000,
    activeLive: true,
    relayArmedAt,
    evidenceFresh: false,
    scorePct: 1,
    comparisonCount: 10,
  });
  assert.equal(stale.shouldTrip, false);
  assert.equal(stale.state.lowObservationCount, 0);
  assert.equal(stale.state.lastResetReason, 'EVIDENCE_STALE_OR_UNKNOWN');

  const empty = advanceLiveFidelityGuard({
    previous: low,
    nowMs: start + 30_000,
    activeLive: true,
    relayArmedAt,
    evidenceFresh: true,
    scorePct: null,
    comparisonCount: 0,
  });
  assert.equal(empty.shouldTrip, false);
  assert.equal(empty.state.lastResetReason, 'NO_MEANINGFUL_COMPARISON_DATA');

  const inactive = advanceLiveFidelityGuard({
    previous: low,
    nowMs: start + 30_000,
    activeLive: false,
    relayArmedAt: null,
    evidenceFresh: true,
    scorePct: 0,
    comparisonCount: 10,
  });
  assert.equal(inactive.shouldTrip, false);
  assert.equal(inactive.state.status, 'IDLE');
  assert.equal(inactive.state.lastResetReason, 'LIVE_RELAY_INACTIVE');

  const rearmed = advanceLiveFidelityGuard({
    previous: low,
    nowMs: start + 120_000,
    activeLive: true,
    relayArmedAt: '2026-07-31T00:02:00.000Z',
    evidenceFresh: true,
    scorePct: 10,
    comparisonCount: 1,
  });
  assert.equal(rearmed.shouldTrip, false);
  assert.equal(rearmed.state.lowObservationCount, 1);
  assert.equal(rearmed.state.breachStartedAt, '2026-07-31T00:02:00.000Z');
});

test('live fidelity evidence rejects noncanonical, stale, errored, and mismatched reconcile data', () => {
  const now = Date.parse('2026-07-31T00:00:30.000Z');
  const bot = {
    dashboard_owner: true,
    dashboard_port: 7002,
    bot_instance_id: 'fly-machine-1',
    source_git_rev: 'abc123',
    server_ts: '2026-07-31T00:00:20.000Z',
  };
  assert.equal(isFreshCanonicalFidelityBotState(bot as never, now), true);
  assert.equal(
    isFreshCanonicalFidelityBotState(
      { ...bot, dashboard_owner: false } as never,
      now,
    ),
    false,
  );
  assert.equal(
    isFreshCanonicalFidelityBotState(
      { ...bot, server_ts: '2026-07-30T23:59:00.000Z' } as never,
      now,
    ),
    false,
  );
  assert.equal(
    isFreshCanonicalFidelityBotState(
      { ...bot, api_state_error: 'snapshot incomplete' } as never,
      now,
    ),
    false,
  );

  const reconcile = {
    updatedAt: '2026-07-31T00:00:20.000Z',
    alert: false,
    deltaBtc: 0,
  };
  assert.equal(isFreshExactFidelityReconcile(reconcile, now), true);
  assert.equal(
    isFreshExactFidelityReconcile({ ...reconcile, deltaBtc: 0.00000001 }, now),
    false,
  );
  assert.equal(
    isFreshExactFidelityReconcile(
      { ...reconcile, updatedAt: '2026-07-30T23:59:00.000Z' },
      now,
    ),
    false,
  );
});

test('live relay fidelity counts actionable identity/gap defects but excludes offline misses', () => {
  const observation = liveRelayFidelityObservation({
    rows: [
      {
        tradeId: 'cont-good',
        localBotTradeId: 'cont-good',
        matchKind: 'exact',
        bitfinexEntry: 64_000,
        showcaseEntry: 64_000,
        bitfinexExit: 64_100,
        showcaseExit: 64_100,
      },
      {
        tradeId: 'cont-orphan',
        localBotTradeId: null,
        matchKind: 'none',
        bitfinexEntry: 64_000,
        showcaseEntry: null,
        bitfinexExit: null,
        showcaseExit: null,
      },
    ],
    summary: {
      unmatchedShowcaseCount: 1,
      unmatchedShowcaseOfflineCount: 99,
    },
  } as never);
  assert.equal(observation.comparisonCount, 3);
  assert.equal(observation.scorePct, 33.33);
  assert.deepEqual(
    liveRelayFidelityObservation({
      rows: [],
      summary: {
        unmatchedShowcaseCount: 0,
        unmatchedShowcaseOfflineCount: 20,
      },
    } as never),
    { scorePct: null, comparisonCount: 0 },
  );
});

test('retries immediate-flat proof only for an unambiguous managed exit race', () => {
  const base = {
    signedExchangeAmount: 0,
    signedLedgerOpenAmount: -0.03085,
    openLots: 1,
    pendingLots: 0,
    directionConflict: false,
    foreignActiveOrders: 0,
  };
  assert.equal(shouldRetryImmediateFlatReconcile(base), true);
  assert.equal(
    shouldRetryImmediateFlatReconcile({ ...base, signedExchangeAmount: -0.03085 }),
    false,
  );
  assert.equal(
    shouldRetryImmediateFlatReconcile({ ...base, signedLedgerOpenAmount: 0 }),
    false,
  );
  assert.equal(
    shouldRetryImmediateFlatReconcile({ ...base, pendingLots: 1 }),
    false,
  );
  assert.equal(
    shouldRetryImmediateFlatReconcile({ ...base, directionConflict: true }),
    false,
  );
  assert.equal(
    shouldRetryImmediateFlatReconcile({ ...base, foreignActiveOrders: 1 }),
    false,
  );
});

test('showcase close fails closed when linked entry remainder state is unreadable', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  let cancelCalled = false;
  service.activeTrading = {
    findOrder: async () => {
      throw new Error('Bitfinex order read unavailable');
    },
  };
  service.cancelManagedOrderGone = async () => {
    cancelCalled = true;
    return { gone: true, attempts: 1 };
  };

  await assert.rejects(
    service.cancelLinkedPendingLimits({}, { bitfinexOrderId: 123 }),
    /Bitfinex order read unavailable/,
  );
  assert.equal(cancelCalled, false);
});

test('showcase close cancels and confirms a live managed entry remainder', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  let cancelledOrderId: number | null = null;
  service.activeTrading = {
    findOrder: async () => ({
      id: 456,
      amount: -0.00002,
      amountOrig: -0.00004,
      price: 65_000,
    }),
  };
  service.cancelManagedOrderGone = async (
    _creds: unknown,
    orderId: number,
  ) => {
    cancelledOrderId = orderId;
    return { gone: true, attempts: 1 };
  };

  await service.cancelLinkedPendingLimits({}, { bitfinexOrderId: 456 });
  assert.equal(cancelledOrderId, 456);
});

test('showcase close refuses EXIT when linked entry cancellation is unconfirmed', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.activeTrading = {
    findOrder: async () => ({
      id: 789,
      amount: 0.00004,
      amountOrig: 0.00004,
      price: 65_000,
    }),
  };
  service.cancelManagedOrderGone = async () => ({
    gone: false,
    attempts: 3,
    reason: 'exchange unavailable',
  });

  await assert.rejects(
    service.cancelLinkedPendingLimits({}, { bitfinexOrderId: 789 }),
    /LINKED_ENTRY_CANCEL_FAILED/,
  );
});

test('managed cancel requires an active-book read even after cancel acknowledgement', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.logger = { error: () => {}, warn: () => {} };
  service.activeTrading = {
    cancelOrder: async () => {},
    findOrder: async () => ({ id: 901 }),
  };

  assert.deepEqual(
    await service.cancelManagedOrderGone({}, 901, 'test cancel'),
    {
      gone: false,
      reason: 'CANCEL_ACK_NOT_CONFIRMED',
      attempts: 1,
    },
  );

  service.activeTrading.findOrder = async () => null;
  assert.deepEqual(
    await service.cancelManagedOrderGone({}, 901, 'test cancel'),
    {
      gone: true,
      reason: undefined,
      attempts: 1,
    },
  );
});

function buildImmediateFlatHarness(
  opts: {
    findOrder: () => Promise<any>;
    postCancelPosition: { amount: number } | null;
  },
) {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  let positionReadCount = 0;
  let exitRecorded = false;
  let paused = false;
  service.activeTrading = {
    getOpenPositionDetail: async () => {
      positionReadCount += 1;
      return positionReadCount === 1 ? null : opts.postCancelPosition;
    },
    getMarkPrice: async () => 65_000,
    findOrder: opts.findOrder,
  };
  service.prisma = {
    signalCycleParticipant: {
      findMany: async () => [{
        id: 'participant-1',
        cycleId: 'cycle-1',
        fillPrice: 65_000,
        cycle: {},
      }],
    },
  };
  service.hasParticipantExited = async () => false;
  service.loadExecutionMeta = async () => ({
    bitfinexOrderId: 321,
    direction: 'LONG',
    qty: 0.00004,
    limitPrice: 65_000,
  });
  service.resolveAlreadyFlatPnl = async () => ({
    pnlUsd: 0,
    pnlSource: 'reconstructed',
  });
  service.cancelManagedOrderGone = async () => ({ gone: true, attempts: 1 });
  service.pauseUserRelayForPositionMismatch = async () => {
    paused = true;
  };
  service.cycles = {
    recordHireExecutionEvent: async () => {
      exitRecorded = true;
    },
  };
  service.logger = { log: () => {}, warn: () => {} };

  return {
    service,
    state: () => ({ exitRecorded, paused }),
  };
}

test('immediate-flat reconciliation does not record EXIT when order lookup fails', async () => {
  const { service, state } = buildImmediateFlatHarness({
    findOrder: async () => {
      throw new Error('active book unavailable');
    },
    postCancelPosition: null,
  });

  const safe = await service.reconcileImmediateExchangeFlat(
    'agent-1',
    { userId: 'user-1' },
    {},
  );
  assert.equal(safe, false);
  assert.deepEqual(state(), { exitRecorded: false, paused: true });
});

test('immediate-flat reconciliation detects a cancel-race fill before EXIT', async () => {
  const { service, state } = buildImmediateFlatHarness({
    findOrder: async () => ({
      id: 321,
      amount: 0.00004,
      amountOrig: 0.00004,
      price: 65_000,
    }),
    postCancelPosition: { amount: 0.00004 },
  });

  const safe = await service.reconcileImmediateExchangeFlat(
    'agent-1',
    { userId: 'user-1' },
    {},
  );
  assert.equal(safe, false);
  assert.deepEqual(state(), { exitRecorded: false, paused: true });
});

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

test('managed pending fill gets one bounded ledger-reconcile grace window', () => {
  const nowMs = Date.parse('2026-07-27T12:45:25.000Z');
  const first = pendingFillReconcileDecision({
    nowMs,
    signedDeltaBtc: -0.0307,
    pending: [
      {
        participantId: 'participant-1',
        direction: 'SHORT',
        qty: 0.0307,
        bitfinexOrderId: 241167676255,
      },
    ],
    managedOrderIds: [241167676255],
    activeOrders: [],
  });
  assert.deepEqual(first, {
    defer: true,
    reason: 'DEFER_PENDING_FILL',
    direction: 'SHORT',
    ownerParticipantIds: ['participant-1'],
    firstObservedAtMs: nowMs,
  });

  const nextTick = pendingFillReconcileDecision({
    nowMs: nowMs + 5_000,
    signedDeltaBtc: -0.0307,
    pending: [
      {
        participantId: 'participant-1',
        direction: 'SHORT',
        qty: 0.0307,
        bitfinexOrderId: 241167676255,
      },
    ],
    managedOrderIds: [241167676255],
    activeOrders: [],
    prior: {
      firstObservedAtMs: first.firstObservedAtMs!,
      direction: 'SHORT',
      ownerParticipantIds: first.ownerParticipantIds,
    },
  });
  assert.equal(nextTick.defer, true);
  assert.equal(nextTick.firstObservedAtMs, nowMs);

  const stillInsideDefaultWindow = pendingFillReconcileDecision({
    nowMs: nowMs + 59_999,
    signedDeltaBtc: -0.0307,
    pending: [
      {
        participantId: 'participant-1',
        direction: 'SHORT',
        qty: 0.0307,
        bitfinexOrderId: 241167676255,
      },
    ],
    managedOrderIds: [241167676255],
    activeOrders: [],
    prior: {
      firstObservedAtMs: first.firstObservedAtMs!,
      direction: 'SHORT',
      ownerParticipantIds: first.ownerParticipantIds,
    },
  });
  assert.equal(stillInsideDefaultWindow.defer, true);

  const expiredDefaultWindow = pendingFillReconcileDecision({
    nowMs: nowMs + 60_001,
    signedDeltaBtc: -0.0307,
    pending: [
      {
        participantId: 'participant-1',
        direction: 'SHORT',
        qty: 0.0307,
        bitfinexOrderId: 241167676255,
      },
    ],
    managedOrderIds: [241167676255],
    activeOrders: [],
    prior: {
      firstObservedAtMs: first.firstObservedAtMs!,
      direction: 'SHORT',
      ownerParticipantIds: first.ownerParticipantIds,
    },
  });
  assert.equal(expiredDefaultWindow.defer, false);
  assert.equal(expiredDefaultWindow.reason, 'GRACE_EXPIRED');
});

test('showcase position absence needs both repeated misses and a 60-second convergence window', () => {
  const firstAbsentAtMs = Date.parse('2026-08-02T09:32:03.000Z');
  assert.equal(
    showcasePositionAbsenceActionable({
      misses: 100,
      firstAbsentAtMs,
      nowMs: firstAbsentAtMs + 59_999,
    }),
    false,
  );
  assert.equal(
    showcasePositionAbsenceActionable({
      misses: 1,
      firstAbsentAtMs,
      nowMs: firstAbsentAtMs + 60_000,
    }),
    false,
  );
  assert.equal(
    showcasePositionAbsenceActionable({
      misses: 2,
      firstAbsentAtMs,
      nowMs: firstAbsentAtMs + 60_000,
    }),
    true,
  );
});

test('pending fill grace treats resting managed entry + exchange qty as fill-in-flight', () => {
  const unchanged = pendingFillReconcileDecision({
    nowMs: 1_000,
    signedDeltaBtc: 0.01,
    pending: [
      {
        participantId: 'participant-long',
        direction: 'LONG',
        qty: 0.03,
        bitfinexOrderId: 123,
      },
    ],
    managedOrderIds: [123],
    activeOrders: [{ id: 123, amount: 0.03, amountOrig: 0.03 }],
  });
  assert.equal(unchanged.defer, true);
  assert.equal(unchanged.reason, 'DEFER_PENDING_FILL');

  const partial = pendingFillReconcileDecision({
    nowMs: 1_000,
    signedDeltaBtc: 0.01,
    pending: [
      {
        participantId: 'participant-long',
        direction: 'LONG',
        qty: 0.03,
        bitfinexOrderId: 123,
      },
    ],
    managedOrderIds: [123],
    activeOrders: [{ id: 123, amount: 0.02, amountOrig: 0.03 }],
  });
  assert.equal(partial.defer, true);
  assert.equal(partial.reason, 'DEFER_PENDING_FILL');

  const tinyRoundingGap = pendingFillReconcileDecision({
    nowMs: 1_000,
    signedDeltaBtc: -0.0308,
    pending: [
      {
        participantId: 'participant-1',
        direction: 'SHORT',
        qty: 0.0307,
        bitfinexOrderId: 123,
      },
    ],
    managedOrderIds: [123],
    activeOrders: [],
  });
  assert.equal(tinyRoundingGap.defer, true);
  assert.equal(tinyRoundingGap.reason, 'DEFER_PENDING_FILL');

  const wrongDirection = pendingFillReconcileDecision({
    nowMs: 1_000,
    signedDeltaBtc: 0.02,
    pending: [
      {
        participantId: 'participant-long',
        direction: 'LONG',
        qty: 0.03,
        bitfinexOrderId: 123,
      },
    ],
    managedOrderIds: [123],
    activeOrders: [{ id: 123, amount: -0.02, amountOrig: -0.03 }],
  });
  assert.equal(wrongDirection.defer, false);
  assert.equal(wrongDirection.reason, 'NO_MANAGED_PENDING_OWNER');
});

test('pending fill grace fails closed for foreign orders, wrong ownership, and expiry', () => {
  const base = {
    signedDeltaBtc: -0.0307,
    pending: [
      {
        participantId: 'participant-1',
        direction: 'SHORT' as const,
        qty: 0.0307,
        bitfinexOrderId: 123,
      },
    ],
    managedOrderIds: [123],
  };

  assert.equal(
    pendingFillReconcileDecision({
      ...base,
      nowMs: 1_000,
      activeOrders: [{ id: 999, amount: -0.01, amountOrig: -0.01 }],
    }).reason,
    'FOREIGN_ACTIVE_ORDER',
  );
  assert.equal(
    pendingFillReconcileDecision({
      ...base,
      nowMs: 1_000,
      signedDeltaBtc: 0.0307,
      activeOrders: [],
    }).reason,
    'NO_MANAGED_PENDING_OWNER',
  );
  assert.equal(
    pendingFillReconcileDecision({
      ...base,
      nowMs: 1_000,
      pending: [
        ...base.pending,
        {
          participantId: 'participant-2',
          direction: 'SHORT',
          qty: 0.0307,
          bitfinexOrderId: 124,
        },
      ],
      managedOrderIds: [123, 124],
      activeOrders: [],
    }).reason,
    'AMBIGUOUS_MANAGED_PENDING_OWNER',
  );
  assert.equal(
    pendingFillReconcileDecision({
      ...base,
      nowMs: 16_001,
      activeOrders: [],
      prior: {
        firstObservedAtMs: 1_000,
        direction: 'SHORT',
        ownerParticipantIds: ['participant-1'],
      },
      graceMs: 15_000,
    }).reason,
    'GRACE_EXPIRED',
  );
});

test('ledger reconcile defers a proven pending fill without pausing or accepting another entry', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  let paused = false;
  const persisted: { dashboard: Record<string, unknown> | null } = {
    dashboard: null,
  };
  service.buildVirtualLotSummary = async () => ({
    openQty: 0,
    signedOpenQty: 0,
    open: 0,
    pending: 1,
    directionConflict: false,
  });
  service.activeTrading = {
    getOpenPositionDetail: async () => ({
      amount: -0.0307,
      symbol: 'tBTCF0:USTF0',
      direction: 'SHORT',
    }),
    getMarkPrice: async () => 65_100,
  };
  service.relaySim = {
    buildReconcileSnapshot: () => ({ alert: true }),
  };
  service.prisma = {
    tradingAgentInstance: {
      findUnique: async () => ({ dashboardState: {} }),
      update: async ({ data }: any) => {
        persisted.dashboard = data.dashboardState;
      },
    },
  };
  service.pauseRelayForPositionMismatch = async () => {
    paused = true;
  };
  service.logger = { warn: () => undefined };

  const reconciled = await service.reconcileLotLedger(
    'agent-1',
    { id: 'instance-1', userId: 'user-1', dashboardState: {} },
    [{ id: 'participant-1', status: SignalCycleStatus.PENDING_ENTRY }],
    {},
    new Set([123]),
    new Set(),
    new Map([
      [
        'participant-1',
        {
          direction: 'SHORT',
          qty: 0.0307,
          bitfinexOrderId: 123,
        },
      ],
    ]),
    false,
  );

  assert.equal(reconciled, false);
  assert.equal(paused, false);
  assert.equal(
    (persisted.dashboard?.pendingFillReconcileGrace as Record<string, unknown>)?.reason,
    'DEFER_PENDING_FILL',
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
  assert.equal(
    marketCloseReductionConfirmed({
      direction: 'SHORT',
      beforeQty: 0.00004,
      closeQty: 0.00004,
      afterAmount: -0.00003999,
    }),
    false,
    'a one-satoshi reduction is not a confirmed full close',
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
  assert.equal(
    relayEntryOrderIsCompletelyUnfilled({ amountOrig: -0.00004, amount: -0.00003999 }),
    false,
  );
  assert.equal(
    exchangeOrderFilledQtySats({ amountOrig: 0.00004, amount: 0 }),
    4_000,
  );
  assert.equal(
    exchangeOrderFilledQtySats({ amountOrig: -0.00004, amount: -0.00003999 }),
    1,
  );
});

test('relay lot exits target the remaining merged ledger exactly', () => {
  assert.deepEqual(
    relayLotExitTarget({
      currentAmount: -0.06066,
      remainingLedgerAmount: -0.03033,
      exitingLedgerQty: 0.03033,
      direction: 'SHORT',
    }),
    {
      ok: true,
      currentAmount: -0.06066,
      targetAmount: -0.03033,
      closeQty: 0.03033,
      finalAccountFlatten: false,
    },
  );
  assert.deepEqual(
    relayLotExitTarget({
      currentAmount: -0.00003999,
      remainingLedgerAmount: 0,
      exitingLedgerQty: 0.00004,
      direction: 'SHORT',
    }),
    {
      ok: true,
      currentAmount: -0.00003999,
      targetAmount: 0,
      closeQty: 0.00003999,
      finalAccountFlatten: true,
    },
  );
  assert.equal(
    relayLotExitTarget({
      currentAmount: -0.03033,
      remainingLedgerAmount: 0.03033,
      exitingLedgerQty: 0.03033,
      direction: 'SHORT',
    }).ok,
    false,
  );
  assert.equal(
    relayLotExitTarget({
      currentAmount: -0.06067,
      remainingLedgerAmount: -0.03033,
      exitingLedgerQty: 0.03033,
      direction: 'SHORT',
    }).reason,
    'UNATTRIBUTED_EXCHANGE_EXPOSURE',
    'same-direction manual exposure is never consumed by an automatic relay exit',
  );
  assert.deepEqual(
    relayLotExitTarget({
      currentAmount: -0.045,
      remainingLedgerAmount: -0.03,
      exitingLedgerQty: 0.03,
      direction: 'SHORT',
    }),
    {
      ok: true,
      currentAmount: -0.045,
      targetAmount: -0.03,
      closeQty: 0.015,
      finalAccountFlatten: false,
    },
    'post-submit recovery protects only the verified exiting-lot remainder',
  );
});

test('satoshi accounting preserves residual exposure below the entry minimum', () => {
  assert.equal(btcToSats(0.00003999), 3_999);
  assert.equal(rawExchangeQtyBtc(-0.00003999), 0.00003999);
  assert.equal(effectiveExchangeQtyBtc(-0.00003999), 0);
});

test('signed reconciliation rejects equal-size opposite exposure', () => {
  assert.equal(relayPositionDeltaSats(-0.03, 0.03), -6_000_000);
  assert.equal(relayPositionDeltaSats(0.03, 0.03), 0);
});

test('Bitfinex close flags keep partial exits reduce-only', () => {
  assert.equal(BITFINEX_POSITION_CLOSE_FLAG, 512);
  assert.equal(BITFINEX_REDUCE_ONLY_FLAG, 1024);
  assert.equal(BITFINEX_SAFE_CLOSE_FLAGS, 1536);
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

test('dedicated relay worker always restarts after a stuck tick', () => {
  assert.equal(relayWatchdogShouldRestart(0, true), true);
  assert.equal(relayWatchdogShouldRestart(1, true), true);
  assert.equal(relayWatchdogShouldRestart(null, true), true);
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

test('active NEXT_FRESH_ONLY relay suppresses source-only entities born before arm', () => {
  const armedAt = Date.parse('2026-07-27T10:00:00.000Z');
  const diffs = [
    {
      type: 'SHOWCASE_POSITION_NOT_MIRRORED',
      tradeId: 'pre-arm-position',
      sourceCreatedAtMs: armedAt - 1,
    },
    {
      type: 'SHOWCASE_ORDER_NOT_MIRRORED',
      tradeId: 'fresh-order',
      sourceCreatedAtMs: armedAt + 1,
    },
    { type: 'COPY_POSITION_NO_SHOWCASE', tradeId: 'copy-risk' },
  ];
  assert.deepEqual(
    reportableMirrorDiffsForRelayMode(diffs, true, null, armedAt),
    diffs.slice(1),
  );
});

test('source position birth time survives a post-arm fill', () => {
  const signalCreatedAt = Date.parse('2026-07-27T10:48:10.315Z');
  const filledAt = Date.parse('2026-07-27T10:58:14.056Z');
  assert.equal(
    sourceEntityCreatedAtMs({
      signal_created_ts: signalCreatedAt / 1000,
      entry_ts: filledAt / 1000,
    }),
    signalCreatedAt,
  );
  assert.equal(
    sourceEntityCreatedAtMs({
      entry_ts: filledAt / 1000,
      signal_age_sec: (filledAt - signalCreatedAt) / 1000,
    }),
    signalCreatedAt,
  );
});

test('active relay suppresses a source position only after an audited missed-showcase expiry', () => {
  const diffs = [
    {
      type: 'SHOWCASE_POSITION_NOT_MIRRORED',
      tradeId: 'audited-missed-fill',
      sourceCreatedAtMs: Date.parse('2026-07-27T10:22:00.000Z'),
    },
    {
      type: 'SHOWCASE_POSITION_NOT_MIRRORED',
      tradeId: 'unexplained-fresh-gap',
      sourceCreatedAtMs: Date.parse('2026-07-27T10:23:00.000Z'),
    },
    { type: 'COPY_POSITION_NO_SHOWCASE', tradeId: 'copy-risk' },
  ];
  assert.deepEqual(
    reportableMirrorDiffsForRelayMode(
      diffs,
      true,
      null,
      Date.parse('2026-07-27T10:21:00.000Z'),
      new Set(['audited-missed-fill']),
    ),
    diffs.slice(1),
  );
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

test('fresh source fill retains the managed copy order during settlement grace', () => {
  const now = Date.parse('2026-08-02T13:05:20.000Z');
  const bot = {
    orders: [],
    positions: [
      {
        trade_id: 'cont-settle',
        entry_ts: (now - 8_000) / 1000,
      },
    ],
  } as never;
  const terminalFallbackIntent = {
    action: 'ENTER',
    trade_id: 'cont-settle',
    context: {
      signed_showcase_event: true,
      showcase_event: 'LIMIT_UPDATED',
      showcase_event_at: '2026-08-02T13:04:55.000Z',
      showcase_event_seq: 4,
      marketable_fallback: true,
      relay_settle_not_before_ts: '2026-08-02T13:05:10.000Z',
    },
  };
  assert.equal(
    missedShowcaseFillWithinSettlementGrace(
      bot,
      'cont-settle',
      terminalFallbackIntent,
      now,
      60_000,
    ),
    true,
  );
  assert.equal(
    missedShowcaseFillWithinSettlementGrace(
      bot,
      'cont-settle',
      terminalFallbackIntent,
      now + 60_001,
      60_000,
    ),
    false,
  );
  assert.equal(
    missedShowcaseFillWithinSettlementGrace(
      bot,
      'other-trade',
      terminalFallbackIntent,
      now,
      60_000,
    ),
    false,
  );
  assert.equal(
    missedShowcaseFillWithinSettlementGrace(
      bot,
      'cont-settle',
      {
        ...terminalFallbackIntent,
        context: {
          ...terminalFallbackIntent.context,
          marketable_fallback: false,
        },
      },
      now,
      60_000,
    ),
    false,
  );
});

test('a still-resting showcase limit remains eligible for exact-price chase', () => {
  const bot = {
    orders: [
      {
        trade_id: 'cont-pending',
        status: 'PENDING',
        limit_price: 66_000,
        entry_limit_policy: 'micro_sr_structural_limit_v1',
      },
    ],
    positions: [],
  };
  assert.equal(
    pendingCopyShowcaseDisposition(bot as never, 'cont-pending'),
    'SHOWCASE_PENDING',
  );
  assert.equal(
    pendingCopyShowcaseDisposition(
      {
        ...bot,
        orders: [{
          ...bot.orders[0],
          entry_limit_policy: 'legacy_pullback_pct',
        }],
      } as never,
      'cont-pending',
    ),
    'SHOWCASE_ABSENT',
  );
});

test('deterministic 0.1% offset anchor is the canonical executable policy', () => {
  // Danish decision 3 (2026-08-01) — the live production anchor is the
  // deterministic 0.1% offset policy. It must be recognised as executable
  // everywhere the legacy structural policy is.
  const deterministicBot = {
    orders: [
      {
        trade_id: 'cont-det',
        status: 'PENDING',
        limit_price: 62_937,
        entry_limit_policy: 'deterministic_0.1pct_offset_v1',
      },
    ],
    positions: [],
  };
  assert.equal(
    pendingCopyShowcaseDisposition(deterministicBot as never, 'cont-det'),
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

test('treats an exact minimum-size lot as complete metadata', () => {
  assert.equal(
    shouldPersistLotMetaRepair(
      { qty: 0.00004, direction: 'SHORT' },
      { qty: 0.02, direction: 'SHORT' },
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
        entry_limit_policy: 'micro_sr_structural_limit_v1',
      },
    ],
  });
  assert.deepEqual(result, [live]);
});

test('rejects terminal or price-less canonical orders', () => {
  const cycles = [
    { tradeId: 'cont-filled', createdAt: new Date('2026-07-19T12:00:00Z') },
    { tradeId: 'cont-no-price', createdAt: new Date('2026-07-19T12:01:00Z') },
    { tradeId: 'cont-legacy', createdAt: new Date('2026-07-19T12:02:00Z') },
  ];
  const result = canonicalPendingIntentCycles(cycles, {
    orders: [
      { trade_id: 'cont-filled', status: 'FILLED', limit_price: 64_500 },
      { trade_id: 'cont-no-price', status: 'PENDING', limit_price: 0 },
      {
        trade_id: 'cont-legacy',
        status: 'PENDING',
        limit_price: 64_450,
        entry_limit_policy: 'legacy_pullback_pct',
      },
    ],
  });
  assert.deepEqual(result, []);
});

test('preserves canonical book order when more than one live intent is ready', () => {
  const firstCreated = { tradeId: 'cont-a', createdAt: new Date('2026-07-19T12:00:00Z') };
  const secondCreated = { tradeId: 'cont-b', createdAt: new Date('2026-07-19T12:01:00Z') };
  const result = canonicalPendingIntentCycles([firstCreated, secondCreated], {
    orders: [
      {
        trade_id: 'cont-b',
        status: 'ORDERED',
        limit_price: 64_600,
        entry_limit_policy: 'micro_sr_structural_limit_v1',
      },
      {
        trade_id: 'cont-a',
        status: 'PENDING',
        limit_price: 64_500,
        entry_limit_policy: 'micro_sr_structural_limit_v1',
      },
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
        signalId: 'cont-fast',
        trade_id: 'cont-fast',
        action: 'ENTER',
        direction: 'SHORT',
        entry: {
          mode: 'EXACT_LIMIT',
          reference: 'SHOWCASE_EXACT_LIMIT',
          exact_limit_price: 64_555.25,
        },
        context: {
          signed_showcase_event: true,
          showcase_event: 'ORDER_PLACED',
          showcase_event_at: '2026-07-20T01:02:01.750Z',
          platform_received_at: '2026-07-20T01:02:02.250Z',
          entry_limit_policy: 'micro_sr_structural_limit_v1',
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
    signalId: 'cont-base',
    trade_id: 'cont-base',
    action: 'ENTER',
    direction: 'LONG',
    entry: {
      mode: 'EXACT_LIMIT',
      reference: 'SHOWCASE_EXACT_LIMIT',
      exact_limit_price: 64_500,
    },
    context: {
      signed_showcase_event: true,
      showcase_event: 'ORDER_PLACED',
      platform_received_at: '2026-07-20T01:02:29.000Z',
      entry_limit_policy: 'micro_sr_structural_limit_v1',
    },
  };
  assert.equal(
    readFreshSignedShowcaseExactLimit('cont-base', {
      ...base,
      context: { ...base.context, signed_showcase_event: false },
    }, now),
    null,
  );
  assert.equal(
    readFreshSignedShowcaseExactLimit('cont-base', {
      ...base,
      context: {
        ...base.context,
        platform_received_at: '2026-07-20T01:02:00.000Z',
      },
    }, now),
    null,
  );
  assert.equal(
    readFreshSignedShowcaseExactLimit('cont-base', {
      ...base,
      context: { ...base.context, showcase_event: 'APPROVE_PENDING' },
    }, now),
    null,
  );
  assert.equal(
    readFreshSignedShowcaseExactLimit('cont-base', {
      ...base,
      entry: {
        mode: 'PULLBACK_PCT',
        reference: 'SUBSCRIBER_MARK_AT_RECEIPT',
        offset_pct: -0.1,
        exact_limit_price: 64_500,
      },
    }, now),
    null,
  );
});

test('rejects a signed exact limit whose envelope identity does not match the cycle trade', () => {
  const now = Date.parse('2026-07-20T01:02:30.000Z');
  assert.equal(
    readFreshSignedShowcaseExactLimit(
      'cont-requested',
      {
        schema: 'dcf-signal-intent/v1',
        signalId: 'cont-other',
        trade_id: 'cont-other',
        action: 'ENTER',
        direction: 'LONG',
        entry: {
          mode: 'EXACT_LIMIT',
          reference: 'SHOWCASE_EXACT_LIMIT',
          exact_limit_price: 64_500,
        },
        context: {
          signed_showcase_event: true,
          showcase_event: 'ORDER_PLACED',
          platform_received_at: '2026-07-20T01:02:29.000Z',
          entry_limit_policy: 'micro_sr_structural_limit_v1',
        },
      },
      now,
    ),
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

// ---------------------------------------------------------------------------
// Cure 1 — cycle.tradeId desync re-link helper (pure function tests).
// Mirrors the cont-de8f316fd3c0 production incident: a real fill that landed
// on a different showcase signal than the cycle was tracking.
// ---------------------------------------------------------------------------

test('resolveShowcaseRelinkForRealFill: normal case (fill matches own cycle) returns null — no orphan', () => {
  // Cycle A tracks cont-A. Real fill price matches cont-A's showcase entry
  // exactly. No re-link should occur.
  const now = Date.parse('2026-08-07T22:00:00.000Z');
  const res = resolveShowcaseRelinkForRealFill({
    showcasePositions: [
      {
        trade_id: 'cont-7805cc21c534',
        entry: 65_016,
        dir: 'LONG',
        entry_ts: now / 1000,
      },
    ],
    realFill: { price: 65_016, direction: 'LONG' },
    currentTradeId: 'cont-7805cc21c534',
    nowMs: now,
  });
  assert.equal(res, null);
});

test('resolveShowcaseRelinkForRealFill: tonight\'s case (fill matches a DIFFERENT signal) returns the new trade', () => {
  // Cycle A tracks cont-7805cc21c534. Real fill landed at 65_016 which
  // matches cont-de8f316fd3c0's showcase entry. Must re-link to cont-de8f316fd3c0.
  const now = Date.parse('2026-08-07T22:00:00.000Z');
  const res = resolveShowcaseRelinkForRealFill({
    showcasePositions: [
      {
        trade_id: 'cont-7805cc21c534',
        entry: 64_500, // different price — does NOT match the real fill
        dir: 'LONG',
        entry_ts: now / 1000 - 60,
      },
      {
        trade_id: 'cont-de8f316fd3c0',
        entry: 65_016, // matches real fill exactly
        dir: 'LONG',
        entry_ts: now / 1000,
      },
    ],
    realFill: { price: 65_016, direction: 'LONG' },
    currentTradeId: 'cont-7805cc21c534',
    nowMs: now,
  });
  assert.ok(res, 'expected a re-link candidate');
  assert.equal(res!.tradeId, 'cont-de8f316fd3c0');
  assert.equal(res!.direction, 'LONG');
  assert.equal(res!.entryPrice, 65_016);
  assert.ok(res!.priceBandPct < SHOWCASE_RELINK_PRICE_BAND_PCT);
});

test('resolveShowcaseRelinkForRealFill: true orphan (no showcase position matches) returns null', () => {
  // Real fill at a price no showcase position has — manual trade, or
  // showcase already wiped. Must NOT re-link to anything.
  const now = Date.parse('2026-08-07T22:00:00.000Z');
  const res = resolveShowcaseRelinkForRealFill({
    showcasePositions: [
      {
        trade_id: 'cont-7805cc21c534',
        entry: 64_500,
        dir: 'LONG',
        entry_ts: now / 1000,
      },
    ],
    realFill: { price: 70_000, direction: 'LONG' },
    currentTradeId: 'cont-7805cc21c534',
    nowMs: now,
  });
  assert.equal(res, null);
});

test('resolveShowcaseRelinkForRealFill: direction mismatch is rejected', () => {
  // Showcase has a SHORT position at the fill price; real fill is LONG.
  // Cannot re-link — opposite sides.
  const now = Date.parse('2026-08-07T22:00:00.000Z');
  const res = resolveShowcaseRelinkForRealFill({
    showcasePositions: [
      {
        trade_id: 'cont-short',
        entry: 65_016,
        dir: 'SHORT',
        entry_ts: now / 1000,
      },
    ],
    realFill: { price: 65_016, direction: 'LONG' },
    currentTradeId: 'cont-7805cc21c534',
    nowMs: now,
  });
  assert.equal(res, null);
});

test('resolveShowcaseRelinkForRealFill: outside the time window is rejected', () => {
  // A stale showcase position from hours ago at the same price must not
  // steal a real fill that just happened.
  const now = Date.parse('2026-08-07T22:00:00.000Z');
  const res = resolveShowcaseRelinkForRealFill({
    showcasePositions: [
      {
        trade_id: 'cont-stale',
        entry: 65_016,
        dir: 'LONG',
        // 30 minutes ago — outside the 10-minute window.
        entry_ts: now / 1000 - 30 * 60,
      },
    ],
    realFill: { price: 65_016, direction: 'LONG' },
    currentTradeId: 'cont-7805cc21c534',
    nowMs: now,
  });
  assert.equal(res, null);
});

test('resolveShowcaseRelinkForRealFill: does not steal a trade already claimed by another OPEN participant', () => {
  // Two real fills, both could match the same showcase trade. The first to
  // re-link claims it; the second must not double-claim.
  const now = Date.parse('2026-08-07T22:00:00.000Z');
  const res = resolveShowcaseRelinkForRealFill({
    showcasePositions: [
      {
        trade_id: 'cont-de8f316fd3c0',
        entry: 65_016,
        dir: 'LONG',
        entry_ts: now / 1000,
      },
    ],
    realFill: { price: 65_016, direction: 'LONG' },
    currentTradeId: 'cont-7805cc21c534',
    nowMs: now,
    alreadyRelinkedTo: new Set(['cont-de8f316fd3c0']),
  });
  assert.equal(res, null);
});

test('resolveShowcaseRelinkForRealFill: picks the closest match when multiple candidates are in band', () => {
  // Two showcase positions at slightly different prices both within the
  // 0.15% band. The closest one (smallest price band pct) wins.
  const now = Date.parse('2026-08-07T22:00:00.000Z');
  const res = resolveShowcaseRelinkForRealFill({
    showcasePositions: [
      {
        trade_id: 'cont-far',
        entry: 65_080, // ~0.098% off 65_016
        dir: 'LONG',
        entry_ts: now / 1000,
      },
      {
        trade_id: 'cont-near',
        entry: 65_020, // ~0.006% off 65_016
        dir: 'LONG',
        entry_ts: now / 1000,
      },
    ],
    realFill: { price: 65_016, direction: 'LONG' },
    currentTradeId: 'cont-7805cc21c534',
    nowMs: now,
  });
  assert.ok(res);
  assert.equal(res!.tradeId, 'cont-near');
  assert.ok(res!.priceBandPct < 0.01);
});

test('resolveShowcaseRelinkForRealFill: handles entry_ts in milliseconds too', () => {
  const now = Date.parse('2026-08-07T22:00:00.000Z');
  const res = resolveShowcaseRelinkForRealFill({
    showcasePositions: [
      {
        trade_id: 'cont-ms',
        entry: 65_016,
        dir: 'LONG',
        entry_ts: now, // ms (not seconds)
      },
    ],
    realFill: { price: 65_016, direction: 'LONG' },
    currentTradeId: 'cont-other',
    nowMs: now,
  });
  assert.ok(res);
  assert.equal(res!.tradeId, 'cont-ms');
  assert.ok(res!.timeDeltaMs != null && Math.abs(res!.timeDeltaMs!) < 1000);
});

test('SHOWCASE_RELINK constants are conservative', () => {
  // 0.15% band = ~$98 at $65k BTC. Tight enough to avoid cross-matching
  // nearby signals on normal days, loose enough to absorb maker slippage.
  assert.ok(SHOWCASE_RELINK_PRICE_BAND_PCT > 0 && SHOWCASE_RELINK_PRICE_BAND_PCT <= 0.5);
  // 10 minutes — longer than the longest maker fill race (~60s) but shorter
  // than the cycle TTL (30m) so a stale signal can't claim a fresh fill.
  assert.ok(SHOWCASE_RELINK_TIME_WINDOW_MS >= 5 * 60 * 1000);
  assert.ok(SHOWCASE_RELINK_TIME_WINDOW_MS <= 30 * 60 * 1000);
});

// ---------------------------------------------------------------------------
// resolveShowcaseMirrorTradeIdFromInputs — regression tests for the
// cont-de8f316fd3c0 case. A bug here silently orphans real money: the mirror
// exit machinery keys off this id and never fires if it points at the wrong
// (stale) showcase signal.
// ---------------------------------------------------------------------------

test('resolveShowcaseMirrorTradeIdFromInputs: plain tradeId returns as-is', () => {
  assert.equal(
    resolveShowcaseMirrorTradeIdFromInputs('cont-abc123', null),
    'cont-abc123',
  );
  assert.equal(
    resolveShowcaseMirrorTradeIdFromInputs('cont-abc123', 'cont-other'),
    'cont-abc123',
  );
});

test('resolveShowcaseMirrorTradeIdFromInputs: null tradeId returns null', () => {
  assert.equal(resolveShowcaseMirrorTradeIdFromInputs(null, 'x'), null);
  assert.equal(resolveShowcaseMirrorTradeIdFromInputs(undefined, 'x'), null);
  assert.equal(resolveShowcaseMirrorTradeIdFromInputs('', 'x'), null);
});

test('resolveShowcaseMirrorTradeIdFromInputs: adopt: with no originTradeId parses from string', () => {
  assert.equal(
    resolveShowcaseMirrorTradeIdFromInputs('adopt:cont-origin:1700000000000', null),
    'cont-origin',
  );
  assert.equal(
    resolveShowcaseMirrorTradeIdFromInputs('adopt:cont-origin:1700000000000', undefined),
    'cont-origin',
  );
});

test('resolveShowcaseMirrorTradeIdFromInputs: adopt: with originTradeId prefers it (Cure 1 re-link target)', () => {
  // After a Cure 1 re-link on an adopted cycle, originTradeId is the matched
  // showcase id, NOT the prior origin embedded in the adopt string.
  assert.equal(
    resolveShowcaseMirrorTradeIdFromInputs('adopt:cont-origin:1700000000000', 'cont-matched'),
    'cont-matched',
  );
});

test('resolveShowcaseMirrorTradeIdFromInputs: adopt: ignores originTradeId that equals the embedded prior origin (stale)', () => {
  // Defensive guard — if originTradeId is the same as the embedded prior
  // origin (i.e. no actual re-link happened), it's stale; fall back to the
  // string's embedded origin.
  assert.equal(
    resolveShowcaseMirrorTradeIdFromInputs('adopt:cont-origin:1700000000000', 'cont-origin'),
    'cont-origin',
  );
});

test('resolveShowcaseMirrorTradeIdFromInputs: relink: parses NEW id from index 2 when originTradeId is missing', () => {
  // relink:<origin>:<new>:<ts> — the NEW showcase id is at index 2.
  assert.equal(
    resolveShowcaseMirrorTradeIdFromInputs(
      'relink:cont-stale:cont-new:1700000000000',
      null,
    ),
    'cont-new',
  );
});

test('resolveShowcaseMirrorTradeIdFromInputs: CRITICAL regression — stale originTradeId pointing at the PRE-relink origin does NOT win', () => {
  // This is the cont-de8f316fd3c0 bug. Without the defensive guard, the
  // stale originTradeId (still pointing at cont-stale) would win and the
  // mirror exit would watch cont-stale forever — re-creating the orphan.
  // With the guard, we fall through to the NEW id encoded in the string.
  assert.equal(
    resolveShowcaseMirrorTradeIdFromInputs(
      'relink:cont-stale:cont-new:1700000000000',
      'cont-stale',
    ),
    'cont-new',
  );
});

test('resolveShowcaseMirrorTradeIdFromInputs: relink: with non-stale originTradeId prefers it', () => {
  // The post-relink originTradeId (loaded from the CYCLE_TRADE_ID_RELINK
  // event) should be the NEW showcase id; trust it.
  assert.equal(
    resolveShowcaseMirrorTradeIdFromInputs(
      'relink:cont-stale:cont-new:1700000000000',
      'cont-new',
    ),
    'cont-new',
  );
});

test('resolveShowcaseMirrorTradeIdFromInputs: relink: ignores "unknown" placeholder', () => {
  assert.equal(
    resolveShowcaseMirrorTradeIdFromInputs(
      'relink:unknown:cont-new:1700000000000',
      'unknown',
    ),
    'cont-new',
  );
});

test('resolveShowcaseMirrorTradeIdFromInputs: adopt: malformed returns null safely', () => {
  assert.equal(resolveShowcaseMirrorTradeIdFromInputs('adopt:', null), null);
  assert.equal(
    resolveShowcaseMirrorTradeIdFromInputs('adopt:unknown', null),
    null,
  );
});

test('resolveShowcaseMirrorTradeIdFromInputs: relink: malformed falls back gracefully', () => {
  // relink: with no segments — return null.
  assert.equal(resolveShowcaseMirrorTradeIdFromInputs('relink:', null), null);
  // relink: with only "unknown" segments — return null.
  assert.equal(
    resolveShowcaseMirrorTradeIdFromInputs('relink:unknown:unknown:0', null),
    null,
  );
});
