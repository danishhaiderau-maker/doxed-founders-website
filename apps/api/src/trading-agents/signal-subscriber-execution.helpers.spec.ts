import assert from 'node:assert/strict';
import test from 'node:test';
import {
  executorWakeAuthorized,
  parseExecutorWakeRequest,
} from '../relay-executor-wake-http';
import { SignalCycleStatus, TradingAgentInstanceStatus } from '@prisma/client';
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
  BitfinexTradingClient,
} from '../exchanges/bitfinex-api.client';
import { BitfinexAuthTradeStream } from '../exchanges/bitfinex-auth-trade-stream';
import {
  SignalSubscriberExecutionService,
  finiteDecimalLikeNumber,
  canonicalPendingIntentCycles,
  signedCanonicalPendingIntentCycles,
  buildRelayExecutorHealth,
  capRelayLimitAtShowcaseFill,
  aggressiveCatchupEnabled,
  boundedAggressiveCatchupLimit,
  aggressiveCatchupIsWithinBound,
  mirrorPositionQuantityDelta,
  partialEntryFillDisposition,
  entryTtlPartialFillDisposition,
  finalizedEntryFillQty,
  protectiveStopReferencePrice,
  mirrorDiffPriceDeltaIsWithinSignedRepriceGrace,
  shouldRecordAggressiveCatchupDeferred,
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
  buildShowcasePositionAbsenceEvidence,
  pendingCopyShowcaseDisposition,
  shouldRetainLateEntryContinuation,
  shouldRetainActiveAggressiveCatchup,
  shouldActivateAggressiveCatchup,
  showcaseAbsentWithinOrderPropagationGrace,
  shouldDeferCancelByExchangeForReplacement,
  pendingEntryOwnershipAdvanced,
  advanceReplacementMissingProbe,
  managedOrderExchangeAckAtMs,
  shouldDeferRecentOrphanOrderAdoption,
  missedShowcaseFillWithinSettlementGrace,
  relayEntryOrderIsCompletelyUnfilled,
  relayLotExitTarget,
  relayWatchdogShouldRestart,
  resolveMissedShowcaseFill,
  untrackedActiveOrderIds,
  ownedStopOrderIds,
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
  participantCanOwnOrphanOrder,
  isFreshCanonicalFidelityBotState,
  isFreshExactFidelityReconcile,
  liveRelayFidelityObservation,
  LIVE_FIDELITY_GUARD_THRESHOLD_PCT,
  resolveShowcaseRelinkForRealFill,
  SHOWCASE_RELINK_PRICE_BAND_PCT,
  SHOWCASE_RELINK_TIME_WINDOW_MS,
  SHOWCASE_RELINK_MAX_ENTRY_LEAD_MS,
  mayCrossTradeRelink,
  resolveShowcaseMirrorTradeIdFromInputs,
  persistedCloseWakeMatchesParticipant,
  hireExpiryBlocksNewLiveEntries,
  hireExpiryRequiresExitOnlyProcessing,
  expiredHireShouldRunExitOnly,
  readRelayExecutorWakeRequest,
  pollForVerifiedEntryFill,
  shouldRunLocalRealSideSafetyNet,
  resolveExactShowcaseEntryQty,
  resolveEffectiveStopLossMarginPct,
} from './signal-subscriber-execution.service';

test('mirror-mode exchange protection never widens the Scenario C -13% hard stop', () => {
  assert.equal(resolveEffectiveStopLossMarginPct(-40, { mirrorMode: true, simActive: false }), -13);
  assert.equal(resolveEffectiveStopLossMarginPct(-10, { mirrorMode: true, simActive: false }), -10);
  assert.equal(resolveEffectiveStopLossMarginPct(-40, { mirrorMode: false, simActive: false }), -13);
  assert.equal(resolveEffectiveStopLossMarginPct(-40, { mirrorMode: true, simActive: true }), -40);
});

test('decimal-like terminal fill prices remain finite for partial-close P&L accounting', () => {
  assert.equal(finiteDecimalLikeNumber({ toNumber: () => 63_642 }), 63_642);
  assert.equal(finiteDecimalLikeNumber({ toNumber: () => Number.NaN }), null);
  assert.equal(finiteDecimalLikeNumber(63_642), 63_642);
  assert.equal(finiteDecimalLikeNumber(null), null);
});

test('exact showcase quantity is preserved below the subscriber cap', () => {
  const result = resolveExactShowcaseEntryQty({
    exactQtyBtc: 0.02361832782239017,
    maxMarginUsd: 20,
    leverage: 100,
    limitPrice: 63_614.55,
  });
  assert.deepEqual(result, {
    ok: true,
    qty: 0.02361,
    requiredMarginUsd: 0.02361 * 63_614.55 / 100,
    capQty: 0.03143,
  });
});

test('late-entry continuation is opt-in and retains only the same live showcase trade', () => {
  assert.equal(
    shouldRetainLateEntryContinuation({
      enabled: false,
      showcaseTradeOpen: true,
      participantStatus: SignalCycleStatus.PENDING_ENTRY,
      hasManagedOrder: true,
    }),
    false,
  );
  assert.equal(
    shouldRetainLateEntryContinuation({
      enabled: true,
      showcaseTradeOpen: true,
      participantStatus: SignalCycleStatus.PENDING_ENTRY,
      hasManagedOrder: true,
    }),
    true,
  );
  assert.equal(
    shouldRetainLateEntryContinuation({
      enabled: true,
      showcaseTradeOpen: false,
      participantStatus: SignalCycleStatus.PENDING_ENTRY,
      hasManagedOrder: true,
    }),
    false,
  );
  assert.equal(
    shouldRetainLateEntryContinuation({
      enabled: true,
      showcaseTradeOpen: true,
      participantStatus: SignalCycleStatus.OPEN,
      hasManagedOrder: true,
    }),
    false,
  );
});

test('active bounded catch-up retains its managed order only while the exact source trade remains open', () => {
  const base = {
    enabled: true,
    catchupActive: true,
    showcaseTradeOpen: true,
    participantStatus: SignalCycleStatus.PENDING_ENTRY,
    hasManagedOrder: true,
  };
  assert.equal(shouldRetainActiveAggressiveCatchup(base), true);
  assert.equal(shouldRetainActiveAggressiveCatchup({ ...base, enabled: false }), false);
  assert.equal(shouldRetainActiveAggressiveCatchup({ ...base, catchupActive: false }), false);
  // A signed source fill is durable authority. A later snapshot may be stale
  // or temporarily omit the position, but that must not unlock phantom cancel.
  assert.equal(shouldRetainActiveAggressiveCatchup({ ...base, showcaseTradeOpen: false }), true);
  assert.equal(shouldRetainActiveAggressiveCatchup({ ...base, hasManagedOrder: false }), false);
  assert.equal(
    shouldRetainActiveAggressiveCatchup({ ...base, participantStatus: SignalCycleStatus.OPEN }),
    false,
  );
});

test('canonical source position starts bounded catch-up only once for its exact pending order', () => {
  const base = {
    enabled: true,
    catchupActive: false,
    showcaseTradeOpen: true,
    showcaseFill: 63_474.01,
    participantStatus: SignalCycleStatus.PENDING_ENTRY,
    hasManagedOrder: true,
  };
  assert.equal(shouldActivateAggressiveCatchup(base), true);
  assert.equal(shouldActivateAggressiveCatchup({ ...base, catchupActive: true }), false);
  assert.equal(shouldActivateAggressiveCatchup({ ...base, enabled: false }), false);
  assert.equal(shouldActivateAggressiveCatchup({ ...base, showcaseTradeOpen: false }), false);
  assert.equal(shouldActivateAggressiveCatchup({ ...base, showcaseFill: 0 }), false);
  assert.equal(shouldActivateAggressiveCatchup({ ...base, hasManagedOrder: false }), false);
});

test('exact showcase quantity exceeding the subscriber cap is blocked, never resized', () => {
  assert.deepEqual(
    resolveExactShowcaseEntryQty({
      exactQtyBtc: 0.03143,
      maxMarginUsd: 15,
      leverage: 100,
      limitPrice: 63_614.55,
    }),
    { ok: false, reason: 'SOURCE_QTY_EXCEEDS_SUBSCRIBER_CAP' },
  );
  assert.deepEqual(
    resolveExactShowcaseEntryQty({
      exactQtyBtc: null,
      maxMarginUsd: 20,
      leverage: 100,
      limitPrice: 63_614.55,
    }),
    { ok: false, reason: 'MISSING_EXACT_QTY' },
  );
});

test('exact showcase quantity allows only the deterministic anchor margin overhead', () => {
  const canonical = resolveExactShowcaseEntryQty({
    exactQtyBtc: 0.03143566690767344,
    maxMarginUsd: 20,
    leverage: 100,
    limitPrice: 63_685.62,
  });
  assert.deepEqual(canonical, {
    ok: true,
    qty: 0.03143,
    requiredMarginUsd: 0.03143 * 63_685.62 / 100,
    capQty: 0.0314,
  });
  assert.deepEqual(
    resolveExactShowcaseEntryQty({
      exactQtyBtc: 0.03148,
      maxMarginUsd: 20,
      leverage: 100,
      limitPrice: 63_685.62,
    }),
    { ok: false, reason: 'SOURCE_QTY_EXCEEDS_SUBSCRIBER_CAP' },
  );
});

test('entry money path submits the venue-rounded showcase quantity, not the margin cap quantity', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  const now = Date.now();
  const createdAt = new Date(now);
  const dashboardState = {
    relayExecutionMode: 'LIVE',
    relayPolicyVersion: 'continuous_only_v5',
    realTradingConfirmedAt: new Date(now - 2_000).toISOString(),
    relayArmedAt: new Date(now - 1_000).toISOString(),
  };
  const instance = {
    id: 'instance-exact-qty',
    userId: 'user-exact-qty',
    agentId: 'agent-exact-qty',
    status: TradingAgentInstanceStatus.ACTIVE,
    dashboardState,
  };
  const envelope = {
    schema: 'dcf-signal-intent/v1',
    cycleId: 'cycle-exact-qty',
    signalId: 'cont-e0ac7001',
    trade_id: 'cont-e0ac7001',
    version: 'test',
    action: 'ENTER',
    direction: 'SHORT',
    entry: {
      type: 'LIMIT', mode: 'EXACT_LIMIT', offset_pct: 0,
      exact_limit_price: 63_614.55,
      exact_qty_btc: 0.02361832782239017,
      reference: 'SHOWCASE_EXACT_LIMIT', ttl_sec: 1_800,
    },
    risk: { stop_loss_margin_pct: -18, take_profit_ladder: [], leverage_hint: 100, max_margin_usd: 20 },
    context: {
      regime: 'UNKNOWN', edge: 0, ai_win_prob: 0,
      entry_mode_source: 'test', entry_limit_policy: 'micro_sr_structural_limit_v1',
      research_venue: 'bitfinex', disclaimer: 'test',
      signed_showcase_event: true, showcase_event: 'ORDER_PLACED',
      showcase_event_at: new Date(now - 200).toISOString(),
      platform_received_at: new Date(now - 100).toISOString(),
    },
  };
  const submitted: Array<Record<string, unknown>> = [];
  const events: Array<Record<string, unknown>> = [];
  service.logger = { log() {}, warn() {}, error() {} };
  service.cycleAudit = { stage() {} };
  service.activeTrading = {
    getOpenPositionDetail: async () => null,
    submitLimitOrder: async (_creds: unknown, order: Record<string, unknown>) => {
      submitted.push(order);
      return 9001;
    },
  };
  service.prisma = {
    signalCycleParticipant: {
      create: async () => ({ id: 'participant-exact-qty' }),
      delete: async () => ({}),
      findUnique: async () => null,
    },
    tradingAgentInstance: {
      findUnique: async () => ({ status: TradingAgentInstanceStatus.ACTIVE, dashboardState }),
      update: async () => ({}),
    },
    signalCycle: { findUnique: async () => ({ createdAt }) },
  };
  service.cycles = {
    recordHireExecutionEvent: async (...args: unknown[]) => {
      events.push(args.at(-1) as Record<string, unknown>);
    },
  };
  service.applyLimitChase = async () => {};

  const placed = await service.placeEntry(
    instance.agentId,
    instance,
    'cycle-exact-qty',
    envelope,
    { apiKey: 'redacted', apiSecret: 'redacted' },
    20,
    'cont-e0ac7001',
    'bitfinex',
    { availableUsd: 100, markPrice: 63_620, exchangeBookProvenEmpty: true },
  );
  assert.equal(placed, true);
  assert.equal(submitted.length, 1);
  assert.equal(submitted[0].qty, 0.02361);
  assert.notEqual(submitted[0].qty, 0.03143);
  assert.equal(events.at(-1)?.source_exact_qty_btc, 0.02361832782239017);
  assert.equal(events.at(-1)?.venue_qty_btc, 0.02361);
  assert.equal(events.at(-1)?.margin_cap_usd, 20);
});

test('market catch-up money path also submits the exact showcase position quantity', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  const submitted: Array<Record<string, unknown>> = [];
  const events: Array<Record<string, unknown>> = [];
  service.logger = { log() {}, warn() {}, error() {} };
  service.positionRuntime = new Map();
  service.showcaseUnreachableSince = new Map();
  service.showcaseRecoveryHits = new Map();
  service.showcaseSafeModeNoticeAt = new Map();
  service.prisma = {
    signalCycleParticipant: {
      findUnique: async () => null,
      create: async () => ({ id: 'participant-catchup-exact-qty' }),
      delete: async () => ({}),
      update: async () => ({}),
    },
  };
  service.activeTrading = {
    getDerivativesAvailableUsd: async () => 100,
    submitMarketEntry: async (_creds: unknown, order: Record<string, unknown>) => {
      submitted.push(order);
      return 9101;
    },
    submitStopOrder: async () => 9102,
  };
  service.cycles = {
    recordHireExecutionEvent: async (...args: unknown[]) => {
      events.push(args.at(-1) as Record<string, unknown>);
    },
  };
  service.healStuckPendingFill = async () => {};

  const placed = await service.placeMirrorCatchupEntry(
    'agent-catchup',
    {
      id: 'instance-catchup', userId: 'user-catchup', agentId: 'agent-catchup',
      status: TradingAgentInstanceStatus.ACTIVE, dashboardState: {},
    },
    'cycle-catchup',
    {
      direction: 'SHORT',
      entry: { type: 'LIMIT', mode: 'EXACT_LIMIT', offset_pct: 0, reference: 'SHOWCASE_EXACT_LIMIT', ttl_sec: 1800 },
      risk: { stop_loss_margin_pct: -18, take_profit_ladder: [], leverage_hint: 100, max_margin_usd: 20 },
    },
    { apiKey: 'redacted', apiSecret: 'redacted' },
    20,
    'cont-catchup-exact',
    63_614.55,
    0.02361832782239017,
    63_620,
    5.45,
  );

  assert.equal(placed, true);
  assert.equal(submitted.length, 1);
  assert.equal(submitted[0].qty, 0.02361);
  assert.notEqual(submitted[0].qty, 0.03143);
  assert.equal(events[0].source_exact_qty_btc, 0.02361832782239017);
  assert.equal(events[0].venue_qty_btc, 0.02361);
  assert.equal(events[0].margin_cap_usd, 20);
});

test('mirror position diff flags material quantity under-copy despite matching row counts', () => {
  const delta = mirrorPositionQuantityDelta(0.031206116, 0.015);
  assert.ok(delta != null);
  assert.ok(delta! < 0);
  assert.equal(Math.abs(delta! + 0.016206116) < 1e-12, true);
});

test('mirror position diff ignores only bounded exchange quantity rounding', () => {
  assert.equal(mirrorPositionQuantityDelta(0.031206116, 0.0312), null);
  assert.equal(mirrorPositionQuantityDelta(0, 0.015), null);
});

test('partial entry lifecycle retains only a live nonterminal remainder', () => {
  assert.equal(
    partialEntryFillDisposition({
      intendedQty: 0.031206116,
      filledQty: 0.015,
      orderResting: true,
      terminalSource: false,
    }),
    'RETAIN_PROTECTED_REMAINDER',
  );
  assert.equal(
    partialEntryFillDisposition({
      intendedQty: 0.031206116,
      filledQty: 0.031206116,
      orderResting: false,
      terminalSource: false,
    }),
    'FINALIZE_FILL',
  );
  assert.equal(
    partialEntryFillDisposition({
      intendedQty: 0.031206116,
      filledQty: 0.015,
      orderResting: true,
      terminalSource: true,
    }),
    'FINALIZE_FILL',
  );
});

test('entry TTL cancels only a partial remainder and keeps authenticated exposure open', () => {
  assert.equal(
    entryTtlPartialFillDisposition({
      context: 'SIGNAL_TTL_EXPIRED',
      intendedQty: 0.03,
      filledQty: 0.018,
      orderResting: true,
    }),
    'CANCEL_REMAINDER_AND_OPEN',
  );
  assert.equal(
    entryTtlPartialFillDisposition({
      context: 'SHOWCASE_ORDER_EXPIRED',
      intendedQty: 0.03,
      filledQty: 0.018,
      orderResting: true,
    }),
    'CANCEL_REMAINDER_AND_OPEN',
  );
  assert.equal(
    entryTtlPartialFillDisposition({
      context: 'SHOWCASE_CYCLE_CLOSED',
      intendedQty: 0.03,
      filledQty: 0.018,
      orderResting: true,
    }),
    'NORMAL_FILL_PATH',
  );
});

test('finalized entry quantity snaps only a terminal one-satoshi transport artifact', () => {
  assert.equal(
    finalizedEntryFillQty({
      intendedQty: 0.02359,
      filledQty: 0.02358999,
      orderResting: false,
    }),
    0.02359,
  );
  assert.equal(
    finalizedEntryFillQty({
      intendedQty: 0.02359,
      filledQty: 0.02358999,
      orderResting: true,
    }),
    0.02358999,
  );
  assert.equal(
    finalizedEntryFillQty({
      intendedQty: 0.02359,
      filledQty: 0.02358998,
      orderResting: false,
    }),
    0.02358998,
  );
  assert.equal(
    finalizedEntryFillQty({
      intendedQty: 0.02359,
      filledQty: 0.02359001,
      orderResting: false,
    }),
    0.02359001,
  );
});

test('service money path protects a partial fill without cancelling its entry remainder', async () => {
  const events: Array<{ type: string; payload: Record<string, unknown> }> = [];
  let entryCancelCalls = 0;
  let resolveSubmit!: (id: number) => void;
  const submit = new Promise<number>((resolve) => { resolveSubmit = resolve; });
  const originalNow = Date.now;
  const timestamps = [1_000, 1_100, 1_400];
  Date.now = () => timestamps.shift() ?? 1_400;
  const service = new SignalSubscriberExecutionService(
    {} as never,
    {} as never,
    {
      recordHireExecutionEvent: async (
        _userId: string,
        _agentId: string,
        _cycleId: string,
        type: string,
        payload: Record<string, unknown>,
      ) => events.push({ type, payload }),
    } as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
  );
  (service as any).activeTrading = {
    submitStopOrder: async () => submit,
    getMarkPrice: async () => 64_126.31,
    cancelOrder: async () => {
      entryCancelCalls += 1;
    },
  };
  const protecting = (service as any).protectPartialFillAndRetainRemainder(
    'agent',
    'user',
    'cycle',
    'participant',
    {
      direction: 'SHORT',
      qty: 0.031206116,
      bitfinexOrderId: 6001,
      limitPrice: 64_126.31,
    },
    {},
    { risk: { stop_loss_margin_pct: 10 } },
    0.015,
  );
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(events.length, 0, 'no ownership event may persist before exchange ACK');
  resolveSubmit(7001);
  await protecting.finally(() => { Date.now = originalNow; });
  assert.equal(entryCancelCalls, 0);
  assert.equal(events.length, 2);
  assert.equal(events.at(0)?.type, 'UPDATE_STOPS');
  assert.equal(events.at(0)?.payload.event, 'PARTIAL_FILL_STOP_REPLACEMENT_ACKNOWLEDGED');
  assert.equal(events.at(1)?.type, 'UPDATE_STOPS');
  assert.equal(events.at(1)?.payload.event, 'PARTIAL_FILL_REMAINDER_RETAINED');
  assert.equal(events.at(0)?.payload.bitfinexOrderId, 6001);
  assert.equal(events.at(0)?.payload.partialFillStopOrderId, 7001);
  assert.equal(events.at(0)?.payload.partialFillQty, 0.015);
  assert.ok(Number(events.at(0)?.payload.remaining_qty) > 0);
  const timing = (payload: Record<string, unknown> | undefined) => ({
    detected: payload?.partial_fill_detected_at,
    submit: payload?.stop_submit_started_at,
    ack: payload?.stop_exchange_ack_at,
    detectionToAck: payload?.detection_to_stop_ack_ms,
    submitToAck: payload?.stop_submit_to_ack_ms,
  });
  assert.deepEqual(timing(events.at(0)?.payload), {
    detected: new Date(1_000).toISOString(),
    submit: new Date(1_100).toISOString(),
    ack: new Date(1_400).toISOString(),
    detectionToAck: 400,
    submitToAck: 300,
  });
  assert.deepEqual(timing(events.at(1)?.payload), timing(events.at(0)?.payload));
  assert.ok(Date.parse(String(events.at(0)?.payload.partial_fill_detected_at))
    <= Date.parse(String(events.at(0)?.payload.stop_submit_started_at)));
  assert.ok(Date.parse(String(events.at(0)?.payload.stop_submit_started_at))
    <= Date.parse(String(events.at(0)?.payload.stop_exchange_ack_at)));
});

test('service persists replacement stop ownership before cancelling the old partial stop', async () => {
  const order: string[] = [];
  const service = new SignalSubscriberExecutionService(
    {} as never,
    {} as never,
    { recordHireExecutionEvent: async () => order.push('persist') } as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
  );
  (service as any).activeTrading = {
    submitStopOrder: async () => 7002,
    getMarkPrice: async () => 64_126.31,
  };
  (service as any).cancelManagedOrderGone = async () => {
    order.push('cancel-old');
    return { gone: true, attempts: 1 };
  };
  await (service as any).protectPartialFillAndRetainRemainder(
    'agent', 'user', 'cycle', 'participant',
    {
      direction: 'SHORT', qty: 0.031206116, bitfinexOrderId: 6001,
      limitPrice: 64_126.31, partialFillQty: 0.01, partialFillStopOrderId: 7001,
    },
    {}, { risk: { stop_loss_margin_pct: 10 } }, 0.015,
  );
  assert.deepEqual(order, ['persist', 'cancel-old', 'persist']);
});

test('pre-enrichment limit stop reference is no wider than the real limit execution boundary', () => {
  const buyLimit = 64_100;
  const buyExecution = 64_000; // BUY limits fill at or below their limit.
  const longReference = protectiveStopReferencePrice(buyLimit, buyLimit, 63_900);
  assert.equal(longReference, buyLimit);
  assert.ok(longReference * 0.999 >= buyExecution * 0.999);

  const sellLimit = 64_100;
  const sellExecution = 64_200; // SELL limits fill at or above their limit.
  const shortReference = protectiveStopReferencePrice(sellLimit, sellLimit, 64_300);
  assert.equal(shortReference, sellLimit);
  assert.ok(shortReference * 1.001 <= sellExecution * 1.001);

  assert.equal(protectiveStopReferencePrice(0, buyLimit, 63_900), buyLimit);
  assert.equal(protectiveStopReferencePrice(0, 0, 63_900), 63_900);
});

test('verified fill submits protective stop before trade enrichment and persists exact ACK timing', async () => {
  const order: string[] = [];
  const events: Array<{ type: string; payload: Record<string, unknown> }> = [];
  const exchangeFillAtMs = Date.now() - 2_000;
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.activeTrading = {
    submitStopOrder: async () => {
      order.push('stop-submit');
      await new Promise<void>((resolve) => setTimeout(resolve, 5));
      order.push('stop-ack');
      return 7003;
    },
  };
  service.cancelManagedOrderGone = async () => {
    order.push('cancel-start');
    await new Promise<void>((resolve) => setTimeout(resolve, 12));
    order.push('cancel-confirmed');
    return { gone: true, attempts: 1, reason: 'CANCELLED' };
  };
  service.resolveExchangeTradesFillEvidence = async () => {
    order.push('trade-enrichment');
    return {
      price: 64_279,
      qty: 0.0311,
      firstExecutedAtMs: exchangeFillAtMs,
      lastExecutedAtMs: exchangeFillAtMs,
    };
  };
  service.cycles = {
    recordHireExecutionEvent: async (
      _userId: string,
      _agentId: string,
      _cycleId: string,
      type: string,
      payload: Record<string, unknown>,
    ) => {
      order.push(type);
      events.push({ type, payload });
    },
  };
  service.prisma = {
    signalCycleEvent: { count: async () => 0 },
    signalCycle: { update: async () => ({}) },
  };
  service.healStuckPendingFill = async () => {};
  service.executeShowcaseMirrorClose = async () => true;
  service.positionRuntime = new Map();
  service.stopManagerCircuitOpen = new Map();

  const recorded = await service.recordCancelRaceFillOwned(
    'agent',
    'user',
    { id: 'cycle', status: SignalCycleStatus.CLOSED, tradeId: 'cont-timing' },
    'participant',
    { direction: 'SHORT', qty: 0.0311, bitfinexOrderId: 6003, limitPrice: 64_280 },
    {},
    { risk: { stop_loss_margin_pct: 10 } },
    {
      filledQty: 0.0311,
      fillPrice: 64_280,
      source: 'POSITION_DELTA',
      orderResting: true,
    },
    'SHOWCASE_CYCLE_CLOSED',
    new Date(exchangeFillAtMs).toISOString(),
  );

  assert.equal(recorded, true);
  assert.ok(order.indexOf('cancel-confirmed') < order.indexOf('stop-submit'));
  assert.ok(order.indexOf('stop-ack') < order.indexOf('trade-enrichment'));
  assert.ok(order.indexOf('trade-enrichment') < order.indexOf('FILLED'));
  const filled = events.find((event) => event.type === 'FILLED')?.payload;
  const armed = events.find((event) => event.type === 'STOP_LOSS_ARMED')?.payload;
  assert.ok(filled);
  assert.equal(filled?.fill_detection_path, 'POSITION_DELTA');
  assert.equal(filled?.fill_detection_context, 'SHOWCASE_CYCLE_CLOSED');
  assert.equal(filled?.fill_detected_at, filled?.exchange_fill_detected_at);
  assert.equal(filled?.exchange_fill_mts, new Date(exchangeFillAtMs).toISOString());
  assert.equal(filled?.source_event_at, new Date(exchangeFillAtMs).toISOString());
  assert.equal(filled?.stopOrderId, 7003);
  const submitAt = Date.parse(String(filled?.stop_submit_started_at));
  const ackAt = Date.parse(String(filled?.stop_exchange_ack_at));
  assert.ok(Number.isFinite(submitAt));
  assert.ok(Number.isFinite(ackAt));
  assert.ok(ackAt >= submitAt);
  assert.equal(filled?.stop_submit_to_ack_ms, ackAt - submitAt);
  assert.ok(Number(filled?.fill_detection_to_stop_ack_ms) >= 12);
  assert.equal(filled?.exchange_fill_to_stop_ack_ms, ackAt - exchangeFillAtMs);
  assert.equal(armed?.stop_exchange_ack_at, filled?.stop_exchange_ack_at);
});

test('live close does not await public mark enrichment before reduce-only close submission', () => {
  const source = String(
    (SignalSubscriberExecutionService.prototype as any).executeShowcaseMirrorClose,
  );
  const markPromiseAt = source.indexOf('preparedExitPricePromise');
  const closeAt = source.indexOf('closeParticipantPositionToLedgerTarget', markPromiseAt);
  const auditAwaitAt = source.indexOf('await preparedExitPricePromise', closeAt);
  assert.ok(markPromiseAt >= 0);
  assert.ok(closeAt > markPromiseAt);
  assert.ok(auditAwaitAt > closeAt);
  const criticalPromiseAll = source.slice(
    source.indexOf('Promise.all', markPromiseAt),
    source.indexOf(']);', source.indexOf('Promise.all', markPromiseAt)) + 3,
  );
  assert.doesNotMatch(criticalPromiseAll, /getMarkPrice/);
});

test('live close keeps reduce-only stop protection through exchange close confirmation', () => {
  const source = String(
    (SignalSubscriberExecutionService.prototype as any).closeParticipantPositionToLedgerTarget,
  );
  const submitAt = source.indexOf('submitPositionFlatten');
  const fallbackSubmitAt = source.indexOf('submitMarketClose');
  const confirmationAt = source.indexOf('waitForMarketCloseConfirmation');
  const postCloseCancelAt = source.indexOf('EXIT-TARGET post-close cancel stop');
  assert.ok(submitAt >= 0);
  assert.ok(fallbackSubmitAt >= 0);
  assert.ok(confirmationAt > submitAt);
  assert.ok(postCloseCancelAt > confirmationAt);
});

test('reconcile adoption cannot re-arm a lot while a showcase close owns it', () => {
  const source = String(
    (SignalSubscriberExecutionService.prototype as any).reconcileAdoptLoop,
  );
  const closeFenceAt = source.indexOf('showcase_close_in_progress');
  const rearmAt = source.indexOf('ensureProtectiveStop');
  const durableStatusFenceAt = source.indexOf('terminalized_during_stop_check');
  assert.ok(closeFenceAt >= 0);
  assert.ok(rearmAt > closeFenceAt);
  assert.ok(durableStatusFenceAt > closeFenceAt);
  assert.ok(rearmAt > durableStatusFenceAt);
});

test('authenticated close prewake uses carried terminal evidence without polling Fly or waiting for Neon', async () => {
  const service = new SignalSubscriberExecutionService(
    {} as never, {} as never, {} as never, {} as never, {} as never,
    {} as never, {} as never, {} as never,
  );
  let botFetches = 0;
  let closeOpts: Record<string, unknown> | undefined;
  (service as any).fetchExecutionBotState = async () => {
    botFetches += 1;
    throw new Error('prewake must not poll canonical Fly state');
  };
  (service as any).executeShowcaseMirrorClose = async (
    _agent: unknown, _user: unknown, _cycle: unknown, _participant: unknown,
    _meta: unknown, _creds: unknown, opts: Record<string, unknown>,
  ) => {
    closeOpts = opts;
    return true;
  };
  const priorMirror = process.env.SHOWCASE_MIRROR_ONLY;
  const priorConvergence = process.env.SHOWCASE_MIRROR_EXIT_CONVERGENCE;
  process.env.SHOWCASE_MIRROR_ONLY = 'true';
  process.env.SHOWCASE_MIRROR_EXIT_CONVERGENCE = 'true';
  try {
    const closed = await (service as any).tryImmediateShowcaseMirrorExit(
      'agent', 'user',
      { id: 'cycle', status: SignalCycleStatus.OPEN, tradeId: 'cont-close', intentEnvelope: {} },
      { id: 'participant', fillPrice: 64_500 },
      { direction: 'SHORT', qty: 0.01 },
      { apiKey: 'k', apiSecret: 's' }, false,
      {
        exitPrice: 64_400,
        exitReason: 'PROFIT_LOCK',
        sourceEventAtMs: 1_000,
        platformReceivedAtMs: 2_000,
      },
    );
    assert.equal(closed, true);
    assert.equal(botFetches, 0);
    assert.equal(closeOpts?.trigger, 'SHOWCASE_CLOSED_WEBHOOK');
    assert.equal(closeOpts?.showcaseExitPrice, 64_400);
    assert.equal(closeOpts?.sourceEventAtMs, 1_000);
    assert.equal(closeOpts?.platformReceivedAtMs, 2_000);
  } finally {
    if (priorMirror == null) delete process.env.SHOWCASE_MIRROR_ONLY;
    else process.env.SHOWCASE_MIRROR_ONLY = priorMirror;
    if (priorConvergence == null) delete process.env.SHOWCASE_MIRROR_EXIT_CONVERGENCE;
    else process.env.SHOWCASE_MIRROR_EXIT_CONVERGENCE = priorConvergence;
  }
});

test('service protection failure writes terminal state only after confirmed emergency reduction', async () => {
  const types: string[] = [];
  let emergencyCloseQty = 0;
  const service = new SignalSubscriberExecutionService(
    {} as never,
    {} as never,
    { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, type: string) => types.push(type) } as never,
    {} as never, {} as never, {} as never, {} as never, {} as never,
  );
  (service as any).activeTrading = {
    submitStopOrder: async () => { throw new Error('stop rejected'); },
    getOpenPositionDetail: async () => ({ amount: -0.02 }),
    submitMarketClose: async (_creds: unknown, input: { qty: number }) => {
      emergencyCloseQty = input.qty;
      return 8001;
    },
  };
  (service as any).pauseUserRelayForPositionMismatch = async () => {};
  (service as any).cancelManagedOrderGone = async () => ({ gone: true, attempts: 1 });
  (service as any).waitForMarketCloseConfirmation = async () => true;
  await assert.rejects(() => (service as any).protectPartialFillAndRetainRemainder(
    'agent', 'user', 'cycle', 'participant',
    { direction: 'SHORT', qty: 0.031206116, bitfinexOrderId: 6001, limitPrice: 64_126.31 },
    {}, { risk: { stop_loss_margin_pct: 10 } }, 0.015,
  ));
  assert.deepEqual(types, ['EXPIRED']);
  assert.equal(emergencyCloseQty, 0.02);
});

test('service protection failure never terminalizes an unconfirmed emergency close', async () => {
  const types: string[] = [];
  const service = new SignalSubscriberExecutionService(
    {} as never, {} as never,
    { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, type: string) => types.push(type) } as never,
    {} as never, {} as never, {} as never, {} as never, {} as never,
  );
  (service as any).activeTrading = {
    submitStopOrder: async () => { throw new Error('stop rejected'); },
    getOpenPositionDetail: async () => ({ amount: -0.02 }),
    submitMarketClose: async () => 8001,
  };
  (service as any).pauseUserRelayForPositionMismatch = async () => {};
  (service as any).cancelManagedOrderGone = async () => ({ gone: true, attempts: 1 });
  (service as any).waitForMarketCloseConfirmation = async () => false;
  await assert.rejects(() => (service as any).protectPartialFillAndRetainRemainder(
    'agent', 'user', 'cycle', 'participant',
    { direction: 'SHORT', qty: 0.031206116, bitfinexOrderId: 6001, limitPrice: 64_126.31 },
    {}, { risk: { stop_loss_margin_pct: 10 } }, 0.015,
  ));
  assert.deepEqual(types, ['RECONCILE_CANCEL_FAILED']);
});

test('service distinguishes explicit flat from an unknown exchange read during fail-close', async () => {
  for (const scenario of ['flat', 'unknown'] as const) {
    const types: string[] = [];
    const service = new SignalSubscriberExecutionService(
      {} as never, {} as never,
      { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, type: string) => types.push(type) } as never,
      {} as never, {} as never, {} as never, {} as never, {} as never,
    );
    (service as any).activeTrading = {
      submitStopOrder: async () => { throw new Error('stop rejected'); },
      getOpenPositionDetail: async () => {
        if (scenario === 'unknown') throw new Error('exchange unavailable');
        return null;
      },
    };
    (service as any).pauseUserRelayForPositionMismatch = async () => {};
    (service as any).cancelManagedOrderGone = async () => ({ gone: true, attempts: 1 });
    await assert.rejects(() => (service as any).protectPartialFillAndRetainRemainder(
      'agent', 'user', 'cycle', 'participant',
      { direction: 'SHORT', qty: 0.031206116, bitfinexOrderId: 6001, limitPrice: 64_126.31 },
      {}, { risk: { stop_loss_margin_pct: 10 } }, 0.015,
    ));
    assert.deepEqual(types, [scenario === 'flat' ? 'EXPIRED' : 'RECONCILE_CANCEL_FAILED']);
  }
});

test('service stop-trigger path refuses terminal state while residual exposure remains', async () => {
  const types: string[] = [];
  const service = new SignalSubscriberExecutionService(
    {} as never, {} as never,
    { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, type: string) => types.push(type) } as never,
    {} as never, {} as never, {} as never, {} as never, {} as never,
  );
  (service as any).activeTrading = {
    findOrder: async () => null,
    getOpenPositionDetail: async () => ({ amount: -0.005 }),
  };
  (service as any).pauseUserRelayForPositionMismatch = async () => {};
  (service as any).cancelManagedOrderGone = async () => ({ gone: true, attempts: 1 });
  const handled = await (service as any).reconcilePendingPartialFillStop(
    'agent', 'user', { id: 'cycle', status: SignalCycleStatus.OPEN, tradeId: 'cont-x' },
    'participant',
    { direction: 'SHORT', qty: 0.031206116, bitfinexOrderId: 6001, partialFillQty: 0.015, partialFillStopOrderId: 7001 },
    {}, { risk: { stop_loss_margin_pct: 10 } },
  );
  assert.equal(handled, true);
  assert.deepEqual(types, ['RECONCILE_CANCEL_FAILED']);
});

test('service stop-trigger path records fill and exit only after fresh flat proof', async () => {
  const types: string[] = [];
  const service = new SignalSubscriberExecutionService(
    {} as never, {} as never,
    { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, type: string) => types.push(type) } as never,
    {} as never, {} as never, {} as never, {} as never, {} as never,
  );
  (service as any).activeTrading = {
    findOrder: async () => null,
    getOpenPositionDetail: async () => null,
  };
  (service as any).pauseUserRelayForPositionMismatch = async () => {};
  (service as any).cancelManagedOrderGone = async () => ({ gone: true, attempts: 1 });
  await (service as any).reconcilePendingPartialFillStop(
    'agent', 'user', { id: 'cycle', status: SignalCycleStatus.OPEN, tradeId: 'cont-x' },
    'participant',
    { direction: 'SHORT', qty: 0.031206116, bitfinexOrderId: 6001, limitPrice: 64_126.31, partialFillQty: 0.015, partialFillStopOrderId: 7001 },
    {}, { risk: { stop_loss_margin_pct: 10 } },
  );
  assert.deepEqual(types, ['FILLED', 'EXIT']);
});

test('gone entry order routes any increased trade-evidence quantity', async () => {
  let recordedQty = 0;
  const service = new SignalSubscriberExecutionService(
    {} as never, {} as never, {} as never, {} as never,
    {} as never, {} as never, {} as never, {} as never,
  );
  (service as any).activeTrading = { findOrder: async () => null };
  (service as any).resolveExchangeTradesFillEvidence = async () => ({
    price: 64_127,
    qty: 0.02,
    firstExecutedAtMs: 1,
    lastExecutedAtMs: 2,
  });
  (service as any).recordCancelRaceFill = async (
    _a: string, _u: string, _c: unknown, _p: string, _m: unknown,
    _creds: unknown, _intent: unknown, fill: { filledQty: number },
  ) => {
    recordedQty = fill.filledQty;
    return true;
  };
  const handled = await (service as any).reconcilePendingPartialFillStop(
    'agent', 'user', { id: 'cycle', status: SignalCycleStatus.OPEN, tradeId: 'cont-x' },
    'participant',
    { direction: 'SHORT', qty: 0.031206116, bitfinexOrderId: 6001, partialFillQty: 0.015, partialFillStopOrderId: 7001 },
    {}, { risk: { stop_loss_margin_pct: 10 } },
  );
  assert.equal(handled, true);
  assert.equal(recordedQty, 0.02);
});

test('terminal source takes precedence over partial stop rearm in the same tick', async () => {
  let context = '';
  let closedQty = 0;
  const service = new SignalSubscriberExecutionService(
    {} as never, {} as never, {} as never, {} as never,
    {} as never, {} as never, {} as never, {} as never,
  );
  (service as any).activeTrading = {
    findOrder: async () => { throw new Error('must not rearm before terminal cleanup'); },
    getOpenPositionDetail: async () => ({ amount: -0.015 }),
  };
  (service as any).cancelManagedOrderGone = async () => ({ gone: true, attempts: 1, reason: 'NOT_FOUND' });
  (service as any).resolveExchangeTradesFillEvidence = async () => null;
  (service as any).recordCancelRaceFill = async (...args: unknown[]) => {
    context = String(args[8]);
    closedQty = Number((args[7] as { filledQty: number }).filledQty);
    return true;
  };
  const handled = await (service as any).reconcilePendingPartialFillStop(
    'agent', 'user', { id: 'cycle', status: SignalCycleStatus.CLOSED, tradeId: 'cont-x' },
    'participant',
    { direction: 'SHORT', qty: 0.031206116, bitfinexOrderId: 6001, partialFillQty: 0.015, partialFillStopOrderId: 7001 },
    {}, { risk: { stop_loss_margin_pct: 10 } },
  );
  assert.equal(handled, true);
  assert.equal(context, 'SHOWCASE_CYCLE_CLOSED');
  assert.equal(closedQty, 0.015);
});

test('terminal protected partial already flat records FILLED and EXIT without submitting a stop', async () => {
  const types: string[] = [];
  let stopSubmits = 0;
  const service = new SignalSubscriberExecutionService(
    {} as never, {} as never,
    { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, type: string) => types.push(type) } as never,
    {} as never, {} as never, {} as never, {} as never, {} as never,
  );
  (service as any).activeTrading = {
    getOpenPositionDetail: async () => null,
    submitStopOrder: async () => {
      stopSubmits += 1;
      return 9001;
    },
  };
  (service as any).cancelManagedOrderGone = async () => ({ gone: true, attempts: 1, reason: 'NOT_FOUND' });
  (service as any).resolveExchangeTradesFillEvidence = async () => null;
  const handled = await (service as any).reconcilePendingPartialFillStop(
    'agent', 'user', { id: 'cycle', status: SignalCycleStatus.CLOSED, tradeId: 'cont-x' },
    'participant',
    {
      direction: 'SHORT', qty: 0.031206116, bitfinexOrderId: 6001,
      limitPrice: 64_126.31, partialFillQty: 0.015, partialFillStopOrderId: 7001,
    },
    {}, { risk: { stop_loss_margin_pct: 10 } },
  );
  assert.equal(handled, true);
  assert.deepEqual(types, ['FILLED', 'EXIT']);
  assert.equal(stopSubmits, 0);
});

test('source-fill wake polling records only an exchange-verified fill', async () => {
  let checks = 0;
  const waits: number[] = [];
  const fill = await pollForVerifiedEntryFill({
    detect: async () => (++checks === 3 ? { qty: 0.03125, price: 64_000 } : null),
    attempts: 7,
    intervalMs: 300,
    wait: async (ms) => { waits.push(ms); },
  });
  assert.deepEqual(fill, { qty: 0.03125, price: 64_000 });
  assert.equal(checks, 3);
  assert.deepEqual(waits, [300, 300]);
});

test('source-fill wake polling stays fail-closed when Bitfinex has no fill', async () => {
  let checks = 0;
  let waits = 0;
  const fill = await pollForVerifiedEntryFill({
    detect: async () => { checks += 1; return null; },
    attempts: 7,
    intervalMs: 300,
    wait: async () => { waits += 1; },
  });
  assert.equal(fill, null);
  assert.equal(checks, 7);
  assert.equal(waits, 6);
});

test('independent authenticated direct wakes start on separate trade lanes', async () => {
  const previousExecution = process.env.SUBSCRIBER_EXECUTION_ENABLED;
  const previousWorker = process.env.RELAY_EXECUTOR_WORKER;
  process.env.SUBSCRIBER_EXECUTION_ENABLED = 'true';
  process.env.RELAY_EXECUTOR_WORKER = 'true';
  try {
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.activeDirectWakes = new Map();
    service.pendingDirectWakes = new Map();
    const launched: unknown[] = [];
    service.startDirectExecutorWake = (candidate: unknown) => { launched.push(candidate); };
    const wake = {
      trigger: 'ORDER_PLACED',
      tradeId: 'cont-queued-direct',
      at: new Date().toISOString(),
    };

    assert.equal(await service.acceptDirectExecutorWake(wake), true);
    assert.deepEqual(launched, [wake]);
  } finally {
    if (previousExecution == null) delete process.env.SUBSCRIBER_EXECUTION_ENABLED;
    else process.env.SUBSCRIBER_EXECUTION_ENABLED = previousExecution;
    if (previousWorker == null) delete process.env.RELAY_EXECUTOR_WORKER;
    else process.env.RELAY_EXECUTOR_WORKER = previousWorker;
  }
});

test('post-commit ORDER_PLACED does not queue behind its running pre-wake', async () => {
  const previousExecution = process.env.SUBSCRIBER_EXECUTION_ENABLED;
  const previousWorker = process.env.RELAY_EXECUTOR_WORKER;
  process.env.SUBSCRIBER_EXECUTION_ENABLED = 'true';
  process.env.RELAY_EXECUTOR_WORKER = 'true';
  try {
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.pendingDirectWakes = new Map();
    service.activeDirectWakes = new Map([['trade:cont-same', {
      trigger: 'ORDER_PLACED', tradeId: 'cont-same', at: '2026-08-11T04:35:00.000Z',
    }]]);
    const postCommit = {
      trigger: 'ORDER_PLACED', tradeId: 'cont-same', at: '2026-08-11T04:35:00.250Z',
    };

    assert.equal(await service.acceptDirectExecutorWake(postCommit), true);
    assert.equal(service.pendingDirectWakes.size, 0);
  } finally {
    if (previousExecution == null) delete process.env.SUBSCRIBER_EXECUTION_ENABLED;
    else process.env.SUBSCRIBER_EXECUTION_ENABLED = previousExecution;
    if (previousWorker == null) delete process.env.RELAY_EXECUTOR_WORKER;
    else process.env.RELAY_EXECUTOR_WORKER = previousWorker;
  }
});

test('post-commit POSITION_OPENED does not duplicate its running pre-wake', async () => {
  const previousExecution = process.env.SUBSCRIBER_EXECUTION_ENABLED;
  const previousWorker = process.env.RELAY_EXECUTOR_WORKER;
  process.env.SUBSCRIBER_EXECUTION_ENABLED = 'true';
  process.env.RELAY_EXECUTOR_WORKER = 'true';
  try {
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.pendingDirectWakes = new Map();
    service.activeDirectWakes = new Map([['trade:cont-fill', {
      trigger: 'POSITION_OPENED', tradeId: 'cont-fill', at: '2026-08-11T06:59:32.389Z',
    }]]);
    service.prioritySourceFillWakes = new Set(['POSITION_OPENED:cont-fill']);
    service.startPrioritySourceFillWake = () => undefined;
    assert.equal(await service.acceptDirectExecutorWake({
      trigger: 'POSITION_OPENED', tradeId: 'cont-fill', at: '2026-08-11T06:59:32.500Z',
    }), true);
    assert.equal(service.pendingDirectWakes.size, 0);
  } finally {
    if (previousExecution == null) delete process.env.SUBSCRIBER_EXECUTION_ENABLED;
    else process.env.SUBSCRIBER_EXECUTION_ENABLED = previousExecution;
    if (previousWorker == null) delete process.env.RELAY_EXECUTOR_WORKER;
    else process.env.RELAY_EXECUTOR_WORKER = previousWorker;
  }
});

test('source fill starts beside an unrelated running reprice on its own trade lane', async () => {
  const previousExecution = process.env.SUBSCRIBER_EXECUTION_ENABLED;
  const previousWorker = process.env.RELAY_EXECUTOR_WORKER;
  process.env.SUBSCRIBER_EXECUTION_ENABLED = 'true';
  process.env.RELAY_EXECUTOR_WORKER = 'true';
  try {
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.running = true;
    service.wakeQueued = false;
    service.activeDirectWakes = new Map([['trade:cont-unrelated-reprice', {
      trigger: 'LIMIT_UPDATED', tradeId: 'cont-unrelated-reprice', at: '2026-08-12T00:00:00.000Z',
    }]]);
    service.pendingDirectWakes = new Map();
    service.prioritySourceFillWakes = new Set<string>();
    service.completedDirectWakeAt = new Map<string, number>();
    service.logger = { warn: () => undefined };
    let resolveExecution!: () => void;
    const execution = new Promise<void>((resolve) => { resolveExecution = resolve; });
    const launched: unknown[] = [];
    service.executePersistedFastWake = async (wake: unknown) => {
      launched.push(wake);
      await execution;
    };
    const fillWake = {
      trigger: 'POSITION_OPENED', tradeId: 'cont-exact-fill', at: '2026-08-12T00:00:01.000Z',
    };

    assert.equal(await service.acceptDirectExecutorWake(fillWake), true);
    assert.equal(await service.acceptDirectExecutorWake({ ...fillWake, at: '2026-08-12T00:00:01.200Z' }), true);
    assert.deepEqual(launched, [fillWake]);
    assert.equal(service.pendingDirectWakes.size, 0);
    assert.equal(service.activeDirectWakes.has('trade:cont-exact-fill'), true);
    assert.equal(service.hasQueuedOrActiveSourceFillWake('cont-exact-fill'), true);

    resolveExecution();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(service.activeDirectWakes.has('trade:cont-exact-fill'), false);
    assert.equal(service.hasQueuedOrActiveSourceFillWake('cont-exact-fill'), false);
    assert.equal(service.wakeQueued, true);
  } finally {
    if (previousExecution == null) delete process.env.SUBSCRIBER_EXECUTION_ENABLED;
    else process.env.SUBSCRIBER_EXECUTION_ENABLED = previousExecution;
    if (previousWorker == null) delete process.env.RELAY_EXECUTOR_WORKER;
    else process.env.RELAY_EXECUTOR_WORKER = previousWorker;
  }
});

test('same-trade source fill is retained as the next wake while another trade is independent', () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.activeDirectWakes = new Map([['trade:cont-priority-fill', {
    trigger: 'LIMIT_UPDATED', tradeId: 'cont-priority-fill', at: '2026-08-11T20:43:22.070Z',
  }]]);
  service.pendingDirectWakes = new Map();
  const preWake = {
    trigger: 'POSITION_OPENED', tradeId: 'cont-priority-fill', at: '2026-08-11T20:43:23.114Z',
  };
  const postCommit = {
    trigger: 'POSITION_OPENED', tradeId: 'cont-priority-fill', at: '2026-08-11T20:43:23.842Z',
  };

  service.enqueueDirectWake(preWake);
  service.enqueueDirectWake(postCommit);

  assert.equal(service.pendingDirectWakes.get('trade:cont-priority-fill'), preWake);
  assert.equal(service.hasQueuedOrActiveSourceFillWake('cont-priority-fill'), true);
  assert.equal(service.hasQueuedOrActiveSourceFillWake('cont-other'), false);
});

test('queued LIMIT_UPDATED burst coalesces to newest exact revision', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.activeDirectWakes = new Map([['trade:cont-burst', {
    trigger: 'ORDER_PLACED', tradeId: 'cont-burst', at: '2026-08-11T04:35:00.000Z',
  }]]);
  service.pendingDirectWakes = new Map();
  const oldWake = {
    trigger: 'LIMIT_UPDATED', tradeId: 'cont-burst', at: '2026-08-11T04:35:01.000Z',
  };
  const newWake = {
    trigger: 'LIMIT_UPDATED', tradeId: 'cont-burst', at: '2026-08-11T04:35:01.500Z',
  };

  service.enqueueDirectWake(oldWake);
  service.enqueueDirectWake(newWake);
  assert.equal(service.pendingDirectWakes.get('trade:cont-burst'), newWake);
});

test('new LIMIT_UPDATED queues behind an in-flight older revision', () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.activeDirectWakes = new Map([['trade:cont-active-chase', {
    trigger: 'LIMIT_UPDATED', tradeId: 'cont-active-chase', at: '2026-08-11T04:35:01.000Z',
  }]]);
  service.pendingDirectWakes = new Map();
  const newest = {
    trigger: 'LIMIT_UPDATED', tradeId: 'cont-active-chase', at: '2026-08-11T04:35:02.000Z',
  };

  service.enqueueDirectWake(newest);
  assert.equal(service.pendingDirectWakes.get('trade:cont-active-chase'), newest);
});

test('signed LIMIT_UPDATED fast wake reprices only its exact owned pending order', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  const intent = {
    schema: 'dcf-signal-intent/v1',
    signalId: 'cont-fast-reprice',
    trade_id: 'cont-fast-reprice',
    action: 'ENTER',
    direction: 'SHORT',
    entry: {
      mode: 'EXACT_LIMIT',
      reference: 'SHOWCASE_EXACT_LIMIT',
      exact_limit_price: 64_218.63,
      exact_qty_btc: 0.02361,
    },
    context: {
      signed_showcase_event: true,
      showcase_event: 'LIMIT_UPDATED',
      platform_received_at: new Date().toISOString(),
      entry_limit_policy: 'micro_sr_structural_limit_v1',
    },
  };
  service.prisma = {
    signalCycleParticipant: {
      findMany: async () => [{
        id: 'participant-fast-reprice',
        cycleId: 'cycle-fast-reprice',
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: {
          id: 'cycle-fast-reprice',
          tradeId: 'cont-fast-reprice',
          status: SignalCycleStatus.PENDING_ENTRY,
          intentEnvelope: intent,
        },
      }],
    },
  };
  service.loadExecutionMeta = async () => ({
    bitfinexOrderId: 241795753908,
    direction: 'SHORT',
    limitPrice: 64_238.17,
  });
  const creds = { apiKey: 'redacted', apiSecret: 'redacted' };
  service.exchanges = { getUserCredentials: async () => creds };
  service.activeTrading = { getMarkPrice: async () => 64_220 };
  let replaceArgs: unknown[] | null = null;
  service.replaceRestingLimit = async (...args: unknown[]) => {
    replaceArgs = args;
  };
  const instance = {
    id: 'instance-fast-reprice',
    agentId: 'agent-fast-reprice',
    userId: 'user-fast-reprice',
    exchangeProvider: 'bitfinex',
    status: TradingAgentInstanceStatus.ACTIVE,
    dashboardState: {
      relayExecutionMode: 'LIVE',
      relayPolicyVersion: 'continuous_only_v5',
      realTradingConfirmedAt: new Date().toISOString(),
    },
  };

  assert.equal(
    await service.tryImmediateSignedLimitUpdate(
      instance.agentId,
      instance,
      'cont-fast-reprice',
    ),
    true,
  );
  assert.ok(replaceArgs);
  assert.equal(replaceArgs![2], 'cycle-fast-reprice');
  assert.deepEqual(replaceArgs![7], {
    newLimit: 64_218.63,
    mark: 64_220,
    now: (replaceArgs![7] as { now: number }).now,
    chaseLabel: 'signed-limit=64218.63',
    event: 'BOT_ANCHOR_CHASE',
    tradeId: 'cont-fast-reprice',
  });

  replaceArgs = null;
  assert.equal(
    await service.tryImmediateSignedLimitUpdate(
      instance.agentId,
      instance,
      'cont-other-trade',
    ),
    false,
  );
  assert.equal(replaceArgs, null);
});

test('executor direct wake requires the exact shared control secret', () => {
  assert.equal(executorWakeAuthorized('secret', 'secret'), true);
  assert.equal(executorWakeAuthorized('wrong', 'secret'), false);
  assert.equal(executorWakeAuthorized(undefined, 'secret'), false);
});

test('executor direct wake accepts only fresh bounded relay events', () => {
  assert.equal(parseExecutorWakeRequest({
    trigger: 'ORDER_PLACED',
    at: new Date().toISOString(),
    tradeId: 'cont-test',
  })?.tradeId, 'cont-test');
  assert.equal(parseExecutorWakeRequest({
    trigger: 'ORDER_PLACED',
    at: new Date(Date.now() - 121_000).toISOString(),
    tradeId: 'cont-stale',
  }), null);
  assert.equal(parseExecutorWakeRequest({
    trigger: 'DELETE_EVERYTHING',
    at: new Date().toISOString(),
    tradeId: 'cont-bad',
  }), null);
});

test('persisted close wake matches a cancel-race relink by canonical showcase id', () => {
  assert.equal(
    persistedCloseWakeMatchesParticipant(
      'cont-fa8ce196a716',
      'relink:unknown:cont-fa8ce196a716:1786346662320',
      'cont-fa8ce196a716',
    ),
    true,
  );
  assert.equal(
    persistedCloseWakeMatchesParticipant(
      'cont-other',
      'relink:unknown:cont-fa8ce196a716:1786346662320',
      'cont-fa8ce196a716',
    ),
    false,
  );
  assert.equal(
    persistedCloseWakeMatchesParticipant(
      null,
      'relink:unknown:cont-fa8ce196a716:1786346662320',
      null,
    ),
    false,
  );
});

test('canonical showcase mirror convergence suppresses local thesis exits', () => {
  assert.equal(
    shouldRunLocalRealSideSafetyNet({
      simActive: false,
      showcaseMirrorOnly: true,
      mirrorExitConvergence: true,
    }),
    false,
  );
  assert.equal(
    shouldRunLocalRealSideSafetyNet({
      simActive: false,
      showcaseMirrorOnly: true,
      mirrorExitConvergence: false,
    }),
    true,
  );
  assert.equal(
    shouldRunLocalRealSideSafetyNet({
      simActive: false,
      showcaseMirrorOnly: false,
      mirrorExitConvergence: true,
    }),
    true,
  );
  assert.equal(
    shouldRunLocalRealSideSafetyNet({
      simActive: true,
      showcaseMirrorOnly: false,
      mirrorExitConvergence: false,
    }),
    false,
  );
});

test('orphan cleanup never cancels orders owned by active participants', () => {
  assert.equal(participantCanOwnOrphanOrder(SignalCycleStatus.INTENT), false);
  assert.equal(participantCanOwnOrphanOrder(SignalCycleStatus.PENDING_ENTRY), false);
  assert.equal(participantCanOwnOrphanOrder(SignalCycleStatus.OPEN), false);
  assert.equal(participantCanOwnOrphanOrder(SignalCycleStatus.CLOSED), true);
  assert.equal(participantCanOwnOrphanOrder(SignalCycleStatus.EXPIRED), true);
});

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

test('orphan adoption never steals a newly created exchange order from signed entry ownership', () => {
  const nowMs = Date.parse('2026-08-12T18:26:54.000Z');
  assert.equal(
    shouldDeferRecentOrphanOrderAdoption(nowMs - 5_000, nowMs),
    true,
  );
  assert.equal(
    shouldDeferRecentOrphanOrderAdoption(nowMs - 59_999, nowMs),
    true,
  );
  assert.equal(
    shouldDeferRecentOrphanOrderAdoption(nowMs - 60_000, nowMs),
    false,
  );
  assert.equal(
    shouldDeferRecentOrphanOrderAdoption(nowMs + 5_001, nowMs),
    true,
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

test('source-absence fallback cannot close unless its immutable evidence event persists first', async () => {
  let closeCalls = 0;
  const service = new SignalSubscriberExecutionService(
    {} as never, {} as never,
    { recordHireExecutionEvent: async () => { throw new Error('database unavailable'); } } as never,
    {} as never, {} as never, {} as never, {} as never, {} as never,
  );
  (service as any).fetchExecutionBotState = async () => ({});
  (service as any).detectShowcaseTradeClosed = () => ({ closed: false });
  (service as any).trackShowcasePositionAbsent = () => ({
    schema: 'source_absence_evidence_v1',
    absenceScope: 'POSITION',
    verdict: 'ABSENT_ACTIONABLE',
    canonicalTradeId: 'cont-absence',
    sourceGitRev: 'revision-proof',
    snapshotSeq: 900,
    snapshotTs: '2026-08-15T01:00:00.000Z',
    snapshotAgeSec: 0.1,
    maxSnapshotAgeSec: 15,
    positionsSynced: true,
    ordersSynced: true,
    tradesSynced: true,
    sourcePositionPresent: false,
    sourceOrderPresent: false,
    sourceSignalPresent: false,
    sourceTradePresent: false,
    sourceTradesMapPresent: false,
    misses: 2,
    requiredMisses: 2,
    firstAbsentAtMs: 1_000,
    observedAtMs: 61_000,
    elapsedMs: 60_000,
    graceMs: 60_000,
    missingEvidence: [],
    actionable: true,
  });
  (service as any).executeShowcaseMirrorClose = async () => {
    closeCalls += 1;
    return true;
  };
  const priorMirror = process.env.SHOWCASE_MIRROR_ONLY;
  const priorConvergence = process.env.SHOWCASE_MIRROR_EXIT_CONVERGENCE;
  process.env.SHOWCASE_MIRROR_ONLY = 'true';
  process.env.SHOWCASE_MIRROR_EXIT_CONVERGENCE = 'true';
  try {
    await assert.rejects(
      () => (service as any).tryImmediateShowcaseMirrorExit(
        'agent', 'user',
        { id: 'cycle', status: SignalCycleStatus.OPEN, tradeId: 'cont-absence', intentEnvelope: {} },
        { id: 'participant', fillPrice: 64_500 },
        { direction: 'SHORT', qty: 0.01 },
        { apiKey: 'k', apiSecret: 's' },
      ),
      /database unavailable/,
    );
    assert.equal(closeCalls, 0);
  } finally {
    if (priorMirror == null) delete process.env.SHOWCASE_MIRROR_ONLY;
    else process.env.SHOWCASE_MIRROR_ONLY = priorMirror;
    if (priorConvergence == null) delete process.env.SHOWCASE_MIRROR_EXIT_CONVERGENCE;
    else process.env.SHOWCASE_MIRROR_EXIT_CONVERGENCE = priorConvergence;
  }
});

test('source-position absence evidence is UNKNOWN when canonical snapshot proof is incomplete', () => {
  const observedAtMs = Date.parse('2026-08-15T01:00:00.000Z');
  const evidence = buildShowcasePositionAbsenceEvidence({
    bot: {
      source_git_rev: 'revision-a',
      positions: [],
      orders: [],
      trades: [],
      trades_map: {},
      signal_info: { signals: [] },
    },
    tradeId: 'cont-evidence',
    misses: 100,
    firstAbsentAtMs: observedAtMs - 120_000,
    nowMs: observedAtMs,
  });
  assert.equal(evidence.verdict, 'UNKNOWN');
  assert.equal(evidence.actionable, false);
  assert.equal(evidence.misses, 100);
  assert.equal(evidence.sourceOrderPresent, false);
  assert.equal(evidence.sourceTradesMapPresent, false);
  assert.ok(evidence.missingEvidence.includes('state_integrity.snapshot_seq'));
  assert.ok(evidence.missingEvidence.includes('state_integrity.positions_synced'));
});

test('complete source-position evidence treats a pending canonical lifecycle as PRESENT', () => {
  const firstAbsentAtMs = Date.parse('2026-08-15T01:00:00.000Z');
  const evidence = buildShowcasePositionAbsenceEvidence({
    bot: {
      source_git_rev: 'revision-b',
      positions: [],
      orders: [{ trade_id: 'cont-evidence', status: 'PENDING' }],
      trades: [],
      trades_map: { 'cont-evidence': {} },
      signal_info: { signals: [{ trade_id: 'cont-evidence', status: 'PENDING' }] },
      state_integrity: {
        snapshot_seq: 731,
        snapshot_ts: '2026-08-15T01:01:00.000Z',
        snapshot_age_sec: 0.2,
        positions_synced: true,
        orders_synced: true,
        trades_synced: true,
      },
    },
    tradeId: 'cont-evidence',
    misses: 2,
    firstAbsentAtMs,
    nowMs: firstAbsentAtMs + 60_000,
  });
  assert.equal(evidence.verdict, 'PRESENT');
  assert.equal(evidence.actionable, false);
  assert.equal(evidence.snapshotSeq, 731);
  assert.equal(evidence.snapshotAgeSec, 0.2);
  assert.equal(evidence.sourceGitRev, 'revision-b');
  assert.equal(evidence.sourceOrderPresent, true);
  assert.equal(evidence.sourceSignalPresent, true);
  assert.equal(evidence.sourceTradesMapPresent, true);
  assert.equal(evidence.elapsedMs, 60_000);
  assert.deepEqual(evidence.missingEvidence, []);
});

test('complete fresh evidence is actionable only when the canonical ID is absent everywhere', () => {
  const nowMs = Date.parse('2026-08-15T01:02:00.000Z');
  const evidence = buildShowcasePositionAbsenceEvidence({
    bot: {
      source_git_rev: 'revision-absent',
      positions: [],
      orders: [],
      trades: [],
      trades_map: {},
      signal_info: { signals: [] },
      state_integrity: {
        snapshot_seq: 735,
        snapshot_ts: '2026-08-15T01:02:00.000Z',
        snapshot_age_sec: 0.1,
        positions_synced: true,
        orders_synced: true,
        trades_synced: true,
      },
    },
    tradeId: 'cont-absent',
    misses: 2,
    firstAbsentAtMs: nowMs - 60_000,
    nowMs,
  });
  assert.equal(evidence.verdict, 'ABSENT_ACTIONABLE');
  assert.equal(evidence.actionable, true);
  assert.equal(evidence.sourcePositionPresent, false);
  assert.equal(evidence.sourceOrderPresent, false);
  assert.equal(evidence.sourceSignalPresent, false);
  assert.equal(evidence.sourceTradePresent, false);
  assert.equal(evidence.sourceTradesMapPresent, false);
});

test('a source position present in a complete snapshot can never be absence-actionable', () => {
  const nowMs = Date.parse('2026-08-15T01:01:00.000Z');
  const evidence = buildShowcasePositionAbsenceEvidence({
    bot: {
      source_git_rev: 'revision-c',
      positions: [{ trade_id: 'cont-present' }],
      orders: [],
      trades: [],
      trades_map: {},
      signal_info: { signals: [] },
      state_integrity: {
        snapshot_seq: 732,
        snapshot_ts: '2026-08-15T01:01:00.000Z',
        snapshot_age_sec: 0.1,
        positions_synced: true,
        orders_synced: true,
        trades_synced: true,
      },
    },
    tradeId: 'cont-present',
    misses: 99,
    firstAbsentAtMs: nowMs - 120_000,
    nowMs,
  });
  assert.equal(evidence.verdict, 'PRESENT');
  assert.equal(evidence.actionable, false);
  assert.equal(evidence.sourcePositionPresent, true);
});

test('an internally valid but stale source snapshot is UNKNOWN and cannot close a copy', () => {
  const nowMs = Date.parse('2026-08-15T01:01:30.000Z');
  const evidence = buildShowcasePositionAbsenceEvidence({
    bot: {
      source_git_rev: 'revision-stale',
      positions: [],
      orders: [],
      trades: [],
      trades_map: {},
      signal_info: { signals: [] },
      state_integrity: {
        snapshot_seq: 733,
        snapshot_ts: '2026-08-15T01:01:00.000Z',
        snapshot_age_sec: 0,
        positions_synced: true,
        orders_synced: true,
        trades_synced: true,
      },
    },
    tradeId: 'cont-stale',
    misses: 99,
    firstAbsentAtMs: nowMs - 120_000,
    nowMs,
  });
  assert.equal(evidence.verdict, 'UNKNOWN');
  assert.equal(evidence.actionable, false);
  assert.equal(evidence.snapshotAgeSec, 30);
  assert.ok(evidence.missingEvidence.includes('state_integrity.snapshot_stale'));
});

test('everywhere-absence proof does not count a trade that remains pending or known', () => {
  const nowMs = Date.parse('2026-08-15T01:01:00.000Z');
  const evidence = buildShowcasePositionAbsenceEvidence({
    bot: {
      source_git_rev: 'revision-known',
      positions: [],
      orders: [{ trade_id: 'cont-known', status: 'PENDING' }],
      trades: [],
      trades_map: { 'cont-known': {} },
      signal_info: { signals: [{ trade_id: 'cont-known', status: 'PENDING' }] },
      state_integrity: {
        snapshot_seq: 734,
        snapshot_ts: '2026-08-15T01:01:00.000Z',
        snapshot_age_sec: 0.1,
        positions_synced: true,
        orders_synced: true,
        trades_synced: true,
      },
    },
    tradeId: 'cont-known',
    misses: 3,
    firstAbsentAtMs: nowMs,
    nowMs,
    absenceScope: 'EVERYWHERE',
    requiredMisses: 3,
    graceMs: 0,
  });
  assert.equal(evidence.absenceScope, 'EVERYWHERE');
  assert.equal(evidence.actionable, false);
  assert.equal(evidence.verdict, 'PRESENT');
  assert.equal(evidence.sourceOrderPresent, true);
  assert.equal(evidence.sourceTradesMapPresent, true);
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

test('replacement stop remains owned until its predecessor is confirmed cleared', () => {
  const meta = {
    stopOrderId: 202,
    supersededStopOrderId: 201,
    partialFillStopOrderId: 203,
    supersededPartialStopOrderId: 204,
  };
  assert.deepEqual(ownedStopOrderIds(meta), [202, 203, 204, 201]);
  assert.deepEqual(untrackedActiveOrderIds([201, 202, 203, 204], [meta]), []);
});

test('generic stop rearm persists replacement ownership before clearing a missing predecessor', async () => {
  const service = new SignalSubscriberExecutionService(
    {} as never, {} as never, {} as never, {} as never, {} as never,
    {} as never, {} as never, {} as never,
  ) as any;
  const actions: string[] = [];
  const events: Array<{ type: string; payload: Record<string, unknown> }> = [];
  service.activeTrading = {
    findOrder: async () => null,
    submitStopOrder: async () => {
      actions.push('submit-replacement');
      return 202;
    },
  };
  service.cancelManagedOrderGone = async (_creds: unknown, orderId: number) => {
    actions.push(`cancel-${orderId}`);
    return { gone: true };
  };
  service.cycles = {
    recordHireExecutionEvent: async (_u: unknown, _a: unknown, _c: unknown, type: string, payload: Record<string, unknown>) => {
      events.push({ type, payload });
    },
  };
  await service.ensureProtectiveStop(
    'agent', 'user', 'cycle', 'participant',
    { direction: 'LONG', qty: 0.01, limitPrice: 64_000, stopOrderId: 201 },
    {} as never,
    { risk: { stop_loss_margin_pct: -40 } },
  );
  assert.deepEqual(actions, ['submit-replacement', 'cancel-201']);
  assert.deepEqual(events.map((event) => event.type), ['STOP_LOSS_ARMED', 'UPDATE_STOPS']);
  assert.equal(events[0]?.payload.stopOrderId, 202);
  assert.equal(events[0]?.payload.supersededStopOrderId, 201);
  assert.equal(events[1]?.payload.event, 'SUPERSEDED_STOP_CLEARED');
  assert.equal(events[1]?.payload.supersededStopOrderId, null);
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

test('pending partial remains reportable until fresh exchange convergence proves exact quantity', () => {
  const diffs = [
    { type: 'QTY_DELTA', tradeId: 'cont-partial', copyQty: 0.015, showcaseQty: 0.031206116 },
    { type: 'QTY_DELTA', tradeId: 'cont-open-underfill', copyQty: 0.015, showcaseQty: 0.031206116 },
  ];
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

test('fresh signed order tolerates canonical snapshot propagation lag without weakening abandon safety', () => {
  const now = Date.parse('2026-08-11T04:00:10.000Z');
  const intent = {
    action: 'ENTER',
    trade_id: 'cont-propagating',
    context: {
      signed_showcase_event: true,
      showcase_event: 'ORDER_PLACED',
      showcase_event_at: '2026-08-11T04:00:05.500Z',
      platform_received_at: '2026-08-11T04:00:06.000Z',
    },
  };
  assert.equal(
    showcaseAbsentWithinOrderPropagationGrace('cont-propagating', intent, now, 15_000),
    true,
  );
  assert.equal(
    showcaseAbsentWithinOrderPropagationGrace('cont-propagating', intent, now + 15_001, 15_000),
    false,
  );
  assert.equal(
    showcaseAbsentWithinOrderPropagationGrace('different-trade', intent, now, 15_000),
    false,
  );
  assert.equal(
    showcaseAbsentWithinOrderPropagationGrace(
      'cont-propagating',
      { ...intent, context: { ...intent.context, signed_showcase_event: false } },
      now,
      15_000,
    ),
    false,
  );
  assert.equal(
    showcaseAbsentWithinOrderPropagationGrace(
      'cont-propagating',
      { ...intent, context: { ...intent.context, showcase_event: 'ORDER_CANCELLED' } },
      now,
      15_000,
    ),
    false,
  );
});

test('cancel-by-exchange cannot close a participant during exact-limit replacement', () => {
  const now = Date.parse('2026-08-11T05:55:05.000Z');
  const intent = {
    action: 'ENTER',
    trade_id: 'cont-reprice',
    context: {
      signed_showcase_event: true,
      showcase_event: 'LIMIT_UPDATED',
      showcase_event_at: '2026-08-11T05:55:01.690Z',
      platform_received_at: '2026-08-11T05:55:02.476Z',
    },
  };
  assert.equal(
    shouldDeferCancelByExchangeForReplacement(
      'cont-reprice', intent, 241797238088, 241793992470, now,
    ),
    true,
  );
  assert.equal(
    shouldDeferCancelByExchangeForReplacement(
      'cont-reprice', intent, 241797238088, 241797238088, now,
    ),
    true,
  );
  assert.equal(
    shouldDeferCancelByExchangeForReplacement(
      'cont-reprice', intent, 241797238088, 241797238088, now + 20_000,
    ),
    false,
  );
});

test('stale monitor ownership recognizes a durable order or intent revision advance', () => {
  const oldIntent = { context: { showcase_event_seq: 7, showcase_event_at: '2026-08-11T07:00:00Z' } };
  const newIntent = { context: { showcase_event_seq: 8, showcase_event_at: '2026-08-11T07:00:02Z' } };
  assert.equal(pendingEntryOwnershipAdvanced(1001, oldIntent, 1002, newIntent), true);
  assert.equal(pendingEntryOwnershipAdvanced(1001, oldIntent, 1001, newIntent), true);
  assert.equal(pendingEntryOwnershipAdvanced(1001, oldIntent, 1001, oldIntent), false);
});

test('Bitfinex reprice amends the exact resting order in place without a cancel gap', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  const activeTrading: any = Object.create(BitfinexTradingClient.prototype);
  const calls: string[] = [];
  activeTrading.findOrder = async () => ({ id: 9_002, amountOrig: -0.031, amount: -0.031 });
  activeTrading.updateLimitOrder = async (_creds: unknown, input: { orderId: number; price: number }) => {
    calls.push(`update:${input.orderId}:${input.price}`);
    return input.orderId;
  };
  activeTrading.cancelOrder = async () => {
    calls.push('cancel');
  };
  activeTrading.submitLimitOrder = async () => {
    calls.push('submit');
    return 9_003;
  };
  service.activeTrading = activeTrading;
  service.logger = { log: () => undefined, warn: () => undefined, error: () => undefined };
  service.positionRuntime = new Map();
  service.hydrateRuntime = () => ({});
  let persisted: any = null;
  service.cycles = {
    recordHireExecutionEvent: async (_userId: string, _agentId: string, _cycleId: string, _action: string, payload: Record<string, unknown>) => {
      persisted = payload;
    },
  };

  await service.replaceRestingLimitOwned(
    'agent-native-update',
    'user-native-update',
    'cycle-native-update',
    'participant-native-update',
    {
      bitfinexOrderId: 9_002,
      direction: 'SHORT',
      limitPrice: 64_100,
      qty: 0.031,
      limitChaseCount: 1,
    },
    { apiKey: 'redacted', apiSecret: 'redacted' },
    { action: 'ENTER', direction: 'SHORT', entry: {}, risk: { max_margin_usd: 20 } },
    {
      newLimit: 64_075,
      mark: 64_080,
      now: 1_700_000_000_000,
      chaseLabel: 'signed-limit=64075.00',
      event: 'BOT_ANCHOR_CHASE',
      tradeId: 'cont-native-update',
    },
  );

  assert.deepEqual(calls, ['update:9002:64075']);
  assert.equal(persisted?.bitfinexOrderId, 9_002);
  assert.equal(persisted?.limitPrice, 64_075);
  assert.equal(persisted?.replacementMode, 'BITFINEX_IN_PLACE_UPDATE');
  assert.equal(persisted?.limitChaseCount, 2);
});

test('rapid signed reprices execute only against the newest durable order generation', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.participantMoneyLane = new Map();
  const newestIntent = {
    action: 'ENTER', signalId: 'cont-rapid-reprice', trade_id: 'cont-rapid-reprice', direction: 'SHORT',
    entry: {
      mode: 'EXACT_LIMIT', reference: 'SHOWCASE_EXACT_LIMIT',
      exact_limit_price: 64_050, exact_qty_btc: 0.03,
    },
    context: {
      signed_showcase_event: true, showcase_event: 'LIMIT_UPDATED',
      showcase_event_at: new Date(Date.now() - 100).toISOString(),
      platform_received_at: new Date(Date.now() - 50).toISOString(),
      entry_limit_policy: 'deterministic_0.1pct_offset_v1',
    },
  };
  service.prisma = { signalCycleParticipant: { findUnique: async () => ({
    id: 'participant-rapid', cycleId: 'cycle-rapid', status: SignalCycleStatus.PENDING_ENTRY,
    cycle: { id: 'cycle-rapid', tradeId: 'cont-rapid-reprice', status: SignalCycleStatus.PENDING_ENTRY, intentEnvelope: newestIntent },
  }) } };
  service.loadExecutionMeta = async () => ({
    direction: 'SHORT', bitfinexOrderId: 9002, limitPrice: 64_010, qty: 0.03,
  });
  service.logger = { log: () => undefined, warn: () => undefined, error: () => undefined };
  let ownedArgs: any[] = [];
  service.replaceRestingLimitOwned = async (...args: any[]) => { ownedArgs = args; };

  await service.replaceRestingLimit(
    'agent', 'user', 'cycle-rapid', 'participant-rapid',
    { direction: 'SHORT', bitfinexOrderId: 9001, limitPrice: 64_000, qty: 0.03 },
    {}, { action: 'ENTER' },
    {
      newLimit: 64_000, mark: 64_020, now: Date.now(), chaseLabel: '',
      event: 'BOT_ANCHOR_CHASE', tradeId: 'cont-rapid-reprice',
    },
  );

  assert.equal(ownedArgs.length, 9, 'the latest signed revision must replace after the prior generation persists');
  assert.equal(ownedArgs[4].bitfinexOrderId, 9002, 'never operate on stale order 9001');
  assert.equal(ownedArgs[4].limitPrice, 64_010);
  assert.equal(ownedArgs[7].newLimit, 64_050, 'use the newest signed source limit, not the stale wake target');
});

test('duplicate signed reprice does not churn an already-correct durable order id', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.participantMoneyLane = new Map();
  const intent = {
    action: 'ENTER', signalId: 'cont-duplicate-reprice', trade_id: 'cont-duplicate-reprice', direction: 'SHORT',
    entry: { mode: 'EXACT_LIMIT', reference: 'SHOWCASE_EXACT_LIMIT', exact_limit_price: 64_010, exact_qty_btc: 0.03 },
    context: {
      signed_showcase_event: true, showcase_event: 'LIMIT_UPDATED',
      showcase_event_at: new Date(Date.now() - 100).toISOString(),
      platform_received_at: new Date(Date.now() - 50).toISOString(),
      entry_limit_policy: 'deterministic_0.1pct_offset_v1',
    },
  };
  service.prisma = { signalCycleParticipant: { findUnique: async () => ({
    id: 'participant-duplicate', cycleId: 'cycle-duplicate', status: SignalCycleStatus.PENDING_ENTRY,
    cycle: { id: 'cycle-duplicate', tradeId: 'cont-duplicate-reprice', status: SignalCycleStatus.PENDING_ENTRY, intentEnvelope: intent },
  }) } };
  service.loadExecutionMeta = async () => ({
    direction: 'SHORT', bitfinexOrderId: 9002, limitPrice: 64_010, qty: 0.03,
  });
  service.logger = { log: () => undefined, warn: () => undefined, error: () => undefined };
  let replacements = 0;
  service.replaceRestingLimitOwned = async () => { replacements += 1; };

  await service.replaceRestingLimit(
    'agent', 'user', 'cycle-duplicate', 'participant-duplicate',
    { direction: 'SHORT', bitfinexOrderId: 9001, limitPrice: 64_000, qty: 0.03 },
    {}, { action: 'ENTER' },
    { newLimit: 64_000, mark: 64_020, now: Date.now(), chaseLabel: '', event: 'BOT_ANCHOR_CHASE', tradeId: 'cont-duplicate-reprice' },
  );

  assert.equal(replacements, 0, 'the already-correct replacement must not be cancelled and recreated');
});

test('money lane makes stale gone-order monitor wait for replacement persistence and never expire it', async () => {
  const events: string[] = [];
  const oldIntent = {
    action: 'ENTER', trade_id: 'cont-race', risk: {},
    context: { showcase_event_seq: 7, showcase_event_at: '2026-08-11T07:00:00Z' },
  };
  const newIntent = {
    ...oldIntent,
    context: { showcase_event_seq: 8, showcase_event_at: '2026-08-11T07:00:02Z' },
  };
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.participantMoneyLane = new Map();
  service.botBridge = { isEnabled: () => false };
  service.cancelAbsurdPendingOrders = async () => undefined;
  service.activeTrading = {
    findOrder: async () => null,
    getOpenPositionDetail: async () => {
      throw new Error('position classification must not run for stale ownership');
    },
  };
  service.prisma = {
    signalCycle: { findUnique: async () => ({
      id: 'cycle', tradeId: 'cont-race', intentEnvelope: newIntent,
      expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY,
    }) },
    signalCycleParticipant: { findUnique: async () => ({
      id: 'participant', status: SignalCycleStatus.PENDING_ENTRY,
    }) },
  };
  service.loadExecutionMeta = async () => ({
    direction: 'SHORT', bitfinexOrderId: 1002, limitPrice: 64_000, qty: 0.03,
  });
  service.cycles = {
    recordHireExecutionEvent: async (_u: string, _a: string, _c: string, event: string) => events.push(event),
  };
  service.logger = { warn: () => undefined, log: () => undefined, error: () => undefined };

  const releaseReplacement = await service.acquireParticipantMoneyLane('participant');
  const monitor = service.monitorEntry(
    'agent', 'user',
    { id: 'cycle', tradeId: 'cont-race', intentEnvelope: oldIntent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY },
    { id: 'participant', status: SignalCycleStatus.PENDING_ENTRY },
    { direction: 'SHORT', bitfinexOrderId: 1001, limitPrice: 64_100, qty: 0.03 },
    {}, new Set<number>(), false,
  );
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.deepEqual(events, [], 'monitor cannot classify while replacement owns money lane');
  releaseReplacement();
  await monitor;
  assert.deepEqual(events, []);
});

test('fresh status change defers instead of recursing with a stale active-order snapshot', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.participantMoneyLane = new Map();
  service.botBridge = { isEnabled: () => false };
  service.cancelAbsurdPendingOrders = async () => undefined;
  service.activeTrading = { findOrder: async () => null };
  const intent = {
    action: 'ENTER', trade_id: 'cont-status-race', risk: {},
    context: { showcase_event_seq: 9, showcase_event_at: '2026-08-11T07:01:00Z' },
  };
  service.prisma = {
    signalCycle: { findUnique: async () => ({
      id: 'cycle-status', tradeId: 'cont-status-race', intentEnvelope: intent,
      expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY,
    }) },
    signalCycleParticipant: { findUnique: async () => ({
      id: 'participant-status', status: SignalCycleStatus.PENDING_ENTRY,
    }) },
  };
  service.loadExecutionMeta = async () => ({
    direction: 'SHORT', bitfinexOrderId: 2002, limitPrice: 64_000, qty: 0.03,
  });
  let replacementLaneEntered = false;
  service.applyLimitChase = async () => {
    const release = await service.acquireParticipantMoneyLane('participant-status');
    replacementLaneEntered = true;
    release();
  };
  service.logger = { warn: () => undefined, log: () => undefined, error: () => undefined };

  await Promise.race([
    service.monitorEntry(
      'agent', 'user',
      { id: 'cycle-status', tradeId: 'cont-status-race', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.INTENT },
      { id: 'participant-status', status: SignalCycleStatus.PENDING_ENTRY },
      { direction: 'SHORT', bitfinexOrderId: 2000, limitPrice: 64_100, qty: 0.03 },
      {}, new Set<number>([2001]), false,
    ),
    new Promise<never>((_, reject) => setTimeout(() => reject(new Error('participant lane deadlocked')), 250)),
  ]);
  assert.equal(replacementLaneEntered, false);
});

test('terminal monitor waits for replacement lane and cancels only the durable current order', async () => {
  const cancelled: number[] = [];
  const events: string[] = [];
  const oldIntent = { action: 'ENTER', trade_id: 'cont-terminal-lane', risk: {}, context: { showcase_event_seq: 20 } };
  const newIntent = { ...oldIntent, context: { showcase_event_seq: 21 } };
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.participantMoneyLane = new Map();
  service.prisma = {
    signalCycle: { findUnique: async () => ({
      id: 'cycle-terminal-lane', tradeId: 'cont-terminal-lane', intentEnvelope: newIntent,
      expiresAt: null, status: SignalCycleStatus.CLOSED,
    }) },
    signalCycleParticipant: { findUnique: async () => ({
      id: 'participant-terminal-lane', status: SignalCycleStatus.PENDING_ENTRY,
    }) },
  };
  service.loadExecutionMeta = async () => ({
    direction: 'SHORT', bitfinexOrderId: 8102, limitPrice: 64_000, qty: 0.03,
    replacementExchangeAckAtMs: Date.now(),
  });
  service.detectEntryFillBeforeCancel = async () => null;
  service.cancelManagedOrderGone = async (_creds: unknown, id: number) => {
    cancelled.push(id);
    return { gone: true, reason: 'CANCELLED', attempts: 1 };
  };
  service.cycles = { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, event: string) => events.push(event) };
  service.logger = { warn: () => undefined, log: () => undefined, error: () => undefined };

  const releaseReplacement = await service.acquireParticipantMoneyLane('participant-terminal-lane');
  const monitoring = service.monitorEntry(
    'agent', 'user',
    { id: 'cycle-terminal-lane', tradeId: 'cont-terminal-lane', intentEnvelope: oldIntent, expiresAt: null, status: SignalCycleStatus.CLOSED },
    { id: 'participant-terminal-lane', status: SignalCycleStatus.PENDING_ENTRY },
    { direction: 'SHORT', bitfinexOrderId: 8101, limitPrice: 64_100, qty: 0.03 },
    {}, new Set<number>(), false,
  );
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.deepEqual(cancelled, []);
  releaseReplacement();
  await monitoring;
  assert.deepEqual(cancelled, [8102]);
  assert.deepEqual(events, ['EXPIRED']);
});

test('partial-stop reconciliation waits for replacement and consumes only fresh durable ownership', async () => {
  const seen: number[] = [];
  const intent = { action: 'ENTER', trade_id: 'cont-partial-lane', risk: {}, context: { showcase_event_seq: 22 } };
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.participantMoneyLane = new Map();
  service.prisma = {
    signalCycle: { findUnique: async () => ({ id: 'cycle-partial-lane', tradeId: 'cont-partial-lane', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY }) },
    signalCycleParticipant: { findUnique: async () => ({ id: 'participant-partial-lane', status: SignalCycleStatus.PENDING_ENTRY }) },
  };
  service.loadExecutionMeta = async () => ({ direction: 'SHORT', bitfinexOrderId: 8202, qty: 0.03, partialFillQty: 0.01, partialFillStopOrderId: 9202 });
  service.reconcilePendingPartialFillStop = async (_a: string, _u: string, _c: unknown, _p: string, meta: any) => { seen.push(meta.bitfinexOrderId); return true; };
  service.logger = { warn: () => undefined, log: () => undefined, error: () => undefined };
  const releaseReplacement = await service.acquireParticipantMoneyLane('participant-partial-lane');
  const monitoring = service.monitorEntry(
    'agent', 'user',
    { id: 'cycle-partial-lane', tradeId: 'cont-partial-lane', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY },
    { id: 'participant-partial-lane', status: SignalCycleStatus.PENDING_ENTRY },
    { direction: 'SHORT', bitfinexOrderId: 8201, qty: 0.03, partialFillQty: 0.01, partialFillStopOrderId: 9201 },
    {}, new Set<number>(), false,
  );
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.deepEqual(seen, []);
  releaseReplacement();
  await monitoring;
  assert.deepEqual(seen, [8202]);
});

test('immediate source-fill reconcile waits for lane and rejects a stale pending candidate', async () => {
  let recorded = false;
  const cycle = { id: 'cycle-fast-fill', tradeId: 'cont-fast-fill', status: SignalCycleStatus.PENDING_ENTRY, intentEnvelope: {} };
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.participantMoneyLane = new Map();
  service.prisma = { signalCycleParticipant: {
    findMany: async () => [{ id: 'participant-fast-fill', status: SignalCycleStatus.PENDING_ENTRY, cycle }],
    findUnique: async () => ({ id: 'participant-fast-fill', status: SignalCycleStatus.OPEN, cycle: { ...cycle, status: SignalCycleStatus.OPEN } }),
  } };
  service.exchanges = { getUserCredentials: async () => ({}) };
  service.loadExecutionMeta = async () => ({ direction: 'SHORT', bitfinexOrderId: 8302, qty: 0.03 });
  service.recordCancelRaceFill = async () => { recorded = true; return true; };
  const releaseWriter = await service.acquireParticipantMoneyLane('participant-fast-fill');
  const reconcile = service.tryImmediateShowcaseFillReconcile(
    'agent',
    { userId: 'user', exchangeProvider: 'bitfinex', status: TradingAgentInstanceStatus.ACTIVE, dashboardState: {} },
    'cont-fast-fill',
    new Date().toISOString(),
  );
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(recorded, false);
  releaseWriter();
  assert.equal(await reconcile, false);
  assert.equal(recorded, false);
});

test('signed source fill retires an exchange-proven unfilled pending order without a poll dwell', async () => {
  const events: Array<{ type: string; payload: any }> = [];
  const cancelled: number[] = [];
  const phantom: string[] = [];
  const cycle = {
    id: 'cycle-fast-retire', tradeId: 'cont-fast-retire',
    status: SignalCycleStatus.PENDING_ENTRY, intentEnvelope: {},
  };
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.participantMoneyLane = new Map();
  service.prisma = { signalCycleParticipant: {
    findMany: async () => [{ id: 'participant-fast-retire', status: SignalCycleStatus.PENDING_ENTRY, cycle }],
    findUnique: async () => ({ id: 'participant-fast-retire', status: SignalCycleStatus.PENDING_ENTRY, cycle }),
  } };
  service.exchanges = { getUserCredentials: async () => ({}) };
  service.loadExecutionMeta = async () => ({
    direction: 'SHORT', bitfinexOrderId: 8402, limitPrice: 64_000, qty: 0.03,
  });
  service.detectEntryFillBeforeCancel = async () => null;
  service.cancelManagedOrderGone = async (_creds: unknown, id: number, _note: string, timing: any) => {
    cancelled.push(id);
    timing({ submitStartedAtMs: 1_100, exchangeAckAtMs: 1_300, confirmedAtMs: 1_400 });
    return { gone: true, reason: 'CANCELLED', attempts: 1 };
  };
  service.classifyPostCancelEntry = async () => ({ kind: 'PROVEN_UNFILLED' });
  service.cycles = { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, type: string, payload: any) => events.push({ type, payload }) };
  service.cancelPhantomShowcasePosition = async (_u: string, _a: string, _c: string, tradeId: string) => { phantom.push(tradeId); };
  const handled = await service.tryImmediateShowcaseFillReconcile(
    'agent',
    { userId: 'user', exchangeProvider: 'bitfinex', status: TradingAgentInstanceStatus.ACTIVE, dashboardState: {} },
    'cont-fast-retire',
    new Date(1_000).toISOString(),
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(handled, true);
  assert.deepEqual(cancelled, [8402]);
  assert.deepEqual(events.map((event) => event.type), ['EXPIRED']);
  assert.equal(events[0].payload.reason, 'SHOWCASE_FILLED_BEFORE_COPY_FILL');
  assert.equal(events[0].payload.platform_to_cancel_ack_ms, 300);
  assert.deepEqual(phantom, ['cont-fast-retire']);
});

test('signed source fill replaces the exact pending order with a bounded catch-up limit', async () => {
  const previous = process.env.AGGRESSIVE_CATCHUP_ENABLED;
  process.env.AGGRESSIVE_CATCHUP_ENABLED = 'true';
  try {
    const events: Array<{ type: string; payload: any }> = [];
    const replacements: any[] = [];
    const cycle = {
      id: 'cycle-fast-late', tradeId: 'cont-fast-late',
      status: SignalCycleStatus.PENDING_ENTRY, intentEnvelope: {},
    };
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.participantMoneyLane = new Map();
    service.prisma = { signalCycleParticipant: {
      findMany: async () => [{ id: 'participant-fast-late', status: SignalCycleStatus.PENDING_ENTRY, cycle }],
      findUnique: async () => ({ id: 'participant-fast-late', status: SignalCycleStatus.PENDING_ENTRY, cycle }),
    } };
    service.exchanges = { getUserCredentials: async () => ({}) };
    // The direct, HMAC-verified POSITION_OPENED wake is sufficient authority
    // for bounded catch-up; the optional dashboard bridge must not suppress it.
    service.botBridge = { isEnabled: () => false };
    service.loadExecutionMeta = async () => ({
      direction: 'LONG', bitfinexOrderId: 8404, limitPrice: 64_100, qty: 0.03,
    });
    service.activeTrading = { getMarkPrice: async () => 64_010 };
    service.replaceRestingLimitOwned = async (...args: any[]) => { replacements.push(args); };
    service.cancelManagedOrderGone = async () => assert.fail('bounded in-range catch-up must not cancel the exact managed order');
    service.detectEntryFillBeforeCancel = async () => assert.fail('bounded in-range catch-up must replace before ordinary retirement');
    service.cycles = { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, type: string, payload: any) => events.push({ type, payload }) };

    const handled = await service.tryImmediateShowcaseFillReconcile(
      'agent',
      { userId: 'user', exchangeProvider: 'bitfinex', status: TradingAgentInstanceStatus.ACTIVE, dashboardState: {} },
      'cont-fast-late',
      new Date(1_000).toISOString(),
      { fillPrice: 64_000, sourceEventAtMs: 900, platformReceivedAtMs: 1_000 },
    );
    assert.equal(handled, true);
    assert.deepEqual(events.map((event) => event.payload.event), ['AGGRESSIVE_CATCHUP_BOUNDED']);
    assert.equal(events[0].payload.catchup_max_adverse_bps, 5);
    assert.equal(replacements.length, 1);
    assert.equal(replacements[0][7].newLimit, 64_032);
  } finally {
    if (previous === undefined) delete process.env.AGGRESSIVE_CATCHUP_ENABLED;
    else process.env.AGGRESSIVE_CATCHUP_ENABLED = previous;
  }
});

test('signed source fill outside the catch-up bound retains source ownership and never phantom-cancels', async () => {
  const previous = process.env.AGGRESSIVE_CATCHUP_ENABLED;
  process.env.AGGRESSIVE_CATCHUP_ENABLED = 'true';
  try {
    const events: Array<{ type: string; payload: any }> = [];
    const cycle = {
      id: 'cycle-fast-late-outside-bound', tradeId: 'cont-fast-late-outside-bound',
      status: SignalCycleStatus.PENDING_ENTRY, intentEnvelope: {},
    };
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.participantMoneyLane = new Map();
    service.prisma = { signalCycleParticipant: {
      findMany: async () => [{ id: 'participant-fast-late-outside-bound', status: SignalCycleStatus.PENDING_ENTRY, cycle }],
      findUnique: async () => ({ id: 'participant-fast-late-outside-bound', status: SignalCycleStatus.PENDING_ENTRY, cycle }),
    } };
    service.exchanges = { getUserCredentials: async () => ({}) };
    service.botBridge = { isEnabled: () => false };
    service.loadExecutionMeta = async () => ({
      direction: 'LONG', bitfinexOrderId: 8405, limitPrice: 64_100, qty: 0.03,
    });
    service.activeTrading = { getMarkPrice: async () => 64_100 };
    service.replaceRestingLimitOwned = async () => assert.fail('out-of-bound catch-up must not replace at an unsafe price');
    service.cancelManagedOrderGone = async () => assert.fail('out-of-bound catch-up must not cancel the source trade or its order');
    service.detectEntryFillBeforeCancel = async () => assert.fail('out-of-bound catch-up must retain before ordinary retirement');
    service.cycles = { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, type: string, payload: any) => events.push({ type, payload }) };

    const handled = await service.tryImmediateShowcaseFillReconcile(
      'agent',
      { userId: 'user', exchangeProvider: 'bitfinex', status: TradingAgentInstanceStatus.ACTIVE, dashboardState: {} },
      'cont-fast-late-outside-bound',
      new Date(1_000).toISOString(),
      { fillPrice: 64_000, sourceEventAtMs: 900, platformReceivedAtMs: 1_000 },
    );
    assert.equal(handled, true);
    assert.deepEqual(events.map((event) => event.payload.event), ['AGGRESSIVE_CATCHUP_DEFERRED']);
    assert.equal(events[0].payload.reason, 'OUTSIDE_5_BPS_BOUND');
    assert.equal(events[0].payload.aggressiveCatchupActive, true);
    assert.equal(events[0].payload.aggressiveCatchupSourceFill, 64_000);
  } finally {
    if (previous === undefined) delete process.env.AGGRESSIVE_CATCHUP_ENABLED;
    else process.env.AGGRESSIVE_CATCHUP_ENABLED = previous;
  }
});

test('signed source fill promotes a cancel-race execution instead of expiring it', async () => {
  const cycle = {
    id: 'cycle-fast-race', tradeId: 'cont-fast-race',
    status: SignalCycleStatus.PENDING_ENTRY, intentEnvelope: {},
  };
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.participantMoneyLane = new Map();
  service.prisma = { signalCycleParticipant: {
    findMany: async () => [{ id: 'participant-fast-race', status: SignalCycleStatus.PENDING_ENTRY, cycle }],
    findUnique: async () => ({ id: 'participant-fast-race', status: SignalCycleStatus.PENDING_ENTRY, cycle }),
  } };
  service.exchanges = { getUserCredentials: async () => ({}) };
  service.loadExecutionMeta = async () => ({ direction: 'SHORT', bitfinexOrderId: 8403, limitPrice: 64_000, qty: 0.03 });
  service.detectEntryFillBeforeCancel = async () => null;
  service.cancelManagedOrderGone = async (_creds: unknown, _id: number, _note: string, timing: any) => {
    timing({ submitStartedAtMs: 1_100, exchangeAckAtMs: 1_300, confirmedAtMs: 1_400 });
    return { gone: true, reason: 'CANCELLED', attempts: 1 };
  };
  service.classifyPostCancelEntry = async () => ({ kind: 'FILL', fill: { filledQty: 0.01, fillPrice: 64_000, source: 'ORDER_PARTIAL', orderResting: false } });
  let recorded: any = null;
  service.recordCancelRaceFill = async (...args: any[]) => { recorded = args; return true; };
  service.cycles = { recordHireExecutionEvent: async () => assert.fail('cancel-race fill must not expire') };
  const handled = await service.tryImmediateShowcaseFillReconcile(
    'agent',
    { userId: 'user', exchangeProvider: 'bitfinex', status: TradingAgentInstanceStatus.ACTIVE, dashboardState: {} },
    'cont-fast-race',
    new Date(1_000).toISOString(),
  );
  assert.equal(handled, true);
  assert.equal(recorded[8], 'SHOWCASE_POSITION_OPENED_WAKE');
});

test('same durable replacement survives first Bitfinex visibility miss and later active-list proof', async () => {
  const events: string[] = [];
  let findCalls = 0;
  let positionCalls = 0;
  const intent = {
    action: 'ENTER', trade_id: 'cont-visibility', risk: {},
    context: { showcase_event_seq: 10, showcase_event_at: '2026-08-11T10:46:07Z' },
  };
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.participantMoneyLane = new Map();
  service.replacementMissingProbe = new Map();
  service.botBridge = { isEnabled: () => false };
  service.cancelAbsurdPendingOrders = async () => undefined;
  service.activeTrading = {
    findOrder: async () => { findCalls += 1; return null; },
    listActiveOrders: async () => [{ id: 241808793805 }],
    getOpenPositionDetail: async () => { positionCalls += 1; return null; },
  };
  service.prisma = {
    signalCycle: { findUnique: async () => ({
      id: 'cycle-visibility', tradeId: 'cont-visibility', intentEnvelope: intent,
      expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY,
    }) },
    signalCycleParticipant: { findUnique: async () => ({
      id: 'participant-visibility', status: SignalCycleStatus.PENDING_ENTRY,
    }) },
  };
  service.loadExecutionMeta = async () => ({
    direction: 'SHORT', bitfinexOrderId: 241808793805, limitPrice: 64_000,
    qty: 0.03, replacementExchangeAckAtMs: Date.now() - 3_000,
  });
  service.cycles = {
    recordHireExecutionEvent: async (_u: string, _a: string, _c: string, event: string) => events.push(event),
  };
  service.logger = { warn: () => undefined, log: () => undefined, error: () => undefined };

  await service.monitorEntry(
    'agent', 'user',
    { id: 'cycle-visibility', tradeId: 'cont-visibility', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY },
    { id: 'participant-visibility', status: SignalCycleStatus.PENDING_ENTRY },
    { direction: 'SHORT', bitfinexOrderId: 241808793805, limitPrice: 64_000, qty: 0.03, replacementExchangeAckAtMs: Date.now() - 3_000 },
    {}, new Set<number>(), false,
  );
  assert.equal(findCalls, 1);
  assert.equal(positionCalls, 0);
  assert.deepEqual(events, []);
});

test('merged same-direction position cannot phantom-expire a hidden replacement without terminal history', async () => {
  const originalNow = Date.now;
  let now = 100_000;
  Date.now = () => now;
  try {
    const events: string[] = [];
    const intent = { action: 'ENTER', trade_id: 'cont-phantom', risk: {}, context: { showcase_event_seq: 12 } };
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.participantMoneyLane = new Map();
    service.replacementMissingProbe = new Map();
    service.botBridge = { isEnabled: () => false };
    service.cancelAbsurdPendingOrders = async () => undefined;
    service.activeTrading = {
      listActiveOrders: async () => [],
      getOpenPositionDetail: async () => ({ amount: -0.001, basePrice: 64_000 }),
    };
    const durableMeta = { direction: 'SHORT', bitfinexOrderId: 4001, limitPrice: 64_000, qty: 0.03, replacementExchangeAckAtMs: 80_000 };
    service.loadExecutionMeta = async (id: string) => id === 'other' ? { direction: 'SHORT', qty: 0.03 } : durableMeta;
    service.prisma = {
      signalCycle: { findUnique: async () => ({ id: 'cycle-p', tradeId: 'cont-phantom', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY }) },
      signalCycleParticipant: {
        findUnique: async () => ({ id: 'participant-p', status: SignalCycleStatus.PENDING_ENTRY }),
        findMany: async () => [{ id: 'other' }],
      },
    };
    service.bitfinex = { fetchOrderTrades: async () => [], fetchOrderHistoryEvidence: async () => null };
    service.cycles = { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, event: string) => events.push(event) };
    service.logger = { warn: () => undefined, log: () => undefined, error: () => undefined };
    const args = [
      'agent', 'user',
      { id: 'cycle-p', tradeId: 'cont-phantom', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY },
      { id: 'participant-p', status: SignalCycleStatus.PENDING_ENTRY }, durableMeta, {}, new Set<number>([999]), false,
    ];
    await service.monitorEntry(...args);
    now += 16_000;
    await service.monitorEntry(...args);
    assert.deepEqual(events, []);
  } finally {
    Date.now = originalNow;
  }
});

test('terminal NOT_FOUND cannot expire a hidden replacement without terminal-unfilled history', async () => {
  const events: string[] = [];
  const intent = { action: 'ENTER', trade_id: 'cont-terminal-hidden', risk: {}, context: { showcase_event_seq: 13 } };
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.loadExecutionMeta = async () => ({ direction: 'SHORT', bitfinexOrderId: 5001, limitPrice: 64_000, qty: 0.03, replacementExchangeAckAtMs: Date.now() - 20_000 });
  service.detectEntryFillBeforeCancel = async () => null;
  service.cancelManagedOrderGone = async () => ({ gone: true, reason: 'NOT_FOUND', attempts: 1 });
  service.bitfinex = { fetchOrderTrades: async () => [], fetchOrderHistoryEvidence: async () => null };
  service.prisma = {
    signalCycleParticipant: { findUnique: async () => ({ id: 'participant-t', status: SignalCycleStatus.PENDING_ENTRY }) },
    signalCycle: { findUnique: async () => ({ id: 'cycle-t', tradeId: 'cont-terminal-hidden', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.CLOSED }) },
  };
  service.cycles = { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, event: string) => events.push(event) };
  service.logger = { warn: () => undefined, log: () => undefined, error: () => undefined };
  await service.monitorEntry(
    'agent', 'user',
    { id: 'cycle-t', tradeId: 'cont-terminal-hidden', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.CLOSED },
    { id: 'participant-t', status: SignalCycleStatus.PENDING_ENTRY },
    { direction: 'SHORT', bitfinexOrderId: 5001, limitPrice: 64_000, qty: 0.03, replacementExchangeAckAtMs: Date.now() - 20_000 },
    {}, new Set<number>(), false,
  );
  assert.deepEqual(events, []);
});

test('missed-showcase-fill NOT_FOUND cannot expire a hidden replacement without terminal history', async () => {
  const events: string[] = [];
  const intent = { action: 'ENTER', trade_id: 'cont-missed-hidden', risk: {}, context: {} };
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.loadExecutionMeta = async () => ({ direction: 'SHORT', bitfinexOrderId: 6001, limitPrice: 64_000, qty: 0.03, replacementExchangeAckAtMs: Date.now() - 20_000 });
  service.botBridge = { isEnabled: () => true };
  service.cancelAbsurdPendingOrders = async () => undefined;
  service.fetchExecutionBotState = async () => ({ positions: [] });
  service.resolveShowcaseMirrorTradeId = () => 'cont-missed-hidden';
  service.showcaseEntryAbandoned = () => ({ abandoned: true, reason: 'MISSED_SHOWCASE_FILL' });
  service.detectEntryFillBeforeCancel = async () => null;
  service.cancelManagedOrderGone = async () => ({ gone: true, reason: 'NOT_FOUND', attempts: 1 });
  service.bitfinex = { fetchOrderTrades: async () => [], fetchOrderHistoryEvidence: async () => null };
  service.prisma = {
    signalCycleParticipant: { findUnique: async () => ({ id: 'participant-m', status: SignalCycleStatus.PENDING_ENTRY }) },
    signalCycle: { findUnique: async () => ({ id: 'cycle-m', tradeId: 'cont-missed-hidden', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY }) },
  };
  service.cycles = { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, event: string) => events.push(event) };
  service.logger = { warn: () => undefined, log: () => undefined, error: () => undefined };
  await service.monitorEntry(
    'agent', 'user',
    { id: 'cycle-m', tradeId: 'cont-missed-hidden', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY },
    { id: 'participant-m', status: SignalCycleStatus.PENDING_ENTRY },
    { direction: 'SHORT', bitfinexOrderId: 6001, limitPrice: 64_000, qty: 0.03, replacementExchangeAckAtMs: Date.now() - 20_000 },
    {}, new Set<number>(), false,
  );
  assert.deepEqual(events, []);
});

test('snapshot missed-fill fallback defers while its exact signed source-fill wake is queued', async () => {
  let detectCalls = 0;
  let cancelCalls = 0;
  const intent = { action: 'ENTER', trade_id: 'cont-fill-priority', risk: {}, context: {} };
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.pendingDirectWakes = new Map([['trade:cont-fill-priority', {
    trigger: 'POSITION_OPENED', tradeId: 'cont-fill-priority', at: new Date().toISOString(),
  }]]);
  service.activeDirectWakes = new Map([['trade:cont-fill-priority', {
    trigger: 'LIMIT_UPDATED', tradeId: 'cont-fill-priority', at: new Date().toISOString(),
  }]]);
  service.prioritySourceFillWakes = new Set<string>();
  service.loadExecutionMeta = async () => ({
    direction: 'SHORT', bitfinexOrderId: 6002, limitPrice: 64_000, qty: 0.03,
  });
  service.botBridge = { isEnabled: () => true };
  service.cancelAbsurdPendingOrders = async () => undefined;
  service.fetchExecutionBotState = async () => ({ positions: [] });
  service.resolveShowcaseMirrorTradeId = () => 'cont-fill-priority';
  service.showcaseEntryAbandoned = () => ({ abandoned: true, reason: 'MISSED_SHOWCASE_FILL' });
  service.detectEntryFillBeforeCancel = async () => { detectCalls += 1; return null; };
  service.cancelManagedOrderGone = async () => { cancelCalls += 1; return { gone: true, reason: 'CANCELLED', attempts: 1 }; };
  service.prisma = {
    signalCycleParticipant: { findUnique: async () => ({ id: 'participant-fill-priority', status: SignalCycleStatus.PENDING_ENTRY }) },
    signalCycle: { findUnique: async () => ({ id: 'cycle-fill-priority', tradeId: 'cont-fill-priority', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY }) },
  };
  service.cycles = { recordHireExecutionEvent: async () => assert.fail('queued signed fill wake must own terminalization') };
  service.logger = { warn: () => undefined, log: () => undefined, error: () => undefined };

  await service.monitorEntry(
    'agent', 'user',
    { id: 'cycle-fill-priority', tradeId: 'cont-fill-priority', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY },
    { id: 'participant-fill-priority', status: SignalCycleStatus.PENDING_ENTRY },
    { direction: 'SHORT', bitfinexOrderId: 6002, limitPrice: 64_000, qty: 0.03 },
    {}, new Set<number>([6002]), false,
  );

  assert.equal(detectCalls, 0);
  assert.equal(cancelCalls, 0);
});

test('mirror diff gives a just-acknowledged signed reprice time to reach the display snapshot', () => {
  const acknowledgedAtMs = 10_000;
  assert.equal(
    mirrorDiffPriceDeltaIsWithinSignedRepriceGrace(acknowledgedAtMs, acknowledgedAtMs + 4_999),
    true,
  );
  assert.equal(
    mirrorDiffPriceDeltaIsWithinSignedRepriceGrace(acknowledgedAtMs, acknowledgedAtMs + 5_001),
    false,
  );
  assert.equal(mirrorDiffPriceDeltaIsWithinSignedRepriceGrace(acknowledgedAtMs, acknowledgedAtMs - 1), false);
  assert.equal(mirrorDiffPriceDeltaIsWithinSignedRepriceGrace(undefined, acknowledgedAtMs + 1), false);
});

test('catch-up deferral is audited once per immutable source fill while retry remains possible', () => {
  assert.equal(shouldRecordAggressiveCatchupDeferred(undefined, 63_704.94), true);
  assert.equal(shouldRecordAggressiveCatchupDeferred(63_704.94, 63_704.94), false);
  assert.equal(shouldRecordAggressiveCatchupDeferred(63_704.94, 63_704.96), true);
});

test('monitor yields its participant lane to an exact authenticated Bitfinex fill before snapshot work', async () => {
  const intent = { action: 'ENTER', trade_id: 'cont-ws-priority', risk: {}, context: {} };
  const cycle = { id: 'cycle-ws-priority', tradeId: 'cont-ws-priority', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY };
  const participant = { id: 'participant-ws-priority', status: SignalCycleStatus.PENDING_ENTRY };
  const meta = { direction: 'SHORT', bitfinexOrderId: 6003, limitPrice: 64_000, qty: 0.03 };
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.participantMoneyLane = new Map();
  service.priorityWsFillParticipants = new Set([participant.id]);
  service.prisma = {
    signalCycleParticipant: { findUnique: async () => participant },
    signalCycle: { findUnique: async () => cycle },
  };
  service.loadExecutionMeta = async () => meta;
  service.cancelAbsurdPendingOrders = async () => assert.fail('authenticated fill must run before snapshot exchange reads');
  service.fetchExecutionBotState = async () => assert.fail('authenticated fill must run before source snapshot reads');
  service.logger = { warn: () => undefined, log: () => undefined, error: () => undefined };

  await service.monitorEntry('agent', 'user', cycle, participant, meta, {}, new Set<number>([6003]), false);
});

test('canonical open position retains deferred catch-up even when the auxiliary snapshot calls it absent', async () => {
  const previous = process.env.AGGRESSIVE_CATCHUP_ENABLED;
  process.env.AGGRESSIVE_CATCHUP_ENABLED = 'true';
  try {
    let cancelCalls = 0;
    const events: Array<{ type: string; payload: any }> = [];
    const intent = { action: 'ENTER', trade_id: 'cont-canonical-open', risk: {}, context: {} };
    const cycle = { id: 'cycle-canonical-open', tradeId: 'cont-canonical-open', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY };
    const participant = { id: 'participant-canonical-open', status: SignalCycleStatus.PENDING_ENTRY };
    const meta = { direction: 'SHORT', bitfinexOrderId: 6601, limitPrice: 64_000, qty: 0.03 };
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.participantMoneyLane = new Map();
    service.botBridge = { isEnabled: () => true };
    service.cancelAbsurdPendingOrders = async () => undefined;
    service.fetchExecutionBotState = async () => ({ positions: [{ trade_id: 'cont-canonical-open', entry: 64_000, created_at: Date.now() }] });
    service.resolveShowcaseMirrorTradeId = () => 'cont-canonical-open';
    // Reproduces the stale auxiliary signal/expired-order snapshot that used
    // to let the exact canonical position fall through to phantom cancel.
    service.showcaseEntryAbandoned = () => ({ abandoned: true, reason: 'SHOWCASE_ABSENT' });
    // SHORT requires the market to be at or above the capped sell limit.
    // A lower mark is truly adverse and must leave the owned order resting.
    service.activeTrading = { getMarkPrice: async () => 63_900 };
    service.cancelManagedOrderGone = async () => { cancelCalls += 1; return { gone: true, reason: 'CANCELLED', attempts: 1 }; };
    service.replaceRestingLimitOwned = async () => assert.fail('out-of-bound catch-up must retain the owned order');
    service.prisma = {
      signalCycle: { findUnique: async () => cycle },
      signalCycleParticipant: { findUnique: async () => participant },
    };
    service.loadExecutionMeta = async () => meta;
    service.cycles = { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, type: string, payload: any) => events.push({ type, payload }) };
    service.logger = { warn: () => undefined, log: () => undefined, error: () => undefined };

    await service.monitorEntry('agent', 'user', cycle, participant, meta, {}, new Set<number>([6601]), false);
    assert.equal(cancelCalls, 0);
    assert.deepEqual(events.map((event) => event.payload.event), ['AGGRESSIVE_CATCHUP_DEFERRED']);
    assert.equal(events[0].payload.aggressiveCatchupActive, true);
  } finally {
    if (previous === undefined) delete process.env.AGGRESSIVE_CATCHUP_ENABLED;
    else process.env.AGGRESSIVE_CATCHUP_ENABLED = previous;
  }
});

test('durable deferred catch-up survives a temporary dashboard-bridge outage', async () => {
  const previous = process.env.AGGRESSIVE_CATCHUP_ENABLED;
  process.env.AGGRESSIVE_CATCHUP_ENABLED = 'true';
  try {
    const intent = { action: 'ENTER', trade_id: 'cont-deferred-bridge-outage', risk: {}, context: {} };
    const cycle = {
      id: 'cycle-deferred-bridge-outage', tradeId: 'cont-deferred-bridge-outage',
      intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY,
    };
    const participant = { id: 'participant-deferred-bridge-outage', status: SignalCycleStatus.PENDING_ENTRY };
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.participantMoneyLane = new Map();
    service.priorityWsFillParticipants = new Set();
    service.botBridge = { isEnabled: () => false };
    service.cancelAbsurdPendingOrders = async () => undefined;
    service.cancelManagedOrderGone = async () => assert.fail('durable catch-up must not fall through to missed-fill cancellation during bridge outage');
    service.cancelPhantomShowcasePosition = async () => assert.fail('durable catch-up must never phantom-cancel the source trade during bridge outage');
    service.prisma = {
      signalCycleParticipant: { findUnique: async () => participant },
      signalCycle: { findUnique: async () => cycle },
    };
    service.loadExecutionMeta = async () => ({
      direction: 'SHORT', bitfinexOrderId: 6602, limitPrice: 64_000, qty: 0.03,
      aggressiveCatchupActive: true, aggressiveCatchupSourceFill: 64_000,
    });
    service.logger = { warn: () => undefined, log: () => undefined, error: () => undefined };

    await service.monitorEntry('agent', 'user', cycle, participant, {}, {}, new Set<number>([6602]), false);
  } finally {
    if (previous === undefined) delete process.env.AGGRESSIVE_CATCHUP_ENABLED;
    else process.env.AGGRESSIVE_CATCHUP_ENABLED = previous;
  }
});

test('durable deferred catch-up survives an immediate reconcile without the original signed wake', async () => {
  const previous = process.env.AGGRESSIVE_CATCHUP_ENABLED;
  process.env.AGGRESSIVE_CATCHUP_ENABLED = 'true';
  try {
    const cycle = {
      id: 'cycle-deferred-immediate-reconcile', tradeId: 'cont-deferred-immediate-reconcile',
      status: SignalCycleStatus.PENDING_ENTRY, intentEnvelope: {},
    };
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.participantMoneyLane = new Map();
    service.prisma = { signalCycleParticipant: {
      findMany: async () => [{ id: 'participant-deferred-immediate-reconcile', status: SignalCycleStatus.PENDING_ENTRY, cycle }],
      findUnique: async () => ({ id: 'participant-deferred-immediate-reconcile', status: SignalCycleStatus.PENDING_ENTRY, cycle }),
    } };
    service.exchanges = { getUserCredentials: async () => ({}) };
    service.loadExecutionMeta = async () => ({
      direction: 'SHORT', bitfinexOrderId: 6603, limitPrice: 64_000, qty: 0.03,
      aggressiveCatchupActive: true, aggressiveCatchupSourceFill: 64_000,
    });
    service.cancelManagedOrderGone = async () => assert.fail('durable catch-up must not cancel without a fresh signed wake');
    service.cancelPhantomShowcasePosition = async () => assert.fail('durable catch-up must not phantom-cancel without a fresh signed wake');

    const handled = await service.tryImmediateShowcaseFillReconcile(
      'agent',
      { userId: 'user', exchangeProvider: 'bitfinex', status: TradingAgentInstanceStatus.ACTIVE, dashboardState: {} },
      'cont-deferred-immediate-reconcile',
      new Date().toISOString(),
    );
    assert.equal(handled, true);
  } finally {
    if (previous === undefined) delete process.env.AGGRESSIVE_CATCHUP_ENABLED;
    else process.env.AGGRESSIVE_CATCHUP_ENABLED = previous;
  }
});

test('showcase catch-up cannot clear or duplicate-enter after hidden replacement NOT_FOUND', async () => {
  const events: string[] = [];
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.loadExecutionMeta = async () => ({
    direction: 'SHORT', bitfinexOrderId: 7001, limitPrice: 64_000, qty: 0.03,
    replacementExchangeAckAtMs: Date.now() - 20_000,
  });
  service.detectEntryFillBeforeCancel = async () => null;
  service.cancelManagedOrderGone = async () => ({ gone: true, reason: 'NOT_FOUND', attempts: 1 });
  service.bitfinex = { fetchOrderTrades: async () => [], fetchOrderHistoryEvidence: async () => null };
  service.prisma = {
    signalCycleParticipant: { findUnique: async () => ({ status: SignalCycleStatus.PENDING_ENTRY, cycleId: 'cycle-catchup' }) },
    signalCycle: { findUnique: async () => ({ id: 'cycle-catchup' }) },
  };
  service.cycles = { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, event: string) => events.push(event) };
  service.logger = { warn: () => undefined };
  const cleared = await service.clearPendingForShowcaseCatchup(
    'agent', 'user', { id: 'participant-catchup', cycleId: 'cycle-catchup' }, {},
  );
  assert.equal(cleared, false);
  assert.deepEqual(events, []);
});

test('cancel-by-exchange reconcile cannot close a transient-hidden replacement', async () => {
  let closed = false;
  const meta = { direction: 'SHORT', bitfinexOrderId: 8001, qty: 0.03, replacementExchangeAckAtMs: Date.now() - 30_000 };
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.loadExecutionMeta = async () => meta;
  service.activeTrading = { findOrder: async () => null };
  service.detectEntryFillBeforeCancel = async () => null;
  service.bitfinex = { fetchOrderTrades: async () => [], fetchOrderHistoryEvidence: async () => null };
  service.prisma = {
    signalCycle: { findUnique: async () => ({ id: 'cycle-r', tradeId: 'cont-r', status: SignalCycleStatus.PENDING_ENTRY, intentEnvelope: {} }) },
    signalCycleParticipant: {
    findUnique: async () => ({ status: SignalCycleStatus.PENDING_ENTRY, cycleId: 'cycle-r' }),
    findMany: async () => [{
      id: 'participant-r', cycleId: 'cycle-r', venue: 'bitfinex',
      cycle: { id: 'cycle-r', tradeId: 'cont-r', status: SignalCycleStatus.PENDING_ENTRY, intentEnvelope: {} },
    }],
    update: async () => { closed = true; },
  } };
  service.cycles = { recordHireExecutionEvent: async () => { closed = true; } };
  service.logger = { warn: () => undefined };
  await service.reconcileCancelByExchange('user', 'agent', {}, new Set(), {}, ['participant-r']);
  assert.equal(closed, false);
});

test('watchdog NOT_FOUND cannot expire a transient-hidden replacement', async () => {
  let terminalized = false;
  const meta = { direction: 'SHORT', bitfinexOrderId: 9001, qty: 0.03, replacementExchangeAckAtMs: Date.now() - 30_000 };
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.exchanges = { getUserCredentials: async () => ({}) };
  service.bitfinex = {
    findOrder: async () => ({ id: 9001, amount: -0.03, amountOrig: -0.03 }),
    fetchOrderTrades: async () => [],
    fetchOrderHistoryEvidence: async () => null,
  };
  service.loadExecutionMeta = async () => meta;
  service.cancelManagedOrderGone = async () => ({ gone: true, reason: 'NOT_FOUND', attempts: 1 });
  service.prisma = {
    signalCycle: { findUnique: async () => ({ id: 'cycle-w' }) },
    signalCycleParticipant: {
      findUnique: async () => ({ status: SignalCycleStatus.PENDING_ENTRY, cycleId: 'cycle-w' }),
      findMany: async () => [{ id: 'participant-w', cycleId: 'cycle-w' }],
    },
    $transaction: async () => { terminalized = true; },
  };
  service.logger = { warn: () => undefined };
  const count = await service.cancelVerifiedUnfilledPendingEntries(
    'agent', { userId: 'user', exchangeProvider: 'bitfinex' },
    'EXECUTOR_WATCHDOG_CANCELLED_UNFILLED', 'watchdog',
  );
  assert.equal(count, 0);
  assert.equal(terminalized, false);
});

test('replacement absence needs two fresh ticks and full missing grace for terminal eligibility', () => {
  const ackAt = 1_000;
  const first = advanceReplacementMissingProbe(undefined, '3001:seq:10', ackAt, 2_000);
  assert.equal(first.probe.count, 1);
  assert.equal(first.terminalEligible, false);
  const earlySecond = advanceReplacementMissingProbe(first.probe, '3001:seq:10', ackAt, 10_000);
  assert.equal(earlySecond.probe.count, 2);
  assert.equal(earlySecond.terminalEligible, false);
  const matureThird = advanceReplacementMissingProbe(earlySecond.probe, '3001:seq:10', ackAt, 17_100);
  assert.equal(matureThird.terminalEligible, true);
  const advanced = advanceReplacementMissingProbe(matureThird.probe, '3002:seq:11', 17_000, 17_200);
  assert.equal(advanced.probe.count, 1);
  assert.equal(advanced.terminalEligible, false);
});

test('initial exchange ACK receives the same stale-book visibility fence as a replacement', () => {
  assert.equal(
    managedOrderExchangeAckAtMs({ bitfinexOrderId: 1001, entryExchangeAckAtMs: 1_000 }),
    1_000,
  );
  assert.equal(
    managedOrderExchangeAckAtMs({
      bitfinexOrderId: 1001,
      entryExchangeAckAtMs: 1_000,
      replacementExchangeAckAtMs: 2_000,
    }),
    2_000,
    'the current replacement generation takes precedence over the initial acknowledgement',
  );
  const firstMissing = advanceReplacementMissingProbe(undefined, '1001:seq:0', 1_000, 2_000);
  assert.equal(firstMissing.terminalEligible, false, 'a freshly ACKed initial order cannot expire on one missing book read');
});

test('freshly ACKed initial order defers a transient absent active-book snapshot', async () => {
  const events: string[] = [];
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.participantMoneyLane = new Map();
  service.replacementMissingProbe = new Map();
  service.botBridge = { isEnabled: () => false };
  service.cancelAbsurdPendingOrders = async () => undefined;
  service.activeTrading = {
    findOrder: async () => null,
    listActiveOrders: async () => [],
    getOpenPositionDetail: async () => assert.fail('position classification must wait for missing-order proof'),
  };
  const intent = { action: 'ENTER', trade_id: 'cont-entry-visibility', risk: {}, context: { showcase_event_seq: 0 } };
  service.prisma = {
    signalCycle: { findUnique: async () => ({
      id: 'cycle-entry-visibility', tradeId: 'cont-entry-visibility', intentEnvelope: intent,
      expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY,
    }) },
    signalCycleParticipant: { findUnique: async () => ({ id: 'participant-entry-visibility', status: SignalCycleStatus.PENDING_ENTRY }) },
  };
  service.loadExecutionMeta = async () => ({
    direction: 'SHORT', bitfinexOrderId: 7001, limitPrice: 64_000, qty: 0.03,
    entryExchangeAckAtMs: Date.now(),
  });
  service.cycles = { recordHireExecutionEvent: async (_u: string, _a: string, _c: string, event: string) => events.push(event) };
  service.logger = { warn: () => undefined, log: () => undefined, error: () => undefined };

  await service.monitorEntry(
    'agent', 'user',
    { id: 'cycle-entry-visibility', tradeId: 'cont-entry-visibility', intentEnvelope: intent, expiresAt: null, status: SignalCycleStatus.PENDING_ENTRY },
    { id: 'participant-entry-visibility', status: SignalCycleStatus.PENDING_ENTRY },
    { direction: 'SHORT', bitfinexOrderId: 7001, limitPrice: 64_000, qty: 0.03, entryExchangeAckAtMs: Date.now() },
    {}, new Set<number>(), false,
  );
  assert.deepEqual(events, []);
  assert.equal(service.replacementMissingProbe.get('participant-entry-visibility')?.count, 1);
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

test('signed same-direction fast path permits an exactly ledger-backed OPEN lot plus an owned pending entry', () => {
  const input = {
    status: TradingAgentInstanceStatus.ACTIVE,
    simActive: false,
    hireExpired: false,
    relayArmed: true,
    exchangePositionQty: -0.03153,
    candidateDirection: 'SHORT' as const,
    maxConcurrent: 3,
    virtualLots: [
      { status: SignalCycleStatus.OPEN, direction: 'SHORT' as const, qty: 0.03153 },
      { status: SignalCycleStatus.PENDING_ENTRY, direction: 'SHORT' as const, bitfinexOrderId: 202, qty: 0.0315 },
    ],
    exchangeActiveOrderIds: [202],
  };
  assert.equal(sameDirectionPendingSignedFastPathPreflight(input), true);
  assert.equal(
    sameDirectionPendingSignedFastPathPreflight({ ...input, exchangePositionQty: -0.02 }),
    false,
  );
  assert.equal(
    sameDirectionPendingSignedFastPathPreflight({ ...input, exchangePositionQty: 0.03153 }),
    false,
  );
});

test('signed same-direction fast path accepts only managed stops for existing open lots', () => {
  const input = {
    status: TradingAgentInstanceStatus.ACTIVE,
    simActive: false,
    hireExpired: false,
    relayArmed: true,
    exchangePositionQty: -0.06301,
    candidateDirection: 'SHORT' as const,
    maxConcurrent: 3,
    virtualLots: [
      {
        status: SignalCycleStatus.OPEN,
        direction: 'SHORT' as const,
        qty: 0.0315,
        stopOrderId: 301,
      },
      {
        status: SignalCycleStatus.OPEN,
        direction: 'SHORT' as const,
        qty: 0.03151,
        stopOrderId: 302,
      },
    ],
    exchangeActiveOrderIds: [301, 302],
  };
  assert.equal(sameDirectionPendingSignedFastPathPreflight(input), true);
  assert.equal(
    sameDirectionPendingSignedFastPathPreflight({ ...input, exchangeActiveOrderIds: [301, 999] }),
    false,
  );
});

test('signed same-direction fast path accepts a protected partial remainder with an open lot', () => {
  const input = {
    status: TradingAgentInstanceStatus.ACTIVE,
    simActive: false,
    hireExpired: false,
    relayArmed: true,
    exchangePositionQty: -0.0521129,
    candidateDirection: 'SHORT' as const,
    maxConcurrent: 3,
    virtualLots: [
      {
        status: SignalCycleStatus.OPEN,
        direction: 'SHORT' as const,
        qty: 0.03146,
        stopOrderId: 101,
      },
      {
        status: SignalCycleStatus.PENDING_ENTRY,
        direction: 'SHORT' as const,
        bitfinexOrderId: 202,
        partialFillQty: 0.0206529,
        partialFillStopOrderId: 203,
      },
    ],
    exchangeActiveOrderIds: [101, 202, 203],
  };
  assert.equal(sameDirectionPendingSignedFastPathPreflight(input), true);
  assert.equal(
    sameDirectionPendingSignedFastPathPreflight({ ...input, exchangeActiveOrderIds: [101, 202, 999] }),
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

test('live polling backstop requires a fresh signed envelope as well as a canonical order', () => {
  const now = Date.now();
  const unsigned = {
    tradeId: 'cont-unsigned',
    createdAt: new Date(now),
    intentEnvelope: {
      action: 'ENTER', signalId: 'cont-unsigned', trade_id: 'cont-unsigned',
      direction: 'SHORT',
      entry: { mode: 'EXACT_LIMIT', reference: 'SHOWCASE_EXACT_LIMIT', exact_limit_price: 64_000, exact_qty_btc: 0.03 },
      context: { signed_showcase_event: false, showcase_event: 'ORDER_PLACED', platform_received_at: new Date(now).toISOString(), entry_limit_policy: 'deterministic_0.1pct_offset_v1' },
    },
  };
  const signed = {
    tradeId: 'cont-signed',
    createdAt: new Date(now),
    intentEnvelope: {
      action: 'ENTER', signalId: 'cont-signed', trade_id: 'cont-signed',
      direction: 'SHORT',
      entry: { mode: 'EXACT_LIMIT', reference: 'SHOWCASE_EXACT_LIMIT', exact_limit_price: 64_010, exact_qty_btc: 0.03 },
      context: { signed_showcase_event: true, showcase_event: 'ORDER_PLACED', platform_received_at: new Date(now).toISOString(), entry_limit_policy: 'deterministic_0.1pct_offset_v1' },
    },
  };
  const result = signedCanonicalPendingIntentCycles([unsigned, signed], {
    orders: [
      { trade_id: 'cont-unsigned', status: 'PENDING', limit_price: 64_000, entry_limit_policy: 'deterministic_0.1pct_offset_v1' },
      { trade_id: 'cont-signed', status: 'PENDING', limit_price: 64_010, entry_limit_policy: 'deterministic_0.1pct_offset_v1' },
    ],
  });
  assert.deepEqual(result, [signed]);
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
          exact_qty_btc: 0.02361,
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
      exactQtyBtc: 0.02361,
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
      exact_qty_btc: 0.02361,
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
        showcase_event_at: '2026-08-10T18:46:47.476Z',
        platform_received_at: '2026-08-10T18:46:47.584Z',
        showcase_exit_price: 64_444.25,
        showcase_exit_reason: 'PROFIT_LOCK_LADDER',
      },
    }),
    {
      exitPrice: 64_444.25,
      exitReason: 'PROFIT_LOCK_LADDER',
      sourceEventAtMs: Date.parse('2026-08-10T18:46:47.476Z'),
      platformReceivedAtMs: Date.parse('2026-08-10T18:46:47.584Z'),
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

test('mayCrossTradeRelink: normal signed Showcase cycles are immutable', () => {
  // Regression for cont-94a96f09d2b3: its fill was price-near an older
  // cont-eaea331a0fa4 position, then a relink made the older trade's close
  // flatten this distinct live lot. A normal source-created cont id is never
  // eligible for a cross-trade rewrite.
  assert.equal(mayCrossTradeRelink('cont-94a96f09d2b3'), false);
  assert.equal(mayCrossTradeRelink('cont-eaea331a0fa4'), false);
  assert.equal(mayCrossTradeRelink(''), false);
  assert.equal(mayCrossTradeRelink(null), false);
});

test('mayCrossTradeRelink: synthetic recovery records retain the narrow fallback', () => {
  assert.equal(mayCrossTradeRelink('adopt:cont-original:123'), true);
  assert.equal(mayCrossTradeRelink('relink:cont-old:cont-new:123'), true);
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

test('resolveShowcaseRelinkForRealFill: rejects an older price-near position instead of stealing a fresh canonical fill', () => {
  // cont-02b475bf3a48: an older SHORT was only ~0.11% away in price, so the
  // former symmetric 10-minute window incorrectly re-linked this fresh fill
  // to it.  A candidate may precede a venue fill only by the narrow ordering
  // allowance, never by minutes.
  const now = Date.parse('2026-08-12T15:07:12.236Z');
  const res = resolveShowcaseRelinkForRealFill({
    showcasePositions: [{
      trade_id: 'cont-older-short',
      entry: 63_469.41,
      dir: 'SHORT',
      entry_ts: (now - SHOWCASE_RELINK_MAX_ENTRY_LEAD_MS - 1) / 1000,
    }],
    realFill: { price: 63_541, direction: 'SHORT' },
    currentTradeId: 'cont-fresh-short',
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


test('hire expiry blocks new entries but requires exit-only processing for open risk', () => {
  const expired = '2026-08-08T06:10:39.733Z';
  const now = Date.parse('2026-08-08T06:51:34.000Z');
  assert.equal(hireExpiryBlocksNewLiveEntries(expired, now), true);
  assert.equal(hireExpiryRequiresExitOnlyProcessing(expired, false, now), true);
  assert.equal(hireExpiryRequiresExitOnlyProcessing(expired, true, now), false);
  assert.equal(hireExpiryBlocksNewLiveEntries(null, now), false);
  assert.equal(hireExpiryBlocksNewLiveEntries('2026-08-08T08:00:00.000Z', now), false);
  // cont-ffe6d1689ec2: expired hire + OPEN participant must keep exit ticks alive
  assert.equal(
    expiredHireShouldRunExitOnly({
      simActive: false,
      hireExpired: true,
      openOrPendingParticipantCount: 1,
    }),
    true,
  );
  assert.equal(
    expiredHireShouldRunExitOnly({
      simActive: false,
      hireExpired: true,
      openOrPendingParticipantCount: 0,
    }),
    false,
  );
  assert.equal(
    expiredHireShouldRunExitOnly({
      simActive: true,
      hireExpired: true,
      openOrPendingParticipantCount: 1,
    }),
    false,
  );
});

test('flat signed fast path refuses entries when hire expired', () => {
  assert.equal(
    flatSignedFastPathPreflight({
      status: TradingAgentInstanceStatus.ACTIVE,
      simActive: false,
      hireExpired: true,
      relayArmed: true,
      virtualOpenOrPending: 0,
      exchangeActiveOrders: 0,
      exchangePositionQty: 0,
    }),
    false,
  );
});

test('relay executor wake request reads POSITION_CLOSED payload', () => {
  const wake = readRelayExecutorWakeRequest({
    relayExecutorWake: {
      trigger: 'POSITION_CLOSED',
      at: '2026-08-08T06:51:34.000Z',
      tradeId: 'cont-ffe6d1689ec2',
    },
  });
  assert.equal(wake?.trigger, 'POSITION_CLOSED');
  assert.equal(wake?.tradeId, 'cont-ffe6d1689ec2');
  assert.equal(readRelayExecutorWakeRequest({}), null);
});

test('close wake selects exactly its matching participant and no others', () => {
  const lots = [
    { cycleTradeId: 'cont-target', originTradeId: null },
    { cycleTradeId: 'cont-other', originTradeId: null },
    { cycleTradeId: 'relink:unknown:cont-third:1', originTradeId: 'cont-third' },
  ];
  assert.deepEqual(
    lots.filter((lot) => persistedCloseWakeMatchesParticipant(
      'cont-target', lot.cycleTradeId, lot.originTradeId,
    )),
    [lots[0]],
  );
  assert.equal(lots.filter((lot) => persistedCloseWakeMatchesParticipant(
    'cont-mismatch', lot.cycleTradeId, lot.originTradeId,
  )).length, 0);
});

test('duplicate pre/post POSITION_CLOSED wake cannot queue a second close', async () => {
  const previousExecution = process.env.SUBSCRIBER_EXECUTION_ENABLED;
  const previousWorker = process.env.RELAY_EXECUTOR_WORKER;
  process.env.SUBSCRIBER_EXECUTION_ENABLED = 'true';
  process.env.RELAY_EXECUTOR_WORKER = 'true';
  try {
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.pendingDirectWakes = new Map();
    service.activeDirectWakes = new Map([['trade:cont-close-one', {
      trigger: 'POSITION_CLOSED', tradeId: 'cont-close-one', at: '2026-08-11T12:09:56.107Z',
    }]]);
    assert.equal(await service.acceptDirectExecutorWake({
      trigger: 'POSITION_CLOSED', tradeId: 'cont-close-one', at: '2026-08-11T12:09:56.107Z',
    }), true);
    assert.equal(service.pendingDirectWakes.size, 0);
  } finally {
    if (previousExecution == null) delete process.env.SUBSCRIBER_EXECUTION_ENABLED;
    else process.env.SUBSCRIBER_EXECUTION_ENABLED = previousExecution;
    if (previousWorker == null) delete process.env.RELAY_EXECUTOR_WORKER;
    else process.env.RELAY_EXECUTOR_WORKER = previousWorker;
  }
});

test('private wake parser preserves only bounded POSITION_CLOSED evidence', () => {
  const now = Date.now();
  const wake = parseExecutorWakeRequest({
    trigger: 'POSITION_CLOSED', at: new Date(now).toISOString(), tradeId: 'cont-c105efa5',
    signedClose: {
      exitPrice: 64_400, exitReason: 'PROFIT_LOCK',
      sourceEventAtMs: now - 1_200, platformReceivedAtMs: now,
    },
  });
  assert.equal(wake?.signedClose?.exitPrice, 64_400);
  assert.equal(wake?.signedClose?.sourceEventAtMs, now - 1_200);
  for (const bad of [
    { trigger: 'POSITION_CLOSED', at: new Date(now).toISOString(), signedClose: { sourceEventAtMs: now - 1, platformReceivedAtMs: now } },
    { trigger: 'POSITION_CLOSED', at: new Date(now).toISOString(), tradeId: '', signedClose: { sourceEventAtMs: now - 1, platformReceivedAtMs: now } },
    { trigger: 'POSITION_CLOSED', at: new Date(now).toISOString(), tradeId: 'cont-c105efa5', signedClose: {} },
    { trigger: 'POSITION_CLOSED', at: new Date(now).toISOString(), tradeId: 'cont-c105efa5', signedClose: { sourceEventAtMs: now + 1, platformReceivedAtMs: now } },
  ]) assert.equal(parseExecutorWakeRequest(bad), null);
  assert.equal(parseExecutorWakeRequest({
    trigger: 'ORDER_PLACED', at: new Date(now).toISOString(), tradeId: 'cont-entry',
    signedClose: { exitPrice: 64_400 },
  }), null);
});

test('partial pending entry contributes its protected filled slice to ledger reconciliation', async () => {
  const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
  service.loadExecutionMeta = async (id: string) => id === 'partial'
    ? { direction: 'SHORT', qty: 0.03148, partialFillQty: 0.03022869 }
    : { direction: 'LONG', qty: 0.02 };

  const summary = await service.buildVirtualLotSummary([
    { id: 'partial', status: SignalCycleStatus.PENDING_ENTRY },
  ]);

  assert.equal(summary.pending, 1);
  assert.equal(summary.open, 0);
  assert.equal(summary.openQty, 0.03022869);
  assert.equal(summary.signedOpenQty, -0.03022869);
});

test('bounded aggressive catch-up never exceeds the five-basis-point adverse cap', () => {
  assert.equal(boundedAggressiveCatchupLimit('LONG', 64_000), 64_032);
  assert.equal(boundedAggressiveCatchupLimit('SHORT', 64_000), 63_968);
  assert.equal(aggressiveCatchupIsWithinBound('LONG', 64_031.99, 64_032), true);
  assert.equal(aggressiveCatchupIsWithinBound('LONG', 64_032.01, 64_032), false);
  assert.equal(aggressiveCatchupIsWithinBound('SHORT', 63_968, 63_968), true);
  assert.equal(aggressiveCatchupIsWithinBound('SHORT', 63_967.99, 63_968), false);
});

test('bounded aggressive catch-up is production-default and can be explicitly disabled', () => {
  const previousMode = process.env.NODE_ENV;
  const previousEnabled = process.env.AGGRESSIVE_CATCHUP_ENABLED;
  try {
    process.env.NODE_ENV = 'production';
    delete process.env.AGGRESSIVE_CATCHUP_ENABLED;
    assert.equal(aggressiveCatchupEnabled(), true);
    process.env.AGGRESSIVE_CATCHUP_ENABLED = 'false';
    assert.equal(aggressiveCatchupEnabled(), false);
  } finally {
    if (previousMode === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = previousMode;
    if (previousEnabled === undefined) delete process.env.AGGRESSIVE_CATCHUP_ENABLED;
    else process.env.AGGRESSIVE_CATCHUP_ENABLED = previousEnabled;
  }
});

test('private wake parser preserves only bounded POSITION_OPENED fill evidence', () => {
  const now = Date.now();
  const valid = {
    trigger: 'POSITION_OPENED', at: new Date(now).toISOString(), tradeId: 'cont-c105efa5',
    signedOpen: { fillPrice: 64_200, sourceEventAtMs: now - 500, platformReceivedAtMs: now - 100 },
  };
  assert.equal(parseExecutorWakeRequest(valid)?.signedOpen?.fillPrice, 64_200);
  assert.equal(parseExecutorWakeRequest({ ...valid, signedOpen: {} }), null);
  assert.equal(parseExecutorWakeRequest({ ...valid, signedOpen: { ...valid.signedOpen, fillPrice: '64200' } }), null);
  assert.equal(parseExecutorWakeRequest({ ...valid, signedOpen: { ...valid.signedOpen, sourceEventAtMs: now } }), null);
  assert.equal(parseExecutorWakeRequest({ trigger: 'ORDER_PLACED', at: new Date(now).toISOString(), tradeId: 'cont-c105efa5', signedOpen: valid.signedOpen }), null);
});

test('duplicate pre/post ORDER_EXPIRED wake cannot queue a second cancellation', async () => {
  const previousExecution = process.env.SUBSCRIBER_EXECUTION_ENABLED;
  const previousWorker = process.env.RELAY_EXECUTOR_WORKER;
  process.env.SUBSCRIBER_EXECUTION_ENABLED = 'true';
  process.env.RELAY_EXECUTOR_WORKER = 'true';
  try {
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.pendingDirectWakes = new Map();
    service.activeDirectWakes = new Map([['trade:cont-expiry-one', {
      trigger: 'ORDER_EXPIRED', tradeId: 'cont-expiry-one', at: '2026-08-11T13:14:25.652Z',
      signedExpiry: { sourceExpiresAtMs:1,sourceEventAtMs:2,platformReceivedAtMs:3,eventSeq:1,limitPrice:64000,eventId:'expiry-1',reason:'SIGNAL_TTL_EXPIRED' },
    }]]);
    assert.equal(await service.acceptDirectExecutorWake({
      trigger: 'ORDER_EXPIRED', tradeId: 'cont-expiry-one', at: '2026-08-11T13:14:25.652Z',
      signedExpiry: { sourceExpiresAtMs:1,sourceEventAtMs:2,platformReceivedAtMs:3,eventSeq:1,limitPrice:64000,eventId:'expiry-1',reason:'SIGNAL_TTL_EXPIRED' },
    }), true);
    assert.equal(service.pendingDirectWakes.size, 0);
    assert.equal(await service.acceptDirectExecutorWake({
      trigger: 'ORDER_EXPIRED', tradeId: 'cont-expiry-one', at: '2026-08-11T13:14:26.652Z',
      signedExpiry: { sourceExpiresAtMs:4,sourceEventAtMs:5,platformReceivedAtMs:6,eventSeq:2,limitPrice:63990,eventId:'expiry-2',reason:'SIGNAL_TTL_EXPIRED' },
    }), true);
    assert.equal(service.pendingDirectWakes.size, 1);
  } finally {
    if (previousExecution == null) delete process.env.SUBSCRIBER_EXECUTION_ENABLED;
    else process.env.SUBSCRIBER_EXECUTION_ENABLED = previousExecution;
    if (previousWorker == null) delete process.env.RELAY_EXECUTOR_WORKER;
    else process.env.RELAY_EXECUTOR_WORKER = previousWorker;
  }
});

test('private expiry wake requires exact identity, generation, price, and coherent timestamps', () => {
  const now = Date.now();
  const valid = {
    trigger: 'ORDER_EXPIRED', at: new Date(now).toISOString(), tradeId: 'cont-c105efa5',
    signedExpiry: {
      sourceExpiresAtMs: now - 100, sourceEventAtMs: now - 90,
      platformReceivedAtMs: now, eventSeq: 3, limitPrice: 64_400,
      eventId: 'expiry-3', reason: 'SIGNAL_TTL_EXPIRED',
    },
  };
  assert.equal(parseExecutorWakeRequest(valid)?.signedExpiry?.eventSeq, 3);
  assert.equal(parseExecutorWakeRequest({ ...valid, tradeId: '' }), null);
  assert.equal(parseExecutorWakeRequest({ ...valid, signedExpiry: {} }), null);
  assert.equal(parseExecutorWakeRequest({ ...valid, signedExpiry: { ...valid.signedExpiry, sourceExpiresAtMs: now + 1 } }), null);
  assert.equal(parseExecutorWakeRequest({ ...valid, signedExpiry: { ...valid.signedExpiry, eventSeq: '3' } }), null);
  assert.equal(parseExecutorWakeRequest({ ...valid, signedExpiry: { ...valid.signedExpiry, limitPrice: '64400' } }), null);
});

test('signed expiry cancels only current exact pending generation before terminal persistence', async () => {
  const service = new SignalSubscriberExecutionService(
    {} as never, {} as never, {} as never, {} as never, {} as never,
    {} as never, {} as never, {} as never,
  ) as any;
  service.participantMoneyLane = new Map();
  const cycle = {
    id: 'cycle-expiry', agentId: 'agent', tradeId: 'cont-c105efa5',
    intentEnvelope: { action: 'ENTER', entry: { exact_limit_price: 64_400 }, context: { showcase_event_seq: 3 } },
  };
  const participant = { id: 'participant-expiry', status: SignalCycleStatus.PENDING_ENTRY, cycle };
  service.prisma = { signalCycleParticipant: {
    findMany: async () => [participant], findUnique: async () => participant,
  }};
  service.exchanges = { getUserCredentials: async () => ({ apiKey: 'k', apiSecret: 's' }) };
  service.loadExecutionMeta = async () => ({ bitfinexOrderId: 7001, direction: 'SHORT', qty: 0.01, limitPrice: 64_400 });
  service.detectEntryFillBeforeCancel = async () => null;
  service.bitfinex = { fetchOrderTrades: async () => [] };
  service.activeTrading = { getOpenPositionDetail: async () => null };
  const order: string[] = [];
  service.cancelManagedOrderGone = async (_c:unknown,_o:unknown,_l:unknown,onTiming?:(v:unknown)=>void) => {
    onTiming?.({submitStartedAtMs:1000,exchangeAckAtMs:1100,confirmedAtMs:1200});
    order.push('cancel-confirmed'); return { gone: true, reason: 'CANCELLED' };
  };
  service.cycles = { recordHireExecutionEvent: async () => { order.push('EXPIRED'); } };
  const wake = {
    trigger: 'ORDER_EXPIRED', tradeId: 'cont-c105efa5', at: new Date().toISOString(),
    signedExpiry: { sourceExpiresAtMs: Date.now() - 100, sourceEventAtMs: Date.now() - 90, platformReceivedAtMs: Date.now(), eventSeq: 3, limitPrice: 64_400, eventId:'expiry-3', reason:'SIGNAL_TTL_EXPIRED' },
  };
  assert.equal(await service.tryImmediateSignedOrderExpiry({ id:'i', userId:'u', agentId:'agent', exchangeProvider:'bitfinex' }, wake), true);
  assert.deepEqual(order, ['cancel-confirmed', 'EXPIRED']);
  order.length = 0;
  assert.equal(await service.tryImmediateSignedOrderExpiry({ id:'i', userId:'u', agentId:'agent', exchangeProvider:'bitfinex' }, {
    ...wake, signedExpiry: { ...wake.signedExpiry, eventSeq: 2 },
  }), false);
  assert.deepEqual(order, []);
});

test('signed expiry promotes a detected fill through terminal fill-close funnel and never blindly expires', async () => {
  const service = new SignalSubscriberExecutionService(
    {} as never, {} as never, {} as never, {} as never, {} as never,
    {} as never, {} as never, {} as never,
  ) as any;
  service.participantMoneyLane = new Map();
  const cycle = { id:'c', agentId:'a', tradeId:'cont-c105efa5', intentEnvelope:{ action:'ENTER', entry:{exact_limit_price:64000}, context:{showcase_event_seq:1} } };
  const participant = { id:'p', status:SignalCycleStatus.PENDING_ENTRY, cycle };
  service.prisma = { signalCycleParticipant:{ findMany:async()=>[participant], findUnique:async()=>participant } };
  service.exchanges = { getUserCredentials:async()=>({apiKey:'k',apiSecret:'s'}) };
  service.loadExecutionMeta = async()=>({bitfinexOrderId:7,direction:'SHORT',qty:.01,limitPrice:64000});
  service.detectEntryFillBeforeCancel = async()=>({filledQty:.004,fillPrice:64010,source:'ORDER_PARTIAL',orderResting:true});
  let context = '';
  service.recordCancelRaceFill = async (...args: unknown[]) => { context = String(args[8]); return true; };
  service.cancelManagedOrderGone = async()=>{ throw new Error('must stay in fill funnel'); };
  service.cycles = { recordHireExecutionEvent:async()=>{ throw new Error('must not direct-expire'); } };
  const now=Date.now();
  assert.equal(await service.tryImmediateSignedOrderExpiry({id:'i',userId:'u',agentId:'a',exchangeProvider:'bitfinex'}, {
    trigger:'ORDER_EXPIRED',tradeId:'cont-c105efa5',at:new Date(now).toISOString(),
    signedExpiry:{sourceExpiresAtMs:now-100,sourceEventAtMs:now-90,platformReceivedAtMs:now,eventSeq:1,limitPrice:64000,eventId:'expiry-1',reason:'SIGNAL_TTL_EXPIRED'},
  }), true);
  assert.equal(context, 'SHOWCASE_ORDER_EXPIRED');
});

test('signed expiry rechecks a fill that races cancellation before EXPIRED', async () => {
  const service = new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
  service.participantMoneyLane=new Map();
  const cycle={id:'c',agentId:'a',tradeId:'cont-c105efa5',intentEnvelope:{action:'ENTER',entry:{exact_limit_price:64000},context:{showcase_event_seq:1}}};
  const participant={id:'p',status:SignalCycleStatus.PENDING_ENTRY,cycle};
  service.prisma={signalCycleParticipant:{findMany:async()=>[participant],findUnique:async()=>participant}};
  service.exchanges={getUserCredentials:async()=>({apiKey:'k',apiSecret:'s'})};
  service.loadExecutionMeta=async()=>({bitfinexOrderId:7,direction:'SHORT',qty:.01,limitPrice:64000});
  let reads=0;
  service.detectEntryFillBeforeCancel=async()=>++reads===1?null:{filledQty:.003,fillPrice:64000,source:'POSITION_DELTA',orderResting:false};
  service.bitfinex={fetchOrderTrades:async()=>[{execAmount:.003,execPrice:64000}]};
  service.activeTrading={getOpenPositionDetail:async()=>null};
  service.cancelManagedOrderGone=async(_c:unknown,_o:unknown,_l:unknown,onTiming:(v:unknown)=>void)=>{onTiming({submitStartedAtMs:1,exchangeAckAtMs:2,confirmedAtMs:3});return{gone:true,reason:'CANCELLED'}};
  let promoted=false;
  service.recordCancelRaceFill=async()=>{promoted=true;return true};
  service.cycles={recordHireExecutionEvent:async()=>{throw new Error('must not expire raced fill')}};
  const now=Date.now();
  assert.equal(await service.tryImmediateSignedOrderExpiry({id:'i',userId:'u',agentId:'a',exchangeProvider:'bitfinex'},{trigger:'ORDER_EXPIRED',tradeId:'cont-c105efa5',at:new Date(now).toISOString(),signedExpiry:{sourceExpiresAtMs:now-100,sourceEventAtMs:now-90,platformReceivedAtMs:now,eventSeq:1,limitPrice:64000,eventId:'expiry-1',reason:'SIGNAL_TTL_EXPIRED'}}),true);
  assert.equal(promoted,true);
  assert.equal(reads,1);
});

test('signed expiry cancellation failure remains pending and never records EXPIRED', async () => {
  const service = new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
  service.participantMoneyLane=new Map();
  const cycle={id:'c',agentId:'a',tradeId:'cont-c105efa5',intentEnvelope:{action:'ENTER',entry:{exact_limit_price:64000},context:{showcase_event_seq:1}}};
  const participant={id:'p',status:SignalCycleStatus.PENDING_ENTRY,cycle};
  service.prisma={signalCycleParticipant:{findMany:async()=>[participant],findUnique:async()=>participant},tradingAgentInstance:{update:async()=>({})}};
  service.exchanges={getUserCredentials:async()=>({apiKey:'k',apiSecret:'s'})};
  service.loadExecutionMeta=async()=>({bitfinexOrderId:7,direction:'SHORT',qty:.01,limitPrice:64000});
  service.detectEntryFillBeforeCancel=async()=>null;
  service.bitfinex={fetchOrderTrades:async()=>{throw new Error('read failed')}};
  service.activeTrading={getOpenPositionDetail:async()=>null};
  service.cancelManagedOrderGone=async(_c:unknown,_o:unknown,_l:unknown,onTiming:(v:unknown)=>void)=>{onTiming({submitStartedAtMs:1,exchangeAckAtMs:2,confirmedAtMs:3});return{gone:true,reason:'CANCELLED'}};
  let expired=false;
  service.cycles={recordHireExecutionEvent:async(_u:unknown,_a:unknown,_c:unknown,event:string)=>{if(event==='EXPIRED')expired=true}};
  const now=Date.now();
  assert.equal(await service.tryImmediateSignedOrderExpiry({id:'i',userId:'u',agentId:'a',exchangeProvider:'bitfinex'},{trigger:'ORDER_EXPIRED',tradeId:'cont-c105efa5',at:new Date(now).toISOString(),signedExpiry:{sourceExpiresAtMs:now-100,sourceEventAtMs:now-90,platformReceivedAtMs:now,eventSeq:1,limitPrice:64000,eventId:'expiry-1',reason:'SIGNAL_TTL_EXPIRED'}}),false);
  assert.equal(expired,false);
});

test('post-cancel expiry proof distinguishes zero-flat, NOT_FOUND proof, and unknown reads', async () => {
  const service = new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
  const meta={bitfinexOrderId:7,direction:'SHORT',qty:.01,limitPrice:64000,replacementExchangeAckAtMs:100};
  service.bitfinex={fetchOrderTrades:async()=>[],fetchOrderHistoryEvidence:async()=>({terminal:true,filledQty:0})};
  service.activeTrading={getOpenPositionDetail:async()=>null};
  assert.deepEqual(await service.classifyPostCancelEntry({},meta,'CANCELLED'),{kind:'PROVEN_UNFILLED'});
  assert.deepEqual(await service.classifyPostCancelEntry({},meta,'NOT_FOUND'),{kind:'PROVEN_UNFILLED'});
  service.bitfinex.fetchOrderHistoryEvidence=async()=>{throw new Error('history unavailable')};
  assert.deepEqual(await service.classifyPostCancelEntry({},meta,'NOT_FOUND'),{kind:'UNKNOWN',reason:'ORDER_HISTORY_UNAVAILABLE'});
  service.bitfinex.fetchOrderHistoryEvidence=async()=>({terminal:true,filledQty:0});
  service.activeTrading.getOpenPositionDetail=async()=>{throw new Error('position unavailable')};
  assert.deepEqual(await service.classifyPostCancelEntry({},meta,'CANCELLED'),{kind:'UNKNOWN',reason:'POSITION_UNAVAILABLE'});
});

test('post-cancel position proof treats every attributable partial delta as a fill', async () => {
  const service = new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
  service.bitfinex={fetchOrderTrades:async()=>[]};
  const meta={bitfinexOrderId:7,direction:'SHORT',qty:.01,limitPrice:64000};
  service.activeTrading={getOpenPositionDetail:async()=>({amount:-.000001,basePrice:64001})};
  let result=await service.classifyPostCancelEntry({},meta,'CANCELLED');
  assert.equal(result.kind,'FILL');
  assert.equal(result.fill.filledQty,.000001);

  service.activeTrading.getOpenPositionDetail=async()=>({amount:-.020001,basePrice:64001});
  result=await service.classifyPostCancelEntry({}, {...meta,exchangeQtyAtOrder:.02},'CANCELLED');
  assert.equal(result.kind,'FILL');
  assert.equal(result.fill.filledQty,.000001);

  service.activeTrading.getOpenPositionDetail=async()=>({amount:-.02,basePrice:64001});
  assert.deepEqual(
    await service.classifyPostCancelEntry({}, {...meta,exchangeQtyAtOrder:.02},'CANCELLED'),
    {kind:'PROVEN_UNFILLED'},
  );

  service.activeTrading.getOpenPositionDetail=async()=>({amount:.020001,basePrice:64001});
  assert.deepEqual(
    await service.classifyPostCancelEntry({}, {...meta,exchangeQtyAtOrder:.02},'CANCELLED'),
    {kind:'PROVEN_UNFILLED'},
  );
});

test('signed close retires only its exact current pending order and preserves canonical CLOSED', async () => {
  const service = new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
  service.participantMoneyLane=new Map();
  const cycle={id:'c',agentId:'a',tradeId:'cont-close-pending',status:SignalCycleStatus.PENDING_ENTRY,intentEnvelope:{action:'ENTER'}};
  const participant={id:'p',status:SignalCycleStatus.PENDING_ENTRY,cycle};
  const terminal:string[]=[];
  service.prisma={
    signalCycleParticipant:{findMany:async()=>[participant],findUnique:async()=>participant},
    signalCycle:{update:async(args:{data:{status:string}})=>{terminal.push(args.data.status);return{}}},
  };
  service.loadExecutionMeta=async()=>({bitfinexOrderId:8002,direction:'SHORT',qty:.01,limitPrice:64000,originTradeId:null});
  service.detectEntryFillBeforeCancel=async()=>null;
  let cancelled=0;
  service.cancelManagedOrderGone=async(_c:unknown,oid:number,_l:unknown,onTiming:(v:unknown)=>void)=>{cancelled=oid;onTiming({submitStartedAtMs:100,exchangeAckAtMs:120,confirmedAtMs:130});return{gone:true,reason:'CANCELLED'}};
  service.classifyPostCancelEntry=async()=>({kind:'PROVEN_UNFILLED'});
  service.cycles={recordHireExecutionEvent:async(_u:unknown,_a:unknown,_c:unknown,event:string,body:{reason:string})=>{terminal.push(`${event}:${body.reason}`)}};
  const now=Date.now();
  assert.equal(await service.tryImmediateSignedPendingClose({id:'i',userId:'u',agentId:'a',exchangeProvider:'bitfinex'},{trigger:'POSITION_CLOSED',tradeId:'cont-close-pending',at:new Date(now).toISOString(),signedClose:{sourceEventAtMs:now-100,platformReceivedAtMs:now-50}},{}),true);
  assert.equal(cancelled,8002);
  assert.deepEqual(terminal,['EXPIRED:SHOWCASE_CLOSED_BEFORE_COPY_FILL',SignalCycleStatus.CLOSED]);
});

test('signed close pending path keeps unknown and cancel-race fills retryable', async () => {
  const service = new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
  service.participantMoneyLane=new Map();
  const cycle={id:'c',agentId:'a',tradeId:'cont-close-pending',status:SignalCycleStatus.PENDING_ENTRY,intentEnvelope:{action:'ENTER'}};
  const participant={id:'p',status:SignalCycleStatus.PENDING_ENTRY,cycle};
  service.prisma={signalCycleParticipant:{findMany:async()=>[participant],findUnique:async()=>participant}};
  service.loadExecutionMeta=async()=>({bitfinexOrderId:8002,direction:'SHORT',qty:.01,limitPrice:64000});
  service.detectEntryFillBeforeCancel=async()=>null;
  service.cancelManagedOrderGone=async(_c:unknown,_o:unknown,_l:unknown,onTiming:(v:unknown)=>void)=>{onTiming({submitStartedAtMs:1,exchangeAckAtMs:2,confirmedAtMs:3});return{gone:true,reason:'CANCELLED'}};
  let expired=false;
  service.cycles={recordHireExecutionEvent:async(_u:unknown,_a:unknown,_c:unknown,event:string)=>{if(event==='EXPIRED')expired=true}};
  const now=Date.now();
  const wake={trigger:'POSITION_CLOSED',tradeId:'cont-close-pending',at:new Date(now).toISOString(),signedClose:{sourceEventAtMs:now-100,platformReceivedAtMs:now-50}};
  service.classifyPostCancelEntry=async()=>({kind:'UNKNOWN',reason:'POSITION_UNAVAILABLE'});
  assert.equal(await service.tryImmediateSignedPendingClose({id:'i',userId:'u',agentId:'a',exchangeProvider:'bitfinex'},wake,{}),false);
  assert.equal(expired,false);
  service.classifyPostCancelEntry=async()=>({kind:'FILL',fill:{filledQty:.001,fillPrice:64000,source:'POSITION_DELTA',orderResting:false}});
  let context='';
  service.recordCancelRaceFill=async(...args:unknown[])=>{context=String(args[8]);return false};
  assert.equal(await service.tryImmediateSignedPendingClose({id:'i',userId:'u',agentId:'a',exchangeProvider:'bitfinex'},wake,{}),false);
  assert.equal(context,'SHOWCASE_CYCLE_CLOSED');
  assert.equal(expired,false);
});

test('signed close waits for replacement lane then cancels the freshly durable order id', async () => {
  const service = new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
  const cycle={id:'c',agentId:'a',tradeId:'cont-close-pending',status:SignalCycleStatus.PENDING_ENTRY,intentEnvelope:{action:'ENTER'}};
  const participant={id:'p',status:SignalCycleStatus.PENDING_ENTRY,cycle};
  let releaseLane!:()=>void;
  const lane=new Promise<void>((resolve)=>{releaseLane=resolve});
  service.acquireParticipantMoneyLane=async()=>{await lane;return()=>{}};
  service.prisma={signalCycleParticipant:{findMany:async()=>[participant],findUnique:async()=>participant},signalCycle:{update:async()=>({})}};
  service.loadExecutionMeta=async()=>({bitfinexOrderId:9002,direction:'SHORT',qty:.01,limitPrice:63990});
  service.detectEntryFillBeforeCancel=async()=>null;
  let cancelled=0;
  service.cancelManagedOrderGone=async(_c:unknown,oid:number,_l:unknown,onTiming:(v:unknown)=>void)=>{cancelled=oid;onTiming({submitStartedAtMs:1,exchangeAckAtMs:2,confirmedAtMs:3});return{gone:true,reason:'CANCELLED'}};
  service.classifyPostCancelEntry=async()=>({kind:'PROVEN_UNFILLED'});
  service.cycles={recordHireExecutionEvent:async()=>({})};
  const now=Date.now();
  const pending=service.tryImmediateSignedPendingClose({id:'i',userId:'u',agentId:'a',exchangeProvider:'bitfinex'},{trigger:'POSITION_CLOSED',tradeId:'cont-close-pending',at:new Date(now).toISOString(),signedClose:{sourceEventAtMs:now-100,platformReceivedAtMs:now-50}},{});
  await Promise.resolve();
  assert.equal(cancelled,0);
  releaseLane();
  assert.equal(await pending,true);
  assert.equal(cancelled,9002);
});

test('authenticated Bitfinex trade event records exact owned fill without source POSITION_OPENED', async () => {
  const previousExecution=process.env.SUBSCRIBER_EXECUTION_ENABLED;
  const previousWorker=process.env.RELAY_EXECUTOR_WORKER;
  process.env.SUBSCRIBER_EXECUTION_ENABLED='true';
  process.env.RELAY_EXECUTOR_WORKER='true';
  try {
    const service = new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
    service.participantMoneyLane=new Map();
    const instance={id:'i',userId:'u',agentId:'a',exchangeProvider:'bitfinex',dashboardState:{}};
    const cycle={id:'c',agentId:'a',tradeId:'cont-exchange-fill',status:SignalCycleStatus.PENDING_ENTRY,intentEnvelope:{action:'ENTER'}};
    const participant={id:'p',status:SignalCycleStatus.PENDING_ENTRY,cycle};
    service.prisma={signalCycleParticipant:{findMany:async()=>[{id:'p'}],findUnique:async()=>participant}};
    service.loadExecutionMeta=async()=>({bitfinexOrderId:42,direction:'SHORT',qty:.01,limitPrice:64000});
    let context='';
    let fill:any;
    let receivedAt='';
    service.recordCancelRaceFill=async(...args:unknown[])=>{fill=args[7];context=String(args[8]);receivedAt=String(args[10]);return true};
    await service.handleBitfinexWsTrade('u',{apiKey:'k',apiSecret:'s'},{tradeId:1,orderId:42,symbol:'tBTCF0:USTF0',mts:1000,execAmount:-.004,execPrice:64000,receivedAtMs:1100,cumulativeQty:.004,cumulativeAveragePrice:64000});
    assert.equal(context,'BITFINEX_AUTH_WS_TRADE');
    assert.equal(fill.filledQty,.004);
    assert.equal(receivedAt,new Date(1100).toISOString());
    assert.equal(service.priorityWsFillParticipants.size,0);
  } finally {
    if(previousExecution==null)delete process.env.SUBSCRIBER_EXECUTION_ENABLED;
    else process.env.SUBSCRIBER_EXECUTION_ENABLED=previousExecution;
    if(previousWorker==null)delete process.env.RELAY_EXECUTOR_WORKER;
    else process.env.RELAY_EXECUTOR_WORKER=previousWorker;
  }
});

test('authenticated Bitfinex trade ignores non-owned order id', async () => {
  const previousExecution=process.env.SUBSCRIBER_EXECUTION_ENABLED;
  const previousWorker=process.env.RELAY_EXECUTOR_WORKER;
  process.env.SUBSCRIBER_EXECUTION_ENABLED='true';
  process.env.RELAY_EXECUTOR_WORKER='true';
  try {
    const service = new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
    service.participantMoneyLane=new Map();
    service.prisma={signalCycleParticipant:{findMany:async()=>[{id:'p'}],findUnique:async()=>({id:'p',status:SignalCycleStatus.PENDING_ENTRY,cycle:{agentId:'a',intentEnvelope:{}}})}};
    service.loadExecutionMeta=async()=>({bitfinexOrderId:99,direction:'SHORT'});
    let recorded=false; service.recordCancelRaceFill=async()=>{recorded=true};
    await service.handleBitfinexWsTrade('u',{apiKey:'k',apiSecret:'s'},{tradeId:1,orderId:42,symbol:'tBTCF0:USTF0',mts:1000,execAmount:-.004,execPrice:64000,receivedAtMs:1100,cumulativeQty:.004,cumulativeAveragePrice:64000});
    assert.equal(recorded,false);
  } finally {
    if(previousExecution==null)delete process.env.SUBSCRIBER_EXECUTION_ENABLED;
    else process.env.SUBSCRIBER_EXECUTION_ENABLED=previousExecution;
    if(previousWorker==null)delete process.env.RELAY_EXECUTOR_WORKER;
    else process.env.RELAY_EXECUTOR_WORKER=previousWorker;
  }
});

test('full WebSocket fill is non-resting so protection cannot cancel before stop', async () => {
  const service = new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
  service.participantMoneyLane=new Map();
  const cycle={id:'c',agentId:'a',intentEnvelope:{}};
  service.prisma={signalCycleParticipant:{findMany:async()=>[{id:'p'}],findUnique:async()=>({id:'p',status:SignalCycleStatus.PENDING_ENTRY,cycle})}};
  service.loadExecutionMeta=async()=>({bitfinexOrderId:42,direction:'SHORT',qty:.01});
  let resting:boolean|undefined;
  service.recordCancelRaceFill=async(...args:unknown[])=>{resting=(args[7] as any).orderResting;return true};
  await service.handleBitfinexWsTrade('u',{apiKey:'k',apiSecret:'s'},{tradeId:1,orderId:42,symbol:'tBTCF0:USTF0',mts:1000,execAmount:-.01,execPrice:64000,receivedAtMs:1100,cumulativeQty:.01,cumulativeAveragePrice:64000});
  assert.equal(resting,false);
});

test('WebSocket full fill one sat below the acknowledged quantity is terminal, not a phantom remainder', async () => {
  const service = new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
  service.participantMoneyLane=new Map();
  const cycle={id:'c',agentId:'a',intentEnvelope:{}};
  service.prisma={signalCycleParticipant:{findMany:async()=>[{id:'p'}],findUnique:async()=>({id:'p',status:SignalCycleStatus.PENDING_ENTRY,cycle})}};
  service.loadExecutionMeta=async()=>({bitfinexOrderId:42,direction:'SHORT',qty:.0314});
  let resting:boolean|undefined;
  service.recordCancelRaceFill=async(...args:unknown[])=>{resting=(args[7] as any).orderResting;return true};
  await service.handleBitfinexWsTrade('u',{apiKey:'k',apiSecret:'s'},{tradeId:1,orderId:42,symbol:'tBTCF0:USTF0',mts:1000,execAmount:-.03139999,execPrice:64000,receivedAtMs:1100,cumulativeQty:.03139999,cumulativeAveragePrice:64000});
  assert.equal(resting,false);
});

test('secret-only credential rotation has a distinct fingerprint and retires old stream', async () => {
  const priorExecution=process.env.SUBSCRIBER_EXECUTION_ENABLED;
  const priorWorker=process.env.RELAY_EXECUTOR_WORKER;
  process.env.SUBSCRIBER_EXECUTION_ENABLED='true'; process.env.RELAY_EXECUTOR_WORKER='true';
  try {
  const service = new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
  const oldCreds={apiKey:'same',apiSecret:'old'}; const newCreds={apiKey:'same',apiSecret:'new'};
  const oldKey=service.bitfinexStreamKey('u',oldCreds); const newKey=service.bitfinexStreamKey('u',newCreds);
  assert.notEqual(oldKey,newKey); assert.equal(oldKey.includes('old'),false); assert.equal(newKey.includes('new'),false);
  let stopped=false;
  service.bitfinexTradeStreams=new Map([[oldKey,{stop(){stopped=true}}]]);
  service.relayInstanceCache=new Map([['i',{userId:'u',exchangeProvider:'bitfinex',dashboardState:{}}]]);
  service.exchanges={getUserCredentials:async()=>newCreds};
  service.ensureBitfinexTradeStream=()=>({start(){},stop(){}});
  await service.syncBitfinexTradeStreams();
  assert.equal(stopped,true); assert.equal(service.bitfinexTradeStreams.has(oldKey),false);
  } finally {
    if(priorExecution==null)delete process.env.SUBSCRIBER_EXECUTION_ENABLED;else process.env.SUBSCRIBER_EXECUTION_ENABLED=priorExecution;
    if(priorWorker==null)delete process.env.RELAY_EXECUTOR_WORKER;else process.env.RELAY_EXECUTOR_WORKER=priorWorker;
  }
});

test('actual te socket event reaches exact participant fill funnel immediately', async () => {
  class Socket {
    readyState=1; listeners=new Map<string,((event:any)=>void)[]>();
    addEventListener(type:string,fn:(event:any)=>void){this.listeners.set(type,[...(this.listeners.get(type)??[]),fn])}
    send(_data:string){} close(){} emit(type:string,event:any){for(const fn of this.listeners.get(type)??[])fn(event)}
  }
  const socket=new Socket();
  const service = new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
  service.participantMoneyLane=new Map(); service.cancelRaceFillInFlight=new Set(); service.positionRuntime=new Map(); service.stopManagerCircuitOpen=new Map();
  const cycle={id:'c',agentId:'a',tradeId:'cont-ws-real',status:SignalCycleStatus.PENDING_ENTRY,intentEnvelope:{risk:{stop_loss_margin_pct:10}}};
  service.prisma={signalCycleParticipant:{findMany:async()=>[{id:'p'}],findUnique:async()=>({id:'p',status:SignalCycleStatus.PENDING_ENTRY,cycle})},signalCycleEvent:{count:async()=>0},signalCycle:{update:async()=>({})}};
  service.loadExecutionMeta=async()=>({bitfinexOrderId:42,direction:'SHORT',qty:.01,limitPrice:64000});
  const calls:string[]=[]; let filledResolve!:()=>void; const filledDone=new Promise<void>(resolve=>{filledResolve=resolve});
  service.activeTrading={submitStopOrder:async()=>{calls.push('stop-submit');return 7001},getMarkPrice:async()=>64000};
  service.cancelManagedOrderGone=async()=>{calls.push('entry-cancel');return{gone:true,attempts:1}};
  service.resolveExchangeTradesFillEvidence=async()=>null; service.healStuckPendingFill=async()=>{}; service.executeShowcaseMirrorClose=async()=>true;
  service.cycles={recordHireExecutionEvent:async(_u:string,_a:string,_c:string,type:string)=>{calls.push(type);if(type==='FILLED')filledResolve()}};
  const stream=new BitfinexAuthTradeStream({apiKey:'socket-k',apiSecret:'socket-s'},trade=>service.handleBitfinexWsTrade('u',{apiKey:'socket-k',apiSecret:'socket-s'},trade),()=>socket as any);
  stream.start(); socket.emit('open',{}); socket.emit('message',{data:JSON.stringify({event:'auth',status:'OK'})});
  socket.emit('message',{data:JSON.stringify([0,'te',[7,'tBTCF0:USTF0',1000,42,-.01,64000]])});
  await filledDone;
  assert.equal(calls[0],'stop-submit'); assert.equal(calls.includes('entry-cancel'),false); stream.stop();
});

test('overlapping stream sync serializes rotation and installs only newest fingerprint', async () => {
  const priorExecution=process.env.SUBSCRIBER_EXECUTION_ENABLED, priorWorker=process.env.RELAY_EXECUTOR_WORKER;
  process.env.SUBSCRIBER_EXECUTION_ENABLED='true';process.env.RELAY_EXECUTOR_WORKER='true';
  try {
    const service=new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
    service.relayInstanceCache=new Map([['i',{userId:'u',exchangeProvider:'bitfinex',dashboardState:{}}]]); service.bitfinexTradeStreams=new Map();
    let releaseOld!:(value:any)=>void; const delayed=new Promise(resolve=>{releaseOld=resolve}); let reads=0;
    const newest={apiKey:'k',apiSecret:'new'};
    service.exchanges={getUserCredentials:async()=>{reads+=1;if(reads===1)return delayed;return newest}};
    const installed:string[]=[];
    service.ensureBitfinexTradeStream=(userId:string,creds:any)=>{const key=service.bitfinexStreamKey(userId,creds);installed.push(key);service.bitfinexTradeStreams.set(key,{stop(){}});return{}};
    const first=service.syncBitfinexTradeStreams(); const second=service.syncBitfinexTradeStreams();
    releaseOld({apiKey:'k',apiSecret:'old'}); await Promise.all([first,second]);
    const newKey=service.bitfinexStreamKey('u',newest),oldKey=service.bitfinexStreamKey('u',{apiKey:'k',apiSecret:'old'});
    assert.equal(service.bitfinexTradeStreams.has(newKey),true); assert.equal(service.bitfinexTradeStreams.has(oldKey),false);
    assert.equal(new Set(installed).size,1);
  } finally {
    if(priorExecution==null)delete process.env.SUBSCRIBER_EXECUTION_ENABLED;else process.env.SUBSCRIBER_EXECUTION_ENABLED=priorExecution;
    if(priorWorker==null)delete process.env.RELAY_EXECUTOR_WORKER;else process.env.RELAY_EXECUTOR_WORKER=priorWorker;
  }
});

test('service dedupes concurrent user api-key trade id only after successful handling', async () => {
  const service=new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
  service.participantMoneyLane=new Map(); service.wsTradeInFlight=new Map(); service.wsTradeCompletedAt=new Map();
  service.prisma={signalCycleParticipant:{findMany:async()=>[{id:'p'}],findUnique:async()=>({id:'p',status:SignalCycleStatus.PENDING_ENTRY,cycle:{agentId:'a',intentEnvelope:{}}})}};
  service.loadExecutionMeta=async()=>({bitfinexOrderId:42,direction:'SHORT',qty:.01});
  let calls=0; let release!:()=>void; const gate=new Promise<void>(resolve=>{release=resolve});
  service.recordCancelRaceFill=async()=>{calls+=1;await gate;return true};
  const trade={tradeId:99,orderId:42,symbol:'tBTCF0:USTF0',mts:1000,execAmount:-.01,execPrice:64000,receivedAtMs:1100,cumulativeQty:.01,cumulativeAveragePrice:64000};
  const a=service.handleBitfinexWsTrade('u',{apiKey:'k',apiSecret:'s'},trade); const b=service.handleBitfinexWsTrade('u',{apiKey:'k',apiSecret:'s'},trade);
  await new Promise(resolve=>setImmediate(resolve)); assert.equal(calls,1); release(); assert.deepEqual(await Promise.all([a,b]),[true,true]);
  assert.equal(await service.handleBitfinexWsTrade('u',{apiKey:'k',apiSecret:'s'},trade),true); assert.equal(calls,1);
});

test('service WebSocket success dedupe cleanup is bounded and never prunes in-flight key', () => {
  const service=new SignalSubscriberExecutionService({} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never,{} as never) as any;
  const now=10_000_000; service.wsTradeCompletedAt=new Map(); service.wsTradeInFlight=new Map();
  service.wsTradeCompletedAt.set('expired',now-3_700_000); service.wsTradeCompletedAt.set('active',now-3_700_000); service.wsTradeInFlight.set('active',Promise.resolve(true));
  for(let i=0;i<20_005;i++)service.wsTradeCompletedAt.set(`recent-${i}`,now);
  service.pruneWsTradeDedupe(now);
  assert.equal(service.wsTradeCompletedAt.has('expired'),false); assert.equal(service.wsTradeCompletedAt.has('active'),true);
  assert.ok(service.wsTradeCompletedAt.size<=20_001);
});

test('fast-wake completion latency excludes dashboard telemetry persistence', async () => {
  const service = new SignalSubscriberExecutionService(
    {} as never, {} as never, {} as never, {} as never,
    {} as never, {} as never, {} as never, {} as never,
  ) as any;
  let releaseRead!: () => void;
  const readGate = new Promise<void>((resolve) => { releaseRead = resolve; });
  let persistedPatch: any = null;
  service.prisma = {
    tradingAgentInstance: {
      findUnique: async () => {
        await readGate;
        return { dashboardState: { retained: true } };
      },
      update: async ({ data }: any) => {
        persistedPatch = data.dashboardState;
        return {};
      },
    },
  };
  const originalNow = Date.now;
  let nowCalls = 0;
  Date.now = () => {
    nowCalls += 1;
    return 2_000;
  };
  try {
    const persisting = service.persistFastWakeTelemetry(
      'instance-1',
      { trigger: 'ORDER_PLACED', tradeId: 'cont-telemetry', at: new Date(1_000).toISOString() },
      1_500,
      'ENTRY_PLACED',
    );
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(nowCalls, 1, 'completion must be captured before the delayed telemetry read resolves');
    releaseRead();
    await persisting;
  } finally {
    Date.now = originalNow;
  }
  assert.equal(persistedPatch.retained, true);
  assert.equal(persistedPatch.relayExecutorFastWake.completedAt, new Date(2_000).toISOString());
  assert.equal(persistedPatch.relayExecutorFastWake.latencyMs, 1_000);
  assert.equal(persistedPatch.relayExecutorFastWake.outcome, 'ENTRY_PLACED');
});
