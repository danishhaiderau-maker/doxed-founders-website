import { Injectable, Logger, NotFoundException, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createHash } from 'crypto';
import {
  NotificationType,
  SignalCycleStatus,
  TradingAgentInstanceStatus,
  type TradingAgentInstance,
  Prisma,
} from '@prisma/client';
import { PrismaClientKnownRequestError } from '@prisma/client/runtime/library';
import type { SignalIntentEnvelope } from '@dcf/utils';
import {
  resolveSubscriberExecutionPollMs,
  DEFAULT_SUBSCRIBER_LEVERAGE,
  resolveSubscriberLeverage,
  BITFINEX_COPY_POLICY_VERSION,
  SHOWCASE_STRUCTURAL_ENTRY_POLICY_VERSION,
  SHOWCASE_DETERMINISTIC_ENTRY_POLICY_VERSION,
  isExecutableEntryPolicy,
  resolveMaxConcurrentCopySignals,
  resolveMirrorDisasterStopMarginPct,
  isCopyRelaySimActive,
  readCopyRelaySimState,
  btcToSats,
  satsToBtc,
  relayPositionDeltaSats,
  SUBSCRIBER_CHASE_INTERVAL_MS,
  SUBSCRIBER_CHASE_NEAR_FILL_INTERVAL_MS,
  SUBSCRIBER_SHOWCASE_ANCHOR_CHASE_MS,
  computeLimitFromMark,
  computeStopPrice,
  computeProfitLockStopPrice,
  computeQty,
  computeLimitChaseTarget,
  computeUnrealizedMarginPct,
  evaluateSubscriberLotExit,
  evaluateRealSideSafetyNetExit,
  isShowcaseMirrorOnlyMode,
  isNearChaseFillZone,
  sanitizeLimitPrice,
  getProfitLockFloor,
  solveScenarioCRung,
  SCENARIO_C_LADDER,
  buildCopyRelayCapacity,
  isPaperLaneTradeId,
  isMirrorableLaneTradeId,
  shouldDryRunIntentMirror,
  type CopyRelayCapacitySnapshot,
  type VirtualLotExitReason,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { ExchangesService } from '../exchanges/exchanges.service';
import {
  BitfinexTradingClient,
  type BitfinexActiveOrder,
} from '../exchanges/bitfinex-api.client';
import type { ExchangeCredentials } from '../exchanges/exchange-adapter.interface';
import {
  cancelOrderWithRetry,
  confirmOrderGone,
  type CancelCapableClient,
} from '../exchanges/bitfinex-cancel.util';
import { SignalCyclesService } from './signal-cycles.service';
import { BotBridgeService } from './bot-bridge.service';
import { CopyRelaySimService } from './copy-relay-sim.service';
import { TradeCycleAuditService } from './trade-cycle-audit.service';
import { BitfinexSimTradingClient } from '../exchanges/bitfinex-sim-trading.client';
import {
  applyDashboardPatch,
  applyInstanceDashboardPatch,
  participantTouchesSession,
} from './instance-view.mapper';
import { loadSubscriberMaxMarginUsd } from './subscriber-margin.util';
import {
  isExecutableStructuralShowcaseOrder,
  mapBotStateToAgentStats,
  normalizeBotSessionTrades,
  type BotApiState,
} from './bot-state.mapper';
import {
  buildRelayFidelitySnapshot,
  resolveShowcaseTradeDetails,
  tradeIdsMatch,
  type RelayFidelitySnapshot,
} from './relay-fidelity.mapper';
import { NotificationsService } from '../notifications/notifications.service';
import { BitfinexAuthTradeStream, type BitfinexWsTrade } from '../exchanges/bitfinex-auth-trade-stream';

const AGENT_SLUG = 'conservative-btc';
const POLL_MS = resolveSubscriberExecutionPollMs();
const MIN_QTY_BTC = 0.00004;
const CHASE_INTERVAL_MS = SUBSCRIBER_CHASE_INTERVAL_MS ?? 60_000;
const CHASE_NEAR_FILL_INTERVAL_MS = SUBSCRIBER_CHASE_NEAR_FILL_INTERVAL_MS ?? 250;
const CHASE_BOT_ANCHOR_MS = SUBSCRIBER_SHOWCASE_ANCHOR_CHASE_MS ?? 250;
const SIGNED_SHOWCASE_FAST_PATH_MAX_AGE_MS = 15_000;
const DEFAULT_EXECUTOR_TICK_TIMEOUT_MS = 60_000;
const DEFAULT_EXECUTOR_HEALTH_MAX_AGE_MS = 15_000;
const EXPIRED_STILL_LIVE_LOOKBACK_MS = 6 * 60 * 60 * 1_000;
const EXPIRED_STILL_LIVE_CANDIDATE_LIMIT = 50;
const PENDING_FILL_RECONCILE_GRACE_MS = 60_000;
const SHOWCASE_ORDER_SNAPSHOT_PROPAGATION_GRACE_MS = 15_000;
const BITFINEX_REPLACEMENT_VISIBILITY_GRACE_MS = 15_000;
/**
 * The showcase sizes collateral at its signal price, then posts its canonical
 * deterministic limit up to 0.1% away. Preserve that exact quantity while
 * allowing only a tightly bounded extra margin for the signed anchor offset
 * and decimal transport. Materially larger source sizing still fails closed.
 */
export const EXACT_SHOWCASE_MARGIN_CAP_TOLERANCE_PCT = 0.2;
export const LIVE_FIDELITY_GUARD_THRESHOLD_PCT = 60;
export const LIVE_FIDELITY_GUARD_LOW_OBSERVATIONS = 3;
export const LIVE_FIDELITY_GUARD_MIN_BREACH_MS = 90_000;
export const LIVE_FIDELITY_GUARD_OBSERVATION_INTERVAL_MS = 30_000;
const LIVE_FIDELITY_GUARD_EVIDENCE_MAX_AGE_MS = 30_000;

export type ExactShowcaseEntryQtyResolution =
  | { ok: true; qty: number; requiredMarginUsd: number; capQty: number }
  | { ok: false; reason: 'MISSING_EXACT_QTY' | 'INVALID_SIZING_CONTEXT' | 'BELOW_EXCHANGE_MIN_QTY' | 'SOURCE_QTY_EXCEEDS_SUBSCRIBER_CAP' };

/**
 * Preserve the showcase's canonical quantity at Bitfinex's five-decimal BTC
 * precision. The subscriber margin setting is a ceiling only: exceeding it
 * blocks the entry instead of silently producing a different-sized trade.
 */
export function resolveExactShowcaseEntryQty(input: {
  exactQtyBtc: unknown;
  maxMarginUsd: number;
  leverage: number;
  limitPrice: number;
  minQtyBtc?: number;
}): ExactShowcaseEntryQtyResolution {
  const exact = typeof input.exactQtyBtc === 'number' ? input.exactQtyBtc : Number.NaN;
  if (!Number.isFinite(exact) || exact <= 0) return { ok: false, reason: 'MISSING_EXACT_QTY' };
  if (
    !Number.isFinite(input.maxMarginUsd) || input.maxMarginUsd <= 0
    || !Number.isFinite(input.leverage) || input.leverage <= 0
    || !Number.isFinite(input.limitPrice) || input.limitPrice <= 0
  ) return { ok: false, reason: 'INVALID_SIZING_CONTEXT' };
  const qty = Math.floor((exact + Number.EPSILON) * 1e5) / 1e5;
  const minQty = input.minQtyBtc ?? MIN_QTY_BTC;
  if (qty < minQty) return { ok: false, reason: 'BELOW_EXCHANGE_MIN_QTY' };
  const rawCapQty = input.maxMarginUsd * input.leverage / input.limitPrice;
  const capQty = Math.floor((rawCapQty + Number.EPSILON) * 1e5) / 1e5;
  const requiredMarginUsd = qty * input.limitPrice / input.leverage;
  const toleratedMarginUsd = input.maxMarginUsd
    * (1 + EXACT_SHOWCASE_MARGIN_CAP_TOLERANCE_PCT / 100);
  if (requiredMarginUsd > toleratedMarginUsd + Number.EPSILON) {
    return { ok: false, reason: 'SOURCE_QTY_EXCEEDS_SUBSCRIBER_CAP' };
  }
  return {
    ok: true,
    qty,
    capQty,
    requiredMarginUsd,
  };
}

/**
 * Ops kill-switch for sustained live-fidelity auto-pause.
 * Default ON (unset). Set LIVE_FIDELITY_GUARD_ENABLED=0|false|off|no to skip
 * pause-on-fidelity; only the operator should pause the relay.
 */
export function isLiveFidelityGuardEnabled(
  envValue: string | undefined = process.env.LIVE_FIDELITY_GUARD_ENABLED,
): boolean {
  const v = (envValue ?? '').trim().toLowerCase();
  if (v === '0' || v === 'false' || v === 'off' || v === 'no') return false;
  return true;
}

export function participantCanOwnOrphanOrder(status: SignalCycleStatus): boolean {
  return status === SignalCycleStatus.CLOSED || status === SignalCycleStatus.EXPIRED;
}

export type LiveFidelityGuardState = {
  schema: 'live_fidelity_guard_v1';
  enabled: boolean;
  thresholdPct: number;
  requiredLowObservations: number;
  minBreachDurationMs: number;
  status: 'IDLE' | 'HEALTHY' | 'LOW_PENDING' | 'TRIPPED';
  relayArmedAt: string | null;
  lowObservationCount: number;
  breachStartedAt: string | null;
  lastObservedAt: string | null;
  lastScorePct: number | null;
  comparisonCount: number;
  lastResetReason: string | null;
  lastTrippedAt: string | null;
  action?: {
    relayPaused: boolean;
    pendingEntriesCancelled: number | null;
    pendingEntryCleanupError: string | null;
    openPositionsFlattened: false;
    flattenPolicy: 'NOT_CONFIGURED';
  } | null;
};

export type LiveFidelityGuardDecision = {
  state: LiveFidelityGuardState;
  shouldTrip: boolean;
  observationAccepted: boolean;
};

function readLiveFidelityGuardState(value: unknown): LiveFidelityGuardState | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const raw = value as Partial<LiveFidelityGuardState>;
  if (raw.schema !== 'live_fidelity_guard_v1') return null;
  return raw as LiveFidelityGuardState;
}

function liveFidelityGuardBase(
  previous: LiveFidelityGuardState | null,
  relayArmedAt: string | null,
): LiveFidelityGuardState {
  return {
    schema: 'live_fidelity_guard_v1',
    enabled: isLiveFidelityGuardEnabled(),
    thresholdPct: LIVE_FIDELITY_GUARD_THRESHOLD_PCT,
    requiredLowObservations: LIVE_FIDELITY_GUARD_LOW_OBSERVATIONS,
    minBreachDurationMs: LIVE_FIDELITY_GUARD_MIN_BREACH_MS,
    status: 'IDLE',
    relayArmedAt,
    lowObservationCount: 0,
    breachStartedAt: null,
    lastObservedAt: null,
    lastScorePct: null,
    comparisonCount: 0,
    lastResetReason: null,
    lastTrippedAt: previous?.lastTrippedAt ?? null,
    action: previous?.action ?? null,
  };
}

/**
 * Durable sustained-drop policy. Readiness remains a separate >=98% release
 * gate; this guard only stops an already-active live relay after fidelity is
 * strictly below the user's chosen 60% for 3 observations spanning >=90s.
 */
export function advanceLiveFidelityGuard(input: {
  previous: unknown;
  nowMs: number;
  activeLive: boolean;
  relayArmedAt: string | null;
  evidenceFresh: boolean;
  scorePct: number | null;
  comparisonCount: number;
  resetReason?: string | null;
}): LiveFidelityGuardDecision {
  const parsed = readLiveFidelityGuardState(input.previous);
  const sameEpoch = parsed?.relayArmedAt === input.relayArmedAt;
  const previous = sameEpoch ? parsed : null;
  const base = liveFidelityGuardBase(parsed, input.relayArmedAt);
  const reset = (reason: string): LiveFidelityGuardDecision => ({
    state: {
      ...base,
      lastObservedAt: new Date(input.nowMs).toISOString(),
      lastResetReason: reason,
    },
    shouldTrip: false,
    observationAccepted: true,
  });

  if (!input.activeLive) return reset('LIVE_RELAY_INACTIVE');
  if (!input.evidenceFresh) return reset(input.resetReason ?? 'EVIDENCE_STALE_OR_UNKNOWN');
  if (
    input.comparisonCount <= 0 ||
    input.scorePct == null ||
    !Number.isFinite(input.scorePct)
  ) {
    return reset(input.resetReason ?? 'NO_MEANINGFUL_COMPARISON_DATA');
  }

  const lastObservedMs = previous?.lastObservedAt
    ? Date.parse(previous.lastObservedAt)
    : Number.NaN;
  if (
    Number.isFinite(lastObservedMs) &&
    input.nowMs - lastObservedMs < LIVE_FIDELITY_GUARD_OBSERVATION_INTERVAL_MS
  ) {
    return {
      state: previous!,
      shouldTrip: false,
      observationAccepted: false,
    };
  }

  const observedAt = new Date(input.nowMs).toISOString();
  const scorePct = Math.max(0, Math.min(100, input.scorePct));
  if (scorePct >= LIVE_FIDELITY_GUARD_THRESHOLD_PCT) {
    return {
      state: {
        ...base,
        status: 'HEALTHY',
        lastObservedAt: observedAt,
        lastScorePct: scorePct,
        comparisonCount: input.comparisonCount,
        lastResetReason: 'FIDELITY_RECOVERED',
      },
      shouldTrip: false,
      observationAccepted: true,
    };
  }

  const startedAtMs =
    previous?.breachStartedAt && Number.isFinite(Date.parse(previous.breachStartedAt))
      ? Date.parse(previous.breachStartedAt)
      : input.nowMs;
  const lowObservationCount = (previous?.lowObservationCount ?? 0) + 1;
  const shouldTrip =
    lowObservationCount >= LIVE_FIDELITY_GUARD_LOW_OBSERVATIONS &&
    input.nowMs - startedAtMs >= LIVE_FIDELITY_GUARD_MIN_BREACH_MS;
  return {
    state: {
      ...base,
      status: shouldTrip ? 'TRIPPED' : 'LOW_PENDING',
      lowObservationCount,
      breachStartedAt: new Date(startedAtMs).toISOString(),
      lastObservedAt: observedAt,
      lastScorePct: scorePct,
      comparisonCount: input.comparisonCount,
      lastResetReason: null,
      lastTrippedAt: shouldTrip ? observedAt : (previous?.lastTrippedAt ?? null),
      action: shouldTrip ? null : (previous?.action ?? null),
    },
    shouldTrip,
    observationAccepted: true,
  };
}

/** Only canonical, current Fly state can supply a live fidelity observation. */
export function isFreshCanonicalFidelityBotState(
  bot: BotApiState | null,
  nowMs = Date.now(),
  maxAgeMs = LIVE_FIDELITY_GUARD_EVIDENCE_MAX_AGE_MS,
): boolean {
  if (
    !bot ||
    bot.dashboard_owner !== true ||
    bot.dashboard_port !== 7002 ||
    !bot.bot_instance_id?.trim() ||
    !bot.source_git_rev?.trim() ||
    bot.api_state_error
  ) {
    return false;
  }
  const sourceAt = bot.server_ts ? Date.parse(bot.server_ts) : Number.NaN;
  const ageMs = nowMs - sourceAt;
  return Number.isFinite(sourceAt) && ageMs >= -10_000 && ageMs <= maxAgeMs;
}

/** The exchange/ledger proof must come from this tick and match to the satoshi. */
export function isFreshExactFidelityReconcile(
  value: unknown,
  nowMs = Date.now(),
  maxAgeMs = LIVE_FIDELITY_GUARD_EVIDENCE_MAX_AGE_MS,
): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const reconcile = value as Record<string, unknown>;
  const updatedAt = Date.parse(String(reconcile.updatedAt ?? ''));
  const ageMs = nowMs - updatedAt;
  return (
    Number.isFinite(updatedAt) &&
    ageMs >= 0 &&
    ageMs <= maxAgeMs &&
    reconcile.alert === false &&
    btcToSats(Number(reconcile.deltaBtc ?? Number.NaN)) === 0
  );
}

/**
 * Identity/gap fidelity for the current relay epoch. Offline showcase misses
 * are already excluded by buildRelayFidelitySnapshot and cannot trip this.
 */
export function liveRelayFidelityObservation(
  fidelity: RelayFidelitySnapshot,
): { scorePct: number | null; comparisonCount: number } {
  const actionableShowcaseOnly = fidelity.summary.unmatchedShowcaseCount ?? 0;
  const rows = fidelity.rows ?? [];
  const comparisonCount = rows.length + actionableShowcaseOnly;
  if (comparisonCount <= 0) return { scorePct: null, comparisonCount: 0 };

  const badRelayRows = rows.filter((row) => (
    row.matchKind === 'none' ||
    !row.localBotTradeId ||
    (row.bitfinexEntry != null && row.showcaseEntry == null) ||
    (row.bitfinexExit != null && row.showcaseExit == null)
  )).length;
  const healthy = Math.max(0, rows.length - badRelayRows);
  return {
    scorePct: Math.round((healthy / comparisonCount) * 10_000) / 100,
    comparisonCount,
  };
}

export type RelayExecutorHealthSnapshot = {
  healthy: boolean;
  status: 'STARTING' | 'IDLE' | 'RUNNING' | 'STUCK';
  running: boolean;
  tickStartedAt: string | null;
  lastTickCompletedAt: string | null;
  lastTickDurationMs: number | null;
  currentInstanceId: string | null;
  currentStage: string | null;
  heartbeatAgeMs: number | null;
  runningForMs: number | null;
  timeoutMs: number;
  timeoutCount: number;
  serviceRole?: 'executor-worker' | 'public-api';
  ownerId?: string | null;
  observedAt?: string;
  sourceRevision?: string | null;
  executionEnabled?: boolean;
};

/** Pure liveness calculation shared by runtime health and focused tests. */
export function buildRelayExecutorHealth(input: {
  nowMs: number;
  running: boolean;
  tickStartedAtMs: number;
  lastTickCompletedAtMs: number;
  lastTickDurationMs: number;
  currentInstanceId: string | null;
  currentStage: string | null;
  timeoutMs: number;
  healthMaxAgeMs: number;
  timeoutCount: number;
}): RelayExecutorHealthSnapshot {
  const runningForMs = input.running && input.tickStartedAtMs > 0
    ? Math.max(0, input.nowMs - input.tickStartedAtMs)
    : null;
  const heartbeatAgeMs = input.lastTickCompletedAtMs > 0
    ? Math.max(0, input.nowMs - input.lastTickCompletedAtMs)
    : null;
  const stuck = runningForMs != null && runningForMs > input.timeoutMs;
  const starting = input.lastTickCompletedAtMs <= 0;
  const healthy = !stuck && !starting && (
    input.running || (heartbeatAgeMs != null && heartbeatAgeMs <= input.healthMaxAgeMs)
  );
  const status: RelayExecutorHealthSnapshot['status'] = stuck
    ? 'STUCK'
    : starting
      ? 'STARTING'
      : input.running
        ? 'RUNNING'
        : 'IDLE';
  return {
    healthy,
    status,
    running: input.running,
    tickStartedAt: input.tickStartedAtMs > 0 ? new Date(input.tickStartedAtMs).toISOString() : null,
    lastTickCompletedAt:
      input.lastTickCompletedAtMs > 0 ? new Date(input.lastTickCompletedAtMs).toISOString() : null,
    lastTickDurationMs: input.lastTickDurationMs > 0 ? input.lastTickDurationMs : null,
    currentInstanceId: input.currentInstanceId,
    currentStage: input.currentStage,
    heartbeatAgeMs,
    runningForMs,
    timeoutMs: input.timeoutMs,
    timeoutCount: input.timeoutCount,
  };
}

/**
 * Restart a dedicated executor worker whenever its tick is stuck. A timed-out
 * Promise cannot be cancelled safely, and leaving an isolated worker alive
 * with zero ACTIVE instances makes it permanently unable to pass the next
 * guarded start. The shared public API still restarts only when a stuck
 * executor may have an active live relay so its dashboard remains available.
 * A null count means persistence/query failed, so exposure is unknown and a
 * clean restart remains the safer choice.
 */
export function relayWatchdogShouldRestart(
  activeLiveInstanceCount: number | null,
  dedicatedExecutorWorker = false,
): boolean {
  return (
    dedicatedExecutorWorker
    || activeLiveInstanceCount == null
    || activeLiveInstanceCount > 0
  );
}

/** Watchdog cleanup may cancel an entry only when the exchange reports zero fill. */
export function relayEntryOrderIsCompletelyUnfilled(order: {
  amountOrig: number;
  amount: number;
}): boolean {
  return exchangeOrderFilledQtySats(order) === 0;
}

/** Bitfinex amounts are signed; fill accounting is exact at 8 decimals. */
export function exchangeOrderFilledQtySats(order: {
  amountOrig: number;
  amount: number;
}): number {
  return Math.max(
    0,
    Math.abs(btcToSats(order.amountOrig)) - Math.abs(btcToSats(order.amount)),
  );
}

/** Exact race shape in which the full immediate-flat proof may be retried. */
export function shouldRetryImmediateFlatReconcile(input: {
  signedExchangeAmount: number;
  signedLedgerOpenAmount: number;
  openLots: number;
  pendingLots: number;
  directionConflict: boolean;
  foreignActiveOrders: number;
}): boolean {
  return (
    btcToSats(input.signedExchangeAmount) === 0 &&
    btcToSats(input.signedLedgerOpenAmount) !== 0 &&
    input.openLots > 0 &&
    input.pendingLots === 0 &&
    !input.directionConflict &&
    input.foreignActiveOrders === 0
  );
}

/**
 * Remove exchange orders already attributed to a current virtual lot. The
 * expensive expired-participant recovery query is needed only for genuinely
 * unattributed orders, not for every normal pending entry/protective stop.
 */
export function untrackedActiveOrderIds(
  activeOrderIds: Iterable<number>,
  liveLotMeta: Iterable<Pick<ExecutionPayload, 'bitfinexOrderId' | 'stopOrderId' | 'supersededStopOrderId'>>,
): number[] {
  const untracked = new Set(activeOrderIds);
  for (const meta of liveLotMeta) {
    if (meta.bitfinexOrderId != null) untracked.delete(meta.bitfinexOrderId);
    if (meta.stopOrderId != null) untracked.delete(meta.stopOrderId);
    if (meta.supersededStopOrderId != null) untracked.delete(meta.supersededStopOrderId);
  }
  return [...untracked];
}

/** Every stop currently owned by a virtual lot, including a replacement still awaiting cleanup. */
export function ownedStopOrderIds(
  meta: Pick<ExecutionPayload, 'stopOrderId' | 'partialFillStopOrderId' | 'supersededPartialStopOrderId' | 'supersededStopOrderId'>,
): number[] {
  return [...new Set([
    meta.stopOrderId,
    meta.partialFillStopOrderId,
    meta.supersededPartialStopOrderId,
    meta.supersededStopOrderId,
  ].filter((id): id is number => id != null))];
}

/**
 * A live exchange position must first be reconciled against current pending
 * entries. Autonomous orphan adoption is valid only when no same-direction
 * pending participant can own the fill.
 */
export function pendingEntryMayOwnExchangePosition(
  direction: 'LONG' | 'SHORT',
  pendingMeta: Iterable<Pick<ExecutionPayload, 'direction' | 'qty'>>,
): boolean {
  for (const meta of pendingMeta) {
    if (meta.direction !== direction) continue;
    if ((meta.qty ?? 0) >= MIN_QTY_BTC) return true;
  }
  return false;
}

export type PendingFillReconcileDecision = {
  defer: boolean;
  reason:
    | 'DEFER_PENDING_FILL'
    | 'NO_POSITION_DELTA'
    | 'NO_MANAGED_PENDING_OWNER'
    | 'AMBIGUOUS_MANAGED_PENDING_OWNER'
    | 'FOREIGN_ACTIVE_ORDER'
    | 'GRACE_EXPIRED';
  direction: 'LONG' | 'SHORT' | null;
  ownerParticipantIds: string[];
  firstObservedAtMs: number | null;
};

/**
 * A Bitfinex fill can become visible a few seconds before its trades endpoint
 * exposes enough detail for the participant ledger to move PENDING_ENTRY →
 * OPEN. During that bounded exchange-consistency window, defer the current
 * tick without allowing new entries instead of permanently pausing the relay.
 *
 * Treat PENDING_ENTRY + same-direction exchange qty as fill-in-flight when a
 * single managed pending owner can cover the delta (order gone, partial fill
 * visible, or resting order still showing unfilled while the position already
 * moved — the classic active-order / position race). Foreign active orders
 * still fail closed immediately; unattributed fills fail closed after grace.
 */
export function pendingFillReconcileDecision(input: {
  nowMs: number;
  signedDeltaBtc: number;
  pending: Array<{
    participantId: string;
    direction?: 'LONG' | 'SHORT';
    qty?: number;
    bitfinexOrderId?: number;
  }>;
  managedOrderIds: Iterable<number>;
  activeOrders: Iterable<{
    id: number;
    amount: number;
    amountOrig: number;
  }>;
  prior?: {
    firstObservedAtMs: number;
    direction: 'LONG' | 'SHORT';
    ownerParticipantIds: string[];
  } | null;
  graceMs?: number;
}): PendingFillReconcileDecision {
  const deltaSats = btcToSats(input.signedDeltaBtc);
  if (deltaSats === 0) {
    return {
      defer: false,
      reason: 'NO_POSITION_DELTA',
      direction: null,
      ownerParticipantIds: [],
      firstObservedAtMs: null,
    };
  }

  const direction: 'LONG' | 'SHORT' = deltaSats > 0 ? 'LONG' : 'SHORT';
  const absDeltaSats = Math.abs(deltaSats);
  // Bitfinex rounds submitted qty; allow ~1% or 5e-5 BTC so 0.0308 vs 0.0307
  // still counts as the same managed fill-in-flight.
  const qtyToleranceSats = Math.max(btcToSats(0.00005), Math.floor(absDeltaSats * 0.01));
  const managed = new Set(input.managedOrderIds);
  const active = new Map(
    [...input.activeOrders].map((order) => [order.id, order]),
  );
  for (const orderId of active.keys()) {
    if (!managed.has(orderId)) {
      return {
        defer: false,
        reason: 'FOREIGN_ACTIVE_ORDER',
        direction,
        ownerParticipantIds: [],
        firstObservedAtMs: null,
      };
    }
  }

  const owners = input.pending
    .filter(
      (row) => {
        if (
          row.direction !== direction ||
          row.bitfinexOrderId == null ||
          !managed.has(row.bitfinexOrderId)
        ) {
          return false;
        }

        const pendingQtySats = Math.abs(btcToSats(row.qty ?? 0));
        const qtyCoversDelta = pendingQtySats + qtyToleranceSats >= absDeltaSats;
        if (!qtyCoversDelta) return false;

        const order = active.get(row.bitfinexOrderId);
        if (!order) {
          // Order left the book — fill-in-flight / filled; qty already covers.
          return true;
        }

        const originalSats = btcToSats(order.amountOrig);
        const remainingSats = btcToSats(order.amount);
        const orderDirection: 'LONG' | 'SHORT' =
          originalSats > 0 ? 'LONG' : 'SHORT';
        if (orderDirection !== direction) return false;
        const filledSats = Math.max(
          0,
          Math.abs(originalSats) - Math.abs(remainingSats),
        );
        // Proven partial/full fill OR resting managed entry while exchange
        // already shows the position (stale active-order snapshot race).
        return filledSats + qtyToleranceSats >= absDeltaSats || qtyCoversDelta;
      },
    )
    .sort((a, b) => a.participantId.localeCompare(b.participantId));
  if (owners.length !== 1) {
    return {
      defer: false,
      reason:
        owners.length === 0
          ? 'NO_MANAGED_PENDING_OWNER'
          : 'AMBIGUOUS_MANAGED_PENDING_OWNER',
      direction,
      ownerParticipantIds: [],
      firstObservedAtMs: null,
    };
  }

  const ownerParticipantIds = owners.map((row) => row.participantId);
  const priorMatches =
    input.prior?.direction === direction &&
    input.prior.ownerParticipantIds.length === ownerParticipantIds.length &&
    input.prior.ownerParticipantIds.every((id, index) => id === ownerParticipantIds[index]);
  const firstObservedAtMs = priorMatches
    ? input.prior!.firstObservedAtMs
    : input.nowMs;
  const graceMs = Math.max(1, input.graceMs ?? PENDING_FILL_RECONCILE_GRACE_MS);
  if (input.nowMs - firstObservedAtMs > graceMs) {
    return {
      defer: false,
      reason: 'GRACE_EXPIRED',
      direction,
      ownerParticipantIds,
      firstObservedAtMs,
    };
  }

  return {
    defer: true,
    reason: 'DEFER_PENDING_FILL',
    direction,
    ownerParticipantIds,
    firstObservedAtMs,
  };
}

/** A market close is complete only after the exchange position reflects it. */
export function marketCloseReductionConfirmed(input: {
  direction: 'LONG' | 'SHORT';
  beforeQty: number;
  closeQty: number;
  afterAmount: number;
}): boolean {
  const beforeSats = Math.abs(btcToSats(input.beforeQty));
  const closeSats = Math.abs(btcToSats(input.closeQty));
  if (beforeSats === 0 || closeSats === 0) return false;
  const remainingSats = Math.max(0, beforeSats - closeSats);
  const expectedAfterSats =
    input.direction === 'LONG' ? remainingSats : -remainingSats;
  return btcToSats(input.afterAmount) === expectedAfterSats;
}

export type RelayLotExitTarget = {
  ok: boolean;
  currentAmount: number;
  targetAmount: number;
  closeQty: number;
  finalAccountFlatten: boolean;
  /** Wall-clock time immediately before the authenticated close submit. */
  closeSubmitStartedAtMs?: number;
  /** Wall-clock time when Bitfinex acknowledged the close submit. */
  closeExchangeAckAtMs?: number;
  /** Wall-clock time when the exchange position read confirmed the reduction. */
  closeConfirmedAtMs?: number;
  reason?: string;
};

/**
 * Decide the exact reduction for one virtual lot inside Bitfinex's merged
 * BTC-PERP position. Quantities are compared as integer satoshis.
 */
export function relayLotExitTarget(input: {
  currentAmount: number;
  remainingLedgerAmount: number;
  exitingLedgerQty: number;
  direction: 'LONG' | 'SHORT';
}): RelayLotExitTarget {
  const currentSats = btcToSats(input.currentAmount);
  const targetSats = btcToSats(input.remainingLedgerAmount);
  const exitingSats = Math.abs(btcToSats(input.exitingLedgerQty));
  const expectedSign = input.direction === 'LONG' ? 1 : -1;
  const maximumOwnedSats = Math.abs(targetSats) + exitingSats;
  if (targetSats !== 0 && Math.sign(targetSats) !== expectedSign) {
    return {
      ok: false,
      currentAmount: satsToBtc(currentSats),
      targetAmount: satsToBtc(targetSats),
      closeQty: 0,
      finalAccountFlatten: false,
      reason: 'REMAINING_LEDGER_DIRECTION_MISMATCH',
    };
  }
  if (currentSats === targetSats) {
    return {
      ok: true,
      currentAmount: satsToBtc(currentSats),
      targetAmount: satsToBtc(targetSats),
      closeQty: 0,
      finalAccountFlatten: targetSats === 0,
    };
  }
  if (currentSats === 0 || Math.sign(currentSats) !== expectedSign) {
    return {
      ok: false,
      currentAmount: satsToBtc(currentSats),
      targetAmount: satsToBtc(targetSats),
      closeQty: 0,
      finalAccountFlatten: false,
      reason: 'EXCHANGE_POSITION_DIRECTION_MISMATCH',
    };
  }
  if (Math.abs(currentSats) > maximumOwnedSats) {
    return {
      ok: false,
      currentAmount: satsToBtc(currentSats),
      targetAmount: satsToBtc(targetSats),
      closeQty: 0,
      finalAccountFlatten: false,
      reason: 'UNATTRIBUTED_EXCHANGE_EXPOSURE',
    };
  }
  const reductionSats = currentSats - targetSats;
  if (
    Math.sign(reductionSats) !== expectedSign ||
    Math.abs(targetSats) > Math.abs(currentSats)
  ) {
    return {
      ok: false,
      currentAmount: satsToBtc(currentSats),
      targetAmount: satsToBtc(targetSats),
      closeQty: 0,
      finalAccountFlatten: false,
      reason: 'EXIT_WOULD_INCREASE_OR_REVERSE_EXPOSURE',
    };
  }
  return {
    ok: true,
    currentAmount: satsToBtc(currentSats),
    targetAmount: satsToBtc(targetSats),
    closeQty: satsToBtc(Math.abs(reductionSats)),
    finalAccountFlatten: targetSats === 0,
  };
}

/**
 * Live relay arming boundary. `relayArmedAt` is written on every explicit
 * Start; `realTradingConfirmedAt` is retained as a migration fallback.
 */
export function relayArmTimestampMs(dashboardState: unknown): number | null {
  const state =
    dashboardState && typeof dashboardState === 'object'
      ? (dashboardState as Record<string, unknown>)
      : {};
  for (const value of [state.relayArmedAt, state.realTradingConfirmedAt]) {
    if (typeof value !== 'string' || !value) continue;
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

/**
 * A live relay may copy only cycles born after its most recent explicit Start.
 * Missing/invalid arm state fails closed; paper simulation bypasses this helper.
 */
export function isCycleFreshForRelayArm(
  dashboardState: unknown,
  createdAt: Date,
): boolean {
  const armedAtMs = relayArmTimestampMs(dashboardState);
  return armedAtMs != null && createdAt.getTime() > armedAtMs;
}

/** Prevent two opposing limits from being submitted into one merged position in a tick. */
export function mergedDirectionCompatible(
  submittedDirection: 'LONG' | 'SHORT' | null,
  candidateDirection: 'LONG' | 'SHORT',
): boolean {
  return submittedDirection == null || submittedDirection === candidateDirection;
}

/** A copied entry may improve on the showcase fill, but never pay worse. */
export function capRelayLimitAtShowcaseFill(
  direction: 'LONG' | 'SHORT',
  currentLimit: number,
  showcaseFill: number,
): number {
  if (!(currentLimit > 0) || !(showcaseFill > 0)) return currentLimit;
  return direction === 'LONG'
    ? Math.min(currentLimit, showcaseFill)
    : Math.max(currentLimit, showcaseFill);
}

/**
 * Optional continuation policy for a resting copy order after its exact
 * showcase trade has filled.  It is deliberately disabled by default: when
 * enabled, the existing order may remain only while that same showcase
 * position is OPEN, and applyLimitChase clamps it to a no-worse limit.
 *
 * This is not market catch-up.  A SHORT may sell only at the showcase fill or
 * higher; a LONG may buy only at the showcase fill or lower.  The source exit
 * still owns cancellation and a later real fill still uses the normal
 * protection/terminal funnel.
 */
function lateEntryContinuationEnabled(): boolean {
  const value = (process.env.LATE_ENTRY_CONTINUATION_ENABLED ?? '').trim().toLowerCase();
  return value === '1' || value === 'true' || value === 'on' || value === 'yes';
}

export function shouldRetainLateEntryContinuation(input: {
  enabled: boolean;
  showcaseTradeOpen: boolean;
  participantStatus: SignalCycleStatus;
  hasManagedOrder: boolean;
}): boolean {
  return input.enabled
    && input.showcaseTradeOpen
    && input.participantStatus === SignalCycleStatus.PENDING_ENTRY
    && input.hasManagedOrder;
}

export function partialEntryFillDisposition(input: {
  intendedQty: number;
  filledQty: number;
  orderResting: boolean;
  terminalSource: boolean;
}): 'RETAIN_PROTECTED_REMAINDER' | 'FINALIZE_FILL' {
  const tolerance = Math.max(MIN_QTY_BTC, input.intendedQty * 0.001);
  const materiallyPartial =
    input.orderResting &&
    btcToSats(input.filledQty) + btcToSats(tolerance) < btcToSats(input.intendedQty);
  return materiallyPartial && !input.terminalSource
    ? 'RETAIN_PROTECTED_REMAINDER'
    : 'FINALIZE_FILL';
}

/**
 * Normalize a terminal exchange fill without hiding a real partial quantity.
 * Bitfinex order/trade arithmetic can report a completed amount one satoshi
 * below the acknowledged order amount (for example 0.02358999 for 0.02359).
 * Once the order is proven non-resting, snap only that one-satoshi transport
 * artifact to the durable intended amount. Larger deficits remain exact.
 */
export function finalizedEntryFillQty(input: {
  intendedQty: number;
  filledQty: number;
  orderResting: boolean;
}): number {
  const intendedSats = Math.abs(btcToSats(input.intendedQty));
  const filledSats = Math.abs(btcToSats(input.filledQty));
  const terminalShortfallSats = intendedSats - filledSats;
  if (
    !input.orderResting
    && intendedSats > 0
    && filledSats > 0
    && terminalShortfallSats >= 0
    && terminalShortfallSats <= 1
  ) {
    return satsToBtc(intendedSats);
  }
  return satsToBtc(filledSats);
}

/** Quantity parity for an already matched showcase/copy position identity. */
export function mirrorPositionQuantityDelta(
  showcaseQty: number | null | undefined,
  copyQty: number | null | undefined,
): number | null {
  if (!(showcaseQty != null && showcaseQty > 0) || !(copyQty != null && copyQty > 0)) {
    return null;
  }
  const delta = copyQty - showcaseQty;
  // Ignore exchange rounding only: 0.00005 BTC or 1% of source size.
  const tolerance = Math.max(0.00005, showcaseQty * 0.01);
  return Math.abs(delta) > tolerance ? delta : null;
}

/**
 * Price available at the instant an exchange-verified fill is observed.
 * ORDER_PARTIAL supplies the resting order price; POSITION_DELTA supplies the
 * managed limit (or position base price). This deliberately precedes private
 * trade-history enrichment. For a BUY limit, execution is at/below the limit,
 * so a limit-anchored long stop is equal or tighter; for a SELL limit,
 * execution is at/above the limit, so a limit-anchored short stop is likewise
 * equal or tighter. The optimization therefore cannot widen initial risk.
 */
export function protectiveStopReferencePrice(
  verifiedOrderOrPositionPrice: number | null | undefined,
  managedLimitPrice: number | null | undefined,
  fallbackMarkPrice: number | null | undefined,
): number {
  if (verifiedOrderOrPositionPrice != null && verifiedOrderOrPositionPrice > 0) {
    return verifiedOrderOrPositionPrice;
  }
  if (managedLimitPrice != null && managedLimitPrice > 0) return managedLimitPrice;
  return fallbackMarkPrice != null && fallbackMarkPrice > 0 ? fallbackMarkPrice : 0;
}

type ExecutionPayload = {
  bitfinexOrderId?: number;
  stopOrderId?: number;
  limitPrice?: number;
  originalLimitPrice?: number;
  localMark?: number;
  qty?: number;
  direction?: 'LONG' | 'SHORT';
  /** Exchange merged position qty (BTC) captured at ORDER_PLACED — avoids false fills. */
  exchangeQtyAtOrder?: number;
  margin_usd?: number;
  source?: 'hire';
  peakMarginPct?: number;
  profitLockFloor?: number;
  stopLossPlaced?: boolean;
  lastChaseAtMs?: number;
  replacementExchangeAckAtMs?: number;
  limitChaseCount?: number;
  fillPrice?: number;
  leverage?: number;
  stopLossMarginPct?: number;
  sourceEventAt?: string;
  platformReceivedAt?: string;
  sourceToPlatformMs?: number;
  platformToExchangeAckMs?: number;
  sourceToExchangeAckMs?: number;
  /**
   * Bitfinex client order id (`cid`) — int32. Set on every limit submit
   * (placeEntry + applyLimitChase replacement orders) as a deterministic
   * hash of cycleId|participantId|tradeId so future reconcile-adopt
   * passes can match orders even when bitfinexOrderId was not persisted.
   * BitfinexActiveOrder does not surface cid on read today, so this is
   * forward-looking infrastructure.
   */
  clientOrderId?: number;
  /** Phase 3 — S6b/S6a adoption re-link to origin showcase trade. */
  originParticipantId?: string;
  originCycleId?: string;
  originTradeId?: string;
  /** Cumulative entry execution while the participant remains PENDING_ENTRY. */
  partialFillQty?: number | null;
  /** Current reduce-only stop covering partialFillQty. */
  partialFillStopOrderId?: number | null;
  /** Older reduce-only stop awaiting confirmed cancellation. */
  supersededPartialStopOrderId?: number | null;
  /** Prior full-lot stop retained as managed until the replacement is confirmed cleared. */
  supersededStopOrderId?: number | null;
  /** Opt-in, same-trade resting continuation after a verified showcase fill. */
  lateEntryContinuation?: boolean;
  lateEntryShowcaseFill?: number;
  lateEntryStartedAtMs?: number;
};

type RepairableLotMeta = Pick<ExecutionPayload, 'qty' | 'direction'>;

/**
 * A catch-up entry created after the showcase has already filled may have a
 * durable qty but no durable direction in older event rows. Persist the repair
 * whenever either field is missing so restart reconciliation can attribute the
 * exchange position to the OPEN virtual lot.
 */
export function shouldPersistLotMetaRepair(
  stored: RepairableLotMeta,
  resolved: RepairableLotMeta,
): boolean {
  const qtyMissing = !stored.qty || btcToSats(stored.qty) === 0;
  const directionMissing = !stored.direction;
  return (
    (!!resolved.qty && btcToSats(resolved.qty) > 0 && qtyMissing) ||
    (!!resolved.direction && directionMissing)
  );
}

/**
 * Waiting for a particular showcase order is normal strategy flow, not a
 * service outage. These messages must not keep the Agent Hub in a red error
 * state once the canonical bridge is healthy.
 */
export function isBenignShowcaseEntryWait(message: string | null | undefined): boolean {
  if (!message) return false;
  return (
    message === 'Showcase trade is not present in the current canonical book.' ||
    message === 'Waiting for the showcase to publish its exact resting limit.' ||
    message === 'Showcase filled before copy entry; market catch-up is prohibited.' ||
    message ===
      'Showcase bot has not placed a limit yet (virtual defer / chase bucket) — relay waiting.' ||
    message === 'Waiting for showcase limit.'
  );
}

/** Clear only a historical F1 outage after debounced source recovery. */
export function isRecoveredShowcaseOutageError(
  message: string | null | undefined,
): boolean {
  return !!message && message.startsWith('Showcase unreachable for ');
}

/**
 * A deploy resets the in-memory outage streak but intentionally preserves the
 * lastError audit text in Postgres. Once canonical state is healthy, clear
 * that persisted warning when there is no current tracked outage; otherwise
 * retain the three-hit recovery debounce for an outage observed by this
 * process.
 */
export function shouldClearShowcaseStatusError(input: {
  message: string | null | undefined;
  hadTrackedOutage: boolean;
  recoveredNow: boolean;
}): boolean {
  return (
    isBenignShowcaseEntryWait(input.message) ||
    (isRecoveredShowcaseOutageError(input.message) &&
      (input.recoveredNow || !input.hadTrackedOutage))
  );
}

/**
 * Only intents with a currently resting canonical showcase limit are eligible
 * for pending-order mirroring. Older unfilled INTENT rows must not block a
 * newer order that is actually present in the live book.
 */
export function canonicalPendingIntentCycles<
  T extends { tradeId: string; createdAt: Date },
>(
  cycles: T[],
  bot: Pick<BotApiState, 'orders'> | null,
): T[] {
  if (!bot) return [];
  const canonicalRank = new Map<string, number>();
  for (const [index, order] of (bot.orders ?? []).entries()) {
    const tradeId = order.trade_id;
    if (tradeId && isExecutableStructuralShowcaseOrder(order)) {
      canonicalRank.set(tradeId, index);
    }
  }
  return cycles
    .filter((cycle) => canonicalRank.has(cycle.tradeId))
    .sort((a, b) => {
      const rankDelta = canonicalRank.get(a.tradeId)! - canonicalRank.get(b.tradeId)!;
      return rankDelta !== 0 ? rankDelta : a.createdAt.getTime() - b.createdAt.getTime();
    });
}

type SignedShowcaseExactLimit = {
  tradeId: string;
  direction: 'LONG' | 'SHORT';
  limitPrice: number;
  exactQtyBtc: number;
  receivedAtMs: number;
  sourceEventAtMs?: number;
};

type SignedShowcaseEnvelope = SignalIntentEnvelope & {
  trade_id?: string;
  entry: SignalIntentEnvelope['entry'] & {
    exact_limit_price?: number;
    exact_qty_btc?: number;
  };
  context: SignalIntentEnvelope['context'] & {
    signed_showcase_event?: boolean;
    showcase_event?: string;
    showcase_event_at?: string;
    platform_received_at?: string;
    entry_limit_policy?: string;
  };
};

export function readFreshSignedShowcaseExactLimit(
  tradeId: string,
  envelopeJson: unknown,
  nowMs = Date.now(),
): SignedShowcaseExactLimit | null {
  const intent = envelopeJson as SignedShowcaseEnvelope | null;
  const envelopeTradeId = String(intent?.trade_id ?? intent?.signalId ?? '');
  const receivedAtMs = Date.parse(String(intent?.context?.platform_received_at ?? ''));
  const sourceEventAtMs = Date.parse(String(intent?.context?.showcase_event_at ?? ''));
  const limitPrice = Number(intent?.entry?.exact_limit_price ?? 0);
  const exactQtyBtc = Number(intent?.entry?.exact_qty_btc ?? 0);
  const direction = intent?.direction;
  const event = String(intent?.context?.showcase_event ?? '');
  if (
    !tradeId
    || envelopeTradeId !== tradeId
    || intent?.signalId !== tradeId
    || intent?.action !== 'ENTER'
    || intent?.context?.signed_showcase_event !== true
    || (event !== 'ORDER_PLACED' && event !== 'LIMIT_UPDATED')
    || intent?.entry?.mode !== 'EXACT_LIMIT'
    || intent?.entry?.reference !== 'SHOWCASE_EXACT_LIMIT'
    || !isExecutableEntryPolicy(intent?.context?.entry_limit_policy)
    || (direction !== 'LONG' && direction !== 'SHORT')
    || !Number.isFinite(limitPrice)
    || limitPrice <= 0
    || !Number.isFinite(exactQtyBtc)
    || exactQtyBtc <= 0
    || !Number.isFinite(receivedAtMs)
    || receivedAtMs > nowMs + 5_000
    || nowMs - receivedAtMs > SIGNED_SHOWCASE_FAST_PATH_MAX_AGE_MS
  ) {
    return null;
  }
  return {
    tradeId,
    direction,
    limitPrice,
    exactQtyBtc,
    receivedAtMs,
    ...(Number.isFinite(sourceEventAtMs) ? { sourceEventAtMs } : {}),
  };
}

export function readSignedShowcaseClose(envelopeJson: unknown): {
  exitPrice?: number;
  exitReason?: string;
  sourceEventAtMs?: number;
  platformReceivedAtMs?: number;
} | null {
  const context = (
    envelopeJson as {
      context?: {
        signed_showcase_event?: boolean;
        showcase_event?: string;
        showcase_event_at?: string;
        platform_received_at?: string;
        showcase_exit_price?: number;
        showcase_exit_reason?: string;
      };
    } | null
  )?.context;
  if (
    context?.signed_showcase_event !== true
    || context.showcase_event !== 'POSITION_CLOSED'
  ) {
    return null;
  }
  const rawExitPrice = Number(context.showcase_exit_price ?? 0);
  const sourceEventAtMs = Date.parse(String(context.showcase_event_at ?? ''));
  const platformReceivedAtMs = Date.parse(String(context.platform_received_at ?? ''));
  return {
    ...(Number.isFinite(rawExitPrice) && rawExitPrice > 0
      ? { exitPrice: rawExitPrice }
      : {}),
    ...(Number.isFinite(sourceEventAtMs) ? { sourceEventAtMs } : {}),
    ...(Number.isFinite(platformReceivedAtMs) ? { platformReceivedAtMs } : {}),
    ...(context.showcase_exit_reason
      ? { exitReason: context.showcase_exit_reason }
      : {}),
  };
}

function mergeSignedShowcaseOrders(
  bot: BotApiState | null,
  signedOrders: SignedShowcaseExactLimit[],
): BotApiState | null {
  if (!signedOrders.length) return bot;
  const signedIds = new Set(signedOrders.map((order) => order.tradeId));
  return {
    ...(bot ?? {}),
    orders: [
      ...signedOrders.map((order) => ({
        trade_id: order.tradeId,
        status: 'PENDING',
        limit_price: order.limitPrice,
        entry_limit_policy: SHOWCASE_DETERMINISTIC_ENTRY_POLICY_VERSION,
        side: order.direction,
      })),
      ...(bot?.orders ?? []).filter((order) => !order.trade_id || !signedIds.has(order.trade_id)),
    ],
  };
}

type PositionRuntime = {
  peakMarginPct: number;
  lastChaseAtMs: number;
  lastProfitLockFloor?: number;
  filledRecorded: boolean;
  /**
   * Option A — current protective stop order id live on Bitfinex for this
   * participant. Tracked in participant state so cancel-then-replace always
   * targets the exact previous order (not whatever meta carried in). Keyed
   * by participantId (unique per userId+cycleId) → per-account isolation.
   */
  currentStopOrderId?: number;
  /** Last stop price actually placed on the exchange — for never-loosen check. */
  currentStopPrice?: number;
  /** Last Scenario C rung index placed (0-based) — for SKIP_SAME audit. */
  currentRungIdx?: number;
  /** Consecutive stop-replacement failures — circuit breaker (≥3 → halt). */
  consecutiveStopFailures?: number;
};

function executionEnabled(): boolean {
  // The public API must never own the money path. Both flags are required so
  // missing/copied environment configuration fails closed.
  return (
    process.env.SUBSCRIBER_EXECUTION_ENABLED === 'true' &&
    process.env.RELAY_EXECUTOR_WORKER === 'true'
  );
}

/** Validate the heartbeat persisted by the isolated executor worker. */
export function readPersistedRelayExecutorHealth(
  dashboardState: unknown,
  nowMs = Date.now(),
  maxAgeMs = DEFAULT_EXECUTOR_HEALTH_MAX_AGE_MS,
): RelayExecutorHealthSnapshot {
  const dash =
    dashboardState && typeof dashboardState === 'object' && !Array.isArray(dashboardState)
      ? (dashboardState as Record<string, unknown>)
      : {};
  const raw =
    dash.relayExecutor && typeof dash.relayExecutor === 'object' && !Array.isArray(dash.relayExecutor)
      ? (dash.relayExecutor as Record<string, unknown>)
      : {};
  const lastTickAt = typeof dash.lastTickAt === 'string' ? Date.parse(dash.lastTickAt) : NaN;
  const observedAt = typeof raw.observedAt === 'string' ? Date.parse(raw.observedAt) : NaN;
  const evidenceAt = Number.isFinite(observedAt) ? observedAt : lastTickAt;
  const heartbeatAgeMs = Number.isFinite(evidenceAt) ? Math.max(0, nowMs - evidenceAt) : null;
  const roleOk = raw.serviceRole === 'executor-worker';
  const enabled = raw.executionEnabled === true;
  const fresh = heartbeatAgeMs != null && heartbeatAgeMs <= maxAgeMs;
  const workerHealthy = raw.healthy === true && raw.status !== 'STUCK';
  const status: RelayExecutorHealthSnapshot['status'] =
    raw.status === 'RUNNING' || raw.status === 'IDLE' || raw.status === 'STUCK'
      ? raw.status
      : 'STARTING';
  return {
    healthy: roleOk && enabled && fresh && workerHealthy,
    status,
    running: raw.running === true,
    tickStartedAt: typeof raw.tickStartedAt === 'string' ? raw.tickStartedAt : null,
    lastTickCompletedAt:
      typeof raw.lastTickCompletedAt === 'string' ? raw.lastTickCompletedAt : null,
    lastTickDurationMs:
      typeof raw.lastTickDurationMs === 'number' ? raw.lastTickDurationMs : null,
    currentInstanceId:
      typeof raw.currentInstanceId === 'string' ? raw.currentInstanceId : null,
    currentStage: typeof raw.currentStage === 'string' ? raw.currentStage : null,
    heartbeatAgeMs,
    runningForMs: typeof raw.runningForMs === 'number' ? raw.runningForMs : null,
    timeoutMs:
      typeof raw.timeoutMs === 'number' ? raw.timeoutMs : DEFAULT_EXECUTOR_TICK_TIMEOUT_MS,
    timeoutCount: typeof raw.timeoutCount === 'number' ? raw.timeoutCount : 0,
    serviceRole: roleOk ? 'executor-worker' : 'public-api',
    ownerId: typeof raw.ownerId === 'string' ? raw.ownerId : null,
    observedAt: Number.isFinite(evidenceAt) ? new Date(evidenceAt).toISOString() : undefined,
    sourceRevision: typeof raw.sourceRevision === 'string' ? raw.sourceRevision : null,
    executionEnabled: enabled,
  };
}

/**
 * Phase 2 reconcile-adopt write window. Re-arming a protective stop is a
 * Bitfinex write (submitStopOrder). When disabled, the reconcile-adopt
 * loop surfaces the participant in dashboardState.orphanPositionIds and
 * logs RECONCILE_STOP_REARM_SKIPPED but does NOT write to the exchange.
 * Default "1" (enabled) — set RECONCILE_WRITE_WINDOW=0 to gate off.
 */
function reconcileWriteWindowEnabled(): boolean {
  return process.env.RECONCILE_WRITE_WINDOW !== '0';
}

/**
 * Phase 4 — autonomous orphan adoption master switch. Default enabled ("1").
 * When disabled ("0") the reconcile-adopt orphan path reverts to surface-only
 * (logs RECONCILE_ADOPT_DISABLED, does NOT create adopted participants or
 * place protective stops). Reads once per call so operators can flip it via
 * Railway env without a redeploy.
 */
function reconcileAdoptEnabled(): boolean {
  return process.env.RECONCILE_ADOPT_ENABLED !== '0';
}

/**
 * Phase 5 guardrail — max adoptions per trailing 24h window. Derived from the
 * Neon SignalCycleEvent stream (adoption event types) + synthesized `adopt:%`
 * cycle count, NOT from dashboardState.reconcileAdoptCount — the dashboard
 * counter is wiped by resetAllUserCopySessions on every showcase
 * fresh-collection (production evidence: 5 adoptions in one day against a
 * budget of 2). Default 2: tight enough that a runaway match bug cannot adopt
 * a cascade, generous enough to heal a normal fill/expiry race plus one
 * retry. When exceeded, surface-only + RECONCILE_ADOPT_BUDGET_EXHAUSTED.
 */
function reconcileAdoptBudget(): number {
  const raw = Number(process.env.RECONCILE_ADOPT_BUDGET ?? '2');
  return Number.isFinite(raw) && raw >= 0 ? Math.floor(raw) : 2;
}

/**
 * Phase 1 — "100% mirror" state-convergence master switch. Default ON
 * (since 2026-07-04). Set MIRROR_CONVERGENCE_ENABLED=0|false|off|no to
 * disable in an emergency. When ON:
 *  1. Book-state dedupe: entries mirror the showcase BOOK, not its per-lane
 *     signal spawns — a second participant at an already-resting limit price
 *     is expired ledger-side (DUPLICATE_LIMIT_SKIPPED) without a real order.
 *  2. Chase convergence: bot-anchored replaces are clamped to max 1 per
 *     order per second, and the showcase state fetch is memoised for 1s per
 *     tick so all call sites share a single fresh pull (faster ticks, lower
 *     effective reprice lag).
 * NOTE (adoption hardening): the cancel-race fill check (every entry-order
 * cancel and the RECONCILE_CANCEL_BY_EXCHANGE classification verify the order
 * did not (partially) fill before treating it as cancelled) is ALWAYS ON —
 * misclassifying a real fill as a cancel is a bug, not a feature. Only the
 * dedupe + chase-convergence behaviors above remain behind this flag.
 * Read per call so operators can flip it via Railway env without a redeploy.
 */
function mirrorConvergenceEnabled(): boolean {
  const v = (process.env.MIRROR_CONVERGENCE_ENABLED ?? '').trim().toLowerCase();
  if (v === '0' || v === 'false' || v === 'off' || v === 'no') return false;
  return true;
}

/**
 * Phase 0 — shadow-diff observability (MIRROR_DIFF). Default ON; set
 * MIRROR_DIFF_ENABLED=0 to disable. Pure observability: compares the showcase
 * book (pending limits + open positions from the bot state the tick already
 * fetched) against the copy's ledger (resting orders + OPEN lots) and
 * persists divergences to dashboardState.mirrorDiff plus a throttled
 * MIRROR_DIFF SignalCycleEvent. No exchange API calls, no behavior change.
 */
function mirrorDiffEnabled(): boolean {
  return process.env.MIRROR_DIFF_ENABLED !== '0';
}

/**
 * A copied entry may rest only while the matching showcase order is still
 * pending. Once the showcase has filled, a later copy fill would be a
 * different trade at a potentially worse price, so the managed copy order
 * must enter the cancel-race reconciliation path immediately.
 */
export function pendingCopyShowcaseDisposition(
  bot: BotApiState | null,
  tradeId: string,
): 'SOURCE_UNAVAILABLE' | 'SHOWCASE_PENDING' | 'MISSED_SHOWCASE_FILL' | 'SHOWCASE_ABSENT' {
  if (!bot || !tradeId) return 'SOURCE_UNAVAILABLE';
  const pending = (bot.orders ?? []).some(
    (order) =>
      order.trade_id === tradeId
      && isExecutableStructuralShowcaseOrder(order),
  );
  if (pending) return 'SHOWCASE_PENDING';
  const filled = (bot.positions ?? []).some(
    (position) => String(position.trade_id ?? '') === tradeId,
  );
  return filled ? 'MISSED_SHOWCASE_FILL' : 'SHOWCASE_ABSENT';
}

/**
 * A signed exact-order webhook can reach Railway before the independently
 * published canonical snapshot contains that same order. During this bounded
 * propagation window, absence from the snapshot is not evidence that the
 * showcase abandoned the trade. Explicit terminal evidence (expired order or
 * terminal signal) is handled before this helper and is never delayed.
 */
export function showcaseAbsentWithinOrderPropagationGrace(
  tradeId: string,
  intentEnvelope: unknown,
  nowMs = Date.now(),
  graceMs = SHOWCASE_ORDER_SNAPSHOT_PROPAGATION_GRACE_MS,
): boolean {
  if (!tradeId) return false;
  const intent = intentEnvelope as {
    action?: unknown;
    trade_id?: unknown;
    context?: {
      signed_showcase_event?: unknown;
      showcase_event?: unknown;
      showcase_event_at?: unknown;
      platform_received_at?: unknown;
    };
  } | null;
  const context = intent?.context;
  const event = String(context?.showcase_event ?? '').toUpperCase();
  if (
    intent?.action !== 'ENTER'
    || String(intent?.trade_id ?? '') !== tradeId
    || context?.signed_showcase_event !== true
    || (event !== 'ORDER_PLACED' && event !== 'LIMIT_UPDATED')
  ) {
    return false;
  }
  const receivedAtMs = sourceTimestampMs(context?.platform_received_at);
  const eventAtMs = sourceTimestampMs(context?.showcase_event_at);
  const anchorMs = receivedAtMs ?? eventAtMs;
  if (anchorMs == null || anchorMs > nowMs + 5_000) return false;
  return nowMs - anchorMs <= Math.max(1, graceMs);
}

export function shouldDeferCancelByExchangeForReplacement(
  tradeId: string,
  intentEnvelope: unknown,
  observedOrderId: number,
  latestOrderId: number | undefined,
  nowMs = Date.now(),
): boolean {
  if (latestOrderId != null && latestOrderId !== observedOrderId) return true;
  return showcaseAbsentWithinOrderPropagationGrace(tradeId, intentEnvelope, nowMs);
}

export function showcaseIntentRevision(intentEnvelope: unknown): string | null {
  const intent = intentEnvelope as {
    context?: { showcase_event_seq?: unknown; showcase_event_at?: unknown };
  } | null;
  const seq = Number(intent?.context?.showcase_event_seq);
  if (Number.isInteger(seq) && seq >= 0) return `seq:${seq}`;
  const eventAtMs = sourceTimestampMs(intent?.context?.showcase_event_at);
  return eventAtMs == null ? null : `at:${eventAtMs}`;
}

/** A stale monitor observation must not expire a newly committed replacement. */
export function pendingEntryOwnershipAdvanced(
  observedOrderId: number,
  observedIntent: unknown,
  durableOrderId: number | undefined,
  durableIntent: unknown,
): boolean {
  if (durableOrderId != null && durableOrderId !== observedOrderId) return true;
  const observedRevision = showcaseIntentRevision(observedIntent);
  const durableRevision = showcaseIntentRevision(durableIntent);
  return durableRevision != null && durableRevision !== observedRevision;
}

export function advanceReplacementMissingProbe(
  prior: { generation: string; firstMissingAtMs: number; count: number } | undefined,
  generation: string,
  exchangeAckAtMs: number,
  nowMs: number,
): { probe: { generation: string; firstMissingAtMs: number; count: number }; terminalEligible: boolean } {
  const probe = prior?.generation === generation
    ? { ...prior, count: prior.count + 1 }
    : { generation, firstMissingAtMs: nowMs, count: 1 };
  return {
    probe,
    terminalEligible:
      probe.count >= 2
      && nowMs - exchangeAckAtMs >= BITFINEX_REPLACEMENT_VISIBILITY_GRACE_MS
      && nowMs - probe.firstMissingAtMs >= BITFINEX_REPLACEMENT_VISIBILITY_GRACE_MS,
  };
}


/**
 * A fresh source fill may immediately follow a signed exact-limit reprice.
 * Keep the managed Bitfinex order alive during the bounded convergence window
 * instead of cancelling it before the exchange replacement/fill is visible.
 */
export function missedShowcaseFillWithinSettlementGrace(
  bot: BotApiState | null,
  tradeId: string,
  intentEnvelope: unknown,
  nowMs = Date.now(),
  graceMs = PENDING_FILL_RECONCILE_GRACE_MS,
): boolean {
  if (!bot || !tradeId) return false;
  const intent = intentEnvelope as {
    action?: unknown;
    trade_id?: unknown;
    context?: {
      signed_showcase_event?: unknown;
      showcase_event?: unknown;
      showcase_event_at?: unknown;
      showcase_event_seq?: unknown;
      marketable_fallback?: unknown;
      relay_settle_not_before_ts?: unknown;
    };
  } | null;
  const context = intent?.context;
  if (
    intent?.action !== 'ENTER'
    || String(intent?.trade_id ?? '') !== tradeId
    || context?.signed_showcase_event !== true
    || context?.showcase_event !== 'LIMIT_UPDATED'
    || context?.marketable_fallback !== true
  ) {
    return false;
  }
  const eventSeq = Number(context.showcase_event_seq);
  const eventAtMs = sourceTimestampMs(context.showcase_event_at);
  const settleAtMs = sourceTimestampMs(context.relay_settle_not_before_ts);
  if (
    !Number.isInteger(eventSeq)
    || eventSeq < 0
    || eventAtMs == null
    || settleAtMs == null
    || settleAtMs < eventAtMs
    || settleAtMs - eventAtMs > 120_000
  ) {
    return false;
  }
  const position = (bot.positions ?? []).find(
    (row) => String(row.trade_id ?? '') === tradeId,
  ) as Record<string, unknown> | undefined;
  if (!position) return false;
  const filledAtMs = [position.entry_ts, position.fill_ts, position.filled_ts]
    .map((raw) => sourceTimestampMs(raw))
    .find((value): value is number => value != null);
  if (filledAtMs == null) return false;
  const boundedGraceMs = Math.max(1, graceMs);
  const deadlineMs = Math.min(
    filledAtMs + boundedGraceMs,
    settleAtMs + boundedGraceMs,
  );
  return nowMs >= settleAtMs - 5_000 && nowMs <= deadlineMs;
}

export type MissedShowcaseFillResolution =
  | { outcome: 'FILL_RECORDED' }
  | { outcome: 'PENDING_RETRY_AFTER_FILL' }
  | { outcome: 'PENDING_FILL_CHECK_FAILED'; phase: 'BEFORE_CANCEL' | 'AFTER_CANCEL'; error: string }
  | { outcome: 'EXPIRE_UNFILLED'; cancelReason?: string; cancelAttempts: number }
  | { outcome: 'PENDING_CANCEL_FAILED'; cancelReason?: string; cancelAttempts: number };

/**
 * Deterministic money-path ordering for a showcase fill that the copy missed:
 * reconcile a full/partial cancel-race fill first; only an exchange-proven
 * zero-fill managed order may be cancelled and expired.
 */
export async function resolveMissedShowcaseFill<TFill>(input: {
  managedOrderId: number;
  detectFill: () => Promise<TFill | null>;
  recordFill: (fill: TFill) => Promise<boolean>;
  cancelManagedOrder: (
    orderId: number,
  ) => Promise<{ gone: boolean; reason?: string; attempts: number }>;
}): Promise<MissedShowcaseFillResolution> {
  let fill: TFill | null;
  try {
    fill = await input.detectFill();
  } catch (err) {
    return {
      outcome: 'PENDING_FILL_CHECK_FAILED',
      phase: 'BEFORE_CANCEL',
      error: err instanceof Error ? err.message : String(err),
    };
  }
  if (fill != null) {
    return (await input.recordFill(fill))
      ? { outcome: 'FILL_RECORDED' }
      : { outcome: 'PENDING_RETRY_AFTER_FILL' };
  }
  const cancel = await input.cancelManagedOrder(input.managedOrderId);
  if (!cancel.gone) {
    return {
        outcome: 'PENDING_CANCEL_FAILED',
        cancelReason: cancel.reason,
        cancelAttempts: cancel.attempts,
    };
  }
  // A fill can land between the pre-cancel inspection and the exchange
  // acknowledging cancellation. Reconcile once more before expiring the lot.
  try {
    fill = await input.detectFill();
  } catch (err) {
    return {
      outcome: 'PENDING_FILL_CHECK_FAILED',
      phase: 'AFTER_CANCEL',
      error: err instanceof Error ? err.message : String(err),
    };
  }
  if (fill != null) {
    return (await input.recordFill(fill))
      ? { outcome: 'FILL_RECORDED' }
      : { outcome: 'PENDING_RETRY_AFTER_FILL' };
  }
  return {
    outcome: 'EXPIRE_UNFILLED',
    cancelReason: cancel.reason,
    cancelAttempts: cancel.attempts,
  };
}

/**
 * Source-only gaps are expected while a relay is explicitly stopped.  Keep
 * copy-side exposure mismatches visible in exit-only mode, but do not present
 * a fresh showcase order/position as a relay failure when entries are
 * intentionally disabled.
 */
export function reportableMirrorDiffsForRelayMode<T extends { type: string }>(
  divergences: T[],
  entryEnabled: boolean,
  copyDirection?: 'LONG' | 'SHORT' | null,
  relayArmedAtMs?: number | null,
  expectedMissedShowcaseTradeIds: ReadonlySet<string> = new Set(),
): T[] {
  return divergences.filter(
    (d) => {
      const sourceOnly =
        d.type === 'SHOWCASE_ORDER_NOT_MIRRORED' ||
        d.type === 'SHOWCASE_POSITION_NOT_MIRRORED';
      if (!sourceOnly) return true;
      if (!entryEnabled) return false;
      const sourceCreatedAtMs = Number(
        (d as T & { sourceCreatedAtMs?: number }).sourceCreatedAtMs,
      );
      if (
        relayArmedAtMs != null &&
        Number.isFinite(relayArmedAtMs) &&
        Number.isFinite(sourceCreatedAtMs) &&
        sourceCreatedAtMs <= relayArmedAtMs
      ) {
        return false;
      }
      const tradeId = String((d as T & { tradeId?: string }).tradeId ?? '');
      if (
        d.type === 'SHOWCASE_POSITION_NOT_MIRRORED' &&
        tradeId &&
        expectedMissedShowcaseTradeIds.has(tradeId)
      ) {
        return false;
      }
      const showcaseDirection = String(
        (d as T & { showcaseDir?: string }).showcaseDir ?? '',
      ).toUpperCase();
      return !(
        copyDirection &&
        (showcaseDirection === 'LONG' || showcaseDirection === 'SHORT') &&
        showcaseDirection !== copyDirection
      );
    },
    );
}

export function sourceEntityCreatedAtMs(
  entity: Record<string, unknown>,
): number | null {
  for (const raw of [
    entity.created_ts,
    entity.signal_created_ts,
    entity.order_created_ts,
  ]) {
    const parsed = sourceTimestampMs(raw);
    if (parsed != null) return parsed;
  }
  // Compatibility for positions created by bot revisions that retained only
  // fill time plus the measured signal age. This reconstructs the original
  // source birth watermark so a pre-arm pending order cannot appear fresh
  // merely because it filled after NEXT_FRESH_ONLY was armed.
  const entryAtMs = sourceTimestampMs(entity.entry_ts ?? entity.fill_ts);
  const signalAgeSec = Number(entity.signal_age_sec);
  if (
    entryAtMs != null &&
    Number.isFinite(signalAgeSec) &&
    signalAgeSec >= 0
  ) {
    return entryAtMs - signalAgeSec * 1000;
  }
  for (const raw of [entity.entry_ts, entity.fill_ts, entity.ts]) {
    const parsed = sourceTimestampMs(raw);
    if (parsed != null) return parsed;
  }
  return null;
}

function sourceTimestampMs(raw: unknown): number | null {
  if (typeof raw === 'number' && Number.isFinite(raw) && raw > 0) {
    return raw > 1_000_000_000_000 ? raw : raw * 1000;
  }
  if (typeof raw !== 'string' || !raw.trim()) return null;
  const numeric = Number(raw);
  if (Number.isFinite(numeric) && numeric > 0) {
    return numeric > 1_000_000_000_000 ? numeric : numeric * 1000;
  }
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Hire expiry must block NEW live entries but never abandon OPEN exchange risk. */
export function hireExpiryBlocksNewLiveEntries(expiresAt: Date | string | null | undefined, nowMs = Date.now()): boolean {
  if (expiresAt == null) return false;
  const ms = expiresAt instanceof Date ? expiresAt.getTime() : Date.parse(String(expiresAt));
  return Number.isFinite(ms) && ms <= nowMs;
}

/** Expired hires still need processInstance for mirror exits / pending cancels. */
export function hireExpiryRequiresExitOnlyProcessing(
  expiresAt: Date | string | null | undefined,
  simActive: boolean,
  nowMs = Date.now(),
): boolean {
  return !simActive && hireExpiryBlocksNewLiveEntries(expiresAt, nowMs);
}

const RELAY_EXECUTOR_WAKE_KEY = 'relayExecutorWake';

export type RelayExecutorWakeRequest = {
  trigger: 'POSITION_CLOSED' | 'ORDER_EXPIRED' | 'POSITION_OPENED' | 'ORDER_PLACED' | 'APPROVE_PENDING' | 'LIMIT_UPDATED' | 'USER_RESUME' | 'USER_PAUSE';
  at: string;
  tradeId?: string | null;
  /** HMAC-verified close evidence carried by the latency-only private prewake. */
  signedClose?: {
    exitPrice?: number;
    exitReason?: string;
    sourceEventAtMs?: number;
    platformReceivedAtMs?: number;
  };
  signedExpiry?: {
    sourceEventAtMs: number;
    sourceExpiresAtMs: number;
    platformReceivedAtMs: number;
    eventSeq: number;
    limitPrice: number;
    eventId: string;
    reason: 'SIGNAL_TTL_EXPIRED' | 'TTL_EXPIRED';
  };
};

/** Bounded source-fill hint polling; private exchange state remains authority. */
export async function pollForVerifiedEntryFill<T>(input: {
  detect: () => Promise<T | null>;
  attempts?: number;
  intervalMs?: number;
  wait?: (ms: number) => Promise<void>;
}): Promise<T | null> {
  const attempts = Math.max(1, input.attempts ?? 7);
  const intervalMs = Math.max(0, input.intervalMs ?? 300);
  const wait = input.wait ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const fill = await input.detect();
    if (fill != null) return fill;
    if (attempt + 1 < attempts) await wait(intervalMs);
  }
  return null;
}

export function readRelayExecutorWakeRequest(dashboardState: unknown): RelayExecutorWakeRequest | null {
  const dash =
    dashboardState && typeof dashboardState === 'object' && !Array.isArray(dashboardState)
      ? (dashboardState as Record<string, unknown>)
      : {};
  const raw = dash[RELAY_EXECUTOR_WAKE_KEY];
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const rec = raw as Record<string, unknown>;
  const trigger = typeof rec.trigger === 'string' ? rec.trigger : '';
  const at = typeof rec.at === 'string' ? rec.at : '';
  if (!trigger || !at) return null;
  return {
    trigger: trigger as RelayExecutorWakeRequest['trigger'],
    at,
    tradeId: typeof rec.tradeId === 'string' ? rec.tradeId : null,
  };
}

export function flatSignedFastPathPreflight(input: {
  status: TradingAgentInstanceStatus;
  simActive: boolean;
  hireExpired: boolean;
  relayArmed: boolean;
  virtualOpenOrPending: number;
  exchangeActiveOrders: number;
  exchangePositionQty: number;
}): boolean {
  return (
    input.status === TradingAgentInstanceStatus.ACTIVE &&
    !input.simActive &&
    !input.hireExpired &&
    input.relayArmed &&
    input.virtualOpenOrPending === 0 &&
    input.exchangeActiveOrders === 0 &&
    Math.abs(input.exchangePositionQty) === 0
  );
}

/**
 * Expired live hires must not place new entries, but MUST keep running
 * exit-only ticks while open BF risk remains. Skipping the instance entirely
 * orphans exchange lots after showcase close (cont-ffe6d1689ec2: hire expired
 * 06:10 UTC, POSITION_CLOSED persisted 06:51, Bitfinex stayed LONG).
 */
export function expiredHireShouldRunExitOnly(input: {
  simActive: boolean;
  hireExpired: boolean;
  openOrPendingParticipantCount: number;
}): boolean {
  if (input.simActive || !input.hireExpired) return false;
  return input.openOrPendingParticipantCount > 0;
}

/**
 * A fresh signed limit may bypass the slow full reconciliation pass while the
 * account already has same-direction resting copy limits. This remains
 * fail-closed: every exchange order must be owned by a current pending virtual
 * lot, no position may be open, and the configured capacity must have room.
 */
export function sameDirectionPendingSignedFastPathPreflight(input: {
  status: TradingAgentInstanceStatus;
  simActive: boolean;
  hireExpired: boolean;
  relayArmed: boolean;
  exchangePositionQty: number;
  candidateDirection: 'LONG' | 'SHORT';
  maxConcurrent: number;
  virtualLots: Array<{
    status: SignalCycleStatus;
    direction?: 'LONG' | 'SHORT';
    bitfinexOrderId?: number;
  }>;
  exchangeActiveOrderIds: number[];
}): boolean {
  if (
    input.status !== TradingAgentInstanceStatus.ACTIVE ||
    input.simActive ||
    input.hireExpired ||
    !input.relayArmed ||
    Math.abs(input.exchangePositionQty) !== 0 ||
    input.virtualLots.length === 0 ||
    input.virtualLots.length >= input.maxConcurrent
  ) {
    return false;
  }

  const ownedOrderIds = new Set<number>();
  for (const lot of input.virtualLots) {
    if (
      lot.status !== SignalCycleStatus.PENDING_ENTRY ||
      lot.direction !== input.candidateDirection ||
      !Number.isInteger(lot.bitfinexOrderId) ||
      (lot.bitfinexOrderId ?? 0) <= 0
    ) {
      return false;
    }
    ownedOrderIds.add(lot.bitfinexOrderId!);
  }

  if (
    ownedOrderIds.size !== input.virtualLots.length ||
    input.exchangeActiveOrderIds.length !== ownedOrderIds.size
  ) {
    return false;
  }
  return input.exchangeActiveOrderIds.every((orderId) => ownedOrderIds.has(orderId));
}

// ---------------------------------------------------------------------------
// Cure 1 — cycle.tradeId desync repair.
//
// Background: a copy limit is placed against showcase signal A. Showcase
// reports A as MISSED_SHOWCASE_FILL, but the real Bitfinex order either
// (a) was the dedup mirror-owner for a later/different signal B that reused
// the same limit price, or (b) raced a fill before cancel. Either way the
// REAL fill on Bitfinex corresponds to showcase signal B, while the
// participant's cycle.tradeId still names A. Every mirror-exit path
// (tryImmediateShowcaseMirrorExit / enforceShowcaseFlatOpenFailsafe /
// trackShowcaseVanished) keys off cycle.tradeId and never fires for B, so
// the real position becomes an orphan — exactly the cont-de8f316fd3c0 case.
//
// The fix: when a real fill is recorded, look up the active showcase
// position whose entry price+direction matches the real fill (excluding the
// cycle's own stale tradeId). If a DIFFERENT showcase trade matches, re-link
// the cycle to it via a unique `relink:<original>:<new>:<ts>` tradeId and
// persist `meta.originTradeId = <new>` so resolveShowcaseMirrorTradeId
// returns the right id. The protective stop / Scenario C math are unchanged.
// ---------------------------------------------------------------------------

/** Allowed slippage between the real fill and a showcase position's entry
 *  to consider them the same fill. Same value the showcase bot itself uses
 *  for book-slippage tolerance. */
export const SHOWCASE_RELINK_PRICE_BAND_PCT = 0.15;
/** Real fill must be within this window of the showcase position's entry. */
export const SHOWCASE_RELINK_TIME_WINDOW_MS = 10 * 60 * 1000;

export type ShowcaseRelinkCandidate = {
  tradeId: string;
  entryPrice: number;
  direction: 'LONG' | 'SHORT';
  entryMs: number | null;
  /** How close the showcase entry was to the real fill (percent of price). */
  priceBandPct: number;
  /** How close in time (ms). Negative = showcase entered before the real fill. */
  timeDeltaMs: number | null;
};

/**
 * Pure helper. Given the showcase book + a verified real fill, returns the
 * best matching showcase position — or null if none matches (the cycle is
 * already correctly attributed, or no showcase position exists for this
 * fill — the true-orphan case).
 *
 * Excludes `currentTradeId` (the cycle's existing stale id) so a re-link
 * only fires when the real fill maps to a DIFFERENT signal. Also excludes
 * any tradeId in `alreadyRelinkedTo` (cycles that other OPEN participants
 * have already claimed via re-link) so two real fills can't both re-link to
 * the same showcase position.
 */
export function resolveShowcaseRelinkForRealFill(input: {
  showcasePositions: Array<{
    trade_id?: string;
    entry?: number;
    dir?: string;
    side?: string;
    entry_ts?: number | string;
    created_ts?: number | string;
  }>;
  realFill: { price: number; direction: 'LONG' | 'SHORT' };
  currentTradeId: string | null | undefined;
  nowMs: number;
  alreadyRelinkedTo?: ReadonlySet<string>;
  priceBandPct?: number;
  timeWindowMs?: number;
}): ShowcaseRelinkCandidate | null {
  if (!input.realFill.price || input.realFill.price <= 0) return null;
  const bandPct = input.priceBandPct ?? SHOWCASE_RELINK_PRICE_BAND_PCT;
  const windowMs = input.timeWindowMs ?? SHOWCASE_RELINK_TIME_WINDOW_MS;
  const excluded = new Set<string>();
  if (input.currentTradeId) excluded.add(input.currentTradeId);
  if (input.alreadyRelinkedTo) for (const t of input.alreadyRelinkedTo) excluded.add(t);

  let best: ShowcaseRelinkCandidate | null = null;
  for (const pos of input.showcasePositions) {
    const tid = String(pos.trade_id ?? '').trim();
    if (!tid || excluded.has(tid)) continue;
    const entry = Number(pos.entry ?? 0);
    if (!Number.isFinite(entry) || entry <= 0) continue;
    const dirRaw = String(pos.dir ?? pos.side ?? '').toUpperCase();
    const direction: 'LONG' | 'SHORT' | null =
      dirRaw === 'LONG' || dirRaw === 'BUY'
        ? 'LONG'
        : dirRaw === 'SHORT' || dirRaw === 'SELL'
          ? 'SHORT'
          : null;
    if (!direction || direction !== input.realFill.direction) continue;

    const priceBandPct = (Math.abs(entry - input.realFill.price) / entry) * 100;
    if (priceBandPct > bandPct) continue;

    const rawTs = pos.entry_ts ?? pos.created_ts;
    const entryMs = rawTs == null ? null : Number(rawTs) > 1e12 ? Number(rawTs) : Number(rawTs) * 1000;
    let timeDeltaMs: number | null = null;
    if (entryMs != null && Number.isFinite(entryMs)) {
      timeDeltaMs = entryMs - input.nowMs;
      if (Math.abs(timeDeltaMs) > windowMs) continue;
    }

    const candidate: ShowcaseRelinkCandidate = {
      tradeId: tid,
      entryPrice: entry,
      direction,
      entryMs,
      priceBandPct,
      timeDeltaMs,
    };
    if (!best || candidate.priceBandPct < best.priceBandPct) {
      best = candidate;
    }
  }
  return best;
}

/**
 * Pure helper — resolve which showcase trade_id a copy cycle should mirror for
 * exit convergence, given the cycle's (possibly `adopt:`/`relink:`-prefixed)
 * tradeId and the participant's execution meta.
 *
 * Exported so the contract can be unit-tested: a regression here silently
 * orphans real money (the cont-de8f316fd3c0 case).
 *
 * Rules:
 *   - `adopt:<origin>:<ts>` → watch meta.originTradeId if it is set and NOT
 *     the embedded prior origin; otherwise parse the prior origin from the
 *     string. (Adopt cycles are seeded with originTradeId = origin cycle's
 *     showcase id; a later Cure 1 re-link will overwrite originTradeId with
 *     the matched showcase id, which is what we want to watch.)
 *   - `relink:<origin>:<new>:<ts>` → watch meta.originTradeId if it is set
 *     and NOT equal to the embedded prior origin (otherwise a stale
 *     originTradeId pointing at the PRE-relink origin would silently defeat
 *     the re-link — this was a real bug). Otherwise parse the NEW id from
 *     the string's index 2.
 *   - Otherwise → return the tradeId as-is.
 */
export function resolveShowcaseMirrorTradeIdFromInputs(
  cycleTradeId: string | null | undefined,
  originTradeId: string | null | undefined,
): string | null {
  const tid = cycleTradeId ?? null;
  if (!tid) return null;
  if (tid.startsWith('adopt:')) {
    const parts = tid.split(':');
    const embeddedOrigin = parts.length >= 2 ? parts[1] : undefined;
    if (
      originTradeId &&
      originTradeId !== 'unknown' &&
      originTradeId !== embeddedOrigin
    ) {
      return originTradeId;
    }
    if (embeddedOrigin && embeddedOrigin !== 'unknown') return embeddedOrigin;
    return null;
  }
  if (tid.startsWith('relink:')) {
    const parts = tid.split(':');
    const priorOrigin = parts.length >= 2 ? parts[1] : undefined;
    const newTrade = parts.length >= 3 ? parts[2] : undefined;
    if (
      originTradeId &&
      originTradeId !== 'unknown' &&
      originTradeId !== priorOrigin
    ) {
      return originTradeId;
    }
    if (newTrade && newTrade !== 'unknown') return newTrade;
    if (priorOrigin && priorOrigin !== 'unknown') return priorOrigin;
    return null;
  }
  return tid;
}

/** Match a persisted close wake against the canonical showcase identity.
 * Cancel-race fills rewrite cycle.tradeId to a relink:* audit id, so comparing
 * the raw cycle id makes the fast wake miss the live lot and fall back to the
 * slower reconciliation poll.
 */
export function persistedCloseWakeMatchesParticipant(
  wakeTradeId: string | null | undefined,
  cycleTradeId: string | null | undefined,
  originTradeId: string | null | undefined,
): boolean {
  if (!wakeTradeId) return false;
  const mirrorTradeId = resolveShowcaseMirrorTradeIdFromInputs(
    cycleTradeId,
    originTradeId,
  );
  return Boolean(mirrorTradeId && tradeIdsMatch(wakeTradeId, mirrorTradeId));
}

/**
 * Phase 2 — exit convergence master switch. Default ON (same pattern as
 * MIRROR_CONVERGENCE_ENABLED). When ON in showcase-mirror mode: wide disaster
 * stop only, no profit-lock trail, no local HARD_STOP — exits follow showcase
 * closure (+ SHOWCASE_VANISHED fallback). Set MIRROR_EXIT_CONVERGENCE_ENABLED=0
 * to roll back to legacy independent Scenario C exits on the copy.
 */
function mirrorExitConvergenceEnabled(): boolean {
  const v = (process.env.MIRROR_EXIT_CONVERGENCE_ENABLED ?? '').trim().toLowerCase();
  if (v === '0' || v === 'false' || v === 'off' || v === 'no') return false;
  return true;
}

/**
 * In canonical mirror-convergence mode the showcase owns every thesis exit.
 * Running the local Scenario-C safety evaluator as well can front-run a
 * showcase recovery (real loss while paper later closes profitably). The
 * exchange-side disaster stop remains armed for crash/disconnect insurance.
 */
export function shouldRunLocalRealSideSafetyNet(input: {
  simActive: boolean;
  showcaseMirrorOnly: boolean;
  mirrorExitConvergence: boolean;
}): boolean {
  if (input.simActive) return false;
  return !(input.showcaseMirrorOnly && input.mirrorExitConvergence);
}

/**
 * Option A — Exchange-side protective stops (mirror + safety net). SHIPS DARK.
 *
 * Default OFF (must be enabled explicitly per account via Railway env). When
 * "true": the relay un-gates PROFIT_LOCK_TRAIL so the protective stop on
 * Bitfinex is kept synced to the current Scenario C ladder rung, even in
 * showcase-mirror-only mode (the showcase bot remains the decision maker for
 * EXIT, but the exchange stop advances through 10→6, 19→17, 40→28, ... as
 * unrealized margin grows, so a sudden reverse actually realizes the rung's
 * protected % instead of the initial wide stop). When unset / not "true":
 * behavior is unchanged (Phase 2 exit convergence — wide disaster stop only,
 * showcase EXIT authoritative). Read per call so operators can flip via
 * Railway env without a redeploy.
 *
 * Per-account isolation: state (peak MFE, current rung, current stop order
 * id) is keyed by participantId, which is unique per (userId, cycleId).
 * Never-loosen, cancel-then-replace, idempotent on restart, audit-logged,
 * circuit-broken after 3 consecutive failures.
 */
function exchangeDynamicStopsEnabled(): boolean {
  return (process.env.EXCHANGE_DYNAMIC_STOPS_ENABLED ?? '').trim().toLowerCase() === 'true';
}

/** Option A circuit breaker — consecutive stop-replacement failures before
 *  the relay stops attempting replacements for a participant. Reset to 0 on
 *  any successful replace or on the next FILLED event. */
const STOP_MANAGER_CIRCUIT_THRESHOLD = 3;

/**
 * Real-side protective safety net (independent of showcase mirror exits).
 *
 * Default ON. When ON, every OPEN real Bitfinex lot is re-evaluated each tick
 * against the SAME Scenario C math the showcase bot uses (THESIS_FAST_CUT /
 * PROFIT_LOCK ladder / HARD_STOP), computed from the REAL fill price and REAL
 * mark. If a threshold is breached, the relay market-closes the lot directly —
 * even when no showcase POSITION_CLOSED ever arrives (the paper/real desync
 * case that orphaned cont-de8f316fd3c0 on 2026-08-07).
 *
 * This does NOT replace the showcase mirror exit path. Profitable showcase
 * exits still propagate via tryImmediateShowcaseMirrorExit. The safety net is
 * strictly the last line of defense for real money: it only fires on adverse
 * moves the showcase mirror path missed.
 *
 * Set REAL_SIDE_SAFETY_NET_ENABLED=0 to disable (rollback lever — the relay
 * reverts to showcase-mirror-only behaviour + the wide MIRROR_DISASTER_STOP).
 */
function realSideSafetyNetEnabled(): boolean {
  const v = (process.env.REAL_SIDE_SAFETY_NET_ENABLED ?? '').trim().toLowerCase();
  if (v === '0' || v === 'false' || v === 'off' || v === 'no') return false;
  return true;
}

/**
 * The hard-stop margin % used by the real-side safety net. Defaults to the
 * intent's own stop_loss_margin_pct ceiling (-18), independent of the wider
 * MIRROR_DISASTER_STOP_MARGIN_PCT. Override per deployment via env if a
 * tighter floor is required. Negative number.
 */
function realSideSafetyNetHardStopMarginPct(intentStopLoss?: number): number {
  const env = process.env.REAL_SIDE_SAFETY_NET_HARD_STOP_MARGIN_PCT;
  if (env != null && env.trim() !== '') {
    const n = Number(env);
    if (Number.isFinite(n) && n < 0) return n;
  }
  // Honour a tighter intent stop if the strategy specified one; -18 is the
  // canonical showcase default and the historical relay fallback.
  if (intentStopLoss != null && Number.isFinite(intentStopLoss) && intentStopLoss < 0) {
    return intentStopLoss;
  }
  return -18;
}

/** Exact-copy entries never use the legacy market catch-up path. */
function mirrorCatchupEnabled(): boolean {
  // Exact-mirror policy: never cross the book after the showcase has filled.
  // A missed entry stays missed; a resting limit may only fill no-worse.
  return false;
}

/**
 * Action-match priority (policy v4): no slip cap by default — fill price may differ.
 * Set MIRROR_CATCHUP_MAX_SLIP_USD to a positive number to re-enable a cap.
 * Unset / 0 / invalid → unlimited (null).
 */
function mirrorCatchupMaxSlipUsd(): number | null {
  const env = process.env.MIRROR_CATCHUP_MAX_SLIP_USD;
  if (env == null || env.trim() === '') return null;
  const raw = Number(env);
  if (!Number.isFinite(raw) || raw <= 0) return null;
  return raw;
}

/**
 * Action-match priority (policy v4): no daily catch-up budget by default.
 * Set MIRROR_CATCHUP_BUDGET_PER_DAY to a positive integer to re-enable a cap.
 * Unset / 0 / invalid → unlimited (null).
 */
function mirrorCatchupBudgetPerDay(): number | null {
  const env = process.env.MIRROR_CATCHUP_BUDGET_PER_DAY;
  if (env == null || env.trim() === '') return null;
  const raw = Number(env);
  if (!Number.isFinite(raw) || raw <= 0) return null;
  return Math.floor(raw);
}

/**
 * N3 (intent-mirror) — Ops panic button. When set (any truthy value), the
 * intent-mirror entry path is disabled and the relay reverts to fill-only
 * mirroring. Default unset = feature ON. Kill switch stays in the code
 * forever — it is a permanent operational tool.
 */
function intentMirrorKillSwitchActive(): boolean {
  const v = (process.env.INTENT_MIRROR_KILL_SWITCH ?? '').trim().toLowerCase();
  return v === '1' || v === 'true' || v === 'on' || v === 'yes';
}

/**
 * N4 (intent-mirror) — Dry-run mode. When set (any truthy value), the
 * intent-mirror entry path logs the would-be order and records an
 * `INTENT_MIRROR_ENTER_DRY` audit event WITHOUT calling submitLimitOrder.
 * The authenticated Start action must also stamp the current per-instance
 * relay consent policy. An ops truthy value always forces dry-run; an unset
 * or false value permits execution only for a currently consented instance.
 */
function intentMirrorDryRunActive(instance: TradingAgentInstance): boolean {
  return shouldDryRunIntentMirror(
    process.env.INTENT_MIRROR_DRY_RUN,
    instance.dashboardState,
  );
}

const TERMINAL_PARTICIPANT_STATUSES = new Set<SignalCycleStatus>([
  SignalCycleStatus.CLOSED,
  SignalCycleStatus.EXPIRED,
]);

/** Effective stop-loss margin % — wide disaster stop in mirror+convergence mode. */
function resolveEffectiveStopLossMarginPct(
  intentStopLoss?: number,
  opts?: { mirrorMode?: boolean; simActive?: boolean },
): number {
  if (
    opts?.mirrorMode !== false &&
    !opts?.simActive &&
    isShowcaseMirrorOnlyMode() &&
    mirrorExitConvergenceEnabled()
  ) {
    // Crash/disconnect insurance only — must never front-run showcase thesis exits.
    return resolveMirrorDisasterStopMarginPct();
  }
  return intentStopLoss ?? -18;
}

/** Two copy/showcase limits within this many USD are "the same book entry"
 *  (showcase book dedupes duplicate lane spawns at the EXACT same price). */
const DUPLICATE_LIMIT_EPSILON_USD = 0.01;

/** Price delta below this (USD) is considered converged for MIRROR_DIFF. */
const MIRROR_DIFF_PRICE_EPSILON_USD = 0.01;

/** Max 1 MIRROR_DIFF SignalCycleEvent per participant per this window. */
const MIRROR_DIFF_EVENT_THROTTLE_MS = 60_000;

/** With MIRROR_CONVERGENCE_ENABLED: max 1 chase replace per order per second. */
const MIRROR_CHASE_MIN_REPLACE_MS = 1_000;

/** With MIRROR_CONVERGENCE_ENABLED: showcase state memo TTL for the execution
 *  path — all call sites within ~1s share one fresh fetch. */
const MIRROR_EXEC_STATE_MEMO_MS = 1_000;

/** Fix B — showcase-vanished close rule: an OPEN copy participant whose showcase
 *  trade_id is absent from the canonical state's positions AND trades/trades_map
 *  (e.g. after a Fresh Collection wipe) for this many CONSECUTIVE fresh,
 *  successfully-fetched states is market-closed with exit_reason
 *  SHOWCASE_VANISHED. Failed/unreachable fetches do not count (fail-closed). */
const SHOWCASE_VANISHED_CONSECUTIVE_MISSES = 3;

/** Cross-ID / ghost-fill exit: OPEN copy lot whose trade_id is not in showcase
 *  open positions for repeated fresh bot states and the bounded convergence
 *  grace is market-closed (SHOWCASE_POSITION_ABSENT). Unlike
 *  SHOWCASE_VANISHED, this only checks positions — trades_map may still list
 *  PENDING/VIRTUAL_CHASE for a trade the copy filled but showcase never opened.
 *  Fail-closed on unreachable fetch. */
const SHOWCASE_POSITION_ABSENT_CONSECUTIVE_MISSES = 2;

/**
 * A real Bitfinex fill can be visible just before the canonical Fly paper book
 * publishes the corresponding OPEN position. Do not turn that normal
 * cross-system convergence race into an immediate market close. The exchange
 * protective stop remains armed during this bounded wait; a genuine ghost fill
 * is still closed after both the miss-count and elapsed-time gates pass.
 */
const SHOWCASE_POSITION_ABSENT_GRACE_MS = 60_000;

export function showcasePositionAbsenceActionable(input: {
  misses: number;
  firstAbsentAtMs: number;
  nowMs: number;
  consecutiveMissesRequired?: number;
  graceMs?: number;
}): boolean {
  const missesRequired = Math.max(
    1,
    input.consecutiveMissesRequired ?? SHOWCASE_POSITION_ABSENT_CONSECUTIVE_MISSES,
  );
  const graceMs = Math.max(1, input.graceMs ?? SHOWCASE_POSITION_ABSENT_GRACE_MS);
  return (
    input.misses >= missesRequired &&
    input.nowMs - input.firstAbsentAtMs >= graceMs
  );
}

/** Belt-and-suspenders: OPEN copy lot while showcase is flat/closed for this long
 *  triggers operator alert + forced SHOWCASE_MIRROR close (covers stale EXIT events
 *  or any other idempotency gate that wrongly skips the normal mirror path). */
const SHOWCASE_FLAT_OPEN_FAILSAFE_MS = 120_000;

/**
 * F1/F2/F3 — Showcase-unreachable safe mode (2026-07-07 incident hardening).
 *
 * Today's $10.69 loss for @bitbro4crypto came from a 3h59m Cloudflare tunnel
 * outage: the showcase bot was alive on :7002 but unreachable from the relay,
 * so every fetchExecutionBotState() returned null. Two fail-open code paths
 * let real money move during that window:
 *   (a) placeEntry treated `bot=null` as "ready" and kept submitting limits.
 *   (b) tryImmediateShowcaseMirrorExit's SHOWCASE_POSITION_ABSENT branch was
 *       gated on `bot &&` — structurally unable to detect orphans while dark.
 *
 * These constants implement a per-instance safe mode: after the showcase has
 * been unreachable for SHOWCASE_UNREACHABLE_ENTRY_BLOCK_MS, the relay refuses
 * new entries (F1). After SHOWCASE_UNREACHABLE_ORPHAN_KILL_MS, OPEN copy lots
 * are market-closed with exit_reason SHOWCASE_UNREACHABLE_OPEN_LOT (F2). Both
 * fire even though `bot === null` — fail-closed for money already on the
 * table, not just for money we haven't placed yet. F3 surfaces the state to
 * the user as lastError so the dashboard explains the halt.
 *
 * Override via env without a redeploy:
 *   SHOWCASE_UNREACHABLE_ENTRY_BLOCK_MS (default 60000)
 *   SHOWCASE_UNREACHABLE_ORPHAN_KILL_MS  (default 120000)
 *   SHOWCASE_UNREACHABLE_SAFE_MODE=0     (kill switch — reverts to legacy fail-open)
 */
const SHOWCASE_UNREACHABLE_ENTRY_BLOCK_MS = Number(
  process.env.SHOWCASE_UNREACHABLE_ENTRY_BLOCK_MS ?? 60_000,
);
const SHOWCASE_UNREACHABLE_ORPHAN_KILL_MS = Number(
  process.env.SHOWCASE_UNREACHABLE_ORPHAN_KILL_MS ?? 120_000,
);
function showcaseUnreachableSafeModeEnabled(): boolean {
  const v = (process.env.SHOWCASE_UNREACHABLE_SAFE_MODE ?? '').trim().toLowerCase();
  return v !== '0' && v !== 'false' && v !== 'off' && v !== 'no';
}

/** Fix E — throttle for surfacing an expired hire (lastError + warn log). */
const HIRE_EXPIRY_NOTICE_THROTTLE_MS = 60 * 60_000;

/** Phase 5 guardrail — refuse to adopt an orphan whose qty exceeds this multiple
 *  of the normal lot size (marginCap * leverage / price). A giant orphan is a
 *  red flag (manual position or wrong-symbol order), not a heal target. */
const RECONCILE_ADOPT_SIZE_ANOMALY_MULTIPLE = 2;

/** Match window for the fallback (no-cid) orphan match: the terminal
 *  participant's most recent event must be within this many minutes of NOW.
 *  Tight enough to exclude stale orphans the operator chose to leave flat,
 *  generous enough to catch a fill/expiry race (entry TTL is 30min). */
const RECONCILE_ADOPT_MATCH_WINDOW_MS = 30 * 60_000;

/** Qty tolerance for the fallback match — exchange rounding vs ledger qty. */
const RECONCILE_ADOPT_QTY_TOLERANCE = 0.0005;

/** Price band for the fallback match — orphan entry within X% of the terminal
 *  participant's intended limit. */
const RECONCILE_ADOPT_PRICE_BAND_PCT = 2.0;

/** Ring buffer cap for dashboardState.reconcileAdoptLog. */
const RECONCILE_ADOPT_LOG_CAP = 20;

/** Trailing window for the reset-proof adoption budget (RECONCILE_ADOPT_BUDGET
 *  adoptions per this window, derived from the Neon event stream). */
const RECONCILE_ADOPT_BUDGET_WINDOW_MS = 24 * 60 * 60_000;

/** Fix 4 — double-adopt guard: a non-terminal `adopt:%` participant for the
 *  same user+agent+direction created within this window whose qty matches the
 *  orphan slice blocks a second synthesis (fail-closed, skip + audit). */
const RECONCILE_ADOPT_DUPLICATE_WINDOW_MS = 10 * 60_000;

/** Fix 3 — synthesized S6a (PENDING_ENTRY) adopted cycles are fill-or-expire:
 *  hard TTL after which monitorEntry cancels the resting order (fail-loud)
 *  and marks the participant EXPIRED. Matches the showcase entry TTL. */
const RECONCILE_ADOPT_ORDER_TTL_MS = 30 * 60_000;

/**
 * Deterministic Bitfinex client order id (`cid`) for a participant's entry
 * order. Bitfinex v2 `cid` is a 32-bit signed integer; we derive a stable
 * positive int32 from sha256(cycleId|participantId|tradeId). The full
 * 32-char hex digest is NOT used because Bitfinex rejects non-int cids.
 * Collisions are astronomically unlikely (2^31 bucket, ~tens of participants).
 */
function computeClientOrderId(
  cycleId: string,
  participantId: string,
  tradeId?: string | null,
): number {
  const digest = createHash('sha256')
    .update(`${cycleId}|${participantId}|${tradeId ?? ''}`)
    .digest('hex');
  return parseInt(digest.slice(0, 8), 16) & 0x7fffffff;
}

type EntryEligibility = {
  canEnter: boolean;
  reason: string | null;
  availableUsd: number | null;
  slotsRemaining: number;
};

type VirtualLotSummary = {
  open: number;
  pending: number;
  direction: 'LONG' | 'SHORT' | null;
  openQty: number;
  signedOpenQty: number;
  directionConflict: boolean;
};

type ExecutionTradingClient = BitfinexTradingClient | BitfinexSimTradingClient;

@Injectable()
export class SignalSubscriberExecutionService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(SignalSubscriberExecutionService.name);
  private readonly bitfinex = new BitfinexTradingClient();
  private activeTrading: ExecutionTradingClient;
  private readonly positionRuntime = new Map<string, PositionRuntime>();
  private readonly bitfinexTradeStreams = new Map<string, BitfinexAuthTradeStream>();
  private streamSyncRunning = false;
  private streamSyncQueued = false;
  private readonly wsTradeInFlight = new Map<string, Promise<boolean>>();
  private readonly wsTradeCompletedAt = new Map<string, number>();
  private readonly exitingLots = new Set<string>();
  private running = false;
  private wakeQueued = false;
  private fastWakeRunning = false;
  /** Wake currently owning the serial money lane. Kept separately from the
   * boolean so the post-commit copy of an already-running pre-wake can be
   * identified and discarded instead of delaying the next LIMIT_UPDATED. */
  private activeDirectWake: RelayExecutorWakeRequest | null = null;
  /** Authenticated direct wakes that arrived while a read-only durable-wake
   * poll or another direct wake was finishing. Never drop the latency hint on
   * a transient 409/busy window. */
  private readonly pendingDirectWakes: RelayExecutorWakeRequest[] = [];
  /** Exact direct wakes completed by this process. Prevent the durable
   * crash-fallback copy from executing the same wake a second time. */
  private readonly completedDirectWakeAt = new Map<string, number>();
  /**
   * A verified source fill retires an executable pending order.  It cannot sit
   * behind an unrelated global fast wake: the exact participant money lane
   * below still serializes it against a concurrent limit replacement.
   */
  private prioritySourceFillWakes = new Set<string>();
  /** One writer per participant while a cancel-race fill is promoted to OPEN. */
  private readonly cancelRaceFillInFlight = new Set<string>();
  /** Serializes cancel/replace persistence against gone-order classification. */
  private participantMoneyLane = new Map<string, Promise<void>>();
  private readonly replacementMissingProbe = new Map<
    string,
    { generation: string; firstMissingAtMs: number; count: number }
  >();

  private async acquireParticipantMoneyLane(participantId: string): Promise<() => void> {
    const lane = this.participantMoneyLane ?? (this.participantMoneyLane = new Map());
    const prior = lane.get(participantId) ?? Promise.resolve();
    let release!: () => void;
    const current = new Promise<void>((resolve) => { release = resolve; });
    lane.set(participantId, current);
    await prior;
    return () => {
      release();
      if (lane.get(participantId) === current) {
        lane.delete(participantId);
      }
    };
  }
  /** Recently discovered Bitfinex relay instances. Direct signed wakes start
   * these known subscribers immediately while a fresh discovery query runs in
   * parallel for newly activated subscribers. Every cached row is revalidated
   * inside the signed fast path before any claim or exchange write. */
  private readonly relayInstanceCache = new Map<string, TradingAgentInstance>();
  private tickStartedAtMs = 0;
  private lastTickCompletedAtMs = 0;
  private lastTickDurationMs = 0;
  private currentInstanceId: string | null = null;
  private currentStage: string | null = null;
  private executorTimeoutCount = 0;
  private watchdogHandling = false;
  private readonly executorOwnerId = [
    process.env.RAILWAY_SERVICE_ID ?? 'local',
    process.env.RAILWAY_REPLICA_ID ?? process.env.HOSTNAME ?? 'single',
  ].join(':');
  /** Phase 1 — 1s showcase-state memo for the execution path (flag-gated). */
  private execStateMemo: { at: number; state: BotApiState | null } | null = null;
  /** Phase 0 — MIRROR_DIFF event throttle: participantId → last event ms. */
  private readonly mirrorDiffEventAt = new Map<string, number>();
  /** Phase 0 — participantId → first-divergence ms (for reprice-lag EMA). */
  private readonly mirrorDivergenceSince = new Map<string, number>();
  /** Phase 0 — instanceId → showcase position trade_ids seen last snapshot (fill capture). */
  private readonly mirrorSeenShowcasePositions = new Map<string, Set<string>>();
  /** Fix B — participantId → consecutive fresh canonical states missing its showcase trade.
   *  In-memory by design: resets on process restart (the count simply restarts). */
  private readonly showcaseVanishedMisses = new Map<string, number>();
  /** participantId → consecutive fresh states where showcase has no OPEN position
   *  for this lot's trade_id (cross-ID / ghost-fill exit). In-memory; resets on restart. */
  private readonly showcasePositionAbsentMisses = new Map<string, number>();
  /** participantId → first fresh-state absence time for the bounded convergence grace. */
  private readonly showcasePositionAbsentSince = new Map<string, number>();
  /** Fix E — instanceId → last time the expired-hire notice was surfaced (ms). */
  private readonly hireExpiryNoticeAt = new Map<string, number>();
  /** Action-miss audit throttle: userId:tradeId:reason → last event ms. */
  private readonly actionMissEntryThrottle = new Map<string, number>();
  /** participantId → first ms showcase was flat/closed while copy lot stayed OPEN. */
  private readonly showcaseFlatOpenSince = new Map<string, number>();

  /**
   * F7 (2026-07-07 incident) — Wake-source tracking for fast-path exit mirroring.
   *
   * When the showcase-relay-event webhook fires `POSITION_CLOSED`, it calls
   * wakeNow() which spawns an immediate tick. That tick's
   * tryImmediateShowcaseMirrorExit call will fire with a fresh bot fetch and
   * close the copy lot — typically within ~2 seconds of the showcase closing.
   *
   * We stamp `lastShowcaseWakeAt` here so the audit log can distinguish exits
   * that fired from a fast webhook wake (trigger=SHOWCASE_CLOSED_WEBHOOK) vs
   * exits that fired from the regular 2s poll (trigger=SHOWCASE_CLOSED_POLL).
   * Both are correct; the distinction is for ops telemetry — if median exit
   * lag rises, we want to know whether the wake path or the poll path is the
   * slow one.
   */
  private lastShowcaseWakeAt = 0;
  private lastShowcaseWakeTrigger: 'POSITION_CLOSED' | 'POSITION_OPENED' | 'ORDER_PLACED' | 'APPROVE_PENDING' | 'LIMIT_UPDATED' | null = null;

  /**
   * F1/F2/F3 — per-instance showcase-unreachable tracking.
   *
   * `showcaseUnreachableSince` is set when a fetch returns null AND the bridge
   * is enabled (i.e. we *should* be able to reach the bot but can't). It is
   * cleared on the first successful fetch. The two thresholds
   * (ENTRY_BLOCK_MS, ORPHAN_KILL_MS) gate F1 (refuse new entries) and F2
   * (market-close OPEN orphans) respectively. F3 surfaces the state via
   * lastError on the instance row.
   *
   * Keyed by instanceId — independent per hire, so one user's dark tunnel
   * doesn't trip another's safe mode.
   */
  private readonly showcaseUnreachableSince = new Map<string, number>();
  /** instanceId → last ms we surfaced the safe-mode notice (throttle spam). */
  private readonly showcaseSafeModeNoticeAt = new Map<string, number>();
  /**
   * F8 (2026-07-08 hotfix) — per-instance counter of consecutive successful
   * state fetches since the last null-fetch streak. {@link clearShowcaseUnreachable}
   * only actually clears the entry block once this reaches the required
   * threshold, preventing a single lucky ping from re-arming live copy
   * during a tunnel flap. Reset to 0 on any failed fetch.
   */
  private readonly showcaseRecoveryHits = new Map<string, number>();
  /** F8 — consecutive successful fetches required before clearing safe mode. */
  private readonly SHOWCASE_RECOVERY_HITS_REQUIRED = 3;

  constructor(
    private readonly prisma: PrismaService,
    private readonly exchanges: ExchangesService,
    private readonly cycles: SignalCyclesService,
    private readonly botBridge: BotBridgeService,
    private readonly relaySim: CopyRelaySimService,
    private readonly cycleAudit: TradeCycleAuditService,
    private readonly config: ConfigService,
    private readonly notifications: NotificationsService,
  ) {
    this.activeTrading = this.bitfinex;
  }

  onModuleInit() {
    if (!executionEnabled()) {
      this.startExecutorConnectionKeepalive();
      this.logger.warn('Subscriber execution disabled (SUBSCRIBER_EXECUTION_ENABLED=false)');
      return;
    }
    // Fix G — single-replica invariant. The executor's correctness depends on
    // per-process state: the `this.running` tick mutex, `exitingLots`,
    // `positionRuntime`, and the Fix B vanished-miss counters. A second replica
    // would double-place entries and race exits. Do NOT scale this service
    // horizontally without distributed locking (deliberately not built here).
    const replicaId = (process.env.RAILWAY_REPLICA_ID ?? '').trim();
    this.logger.warn(
      `Subscriber execution assumes a SINGLE replica (in-memory tick mutex / exitingLots / positionRuntime)${replicaId ? ` — RAILWAY_REPLICA_ID=${replicaId}` : ''}`,
    );
    const replicaCountRaw = process.env.RAILWAY_REPLICA_COUNT ?? process.env.RAILWAY_REPLICAS;
    const replicaCount = Number(replicaCountRaw ?? 1);
    if (Number.isFinite(replicaCount) && replicaCount > 1) {
      throw new Error(
        `MULTIPLE RELAY EXECUTOR REPLICAS DETECTED (count=${replicaCount}); refusing to start the money path`,
      );
    }
    void loadSubscriberMaxMarginUsd(this.prisma).then((cap) => {
      this.logger.log(
        `Hire subscriber runner active — Bitfinex copy policy v${BITFINEX_COPY_POLICY_VERSION}, every ${POLL_MS}ms (max $${cap}/trade)`,
      );
    });
    setInterval(() => void this.tick(), POLL_MS);
    setTimeout(() => void this.tick(), POLL_MS);
    // Signed showcase lifecycle events must not wait for the full reconciliation
    // pass. A healthy full pass can take several seconds because Bitfinex auth
    // calls are deliberately serialized on one nonce lane. Poll the durable
    // cross-process wake independently and run only the idempotent atomic-claim
    // entry / exitingLots-guarded close path. The normal tick remains the crash
    // recovery and reconciliation backstop.
    setInterval(() => void this.pollPersistedFastWake(), 250).unref();
    // Authenticated account-info trades are the latency path for exchange fills.
    // They consume no REST nonce/read budget; the ordinary reconciliation tick
    // remains the fail-closed reconnect/outage backstop.
    setInterval(() => void this.syncBitfinexTradeStreams(), 5_000).unref();
    void this.syncBitfinexTradeStreams();
    setInterval(() => void this.watchExecutorLiveness(), 1_000).unref();
  }

  onModuleDestroy() {
    for (const stream of this.bitfinexTradeStreams.values()) stream.stop();
    this.bitfinexTradeStreams.clear();
  }

  private async syncBitfinexTradeStreams(): Promise<void> {
    if (!executionEnabled()) return;
    if (this.streamSyncRunning) { this.streamSyncQueued = true; return; }
    this.streamSyncRunning = true;
    try {
      do {
        this.streamSyncQueued = false;
        await this.syncBitfinexTradeStreamsOwned();
      } while (this.streamSyncQueued);
    } finally { this.streamSyncRunning = false; }
  }

  private async syncBitfinexTradeStreamsOwned(): Promise<void> {
    const wanted = new Set<string>();
    for (const instance of this.relayInstanceCache.values()) {
      if (isCopyRelaySimActive(instance.dashboardState) || instance.exchangeProvider !== 'bitfinex') continue;
      const observed = await this.exchanges.getUserCredentials(instance.userId, 'bitfinex');
      if (!observed) continue;
      // Credential lookup may overlap a rotation. Re-read immediately before
      // installation and use only the newest fingerprint.
      const creds = await this.exchanges.getUserCredentials(instance.userId, 'bitfinex');
      if (!creds) continue;
      const key = this.bitfinexStreamKey(instance.userId, creds);
      wanted.add(key);
      this.ensureBitfinexTradeStream(instance.userId, creds);
    }
    for (const [key, stream] of this.bitfinexTradeStreams) {
      if (!wanted.has(key)) { stream.stop(); this.bitfinexTradeStreams.delete(key); }
    }
  }

  private ensureBitfinexTradeStream(userId: string, creds: ExchangeCredentials): BitfinexAuthTradeStream {
    const key = this.bitfinexStreamKey(userId, creds);
    let stream = this.bitfinexTradeStreams.get(key);
    if (!stream) {
      stream = new BitfinexAuthTradeStream(creds, (trade) => this.handleBitfinexWsTrade(userId, creds, trade));
      this.bitfinexTradeStreams.set(key, stream);
      stream.start();
    }
    return stream;
  }

  private bitfinexStreamKey(userId: string, creds: ExchangeCredentials): string {
    // Internal only; hashing both fields makes secret-only credential rotation
    // replace the old socket without ever logging either credential.
    return `${userId}:${createHash('sha256').update(`${creds.apiKey}\0${creds.apiSecret}`).digest('hex')}`;
  }

  private async handleBitfinexWsTrade(userId: string, creds: ExchangeCredentials, trade: BitfinexWsTrade): Promise<boolean> {
    this.pruneWsTradeDedupe(Date.now());
    const dedupeKey = `${userId}:${createHash('sha256').update(creds.apiKey).digest('hex')}:${trade.tradeId}`;
    const completedAt = this.wsTradeCompletedAt.get(dedupeKey);
    if (completedAt && Date.now() - completedAt < 60 * 60_000) return true;
    const existing = this.wsTradeInFlight.get(dedupeKey);
    if (existing) return existing;
    const work = this.handleBitfinexWsTradeOwned(userId, creds, trade);
    this.wsTradeInFlight.set(dedupeKey, work);
    try {
      const handled = await work;
      if (handled) this.wsTradeCompletedAt.set(dedupeKey, Date.now());
      return handled;
    } finally { this.wsTradeInFlight.delete(dedupeKey); }
  }

  private pruneWsTradeDedupe(nowMs: number): void {
    const cutoff = nowMs - 60 * 60_000;
    for (const [key, at] of this.wsTradeCompletedAt) {
      if (at < cutoff && !this.wsTradeInFlight.has(key)) this.wsTradeCompletedAt.delete(key);
    }
    if (this.wsTradeCompletedAt.size > 20_000) {
      for (const key of this.wsTradeCompletedAt.keys()) {
        if (!this.wsTradeInFlight.has(key)) this.wsTradeCompletedAt.delete(key);
        if (this.wsTradeCompletedAt.size <= 20_000) break;
      }
    }
  }

  private async handleBitfinexWsTradeOwned(userId: string, creds: ExchangeCredentials, trade: BitfinexWsTrade): Promise<boolean> {
    const candidates = await this.prisma.signalCycleParticipant.findMany({
      where: { userId, status: SignalCycleStatus.PENDING_ENTRY },
      select: { id: true },
    });
    for (const candidate of candidates) {
      const release = await this.acquireParticipantMoneyLane(candidate.id);
      try {
        const [participant, meta] = await Promise.all([
          this.prisma.signalCycleParticipant.findUnique({ where: { id: candidate.id }, include: { cycle: true } }),
          this.loadExecutionMeta(candidate.id),
        ]);
        if (!participant || participant.status !== SignalCycleStatus.PENDING_ENTRY || Number(meta.bitfinexOrderId) !== trade.orderId) continue;
        const expectedSign = meta.direction === 'LONG' ? 1 : -1;
        if (Math.sign(trade.execAmount) !== expectedSign) return false;
        const intendedQty = meta.qty ?? trade.cumulativeQty;
        // Bitfinex trade aggregation can finish one satoshi below the
        // acknowledged order quantity.  A one-satoshi remainder is below the
        // venue's executable lot size and must take the terminal-fill path so
        // finalizedEntryFillQty can snap it to the durable order quantity.
        // Larger deficits remain resting and retain their protected remainder.
        const orderResting =
          btcToSats(trade.cumulativeQty) + 1 < btcToSats(intendedQty);
        return await this.recordCancelRaceFill(
          participant.cycle.agentId, userId, participant.cycle, participant.id, meta, creds,
          participant.cycle.intentEnvelope as SignalIntentEnvelope,
          { filledQty: trade.cumulativeQty, fillPrice: trade.cumulativeAveragePrice, source: 'ORDER_PARTIAL', orderResting },
          'BITFINEX_AUTH_WS_TRADE', new Date(trade.mts).toISOString(),
        );
      } finally { release(); }
    }
    return false;
  }

  /** Keep the private/public Railway transport to the isolated executor hot.
   * Signals can be tens of minutes apart; without this health-only probe the
   * first money-path wake pays DNS + TLS + edge connection setup. */
  private startExecutorConnectionKeepalive(): void {
    const base = process.env.RELAY_EXECUTOR_WAKE_URL?.trim().replace(/\/$/, '');
    if (!base) return;
    const probe = () => {
      void fetch(`${base}/api/health/live`, {
        method: 'GET',
        headers: { accept: 'application/json' },
        signal: AbortSignal.timeout(1_500),
      }).then(async (response) => {
        // Drain the tiny health response so Undici can return the socket to
        // its keep-alive pool rather than treating the stream as aborted.
        await response.arrayBuffer();
      }).catch(() => {
        // The durable Neon wake remains the safety backstop. A failed warm-up
        // must never change relay state or create noisy production errors.
      });
    };
    probe();
    setInterval(probe, 3_000).unref();
  }

  getHealthSnapshot(nowMs = Date.now()): RelayExecutorHealthSnapshot {
    const timeoutMs = Math.max(
      10_000,
      Number(this.config.get('SUBSCRIBER_EXECUTOR_TICK_TIMEOUT_MS') ?? DEFAULT_EXECUTOR_TICK_TIMEOUT_MS),
    );
    const healthMaxAgeMs = Math.max(
      POLL_MS * 4,
      Number(this.config.get('SUBSCRIBER_EXECUTOR_HEALTH_MAX_AGE_MS') ?? DEFAULT_EXECUTOR_HEALTH_MAX_AGE_MS),
    );
    const health = buildRelayExecutorHealth({
      nowMs,
      running: this.running,
      tickStartedAtMs: this.tickStartedAtMs,
      lastTickCompletedAtMs: this.lastTickCompletedAtMs,
      lastTickDurationMs: this.lastTickDurationMs,
      currentInstanceId: this.currentInstanceId,
      currentStage: this.currentStage,
      timeoutMs,
      healthMaxAgeMs,
      timeoutCount: this.executorTimeoutCount,
    });
    return {
      ...health,
      serviceRole: process.env.RELAY_EXECUTOR_WORKER === 'true' ? 'executor-worker' : 'public-api',
      ownerId: this.executorOwnerId,
      observedAt: new Date(nowMs).toISOString(),
      sourceRevision:
        process.env.RAILWAY_GIT_COMMIT_SHA ?? process.env.GIT_REVISION ?? process.env.SOURCE_GIT_REV ?? null,
      executionEnabled: executionEnabled(),
    };
  }

  private async watchExecutorLiveness() {
    const health = this.getHealthSnapshot();
    if (health.status !== 'STUCK' || this.watchdogHandling) return;
    this.watchdogHandling = true;
    this.executorTimeoutCount += 1;
    const reason =
      `Relay executor watchdog: tick exceeded ${health.timeoutMs}ms` +
      `${health.currentInstanceId ? ` at instance ${health.currentInstanceId}` : ''}` +
      `${health.currentStage ? ` stage ${health.currentStage}` : ''}. ` +
      'Live entries were disarmed; restart required.';
    this.logger.error(reason);
    let activeLiveInstanceCount: number | null = null;
    try {
      activeLiveInstanceCount = await this.failClosedStuckLiveInstances(reason);
    } catch (err) {
      this.logger.error(
        `Relay watchdog fail-closed persistence failed: ${err instanceof Error ? err.stack ?? err.message : err}`,
      );
    } finally {
      // A timed-out async operation cannot be cancelled safely in-process. Exit only
      // after durable fail-closed state is attempted so Railway restarts a clean executor
      // without allowing the abandoned Promise to submit a duplicate later.
      const restartRequired = relayWatchdogShouldRestart(
        activeLiveInstanceCount,
        process.env.RELAY_EXECUTOR_WORKER === 'true',
      );
      if (
        restartRequired &&
        process.env.NODE_ENV !== 'test' &&
        this.config.get('SUBSCRIBER_EXECUTOR_WATCHDOG_EXIT') !== 'false'
      ) {
        const timer = setTimeout(() => process.exit(1), 250);
        timer.unref();
      } else {
        if (!restartRequired) {
          this.logger.warn(
            'Relay executor is stuck but no ACTIVE Bitfinex relay exists; API remains online and all new live starts stay blocked until executor health recovers.',
          );
        }
        this.watchdogHandling = false;
      }
    }
  }

  private async failClosedStuckLiveInstances(reason: string): Promise<number> {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: AGENT_SLUG } });
    if (!agent) return 0;
    const instances = await this.prisma.tradingAgentInstance.findMany({
      where: {
        agentId: agent.id,
        exchangeProvider: 'bitfinex',
        status: TradingAgentInstanceStatus.ACTIVE,
      },
    });
    const nowIso = new Date().toISOString();
    for (const instance of instances) {
      const dash = (instance.dashboardState ?? {}) as Record<string, unknown>;
      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: {
          status: TradingAgentInstanceStatus.PAUSED,
          lastError: reason.slice(0, 500),
          dashboardState: applyDashboardPatch(dash, {
            relayExecutionMode: 'PAUSED',
            relayArmedAt: null,
            realTradingConfirmedAt: null,
            relayExecutor: {
              ...this.getHealthSnapshot(),
              healthy: false,
              status: 'STUCK',
              failClosedAt: nowIso,
              requiresExplicitRestart: true,
            },
          }) as unknown as Prisma.InputJsonValue,
        },
      });
      try {
        await Promise.race([
          this.cancelVerifiedUnfilledPendingEntries(
            agent.id,
            instance,
            'EXECUTOR_WATCHDOG_CANCELLED_UNFILLED',
            'Relay watchdog',
          ),
          new Promise<never>((_, reject) => {
            const timer = setTimeout(
              () => reject(new Error('watchdog pending-order cleanup timed out after 8000ms')),
              8_000,
            );
            timer.unref();
          }),
        ]);
      } catch (err) {
        this.logger.error(
          `Relay watchdog pending-order cleanup incomplete for ${instance.userId}: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }
    }
    return instances.length;
  }

  /**
   * Cancel only relay-managed entry orders that are proven to be resting and
   * completely unfilled. Partial/full fills and open positions are deliberately
   * left to restart reconciliation and normal exit management.
   */
  private async cancelVerifiedUnfilledPendingEntries(
    agentId: string,
    instance: TradingAgentInstance,
    auditReason: 'EXECUTOR_WATCHDOG_CANCELLED_UNFILLED' | 'LIVE_FIDELITY_GUARD_CANCELLED_UNFILLED',
    logLabel: string,
  ): Promise<number> {
    const creds = await this.exchanges.getUserCredentials(
      instance.userId,
      instance.exchangeProvider,
    );
    if (!creds) {
      throw new Error('EXCHANGE_CREDENTIALS_MISSING_DURING_PENDING_ENTRY_CLEANUP');
    }
    this.activeTrading = this.bitfinex;
    let cancelledCount = 0;
    const pending = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId: instance.userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId },
      },
      select: { id: true, cycleId: true },
    });
    for (const participant of pending) {
      const releaseMoneyLane = await this.acquireParticipantMoneyLane(participant.id);
      try {
      const [freshParticipant, freshCycle, meta] = await Promise.all([
        this.prisma.signalCycleParticipant.findUnique({
          where: { id: participant.id },
          select: { status: true, cycleId: true },
        }),
        this.prisma.signalCycle.findUnique({
          where: { id: participant.cycleId },
          select: { id: true },
        }),
        this.loadExecutionMeta(participant.id),
      ]);
      if (
        !freshParticipant
        || !freshCycle
        || freshParticipant.status !== SignalCycleStatus.PENDING_ENTRY
        || freshParticipant.cycleId !== participant.cycleId
      ) continue;
      const orderId = meta.bitfinexOrderId;
      if (!orderId) continue;
      const order = await this.activeTrading.findOrder(creds, orderId);
      if (!order) continue;
      const filledQty = Math.max(0, Math.abs(order.amountOrig) - Math.abs(order.amount));
      if (!relayEntryOrderIsCompletelyUnfilled(order)) {
        this.logger.warn(
          `${logLabel} left partially filled entry ${orderId} untouched ` +
            `participant=${participant.id} filledQty=${filledQty.toFixed(8)}; restart reconciliation required`,
        );
        continue;
      }
      const cancelled = await this.cancelManagedOrderGone(
        creds,
        orderId,
        `${logLabel} cancel verified-unfilled entry ${orderId} participant=${participant.id}`,
      );
      if (!cancelled.gone) {
        throw new Error(
          `CANCEL_FAILED_ORDER_STILL_LIVE order=${orderId} participant=${participant.id}`,
        );
      }
      if (
        cancelled.reason === 'NOT_FOUND'
        && !(await this.replacementTerminalUnfilledProof(creds, meta))
      ) continue;
      // Keep the participant-level audit truthful without closing the shared
      // showcase cycle: another subscriber may still be mirroring that same
      // source order. The UI can now distinguish watchdog cancellation from
      // ordinary TTL expiry and show both creation and cancellation times.
      await this.prisma.$transaction([
        this.prisma.signalCycleEvent.create({
          data: {
            cycleId: participant.cycleId,
            participantId: participant.id,
            eventType: 'EXPIRED',
            payload: {
              venue: 'bitfinex',
              reason: auditReason,
              event: auditReason,
              bitfinex_order_id: orderId,
              pnl_usd: 0,
              source: 'hire',
            },
          },
        }),
        this.prisma.signalCycleParticipant.update({
          where: { id: participant.id },
          data: {
            status: SignalCycleStatus.EXPIRED,
            pnlUsd: 0,
            settlementStatus: 'WAIVED',
            feeUsd: 0,
            settledAt: new Date(),
          },
        }),
      ]);
      cancelledCount += 1;
      this.logger.warn(
        `${logLabel} cancelled verified-unfilled entry ${orderId} ` +
          `participant=${participant.id} cycle=${participant.cycleId}`,
      );
      } finally {
        releaseMoneyLane();
      }
    }
    return cancelledCount;
  }


  /**
   * Public API cannot run money ticks (executionEnabled=false). Persist a wake
   * so the isolated relay-executor worker picks it up on the next poll / tick.
   */
  async requestExecutorWake(
    trigger: 'POSITION_CLOSED' | 'ORDER_EXPIRED' | 'POSITION_OPENED' | 'ORDER_PLACED' | 'APPROVE_PENDING' | 'LIMIT_UPDATED' | 'USER_RESUME' | 'USER_PAUSE',
    tradeId?: string | null,
    receivedAt?: string,
    signedTerminal?: RelayExecutorWakeRequest['signedClose'] | RelayExecutorWakeRequest['signedExpiry'],
  ): Promise<void> {
    if (executionEnabled()) {
      await this.wakeNow(trigger);
      return;
    }
    const payload: RelayExecutorWakeRequest = {
      trigger,
      // Preserve the ingress identity used by the pre-wake. The direct and
      // durable copies must have the same exact key so a successfully finished
      // pre-wake suppresses its dashboard fallback instead of executing the
      // same expensive exchange preflight twice.
      at: receivedAt && Number.isFinite(Date.parse(receivedAt))
        ? receivedAt
        : new Date().toISOString(),
      tradeId: tradeId ?? null,
      ...(trigger === 'POSITION_CLOSED' && signedTerminal ? { signedClose: signedTerminal as RelayExecutorWakeRequest['signedClose'] } : {}),
      ...(trigger === 'ORDER_EXPIRED' && signedTerminal ? { signedExpiry: signedTerminal as RelayExecutorWakeRequest['signedExpiry'] } : {}),
    };
    // The signed lifecycle event is already durable before this method is
    // queued. Start the authenticated private-network wake immediately; the
    // dashboard copy below remains the crash/restart fallback and must not sit
    // in front of the latency-sensitive dispatch.
    void this.dispatchDirectExecutorWake(payload);
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: AGENT_SLUG } });
    if (!agent) return;
    const instances = await this.prisma.tradingAgentInstance.findMany({
      where: {
        agentId: agent.id,
        exchangeProvider: 'bitfinex',
        status: { in: [TradingAgentInstanceStatus.ACTIVE, TradingAgentInstanceStatus.PAUSED] },
      },
      select: { id: true, dashboardState: true },
      take: 50,
    });
    for (const inst of instances) {
      const next = applyDashboardPatch(
        (inst.dashboardState ?? {}) as Record<string, unknown>,
        { [RELAY_EXECUTOR_WAKE_KEY]: payload },
      );
      await this.prisma.tradingAgentInstance
        .update({ where: { id: inst.id }, data: { dashboardState: next as object } })
        .catch(() => {});
    }
  }

  /**
   * Latency-only direct hint sent after HMAC + owner verification but before
   * the API's canonical Neon transaction completes. It is deliberately not a
   * durable wake. Entry still waits for the exact signed cycle in Neon; close
   * may converge only from the already-open owned lot plus the authenticated
   * canonical showcase state. The ordinary post-commit wake above remains the
   * crash/restart backstop.
   */
  requestExecutorPreWake(
    trigger: 'ORDER_PLACED' | 'POSITION_OPENED' | 'POSITION_CLOSED' | 'ORDER_EXPIRED',
    tradeId?: string | null,
    receivedAt?: string,
    signedTerminal?: RelayExecutorWakeRequest['signedClose'] | RelayExecutorWakeRequest['signedExpiry'],
  ): void {
    if (executionEnabled()) return;
    const payload: RelayExecutorWakeRequest = {
      trigger,
      at: receivedAt && Number.isFinite(Date.parse(receivedAt))
        ? receivedAt
        : new Date().toISOString(),
      tradeId: tradeId ?? null,
      ...(trigger === 'POSITION_CLOSED' && signedTerminal ? { signedClose: signedTerminal as RelayExecutorWakeRequest['signedClose'] } : {}),
      ...(trigger === 'ORDER_EXPIRED' && signedTerminal ? { signedExpiry: signedTerminal as RelayExecutorWakeRequest['signedExpiry'] } : {}),
    };
    void this.dispatchDirectExecutorWake(payload);
  }

  private async dispatchDirectExecutorWake(payload: RelayExecutorWakeRequest): Promise<void> {
    const base = process.env.RELAY_EXECUTOR_WAKE_URL?.trim().replace(/\/$/, '');
    const secret = process.env.BOT_CONTROL_SECRET?.trim();
    if (!base || !secret) return;
    try {
      const response = await fetch(`${base}/api/wake`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-bot-control-secret': secret,
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(1_500),
      });
      if (response.status !== 202 && response.status !== 409) {
        this.logger.warn(`Direct relay executor wake returned HTTP ${response.status}; durable wake retained`);
      }
    } catch (err) {
      this.logger.warn(
        `Direct relay executor wake unavailable; durable wake retained: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  async acceptDirectExecutorWake(wake: RelayExecutorWakeRequest): Promise<boolean> {
    if (!executionEnabled()) return false;
    if (this.fastWakeRunning) {
      if (wake.trigger === 'POSITION_OPENED') {
        this.startPrioritySourceFillWake(wake);
        return true;
      }
      this.enqueueDirectWake(wake);
      return true;
    }
    this.startDirectExecutorWake(wake);
    return true;
  }

  private enqueueDirectWake(wake: RelayExecutorWakeRequest): void {
    const logicalKey = this.executorWakeLogicalKey(wake);
    // ORDER_PLACED is intentionally sent twice: a latency-only pre-wake and a
    // durable post-commit wake. If the pre-wake is already executing, queuing
    // the latter repeats all Bitfinex/Neon preflight work and creates head-of-
    // line blocking for the first chase revision. The durable dashboard copy
    // remains the crash fallback, so dropping only this in-process duplicate
    // cannot lose the entry.
    if (
      (wake.trigger === 'ORDER_PLACED'
        || wake.trigger === 'POSITION_OPENED'
        || wake.trigger === 'POSITION_CLOSED'
        || wake.trigger === 'ORDER_EXPIRED')
      &&
      this.activeDirectWake
      && this.executorWakeLogicalKey(this.activeDirectWake) === logicalKey
    ) return;
    if (
      wake.trigger === 'POSITION_OPENED'
      && (this.prioritySourceFillWakes ?? (this.prioritySourceFillWakes = new Set<string>())).has(logicalKey)
    ) return;
    const key = this.executorWakeKey(wake);
    if (this.pendingDirectWakes.some((candidate) => this.executorWakeKey(candidate) === key)) {
      return;
    }
    // A burst of signed chase revisions needs only its newest exact limit.
    // Replacing an older queued LIMIT_UPDATED is safe because execution reads
    // the latest HMAC-verified envelope from Neon, and prevents obsolete
    // cancel/replace work from delaying the current source price.
    if (wake.trigger === 'LIMIT_UPDATED') {
      const existing = this.pendingDirectWakes.findIndex((candidate) =>
        candidate.trigger === 'LIMIT_UPDATED'
        && this.executorWakeLogicalKey(candidate) === logicalKey,
      );
      if (existing >= 0) {
        this.pendingDirectWakes[existing] = wake;
        return;
      }
    }
    // A verified source fill is terminal for its resting entry.  It must not
    // wait behind a queued reprice: doing so allows the regular reconciliation
    // pass to observe the source position first and take its slower fallback
    // missed-fill path.  Keep the first receipt for the same fill (the
    // pre-commit wake has the earliest causal timestamp), but run it before
    // any non-terminal queued work as soon as the current money operation
    // releases its participant lane.
    if (wake.trigger === 'POSITION_OPENED') {
      const existing = this.pendingDirectWakes.find((candidate) =>
        candidate.trigger === 'POSITION_OPENED'
        && this.executorWakeLogicalKey(candidate) === logicalKey,
      );
      if (existing) return;
      this.pendingDirectWakes.unshift(wake);
      if (this.pendingDirectWakes.length > 20) this.pendingDirectWakes.pop();
      return;
    }
    this.pendingDirectWakes.push(wake);
    if (this.pendingDirectWakes.length > 20) this.pendingDirectWakes.shift();
  }

  /**
   * The normal reconciliation must never terminalize a pending order via its
   * slower snapshot fallback while an authenticated exact source-fill wake is
   * already queued or executing for that same showcase trade.  The wake owns
   * the lower-latency, exchange-proven cancel-or-fill funnel.
   */
  private hasQueuedOrActiveSourceFillWake(tradeId: string | null | undefined): boolean {
    if (!tradeId) return false;
    const matches = (wake: RelayExecutorWakeRequest | null): boolean => {
      if (!wake || wake.trigger !== 'POSITION_OPENED' || !wake.tradeId) return false;
      return tradeIdsMatch(wake.tradeId, tradeId);
    };
    // A priority fill wake runs beside an unrelated direct wake while it waits
    // for the same participant's money lane.  It is deliberately not assigned
    // to activeDirectWake, so account for its in-flight logical key here as
    // well.  Otherwise the normal reconciliation can emit a slower
    // MISSED_SHOWCASE_FILL expiry during a concurrent reprice, defeating the
    // signed source-fill path before it receives the lane.
    const priorityFillRunning = Array.from(this.prioritySourceFillWakes ?? []).some((key) => {
      const prefix = 'POSITION_OPENED:';
      return key.startsWith(prefix) && tradeIdsMatch(key.slice(prefix.length), tradeId);
    });
    return matches(this.activeDirectWake)
      || (this.pendingDirectWakes ?? []).some((wake) => matches(wake))
      || priorityFillRunning;
  }

  private drainQueuedDirectWake(): void {
    if (this.fastWakeRunning) return;
    const next = this.pendingDirectWakes.shift();
    if (next) this.startDirectExecutorWake(next);
  }

  /**
   * Execute an exact signed source-fill wake alongside a non-terminal global
   * wake.  This deliberately does not bypass participantMoneyLane: if the
   * currently running wake owns the same order's replacement, the fill waits
   * only for that one atomic cancel/submit/persist operation, not for all
   * cached instances or subsequent queued work.
   */
  private startPrioritySourceFillWake(wake: RelayExecutorWakeRequest): void {
    const key = this.executorWakeLogicalKey(wake);
    const inFlight = this.prioritySourceFillWakes ?? (this.prioritySourceFillWakes = new Set<string>());
    if (inFlight.has(key)) return;
    if (
      this.activeDirectWake
      && this.executorWakeLogicalKey(this.activeDirectWake) === key
    ) return;
    inFlight.add(key);
    // The pre-wake is the earliest receipt.  Remove any queued durable copy
    // before starting it so a later drain cannot repeat the same cancel path.
    for (let index = this.pendingDirectWakes.length - 1; index >= 0; index -= 1) {
      if (this.executorWakeLogicalKey(this.pendingDirectWakes[index]) === key) {
        this.pendingDirectWakes.splice(index, 1);
      }
    }
    void this.executePersistedFastWake(wake)
      .then(() => {
        this.completedDirectWakeAt.set(this.executorWakeKey(wake), Date.now());
        if (this.running) this.wakeQueued = true;
        else setImmediate(() => void this.tick());
      })
      .catch((err) => {
        this.logger.warn(
          `Priority source-fill wake failed closed; durable wake retained: ${err instanceof Error ? err.message : err}`,
        );
        if (this.running) this.wakeQueued = true;
      })
      .finally(() => {
        inFlight.delete(key);
      });
  }

  private startDirectExecutorWake(wake: RelayExecutorWakeRequest): void {
    this.fastWakeRunning = true;
    this.activeDirectWake = wake;
    // Acknowledge the authenticated private wake immediately. The previous
    // implementation held the HTTP response open until Bitfinex completed,
    // so the API's 1.5s timeout reported a false failure while the order was
    // already being placed. Start the async work in this event-loop turn (it
    // yields on its first database/network await), so the HTTP handler can
    // still return 202 without adding another setImmediate turn to a close
    // whose contract is measured in milliseconds. The persisted dashboard
    // wake remains the crash/restart fallback.
    void this.executePersistedFastWake(wake)
      .then(() => {
        this.completedDirectWakeAt.set(this.executorWakeKey(wake), Date.now());
        if (this.running) this.wakeQueued = true;
        else setImmediate(() => void this.tick());
      })
      .catch((err) => {
        this.logger.warn(
          `Direct relay fast wake failed closed; durable wake retained: ${err instanceof Error ? err.message : err}`,
        );
        if (this.running) this.wakeQueued = true;
      })
      .finally(() => {
        this.activeDirectWake = null;
        this.fastWakeRunning = false;
        this.drainQueuedDirectWake();
      });
  }

  private executorWakeLogicalKey(wake: RelayExecutorWakeRequest): string {
    if (wake.trigger === 'ORDER_EXPIRED' && wake.signedExpiry) {
      return `${wake.trigger}:${wake.tradeId ?? ''}:${wake.signedExpiry.eventSeq}:${wake.signedExpiry.eventId}`;
    }
    return `${wake.trigger}:${wake.tradeId ?? ''}`;
  }

  private executorWakeKey(wake: RelayExecutorWakeRequest): string {
    if (wake.trigger === 'ORDER_EXPIRED' && wake.signedExpiry) {
      return `${wake.trigger}:${wake.tradeId ?? ''}:${wake.signedExpiry.eventSeq}:${wake.signedExpiry.eventId}`;
    }
    return `${wake.trigger}:${wake.tradeId ?? ''}:${wake.at}`;
  }

  private directWakeAlreadyCompleted(wake: RelayExecutorWakeRequest): boolean {
    const cutoff = Date.now() - 120_000;
    for (const [key, completedAt] of this.completedDirectWakeAt) {
      if (completedAt < cutoff) this.completedDirectWakeAt.delete(key);
    }
    return this.completedDirectWakeAt.has(this.executorWakeKey(wake));
  }

  private async consumePersistedExecutorWakes(): Promise<RelayExecutorWakeRequest | null> {
    if (!executionEnabled()) return null;
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: AGENT_SLUG } });
    if (!agent) return null;
    const instances = await this.prisma.tradingAgentInstance.findMany({
      where: {
        agentId: agent.id,
        exchangeProvider: 'bitfinex',
        status: { in: [TradingAgentInstanceStatus.ACTIVE, TradingAgentInstanceStatus.PAUSED] },
      },
      select: { id: true, dashboardState: true },
      take: 50,
    });
    let best: RelayExecutorWakeRequest | null = null;
    for (const inst of instances) {
      const wake = readRelayExecutorWakeRequest(inst.dashboardState);
      if (!wake) continue;
      const wakeMs = Date.parse(wake.at);
      if (!Number.isFinite(wakeMs) || Date.now() - wakeMs > 120_000) {
        const cleared = applyDashboardPatch(
          (inst.dashboardState ?? {}) as Record<string, unknown>,
          { [RELAY_EXECUTOR_WAKE_KEY]: null },
        );
        await this.prisma.tradingAgentInstance
          .update({ where: { id: inst.id }, data: { dashboardState: cleared as object } })
          .catch(() => {});
        continue;
      }
      if (!best || Date.parse(wake.at) >= Date.parse(best.at)) best = wake;
      const cleared = applyDashboardPatch(
        (inst.dashboardState ?? {}) as Record<string, unknown>,
        { [RELAY_EXECUTOR_WAKE_KEY]: null },
      );
      await this.prisma.tradingAgentInstance
        .update({ where: { id: inst.id }, data: { dashboardState: cleared as object } })
        .catch(() => {});
    }
    if (best) {
      this.lastShowcaseWakeAt = Date.parse(best.at) || Date.now();
      this.lastShowcaseWakeTrigger =
        best.trigger === 'POSITION_CLOSED' ? 'POSITION_CLOSED' : null;
    }
    return best;
  }

  /** Cross-process signed-webhook fast lane; safe to overlap the reconciliation tick. */
  private async pollPersistedFastWake(): Promise<void> {
    if (!executionEnabled() || this.fastWakeRunning) return;
    this.fastWakeRunning = true;
    try {
      const wake = await this.consumePersistedExecutorWakes();
      if (!wake) return;
      if (this.directWakeAlreadyCompleted(wake)) return;
      await this.executePersistedFastWake(wake);
      // Always queue the complete pass afterwards so fills, stops, dashboards,
      // and any non-fast-path ambiguity are reconciled authoritatively.
      if (this.running) this.wakeQueued = true;
      else setImmediate(() => void this.tick());
    } catch (err) {
      this.logger.warn(
        `Persisted relay fast wake failed closed: ${err instanceof Error ? err.message : err}`,
      );
      if (this.running) this.wakeQueued = true;
    } finally {
      this.fastWakeRunning = false;
      this.drainQueuedDirectWake();
    }
  }

  private async executePersistedFastWake(wake: RelayExecutorWakeRequest): Promise<void> {
    const startedAtMs = Date.now();
    const discoveredInstancesPromise = this.prisma.tradingAgentInstance.findMany({
      where: {
        agent: { slug: AGENT_SLUG },
        exchangeProvider: 'bitfinex',
        status: { in: [TradingAgentInstanceStatus.ACTIVE, TradingAgentInstanceStatus.PAUSED] },
      },
    });
    const processedInstanceIds = new Set<string>();
    const processInstances = async (instances: TradingAgentInstance[]): Promise<void> => {
      for (const instance of instances) {
        if (processedInstanceIds.has(instance.id)) continue;
        if (isCopyRelaySimActive(instance.dashboardState)) continue;
        this.activeTrading = this.bitfinex;
        if (wake.trigger === 'ORDER_EXPIRED') {
          const handled = await this.tryImmediateSignedOrderExpiry(instance, wake);
          await this.persistFastWakeTelemetry(
            instance.id, wake, startedAtMs,
            handled ? 'ORDER_EXPIRY_DISPATCHED' : 'ORDER_EXPIRY_NOT_ELIGIBLE',
          );
          processedInstanceIds.add(instance.id);
          continue;
        }
        if (wake.trigger === 'POSITION_CLOSED') {
          const [creds, liveLots] = await Promise.all([
            this.exchanges.getUserCredentials(instance.userId, instance.exchangeProvider),
            this.prisma.signalCycleParticipant.findMany({
              where: {
                userId: instance.userId,
                status: { in: [SignalCycleStatus.OPEN, SignalCycleStatus.PENDING_ENTRY] },
                cycle: { agentId: instance.agentId },
              },
              include: { cycle: true },
            }),
          ]);
          if (!creds) {
            await this.persistFastWakeTelemetry(instance.id, wake, startedAtMs, 'CREDENTIALS_MISSING');
            continue;
          }
          const openLots = liveLots.filter((lot) => lot.status === SignalCycleStatus.OPEN);
          const pendingLots = liveLots.filter((lot) => lot.status === SignalCycleStatus.PENDING_ENTRY);
          let exitAttempts = 0;
          for (const participant of openLots) {
            const meta = await this.loadExecutionMeta(participant.id);
            if (
              !persistedCloseWakeMatchesParticipant(
                wake.tradeId,
                participant.cycle.tradeId,
                meta.originTradeId,
              )
            ) continue;
            exitAttempts += 1;
            await this.tryImmediateShowcaseMirrorExit(
              instance.agentId,
              instance.userId,
              participant.cycle,
              participant,
              meta,
              creds,
              false,
              wake.signedClose,
            );
          }
          const pendingHandled = await this.tryImmediateSignedPendingClose(
            instance, wake, creds, pendingLots,
          );
          await this.persistFastWakeTelemetry(
            instance.id,
            wake,
            startedAtMs,
            exitAttempts > 0
              ? 'EXIT_DISPATCHED'
              : pendingHandled
                ? 'PENDING_ENTRY_TERMINATED'
                : 'NO_MATCHING_OPEN_LOT',
          );
          processedInstanceIds.add(instance.id);
          continue;
        }
        if (wake.trigger === 'ORDER_PLACED' && instance.status === TradingAgentInstanceStatus.ACTIVE) {
          const placed = await this.tryFreshSignedFlatEntry(
            instance.agentId,
            instance,
            wake.tradeId ?? undefined,
          );
          await this.persistFastWakeTelemetry(
            instance.id,
            wake,
            startedAtMs,
            placed ? 'ENTRY_PLACED' : 'ENTRY_NOT_ELIGIBLE',
          );
          if (placed) processedInstanceIds.add(instance.id);
        } else if (wake.trigger === 'ORDER_PLACED') {
          await this.persistFastWakeTelemetry(instance.id, wake, startedAtMs, 'ENTRY_SKIPPED_PAUSED');
        } else if (
          wake.trigger === 'LIMIT_UPDATED'
          && instance.status === TradingAgentInstanceStatus.ACTIVE
        ) {
          const repriced = await this.tryImmediateSignedLimitUpdate(
            instance.agentId,
            instance,
            wake.tradeId ?? undefined,
          );
          await this.persistFastWakeTelemetry(
            instance.id,
            wake,
            startedAtMs,
            repriced ? 'LIMIT_REPRICE_DISPATCHED' : 'LIMIT_REPRICE_NOT_ELIGIBLE',
          );
          if (repriced) processedInstanceIds.add(instance.id);
        } else if (wake.trigger === 'LIMIT_UPDATED') {
          await this.persistFastWakeTelemetry(
            instance.id,
            wake,
            startedAtMs,
            'LIMIT_REPRICE_SKIPPED_PAUSED',
          );
        } else if (
          wake.trigger === 'POSITION_OPENED'
          && instance.status === TradingAgentInstanceStatus.ACTIVE
        ) {
          const filled = await this.tryImmediateShowcaseFillReconcile(
            instance.agentId,
            instance,
            wake.tradeId ?? undefined,
            wake.at,
          );
          await this.persistFastWakeTelemetry(
            instance.id,
            wake,
            startedAtMs,
            filled ? 'FILL_RECORDED' : 'FILL_NOT_YET_VISIBLE',
          );
          if (filled) processedInstanceIds.add(instance.id);
        } else if (wake.trigger === 'POSITION_OPENED') {
          await this.persistFastWakeTelemetry(
            instance.id,
            wake,
            startedAtMs,
            'FILL_RECONCILE_SKIPPED_PAUSED',
          );
        }
      }
    };

    const cachedInstances = Array.from(this.relayInstanceCache.values());
    if (cachedInstances.length > 0) {
      await processInstances(cachedInstances);
    }
    const discoveredInstances = await discoveredInstancesPromise;
    for (const instance of discoveredInstances) {
      this.relayInstanceCache.set(instance.id, instance);
    }
    await processInstances(discoveredInstances);
  }

  private async tryImmediateSignedOrderExpiry(
    instance: TradingAgentInstance,
    wake: RelayExecutorWakeRequest,
  ): Promise<boolean> {
    const evidence = wake.signedExpiry;
    if (!wake.tradeId || !evidence) return false;
    const creds = await this.exchanges.getUserCredentials(instance.userId, instance.exchangeProvider);
    if (!creds) return false;
    const candidates = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId: instance.userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId: instance.agentId },
      },
      include: { cycle: true },
    });
    for (const candidate of candidates) {
      const release = await this.acquireParticipantMoneyLane(candidate.id);
      try {
        const participant = await this.prisma.signalCycleParticipant.findUnique({
          where: { id: candidate.id }, include: { cycle: true },
        });
        if (!participant || participant.status !== SignalCycleStatus.PENDING_ENTRY) continue;
        const meta = await this.loadExecutionMeta(participant.id);
        if (!persistedCloseWakeMatchesParticipant(wake.tradeId, participant.cycle.tradeId, meta.originTradeId)) continue;
        const intent = participant.cycle.intentEnvelope as SignalIntentEnvelope;
        const context = (intent?.context ?? {}) as Record<string, unknown>;
        const currentSeq = Number(context.showcase_event_seq ?? 0);
        const currentLimit = Number(intent?.entry?.exact_limit_price ?? meta.limitPrice ?? 0);
        // A stale expiry generation must never cancel a later replacement.
        if (currentSeq !== evidence.eventSeq || Math.abs(currentLimit - evidence.limitPrice) > 1e-8) continue;
        const fill = await this.detectEntryFillBeforeCancel(creds, meta).catch(() => null);
        if (fill) {
          await this.recordCancelRaceFill(
            instance.agentId, instance.userId, participant.cycle, participant.id,
            meta, creds, intent, fill, 'SHOWCASE_ORDER_EXPIRED',
            new Date(evidence.sourceExpiresAtMs).toISOString(),
          );
          return true;
        }
        if (!meta.bitfinexOrderId) return false;
        let cancelTiming: { submitStartedAtMs: number; exchangeAckAtMs: number; confirmedAtMs: number } | undefined;
        const cancel = await this.cancelManagedOrderGone(
          creds, meta.bitfinexOrderId,
          `Signed source expiry oid=${meta.bitfinexOrderId} trade=${wake.tradeId}`,
          (timing) => { cancelTiming = timing; },
        );
        if (!cancel.gone) return false;
        // Close the cancel race with fresh, explicit private evidence. A read
        // failure is UNKNOWN, never equivalent to an unfilled order.
        const postCancel = await this.classifyPostCancelEntry(creds, meta, cancel.reason);
        if (postCancel.kind === 'FILL') {
          await this.recordCancelRaceFill(
            instance.agentId, instance.userId, participant.cycle, participant.id,
            meta, creds, intent, postCancel.fill, 'SHOWCASE_ORDER_EXPIRED',
            new Date(evidence.sourceExpiresAtMs).toISOString(),
          );
          return true;
        }
        if (postCancel.kind === 'UNKNOWN') {
          const error = `SIGNED_EXPIRY_UNCONFIRMED ${postCancel.reason}`;
          await Promise.all([
            this.cycles.recordHireExecutionEvent(
              instance.userId, instance.agentId, participant.cycle.id, 'RECONCILE_CANCEL_FAILED', {
                venue: 'bitfinex', source: 'hire', event: 'SHOWCASE_ORDER_EXPIRED',
                reason: error, bitfinex_order_id: meta.bitfinexOrderId,
                showcase_event_id: evidence.eventId, showcase_event_seq: evidence.eventSeq,
              },
            ).catch(() => undefined),
            this.prisma.tradingAgentInstance.update({
              where: { id: instance.id }, data: { lastError: error.slice(0, 500) },
            }).catch(() => undefined),
          ]);
          return false;
        }
        if (!cancelTiming) return false;
        await this.cycles.recordHireExecutionEvent(
          instance.userId, instance.agentId, participant.cycle.id, 'EXPIRED', {
            venue: 'bitfinex', source: 'hire', event: 'SHOWCASE_ORDER_EXPIRED',
            reason: evidence.reason, bitfinex_order_id: meta.bitfinexOrderId,
            showcase_event_id: evidence.eventId, showcase_event_seq: evidence.eventSeq,
            source_expires_at: new Date(evidence.sourceExpiresAtMs).toISOString(),
            platform_received_at: new Date(evidence.platformReceivedAtMs).toISOString(),
            cancel_submit_started_at: new Date(cancelTiming.submitStartedAtMs).toISOString(),
            cancel_exchange_ack_at: new Date(cancelTiming.exchangeAckAtMs).toISOString(),
            cancel_confirmed_at: new Date(cancelTiming.confirmedAtMs).toISOString(),
            source_to_cancel_ack_ms: cancelTiming.exchangeAckAtMs - evidence.sourceExpiresAtMs,
            platform_to_cancel_ack_ms: cancelTiming.exchangeAckAtMs - evidence.platformReceivedAtMs,
            cancel_submit_to_ack_ms: cancelTiming.exchangeAckAtMs - cancelTiming.submitStartedAtMs,
          },
        );
        return true;
      } finally { release(); }
    }
    return false;
  }

  private async tryImmediateSignedPendingClose(
    instance: TradingAgentInstance,
    wake: RelayExecutorWakeRequest,
    creds: ExchangeCredentials,
    prefetchedCandidates?: Array<{ id: string }>,
  ): Promise<boolean> {
    if (!wake.tradeId || !wake.signedClose) return false;
    const candidates = prefetchedCandidates ?? await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId: instance.userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId: instance.agentId },
      },
      include: { cycle: true },
    });
    for (const candidate of candidates) {
      const release = await this.acquireParticipantMoneyLane(candidate.id);
      try {
        const participant = await this.prisma.signalCycleParticipant.findUnique({
          where: { id: candidate.id }, include: { cycle: true },
        });
        if (!participant || participant.status !== SignalCycleStatus.PENDING_ENTRY) continue;
        const meta = await this.loadExecutionMeta(participant.id);
        if (!persistedCloseWakeMatchesParticipant(
          wake.tradeId, participant.cycle.tradeId, meta.originTradeId,
        )) continue;
        const intent = participant.cycle.intentEnvelope as SignalIntentEnvelope;
        let fill: Awaited<ReturnType<typeof this.detectEntryFillBeforeCancel>>;
        try {
          fill = await this.detectEntryFillBeforeCancel(creds, meta, true);
        } catch {
          await this.cycles.recordHireExecutionEvent(
            instance.userId, instance.agentId, participant.cycle.id, 'RECONCILE_CANCEL_FAILED', {
              venue: 'bitfinex', source: 'hire', event: 'SHOWCASE_CYCLE_CLOSED',
              reason: 'PRE_CANCEL_EXCHANGE_READ_UNAVAILABLE', bitfinex_order_id: meta.bitfinexOrderId,
            },
          ).catch(() => undefined);
          return false;
        }
        if (fill) {
          return await this.recordCancelRaceFill(
            instance.agentId, instance.userId, participant.cycle, participant.id,
            meta, creds, intent, fill, 'SHOWCASE_CYCLE_CLOSED',
            new Date(wake.signedClose.sourceEventAtMs!).toISOString(),
          );
        }
        if (!meta.bitfinexOrderId) return false;
        let cancelTiming: { submitStartedAtMs: number; exchangeAckAtMs: number; confirmedAtMs: number } | undefined;
        const cancel = await this.cancelManagedOrderGone(
          creds, meta.bitfinexOrderId,
          `Signed showcase close pending oid=${meta.bitfinexOrderId} trade=${wake.tradeId}`,
          (timing) => { cancelTiming = timing; },
        );
        if (!cancel.gone) {
          await this.cycles.recordHireExecutionEvent(
            instance.userId, instance.agentId, participant.cycle.id, 'RECONCILE_CANCEL_FAILED', {
              venue: 'bitfinex', source: 'hire', event: 'SHOWCASE_CYCLE_CLOSED',
              reason: cancel.reason ?? 'CANCEL_UNCONFIRMED', bitfinex_order_id: meta.bitfinexOrderId,
            },
          ).catch(() => undefined);
          return false;
        }
        const postCancel = await this.classifyPostCancelEntry(creds, meta, cancel.reason);
        if (postCancel.kind === 'FILL') {
          return await this.recordCancelRaceFill(
            instance.agentId, instance.userId, participant.cycle, participant.id,
            meta, creds, intent, postCancel.fill, 'SHOWCASE_CYCLE_CLOSED',
            new Date(wake.signedClose.sourceEventAtMs!).toISOString(),
          );
        }
        if (postCancel.kind === 'UNKNOWN' || !cancelTiming) {
          await this.cycles.recordHireExecutionEvent(
            instance.userId, instance.agentId, participant.cycle.id, 'RECONCILE_CANCEL_FAILED', {
              venue: 'bitfinex', source: 'hire', event: 'SHOWCASE_CYCLE_CLOSED',
              reason: postCancel.kind === 'UNKNOWN' ? postCancel.reason : 'CANCEL_TIMING_MISSING',
              bitfinex_order_id: meta.bitfinexOrderId,
            },
          ).catch(() => undefined);
          return false;
        }
        await this.cycles.recordHireExecutionEvent(
          instance.userId, instance.agentId, participant.cycle.id, 'EXPIRED', {
            venue: 'bitfinex', source: 'hire', event: 'SHOWCASE_CYCLE_CLOSED',
            reason: 'SHOWCASE_CLOSED_BEFORE_COPY_FILL', bitfinex_order_id: meta.bitfinexOrderId,
            source_close_at: new Date(wake.signedClose.sourceEventAtMs!).toISOString(),
            platform_received_at: new Date(wake.signedClose.platformReceivedAtMs!).toISOString(),
            cancel_submit_started_at: new Date(cancelTiming.submitStartedAtMs).toISOString(),
            cancel_exchange_ack_at: new Date(cancelTiming.exchangeAckAtMs).toISOString(),
            cancel_confirmed_at: new Date(cancelTiming.confirmedAtMs).toISOString(),
            source_to_cancel_ack_ms: cancelTiming.exchangeAckAtMs - wake.signedClose.sourceEventAtMs!,
            platform_to_cancel_ack_ms: cancelTiming.exchangeAckAtMs - wake.signedClose.platformReceivedAtMs!,
            cancel_submit_to_ack_ms: cancelTiming.exchangeAckAtMs - cancelTiming.submitStartedAtMs,
          },
        );
        // A no-fill participant expires, but the canonical source lifecycle is
        // a close. Pre-wake may beat platform persistence, so preserve that
        // source terminal state explicitly instead of leaving the cycle EXPIRED.
        await this.prisma.signalCycle.update({
          where: { id: participant.cycle.id },
          data: { status: SignalCycleStatus.CLOSED, closedAt: new Date() },
        });
        return true;
      } finally { release(); }
    }
    return false;
  }

  private async classifyPostCancelEntry(
    creds: ExchangeCredentials,
    meta: ExecutionPayload,
    cancelReason?: string,
  ): Promise<
    | { kind: 'FILL'; fill: { filledQty: number; fillPrice: number; source: 'ORDER_PARTIAL' | 'POSITION_DELTA'; orderResting: boolean } }
    | { kind: 'PROVEN_UNFILLED' }
    | { kind: 'UNKNOWN'; reason: string }
  > {
    const orderId = meta.bitfinexOrderId;
    if (!orderId || !meta.direction) return { kind: 'UNKNOWN', reason: 'MISSING_ORDER_OWNERSHIP' };
    let trades: Array<{ execAmount: number; execPrice: number }>;
    try {
      trades = await this.bitfinex.fetchOrderTrades(creds, orderId);
    } catch {
      return { kind: 'UNKNOWN', reason: 'ORDER_TRADES_UNAVAILABLE' };
    }
    const executions = trades.filter((trade) => Math.abs(trade.execAmount) > 0);
    if (executions.length > 0) {
      const filledQty = executions.reduce((sum, trade) => sum + Math.abs(trade.execAmount), 0);
      const notional = executions.reduce((sum, trade) => sum + Math.abs(trade.execAmount) * trade.execPrice, 0);
      return { kind: 'FILL', fill: {
        filledQty, fillPrice: notional / filledQty,
        source: 'ORDER_PARTIAL', orderResting: false,
      } };
    }
    let position: { amount: number; basePrice: number } | null;
    try {
      position = await this.activeTrading.getOpenPositionDetail(creds);
    } catch {
      return { kind: 'UNKNOWN', reason: 'POSITION_UNAVAILABLE' };
    }
    const sameDirection = Boolean(position && (
      (meta.direction === 'LONG' && position.amount > 0)
      || (meta.direction === 'SHORT' && position.amount < 0)
    ));
    if (sameDirection && position) {
      const baseline = meta.exchangeQtyAtOrder ?? 0;
      const lotQty = meta.qty ?? MIN_QTY_BTC;
      // Any exchange-representable attributable increase is a fill. The old
      // 85% heuristic could erase a small partial execution after cancel.
      const attributableDeltaSats = Math.max(
        0,
        btcToSats(Math.abs(position.amount)) - btcToSats(baseline),
      );
      if (attributableDeltaSats > 1) {
        const fillPrice = meta.limitPrice && meta.limitPrice > 0
          ? meta.limitPrice
          : position.basePrice;
        if (!(fillPrice > 0)) return { kind: 'UNKNOWN', reason: 'FILL_PRICE_UNAVAILABLE' };
        return { kind: 'FILL', fill: {
          filledQty: Math.min(lotQty, satsToBtc(attributableDeltaSats)),
          fillPrice,
          source: 'POSITION_DELTA', orderResting: false,
        } };
      }
    }
    if (cancelReason === 'NOT_FOUND' && meta.replacementExchangeAckAtMs) {
      try {
        const history = await this.bitfinex.fetchOrderHistoryEvidence(creds, orderId);
        if (!history || history.terminal !== true || history.filledQty !== 0) {
          return { kind: 'UNKNOWN', reason: 'ORDER_HISTORY_NOT_TERMINAL_UNFILLED' };
        }
      } catch {
        return { kind: 'UNKNOWN', reason: 'ORDER_HISTORY_UNAVAILABLE' };
      }
    }
    return { kind: 'PROVEN_UNFILLED' };
  }

  /**
   * A source paper fill retires its exact pending mirror immediately. Private
   * Bitfinex state remains the only fill authority: a pre-cancel check and the
   * post-cancel tri-state classifier either promote a real execution or prove
   * the order was unfilled before it can be expired.
   */
  private async tryImmediateShowcaseFillReconcile(
    agentId: string,
    instance: TradingAgentInstance,
    preferredTradeId: string | undefined,
    sourceFillAt: string,
  ): Promise<boolean> {
    if (!preferredTradeId || instance.status !== TradingAgentInstanceStatus.ACTIVE) return false;
    if (isCopyRelaySimActive(instance.dashboardState)) return false;
    const candidates = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId: instance.userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId, status: SignalCycleStatus.PENDING_ENTRY },
      },
      include: { cycle: true },
      orderBy: { createdAt: 'desc' },
      take: 20,
    });
    const participant = candidates.find((candidate) =>
      tradeIdsMatch(candidate.cycle.tradeId, preferredTradeId),
    );
    if (!participant || !tradeIdsMatch(participant.cycle.tradeId, preferredTradeId)) return false;
    const creds = await this.exchanges.getUserCredentials(
      instance.userId,
      instance.exchangeProvider,
    );
    if (!creds) return false;

    const releaseMoneyLane = await this.acquireParticipantMoneyLane(participant.id);
    try {
    const [freshParticipant, meta] = await Promise.all([
      this.prisma.signalCycleParticipant.findUnique({
        where: { id: participant.id },
        include: { cycle: true },
      }),
      this.loadExecutionMeta(participant.id),
    ]);
    if (
      !freshParticipant
      || freshParticipant.status !== SignalCycleStatus.PENDING_ENTRY
      || freshParticipant.cycle.status !== SignalCycleStatus.PENDING_ENTRY
      || !tradeIdsMatch(freshParticipant.cycle.tradeId, preferredTradeId)
      || !meta.bitfinexOrderId
      || !meta.direction
    ) return false;

    // An authenticated source fill normally retires its exact resting copy
    // immediately.  The opt-in late-entry policy is the one exception: when
    // the *same* showcase trade is still OPEN, retain this exact owned order
    // and cap it at a no-worse entry instead of letting the fast wake bypass
    // the continuation branch in monitorEntry.
    if (lateEntryContinuationEnabled() && this.botBridge.isEnabled()) {
      const botState = await this.fetchExecutionBotState().catch(() => null);
      const showcasePosition = (botState?.positions ?? []).find((position) =>
        tradeIdsMatch(String(position.trade_id ?? ''), freshParticipant.cycle.tradeId),
      );
      const showcaseFill = Number(showcasePosition?.entry ?? 0);
      const retainLateEntry = shouldRetainLateEntryContinuation({
        enabled: true,
        showcaseTradeOpen: !!showcasePosition && Number.isFinite(showcaseFill) && showcaseFill > 0,
        participantStatus: freshParticipant.status,
        hasManagedOrder: true,
      });
      if (retainLateEntry) {
        const lateEntryStartedAtMs = Date.now();
        const continuedMeta: ExecutionPayload = {
          ...meta,
          lateEntryContinuation: true,
          lateEntryShowcaseFill: showcaseFill,
          lateEntryStartedAtMs,
        };
        if (
          !meta.lateEntryContinuation
          || Math.abs(Number(meta.lateEntryShowcaseFill ?? 0) - showcaseFill) >= 0.01
        ) {
          await this.cycles.recordHireExecutionEvent(
            instance.userId,
            agentId,
            freshParticipant.cycle.id,
            'UPDATE_STOPS',
            {
              venue: 'bitfinex',
              source: 'hire',
              event: 'LATE_ENTRY_BETTER_ONLY_CONTINUATION',
              trade_id: freshParticipant.cycle.tradeId,
              bitfinex_order_id: meta.bitfinexOrderId,
              lateEntryContinuation: true,
              lateEntryShowcaseFill: showcaseFill,
              lateEntryStartedAtMs,
              late_entry_continuation: true,
              late_entry_showcase_fill: showcaseFill,
              late_entry_started_at: new Date(lateEntryStartedAtMs).toISOString(),
              source_fill_at: sourceFillAt,
            },
          );
        }
        const cappedLimit = capRelayLimitAtShowcaseFill(
          meta.direction,
          meta.limitPrice ?? showcaseFill,
          showcaseFill,
        );
        if (meta.limitPrice && Math.abs(cappedLimit - meta.limitPrice) >= 0.01) {
          const mark = await this.activeTrading.getMarkPrice();
          await this.replaceRestingLimitOwned(
            agentId,
            instance.userId,
            freshParticipant.cycle.id,
            freshParticipant.id,
            continuedMeta,
            creds,
            freshParticipant.cycle.intentEnvelope as SignalIntentEnvelope,
            {
              newLimit: cappedLimit,
              mark,
              now: Date.now(),
              chaseLabel: `showcase-fill-cap=${showcaseFill.toFixed(2)}`,
              event: 'BOT_ANCHOR_CHASE',
              tradeId: freshParticipant.cycle.tradeId,
            },
          );
        }
        return true;
      }
    }

    // A source POSITION_OPENED is terminal for its resting entry generation.
    // Do one authoritative private check, then retire the exact current order
    // immediately. Waiting through a multi-second poll window leaves a copy
    // order executable after the showcase is already filled, and was the
    // direct cause of cont-7c1b47001742's 12s missed-fill cleanup.
    const fill = await this.detectEntryFillBeforeCancel(creds, meta, true)
      .catch(() => null);
    if (fill) {
      return this.recordCancelRaceFill(
          agentId,
          instance.userId,
          freshParticipant.cycle,
          freshParticipant.id,
          meta,
          creds,
          freshParticipant.cycle.intentEnvelope as SignalIntentEnvelope,
          fill,
          'SHOWCASE_POSITION_OPENED_WAKE',
          sourceFillAt,
      );
    }
    let cancelTiming: { submitStartedAtMs: number; exchangeAckAtMs: number; confirmedAtMs: number } | undefined;
    const cancel = await this.cancelManagedOrderGone(
      creds,
      meta.bitfinexOrderId,
      `Signed showcase fill pending oid=${meta.bitfinexOrderId} trade=${preferredTradeId}`,
      (timing) => { cancelTiming = timing; },
    );
    if (!cancel.gone) {
      await this.cycles.recordHireExecutionEvent(
        instance.userId,
        agentId,
        freshParticipant.cycle.id,
        'RECONCILE_CANCEL_FAILED',
        {
          venue: 'bitfinex',
          source: 'hire',
          event: 'MISSED_SHOWCASE_FILL',
          reason: cancel.reason ?? 'CANCEL_UNCONFIRMED',
          bitfinex_order_id: meta.bitfinexOrderId,
        },
      ).catch(() => undefined);
      return false;
    }
    const postCancel = await this.classifyPostCancelEntry(creds, meta, cancel.reason);
    if (postCancel.kind === 'FILL') {
      return this.recordCancelRaceFill(
        agentId,
        instance.userId,
        freshParticipant.cycle,
        freshParticipant.id,
        meta,
        creds,
        freshParticipant.cycle.intentEnvelope as SignalIntentEnvelope,
        postCancel.fill,
        'SHOWCASE_POSITION_OPENED_WAKE',
        sourceFillAt,
      );
    }
    if (postCancel.kind === 'UNKNOWN' || !cancelTiming) {
      await this.cycles.recordHireExecutionEvent(
        instance.userId,
        agentId,
        freshParticipant.cycle.id,
        'RECONCILE_CANCEL_FAILED',
        {
          venue: 'bitfinex',
          source: 'hire',
          event: 'MISSED_SHOWCASE_FILL',
          reason: postCancel.kind === 'UNKNOWN' ? postCancel.reason : 'CANCEL_TIMING_MISSING',
          bitfinex_order_id: meta.bitfinexOrderId,
        },
      ).catch(() => undefined);
      return false;
    }
    await this.cycles.recordHireExecutionEvent(
      instance.userId,
      agentId,
      freshParticipant.cycle.id,
      'EXPIRED',
      {
        venue: 'bitfinex',
        source: 'hire',
        event: 'MISSED_SHOWCASE_FILL',
        reason: 'SHOWCASE_FILLED_BEFORE_COPY_FILL',
        bitfinex_order_id: meta.bitfinexOrderId,
        source_fill_at: sourceFillAt,
        cancel_submit_started_at: new Date(cancelTiming.submitStartedAtMs).toISOString(),
        cancel_exchange_ack_at: new Date(cancelTiming.exchangeAckAtMs).toISOString(),
        cancel_confirmed_at: new Date(cancelTiming.confirmedAtMs).toISOString(),
        platform_to_cancel_ack_ms: cancelTiming.exchangeAckAtMs - Date.parse(sourceFillAt),
        cancel_submit_to_ack_ms: cancelTiming.exchangeAckAtMs - cancelTiming.submitStartedAtMs,
      },
    );
    void this.cancelPhantomShowcasePosition(
      instance.userId,
      agentId,
      freshParticipant.cycle.id,
      freshParticipant.cycle.tradeId,
      'SHOWCASE_FILLED_BEFORE_COPY_FILL',
    );
    return true;
    } finally {
      releaseMoneyLane();
    }
  }

  /**
   * Reprice one already-owned resting entry directly from a durable signed
   * LIMIT_UPDATED revision. This is deliberately narrower than a full tick:
   * it cannot create a participant or a new position, requires the exact
   * trade id, and delegates cancel-race/fill safety to replaceRestingLimit.
   */
  private async tryImmediateSignedLimitUpdate(
    agentId: string,
    instance: TradingAgentInstance,
    preferredTradeId?: string,
  ): Promise<boolean> {
    if (!preferredTradeId) return false;
    if (instance.status !== TradingAgentInstanceStatus.ACTIVE) return false;
    if (isCopyRelaySimActive(instance.dashboardState)) return false;
    if (intentMirrorKillSwitchActive() || intentMirrorDryRunActive(instance)) return false;

    const participants = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId: instance.userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: {
          agentId,
          status: SignalCycleStatus.PENDING_ENTRY,
        },
      },
      include: { cycle: true },
      take: 20,
    });
    const participant = participants.find((candidate) =>
      tradeIdsMatch(candidate.cycle.tradeId, preferredTradeId),
    );
    if (!participant) return false;

    const intent = participant.cycle.intentEnvelope as SignalIntentEnvelope;
    const signedLimit = readFreshSignedShowcaseExactLimit(
      participant.cycle.tradeId,
      intent,
    );
    if (!signedLimit) return false;

    const meta = await this.loadExecutionMeta(participant.id);
    if (!meta.bitfinexOrderId || !meta.direction || !meta.limitPrice) return false;
    const creds = await this.exchanges.getUserCredentials(
      instance.userId,
      instance.exchangeProvider,
    );
    if (!creds) return false;

    const mark = await this.activeTrading.getMarkPrice().catch(() => null);
    if (mark == null || !Number.isFinite(mark) || mark <= 0) return false;
    const newLimit = sanitizeLimitPrice(mark, signedLimit.limitPrice, meta.direction);
    if (newLimit == null || Math.abs(newLimit - meta.limitPrice) < 0.01) return false;

    // Do not fetch the canonical bot snapshot here. The HMAC-verified revision
    // in Neon is the newest exact source limit and a cross-region snapshot may
    // still contain the preceding anchor. replaceRestingLimit independently
    // proves the managed order is live and unfilled before cancel+replace.
    await this.replaceRestingLimit(
      agentId,
      instance.userId,
      participant.cycleId,
      participant.id,
      meta,
      creds,
      intent,
      {
        newLimit,
        mark,
        now: Date.now(),
        chaseLabel: `signed-limit=${signedLimit.limitPrice.toFixed(2)}`,
        event: 'BOT_ANCHOR_CHASE',
        tradeId: participant.cycle.tradeId,
      },
    );
    return true;
  }

  private async persistFastWakeTelemetry(
    instanceId: string,
    wake: RelayExecutorWakeRequest,
    startedAtMs: number,
    outcome: string,
  ): Promise<void> {
    // The fast-wake SLA ends when the exchange action returns.  Reading and
    // persisting dashboard telemetry is observability work and must not make a
    // completed order/replace/close look slower than it actually was.
    const completedAt = new Date(Date.now());
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instanceId },
      select: { dashboardState: true },
    });
    if (!fresh) return;
    const persistedAtMs = Date.parse(wake.at);
    const latencyMs = Number.isFinite(persistedAtMs)
      ? Math.max(0, completedAt.getTime() - persistedAtMs)
      : null;
    await this.prisma.tradingAgentInstance.update({
      where: { id: instanceId },
      data: {
        dashboardState: applyDashboardPatch(
          (fresh.dashboardState ?? {}) as Record<string, unknown>,
          {
            relayExecutorFastWake: {
              trigger: wake.trigger,
              tradeId: wake.tradeId ?? null,
              persistedAt: wake.at,
              startedAt: new Date(startedAtMs).toISOString(),
              completedAt: completedAt.toISOString(),
              latencyMs,
              outcome,
            },
          },
        ) as unknown as Prisma.InputJsonValue,
      },
    });
  }

  /** Immediate execution wake from showcase bot push (coalesced if tick in flight). */
  async wakeNow(trigger?: 'POSITION_CLOSED' | 'ORDER_EXPIRED' | 'POSITION_OPENED' | 'ORDER_PLACED' | 'APPROVE_PENDING' | 'LIMIT_UPDATED' | 'USER_RESUME' | 'USER_PAUSE') {
    if (!executionEnabled()) return;
    if (trigger) {
      this.lastShowcaseWakeAt = Date.now();
      this.lastShowcaseWakeTrigger =
        trigger === 'POSITION_CLOSED'
          ? 'POSITION_CLOSED'
          : null; // USER_RESUME / USER_PAUSE / etc are not showcase webhook events
    }
    if (this.running) {
      this.wakeQueued = true;
      return;
    }
    await this.tick();
  }

  /**
   * F7 — Read and clear the most-recent wake trigger. Called from
   * tryImmediateShowcaseMirrorExit so the audit event can be tagged with
   * SHOWCASE_CLOSED_WEBHOOK (fast path, <2s typical) or SHOWCASE_CLOSED_POLL
   * (regular 2s cadence). Returns null when the wake was >10s ago (stale —
   * treat as poll-driven).
   */
  private consumeWakeTrigger(): 'WEBHOOK' | 'POLL' {
    const ts = this.lastShowcaseWakeAt;
    const trig = this.lastShowcaseWakeTrigger;
    this.lastShowcaseWakeAt = 0;
    this.lastShowcaseWakeTrigger = null;
    if (ts && trig === 'POSITION_CLOSED' && Date.now() - ts < 10_000) return 'WEBHOOK';
    return 'POLL';
  }

  private async tick() {
    if (!executionEnabled() || this.running) return;
    this.running = true;
    this.tickStartedAtMs = Date.now();
    this.currentStage = 'LOAD_AGENT';
    try {
      const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: AGENT_SLUG } });
      if (!agent) return;

      // Hire expiry (expiresAt) gates LIVE COPY only. The relay sim is the free $20
      // dress rehearsal that must run WITHOUT a paid rental so people can test the real
      // Bitfinex API lifecycle (place/fill/manage/close) before hiring for live copy.
      // So we fetch all ACTIVE/PAUSED bitfinex instances here and skip expired ones ONLY
      // for live copy (simActive=false) inside the loop -- sim instances run regardless
      // of hire expiry.
      const now = Date.now();
      const instances = await this.prisma.tradingAgentInstance.findMany({
        where: {
          agentId: agent.id,
          status: {
            in: [TradingAgentInstanceStatus.ACTIVE, TradingAgentInstanceStatus.PAUSED],
          },
          exchangeProvider: { not: 'paper' },
        },
      });
      const discoveredIds = new Set(instances.map((instance) => instance.id));
      for (const cachedId of this.relayInstanceCache.keys()) {
        if (!discoveredIds.has(cachedId)) this.relayInstanceCache.delete(cachedId);
      }
      for (const instance of instances) {
        if (instance.exchangeProvider === 'bitfinex') {
          this.relayInstanceCache.set(instance.id, instance);
        }
      }

      for (const row of instances) {
        this.currentInstanceId = row.id;
        this.currentStage = 'LOAD_INSTANCE';
        const instance =
          (await this.prisma.tradingAgentInstance.findUnique({ where: { id: row.id } })) ?? row;
        if (instance.exchangeProvider !== 'bitfinex') continue;
        this.relayInstanceCache.set(instance.id, instance);
        const simActive = isCopyRelaySimActive(instance.dashboardState);

        // Live copy hire expiry blocks NEW entries only. OPEN / PENDING risk must
        // still run exit-only processInstance or Bitfinex orphans after showcase close
        // (cont-ffe6d1689ec2: hire expired 06:10 UTC, POSITION_CLOSED 06:51, BF stayed open).
        if (hireExpiryRequiresExitOnlyProcessing(instance.expiresAt, simActive, now)) {
          // Fix E — do not skip silently: the user's live copy is halted and
          // they should see why. Throttled to once per hour per instance.
          await this.surfaceExpiredHire(instance).catch(() => {
            /* surfacing is best-effort — never abort the tick */
          });
          await this.resetLiveFidelityGuardWithoutEvidence(
            instance,
            'HIRE_EXPIRED',
          ).catch(() => {
            /* reset is best-effort; entry path remains blocked */
          });
          try {
            this.activeTrading = this.bitfinex;
            this.currentStage = 'PROCESS_EXPIRED_HIRE_EXIT_ONLY';
            await this.processInstance(agent.id, instance, false, { forceExitOnly: true });
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            this.logger.error(`Expired-hire exit-only ${instance.userId}: ${msg}`);
            await this.prisma.tradingAgentInstance.update({
              where: { id: instance.id },
              data: { lastError: msg.slice(0, 500) },
            });
          }
          continue;
        }

        if (simActive) {
          if (instance.status === TradingAgentInstanceStatus.ACTIVE) {
            await this.prisma.tradingAgentInstance.update({
              where: { id: instance.id },
              data: {
                status: TradingAgentInstanceStatus.PAUSED,
                lastError: 'Relay sim active — real Bitfinex API testing mode (1 order · $20 · 100x cap).',
              },
            });
          }
          try {
            // Sim mode = REAL Bitfinex API, not a paper book. Purpose: prove the live order
            // pipeline (place / cancel / fill / merge) end-to-end with real money but tightly
            // capped — max 1 concurrent position, $20 margin, 100x leverage (the subscriber
            // defaults). Once the trader has seen a full lifecycle, they stop sim and resume
            // live copy for real trading. The real exchange position is the source of truth;
            // reconcileLotLedger reads it directly via this.activeTrading.
            this.activeTrading = this.bitfinex;
            this.currentStage = 'PROCESS_SIM_INSTANCE';
            await this.processInstance(agent.id, instance, true);
            this.currentStage = 'PERSIST_SIM_STATE';
            await this.persistSimTickState(agent.id, instance);
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            this.logger.error(`Sim relay ${instance.userId}: ${msg}`);
            await this.prisma.tradingAgentInstance.update({
              where: { id: instance.id },
              data: { lastError: msg.slice(0, 500) },
            });
          }
          continue;
        }

        try {
          this.activeTrading = this.bitfinex;
          this.currentStage = 'FAST_SIGNED_FLAT_PREFLIGHT';
          await this.tryFreshSignedFlatEntry(agent.id, instance).catch((err) => {
            this.logger.warn(
              `[SIGNED-FLAT-FAST] preflight failed closed ${instance.userId}: ${
                err instanceof Error ? err.message : err
              }`,
            );
          });
          this.currentStage = 'PROCESS_LIVE_INSTANCE';
          await this.processInstance(agent.id, instance, false);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          this.logger.error(`Instance ${instance.userId}: ${msg}`);
          await this.prisma.tradingAgentInstance.update({
            where: { id: instance.id },
            data: { lastError: msg.slice(0, 500) },
          });
        }
      }
    } catch (err) {
      // Fix F — a throw ABOVE the per-instance loops (agent/instance lookup)
      // aborts the WHOLE tick for every user. Previously this surfaced only as
      // an unhandled promise rejection with no context — fail loud instead.
      this.logger.error(
        `Subscriber execution FULL TICK FAILED (all instances skipped this cycle): ${
          err instanceof Error ? (err.stack ?? err.message) : String(err)
        }`,
      );
    } finally {
      const completedAt = Date.now();
      this.lastTickDurationMs = Math.max(0, completedAt - this.tickStartedAtMs);
      this.lastTickCompletedAtMs = completedAt;
      this.tickStartedAtMs = 0;
      this.currentInstanceId = null;
      this.currentStage = null;
      this.running = false;
      if (this.wakeQueued) {
        this.wakeQueued = false;
        setImmediate(() => void this.tick());
      }
    }
  }

  /**
   * Fix E — surface an expired hire instead of silently skipping the instance.
   * Sets lastError + warn log once per hour per instance (in-memory throttle),
   * and sends a one-off user notification (skipped when lastError already
   * carries the expiry notice, so users are not re-notified every restart).
   */
  private async surfaceExpiredHire(instance: TradingAgentInstance) {
    const now = Date.now();
    const lastAt = this.hireExpiryNoticeAt.get(instance.id) ?? 0;
    if (now - lastAt < HIRE_EXPIRY_NOTICE_THROTTLE_MS) return;
    this.hireExpiryNoticeAt.set(instance.id, now);

    const expiredAtIso = instance.expiresAt?.toISOString() ?? 'unknown';
    const msg = `Hire expired ${expiredAtIso} — live copy halted; renew to resume.`;
    this.logger.warn(`Instance ${instance.userId}: ${msg}`);

    const alreadySurfaced =
      typeof instance.lastError === 'string' && instance.lastError.startsWith('Hire expired');
    await this.prisma.tradingAgentInstance
      .update({ where: { id: instance.id }, data: { lastError: msg } })
      .catch(() => {
        /* best-effort */
      });

    if (!alreadySurfaced) {
      await this.notifications
        .notifyUser(instance.userId, {
          type: NotificationType.TRADING_AGENT_UPDATE,
          title: 'Live copy halted — hire expired',
          body: `Your rental expired ${expiredAtIso}. Live copy trading is halted until you renew.`,
          link: `/agent-hub/${AGENT_SLUG}`,
        })
        .catch(() => {
          /* notification is best-effort */
        });
    }
  }

  /**
   * Low-latency entry path for states that can be proven safe cheaply: either
   * a fully flat account, or an account whose complete exchange order book is
   * already owned by same-direction pending virtual lots. Any ambiguity returns
   * false and the unchanged full reconciliation path runs immediately after.
   */
  private async tryFreshSignedFlatEntry(
    agentId: string,
    instance: TradingAgentInstance,
    preferredTradeId?: string,
  ): Promise<boolean> {
    const armedAt = relayArmTimestampMs(instance.dashboardState);
    if (instance.status !== TradingAgentInstanceStatus.ACTIVE) return false;
    if (isCopyRelaySimActive(instance.dashboardState)) return false;
    if (instance.expiresAt && instance.expiresAt.getTime() <= Date.now()) return false;
    if (armedAt == null) return false;
    if (intentMirrorKillSwitchActive() || intentMirrorDryRunActive(instance)) return false;

    const cycleQuery = async () => this.prisma.signalCycle.findMany({
      where: {
        agentId,
        status: SignalCycleStatus.INTENT,
        createdAt: { gt: new Date(armedAt) },
        ...(preferredTradeId ? { tradeId: preferredTradeId } : {}),
      },
      orderBy: { createdAt: 'desc' },
      take: preferredTradeId ? 1 : 5,
    });
    const cyclesPromise = (async () => {
      const deadline = Date.now() + (preferredTradeId ? 1_500 : 0);
      do {
        const rows = await cycleQuery();
        if (rows.length > 0 || !preferredTradeId || Date.now() >= deadline) return rows;
        await new Promise<void>((resolve) => setTimeout(resolve, 75));
      } while (true);
    })();
    const credsPromise = this.exchanges.getUserCredentials(
      instance.userId,
      instance.exchangeProvider,
    );
    const marginCapPromise = loadSubscriberMaxMarginUsd(this.prisma);
    const creds = await credsPromise;
    if (!creds) return false;

    // Pre-wake can reach the worker while the API is still committing the
    // exact cycle. Overlap every read-only account proof with that wait; no
    // claim or exchange write can occur until cyclesPromise returns the exact
    // durable signed envelope below.
    const preflightPromise = Promise.all([
      this.activeTrading.listActiveOrders(creds),
      this.activeTrading.getOpenPositionDetail(creds),
      this.prisma.signalCycleParticipant.findMany({
        where: {
          userId: instance.userId,
          status: { in: [SignalCycleStatus.PENDING_ENTRY, SignalCycleStatus.OPEN] },
          cycle: { agentId },
        },
        select: { id: true, status: true },
      }),
      this.prisma.tradingAgentInstance.findUnique({ where: { id: instance.id } }),
      this.activeTrading.getDerivativesAvailableUsd(creds).catch(() => null),
      this.activeTrading.getMarkPrice().catch(() => null),
    ]);
    const [cycles, marginCap, preflight] = await Promise.all([
      cyclesPromise,
      marginCapPromise,
      preflightPromise,
    ]);
    const cycle = cycles.find((candidate) => {
      if (!isMirrorableLaneTradeId(candidate.tradeId)) return false;
      if (isPaperLaneTradeId(candidate.tradeId)) return false;
      if (candidate.expiresAt && candidate.expiresAt.getTime() <= Date.now()) return false;
      return readFreshSignedShowcaseExactLimit(
        candidate.tradeId,
        candidate.intentEnvelope,
      ) != null;
    });
    if (!cycle) return false;

    const [activeOrders, exchangePosition, virtualLots, freshInstance, availableUsd, markPrice] = preflight;
    if (!freshInstance || availableUsd == null || markPrice == null) return false;
    if (!isCycleFreshForRelayArm(freshInstance.dashboardState, cycle.createdAt)) {
      return false;
    }

    const flatPreflight = flatSignedFastPathPreflight({
        status: freshInstance.status,
        simActive: isCopyRelaySimActive(freshInstance.dashboardState),
        hireExpired: Boolean(
          freshInstance.expiresAt && freshInstance.expiresAt.getTime() <= Date.now(),
        ),
        relayArmed: relayArmTimestampMs(freshInstance.dashboardState) != null,
        virtualOpenOrPending: virtualLots.length,
        exchangeActiveOrders: activeOrders.length,
        exchangePositionQty: exchangePosition?.amount ?? 0,
      });

    const intentDirection = (cycle.intentEnvelope as SignalIntentEnvelope).direction;
    if (!intentDirection) return false;
    const cachedState = this.botBridge.getCachedExecutionState();
    // A non-flat fast path also needs the current canonical capacity snapshot;
    // falling back to the default could overbook if the dashboard cap was
    // lowered. The full reconciliation path will fetch it authoritatively.
    if (!flatPreflight && !cachedState) return false;
    const maxConcurrent = resolveMaxConcurrentCopySignals({
      botMaxActiveSignals: cachedState?.max_active_signals,
      envOverride: process.env.SUBSCRIBER_MAX_CONCURRENT_SIGNALS,
    });
    const virtualLotMeta = flatPreflight
      ? []
      : await Promise.all(
          virtualLots.map(async (lot) => ({
            status: lot.status,
            ...(await this.loadExecutionMeta(lot.id)),
          })),
        );
    const sameDirectionPendingPreflight = flatPreflight
      ? false
      : sameDirectionPendingSignedFastPathPreflight({
          status: freshInstance.status,
          simActive: isCopyRelaySimActive(freshInstance.dashboardState),
          hireExpired: Boolean(
            freshInstance.expiresAt && freshInstance.expiresAt.getTime() <= Date.now(),
          ),
          relayArmed: relayArmTimestampMs(freshInstance.dashboardState) != null,
          exchangePositionQty: exchangePosition?.amount ?? 0,
          candidateDirection: intentDirection,
          maxConcurrent,
          virtualLots: virtualLotMeta,
          exchangeActiveOrderIds: activeOrders.map((order) => order.id),
        });
    if (!flatPreflight && !sameDirectionPendingPreflight) return false;

    // The live exchange book above is authoritative and every non-flat order
    // was matched to an owned virtual lot. Clear only a stale cached orphan
    // warning before the normal eligibility gate reads dashboardState.
    const cleanInstance = {
      ...freshInstance,
      dashboardState: applyDashboardPatch(
        (freshInstance.dashboardState ?? {}) as Record<string, unknown>,
        { orphanOrderIds: [] },
      ) as TradingAgentInstance['dashboardState'],
    };
    const managedOrderIds = new Set(activeOrders.map((order) => order.id));
    const eligibility = await this.evaluateEntryEligibility(
      creds,
      {
        open: 0,
        pending: virtualLots.length,
        direction: flatPreflight ? null : intentDirection,
      },
      managedOrderIds,
      marginCap,
      flatPreflight ? 1 : maxConcurrent,
      intentDirection,
      cleanInstance,
      availableUsd ?? undefined,
      true,
    );
    if (!eligibility.canEnter) return false;

    const placed = await this.placeEntry(
      agentId,
      cleanInstance,
      cycle.id,
      cycle.intentEnvelope,
      creds,
      marginCap,
      cycle.tradeId,
      'bitfinex',
      {
        availableUsd,
        markPrice,
        exchangeBookProvenEmpty: flatPreflight,
      },
    );
    if (placed) {
      this.logger.log(
        `[SIGNED-${flatPreflight ? 'FLAT' : 'SAME-DIR'}-FAST] placed trade=${cycle.tradeId} user=${instance.userId}`,
      );
    }
    return placed;
  }

  private async processInstance(
    agentId: string,
    instance: TradingAgentInstance,
    simActive = false,
    opts?: { forceExitOnly?: boolean },
  ) {
    const forceExitOnly = opts?.forceExitOnly === true;
    let exitOnly =
      forceExitOnly ||
      (instance.status === TradingAgentInstanceStatus.PAUSED && !simActive) ||
      hireExpiryRequiresExitOnlyProcessing(instance.expiresAt, simActive);
    const venue = simActive ? 'bitfinex_sim' : 'bitfinex';
    const simState = simActive ? readCopyRelaySimState(instance.dashboardState) : null;
    const participantSince =
      simState?.startedAt != null ? { createdAt: { gte: new Date(simState.startedAt) } } : {};
    this.currentStage = 'LOAD_CREDENTIALS';
    const creds = await this.exchanges.getUserCredentials(instance.userId, instance.exchangeProvider);
    if (!creds) {
      if (!simActive) {
        await this.resetLiveFidelityGuardWithoutEvidence(
          instance,
          'EXCHANGE_CREDENTIALS_MISSING',
        ).catch(() => {
          /* missing credentials already prevent execution */
        });
      }
      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: { lastError: 'Exchange credentials missing — re-hire with API keys' },
      });
      return;
    }

    // Never place new live money until the private trade stream is authenticated.
    // Existing OPEN risk continues through the ordinary reconciliation path;
    // this gate is reached before any new-entry submission on a fresh hire.
    if (!simActive && instance.exchangeProvider === 'bitfinex') {
      const fillStream = this.ensureBitfinexTradeStream(instance.userId, creds);
      if (!(await fillStream.waitUntilReady())) {
        this.logger.warn(
          `Bitfinex private trade stream not ready for user=${instance.userId}; this tick is exit-only and entry will retry`,
        );
        exitOnly = true;
      }
    }

    this.currentStage = 'LOAD_MARGIN_CAP';
    const marginCap = await loadSubscriberMaxMarginUsd(this.prisma);

    let activeOrderIdSet = new Set<number>();
    let activeOrdersSnapshot: BitfinexActiveOrder[] = [];
    let activeOrdersSnapshotFresh = false;
    if (instance.exchangeProvider === 'bitfinex') {
      try {
        this.currentStage = 'BITFINEX_MARGIN_CHECK';
        const funding = await this.activeTrading.ensureDerivativesMargin(creds, marginCap);
        if (funding.message && funding.transferredUsd > 0) {
          this.logger.log(`Instance ${instance.userId}: ${funding.message}`);
        }
        this.currentStage = 'BITFINEX_ABSURD_ORDER_CHECK';
        await this.cancelAbsurdPendingOrders(creds, instance.userId);
        this.currentStage = 'BITFINEX_ACTIVE_ORDERS';
        const activeOrders = await this.activeTrading.listActiveOrders(creds);
        activeOrdersSnapshot = activeOrders;
        activeOrdersSnapshotFresh = true;
        activeOrderIdSet = new Set(activeOrders.map((o) => o.id));
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`Bitfinex prep ${instance.userId}: ${msg}`);
        await this.pauseRelayForPositionMismatch(
          instance,
          `BITFINEX_PREP_FAILED: exchange orders or margin state is unknown; relay paused. ${msg}`,
        );
        return;
      }
    }

    this.currentStage = 'RECONCILE_FILLED_PARTICIPANTS';
    await this.reconcileFilledParticipants(
      instance.userId,
      agentId,
      participantSince,
      instance.exchangeProvider === 'bitfinex' ? creds : undefined,
      instance.exchangeProvider === 'bitfinex' ? activeOrderIdSet : undefined,
    );

    this.currentStage = 'RECONCILE_GHOST_LOTS';
    await this.reconcileGhostOpenLots(
      agentId,
      instance.userId,
      participantSince,
      marginCap,
    );

    this.currentStage = 'RECONCILE_EXCHANGE_FLAT';
    const exchangePositionReadable = await this.reconcileImmediateExchangeFlat(
      agentId,
      instance,
      creds,
      participantSince,
      simActive,
    );
    if (!exchangePositionReadable) return;

    // Phase 2 — Layer B (NestJS Live Copy) reconcile-adopt pass. Re-arms
    // protective stops for OPEN participants whose meta.stopOrderId died
    // (filled or cancelled) on restart, re-hydrates positionRuntime so
    // monitorOpenPosition resumes Scenario C mirroring next tick, and
    // surfaces PENDING_ENTRY / OPEN participants missing critical meta
    // into dashboardState.orphanPositionIds for manual decision. Gated
    // by RECONCILE_WRITE_WINDOW for the stop re-arm write itself.
    this.currentStage = 'RECONCILE_ADOPT_LOOP';
    await this.reconcileAdoptLoop(
      agentId,
      instance.userId,
      instance.id,
      creds,
      participantSince,
    );

    // Phase 4+5 — autonomous orphan adoption. Reads the exchange directly and
    // re-adopts unattributed resting orders (S6a) and filled positions (S6b)
    // that the legacy reconcile path refused to heal. Guardrailed by env flag,
    // per-session budget cap, size sanity, conservative stop, idempotency, and
    // a full audit trail. See reconcileAdoptOrphans for the decision tree.
    // A vanished LIMIT may have filled between listActiveOrders and this
    // tick. Attribute the exchange-position delta to a current PENDING_ENTRY
    // before autonomous orphan adoption searches terminal history. Production
    // incident 2026-07-22: the reverse order matched a fresh cont-d48fc fill
    // to prior closed cont-68a8 and mirror-closed it immediately.
    this.currentStage = 'RECONCILE_UNATTRIBUTED_FILLS';
    await this.reconcileUnattributedExchangeFills(
      agentId,
      instance.userId,
      creds,
      activeOrderIdSet,
      participantSince,
    );

    this.currentStage = 'RECONCILE_ADOPT_ORPHANS';
    await this.reconcileAdoptOrphans(
      agentId,
      instance,
      creds,
      participantSince,
      marginCap,
    ).catch((err) => {
      // Money-path: never abort the tick on the adoption path. Surface + log.
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.warn(`[RECONCILE-ADOPT] orphan adoption loop error ${instance.userId}: ${msg}`);
    });

    if (!simActive) {
      const currentStatus = await this.prisma.tradingAgentInstance.findUnique({
        where: { id: instance.id },
        select: { status: true, expiresAt: true },
      });
      if (!currentStatus) return;
      exitOnly =
        forceExitOnly ||
        currentStatus.status !== TradingAgentInstanceStatus.ACTIVE ||
        hireExpiryRequiresExitOnlyProcessing(currentStatus.expiresAt, simActive);
    }

    const MIN_INTENT_TTL_MS = 90_000;

    this.currentStage = 'LOAD_SIGNAL_CYCLES';
    const cycles = await this.prisma.signalCycle.findMany({
      where: {
        agentId,
        status: {
          in: [
            SignalCycleStatus.INTENT,
            SignalCycleStatus.PENDING_ENTRY,
            SignalCycleStatus.OPEN,
          ],
        },
      },
      orderBy: { createdAt: 'desc' },
      take: 20,
    });
    const entryCycles =
      simActive || instance.exchangeProvider === 'paper'
        ? cycles
        : cycles.filter((cycle) =>
            isCycleFreshForRelayArm(instance.dashboardState, cycle.createdAt),
          );
    const signedFastOrders = entryCycles
      .filter((cycle) => cycle.status === SignalCycleStatus.INTENT)
      .map((cycle) =>
        readFreshSignedShowcaseExactLimit(cycle.tradeId, cycle.intentEnvelope),
      )
      .filter((order): order is SignedShowcaseExactLimit => order != null);

    this.currentStage = 'LOAD_MANAGED_CYCLES';
    const userManagedCycles = await this.prisma.signalCycle.findMany({
      where: {
        agentId,
        participants: {
          some: {
            userId: instance.userId,
            status: { in: [SignalCycleStatus.PENDING_ENTRY, SignalCycleStatus.OPEN] },
          },
        },
      },
      orderBy: { createdAt: 'desc' },
    });

    this.currentStage = 'LOAD_EXIT_CYCLES';
    const exitPendingCycles = await this.prisma.signalCycle.findMany({
      where: {
        agentId,
        status: { in: [SignalCycleStatus.CLOSED, SignalCycleStatus.EXPIRED] },
        participants: {
          some: { userId: instance.userId, status: SignalCycleStatus.OPEN },
        },
      },
      orderBy: { createdAt: 'desc' },
      take: 5,
    });

    const cycleById = new Map<string, (typeof cycles)[number]>();
    for (const c of [...userManagedCycles, ...cycles, ...exitPendingCycles]) {
      cycleById.set(c.id, c);
    }
    const allCycles = [...cycleById.values()];

    let managedOpenTrade = false;

    // Pass 1 — manage existing copy trades (fills, stops, exits) before any new entries.
    this.currentStage = 'MANAGE_EXISTING_LOTS';
    for (const cycle of allCycles) {
      const participant = await this.prisma.signalCycleParticipant.findUnique({
        where: { cycleId_userId: { cycleId: cycle.id, userId: instance.userId } },
      });
      if (!participant) continue;

      const meta = await this.resolveLotMeta(
        participant.id,
        cycle.id,
        instance.userId,
        agentId,
        cycle.intentEnvelope,
        marginCap,
      );

      if (participant.status === SignalCycleStatus.PENDING_ENTRY) {
        managedOpenTrade = true;
        await this.monitorEntry(
          agentId,
          instance.userId,
          cycle,
          participant,
          meta,
          creds,
          activeOrderIdSet,
          exitOnly,
        );
        continue;
      }

      if (participant.status === SignalCycleStatus.OPEN) {
        managedOpenTrade = true;
        // F2 — Showcase-unreachable orphan kill. If the showcase has been dark
        // past ORPHAN_KILL_MS this lot is by definition an orphan (the normal
        // SHOWCASE_POSITION_ABSENT path requires a successful fetch to fire,
        // which is exactly the case the outage defeats). Force-close at market.
        // Skip in sim mode — sim lots are the user's own test money and the
        // showcase is allowed to be dark for sim testing.
        if (!simActive) {
          const orphanCheck = this.openLotOrphanedByShowcaseOutage(instance.id);
          if (orphanCheck.orphan) {
            const killed = await this.enforceShowcaseOutageOrphanKill(
              agentId,
              instance.userId,
              instance.id,
              cycle,
              participant,
              meta,
              creds,
              orphanCheck.elapsedMs,
            ).catch((err) => {
              this.logger.warn(
                `[F2] orphan kill failed ${instance.userId} cycle=${cycle.id}: ${err instanceof Error ? err.message : err}`,
              );
              return false;
            });
            if (killed) {
              this.showcaseFlatOpenSince.delete(participant.id);
              continue;
            }
          }
        }
        const mirrorExited = await this.tryImmediateShowcaseMirrorExit(
          agentId,
          instance.userId,
          cycle,
          participant,
          meta,
          creds,
          simActive,
        );
        if (mirrorExited) {
          this.showcaseFlatOpenSince.delete(participant.id);
          continue;
        }
        const failsafeExited = await this.enforceShowcaseFlatOpenFailsafe(
          agentId,
          instance.userId,
          cycle,
          participant,
          meta,
          creds,
          simActive,
        );
        if (failsafeExited) {
          this.showcaseFlatOpenSince.delete(participant.id);
          continue;
        }
        const attributed = await this.reconcileAttributedLotClose(
          agentId,
          instance.userId,
          cycle,
          participant,
          meta,
          creds,
        );
        const reconciled =
          attributed ||
          (await this.reconcileManualClose(
            agentId,
            instance.userId,
            cycle,
            participant,
            meta,
            creds,
          ));
        if (!reconciled) {
          await this.monitorOpenPosition(
            agentId,
            instance.userId,
            cycle,
            participant,
            meta,
            creds,
            simActive,
          );
        await this.monitorExit(agentId, instance.userId, cycle, participant, meta, creds);
        }
      }
    }

    this.currentStage = 'LOAD_OPEN_PARTICIPANTS';
    const openParticipantAfter = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId: instance.userId,
        status: { in: [SignalCycleStatus.PENDING_ENTRY, SignalCycleStatus.OPEN] },
        cycle: { agentId },
        ...participantSince,
      },
      orderBy: { createdAt: 'desc' },
      include: { cycle: { select: { tradeId: true } } },
    });

    const managedOrderIds = new Set<number>();
    // Phase 0: cache the metas loaded here so the mirror-diff pass below can
    // reuse them without re-querying the event stream per participant.
    const execMetaById = new Map<string, ExecutionPayload>();
    for (const p of openParticipantAfter) {
      const m = await this.loadExecutionMeta(p.id);
      execMetaById.set(p.id, m);
      if (m.bitfinexOrderId) managedOrderIds.add(m.bitfinexOrderId);
      if (m.stopOrderId) managedOrderIds.add(m.stopOrderId);
      if (m.supersededStopOrderId) managedOrderIds.add(m.supersededStopOrderId);
    }

    // Refresh the active-order book before ledger reconcile. The early-tick
    // snapshot can still show a fully resting entry while Bitfinex already
    // reports the fill as a position — that race used to trip an immediate
    // mismatch pause (PENDING_ENTRY + exchange qty) before EXCHANGE_FILL_RECONCILE
    // could promote the lot.
    if (instance.exchangeProvider === 'bitfinex') {
      try {
        activeOrdersSnapshot = await this.activeTrading.listActiveOrders(creds);
        activeOrdersSnapshotFresh = true;
        activeOrderIdSet = new Set(activeOrdersSnapshot.map((o) => o.id));
      } catch (err) {
        activeOrdersSnapshotFresh = false;
        this.logger.warn(
          `Bitfinex active-order refresh before ledger reconcile ${instance.userId}: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }
    }

    this.currentStage = 'RECONCILE_LOT_LEDGER';
    const ledgerReconciled = await this.reconcileLotLedger(
      agentId,
      instance,
      openParticipantAfter,
      creds,
      managedOrderIds,
      activeOrdersSnapshot,
      execMetaById,
      simActive,
    );
    if (!ledgerReconciled) return;

    const lotSummary = await this.buildVirtualLotSummary(openParticipantAfter);

    // Phase 6 fix 3 — per-tick orphan surfacer. Runs every tick (after
    // reconcileLotLedger / reconcileAdoptOrphans) so dashboardState.orphanOrderIds
    // stays current even when the bot is idle and no new signal arrives. The
    // fetched foreign orders are handed to cleanupOrphanCopyOrders (fix 4) so we
    // don't double-query the exchange. evaluateEntryEligibility now READS the
    // persisted orphanOrderIds instead of re-querying listActiveOrders.
    this.currentStage = 'SURFACE_ORPHAN_ORDERS';
    const foreignOrphanOrders = await this.surfaceOrphanOrders(
      instance,
      creds,
      managedOrderIds,
      activeOrdersSnapshotFresh ? activeOrdersSnapshot : undefined,
    );

    this.currentStage = 'CLEANUP_ORPHAN_ORDERS';
    await this.cleanupOrphanCopyOrders(
      instance.userId,
      instance.id,
      agentId,
      creds,
      managedOrderIds,
      foreignOrphanOrders,
    );

    const signedCachedState = signedFastOrders.length
      ? this.botBridge.getCachedExecutionState()
      : null;
    // A fresh HMAC-authenticated ORDER_PLACED carries the exact canonical
    // limit. Do not hold it behind a 20-30s tunnel request. Reuse a recent
    // canonical cache for caps/observability and refresh the tunnel in the
    // background; unsigned or stale intents retain the fail-closed fetch.
    this.currentStage = 'FETCH_CANONICAL_EXECUTION_STATE';
    const botStateForCap = signedFastOrders.length
      ? signedCachedState
      : await this.fetchExecutionBotState();
    // Capacity and money decisions must share the same bounded canonical
    // execution snapshot. The legacy resolveMaxConcurrentSignals() call used
    // BotBridge.fetchState(), whose display-oriented fallback waits up to
    // 20s for /api/relay-state and another 30s for /api/state. An idle relay
    // therefore spent ~50s before its actual bounded execution fetch, tripped
    // the 60s watchdog, and made Start fail closed. The canonical execution
    // fetch above is capped at 2.5s and already carries max_active_signals.
    const maxConcurrent = simActive
      ? 1
      : resolveMaxConcurrentCopySignals({
          botMaxActiveSignals:
            botStateForCap?.max_active_signals ?? signedCachedState?.max_active_signals,
          envOverride: process.env.SUBSCRIBER_MAX_CONCURRENT_SIGNALS,
        });
    if (signedFastOrders.length) {
      void this.fetchExecutionBotState().catch((err) => {
        this.logger.warn(
          `[SIGNED-FAST] canonical refresh failed: ${err instanceof Error ? err.message : err}`,
        );
      });
    }
    const canonicalSignedBook = mergeSignedShowcaseOrders(
      botStateForCap,
      signedFastOrders,
    );

    // F1/F2/F3 — Showcase-unreachable safe mode (2026-07-07 incident hardening),
    // F8 debounced-clear (2026-07-08 hotfix).
    // Track per-instance streak of null fetches. Cleared only after
    // SHOWCASE_RECOVERY_HITS_REQUIRED (3) consecutive successful fetches — a
    // single lucky ping during a tunnel flap no longer re-arms live copy.
    // The two helpers below read this streak to gate new entries (F1) and
    // force-close OPEN orphans (F2). When the streak passes ENTRY_BLOCK_MS we
    // also surface a user-visible lastError (F3) so the dashboard explains
    // why live copy has halted.
    if (!simActive && botStateForCap == null && signedFastOrders.length === 0) {
      const elapsed = this.markShowcaseUnreachable(instance.id);
      const blocked = elapsed >= SHOWCASE_UNREACHABLE_ENTRY_BLOCK_MS;
      if (blocked && showcaseUnreachableSafeModeEnabled()) {
        const lastNotice = this.showcaseSafeModeNoticeAt.get(instance.id) ?? 0;
        if (Date.now() - lastNotice > 5 * 60_000) {
          this.showcaseSafeModeNoticeAt.set(instance.id, Date.now());
          const msg = `Showcase unreachable for ${Math.round(elapsed / 1000)}s — live copy in safe mode: no new entries (F1); open lots will be closed past ${Math.round(SHOWCASE_UNREACHABLE_ORPHAN_KILL_MS / 1000)}s (F2).`;
          this.logger.warn(`[SAFE-MODE] ${instance.userId}: ${msg}`);
          await this.prisma.tradingAgentInstance
            .update({ where: { id: instance.id }, data: { lastError: msg } })
            .catch(() => {});
        }
      }
    } else if (botStateForCap != null || signedFastOrders.length > 0) {
      const hadTrackedOutage = this.showcaseUnreachableSince.has(instance.id);
      const showcaseRecovered = this.clearShowcaseUnreachable(instance.id);
      if (shouldClearShowcaseStatusError({
        message: instance.lastError,
        hadTrackedOutage,
        recoveredNow: showcaseRecovered,
      })) {
        await this.prisma.tradingAgentInstance
          .updateMany({
            where: { id: instance.id, lastError: instance.lastError },
            data: { lastError: null },
          })
          .catch(() => {});
      }
    }
    const botMaxRaw = botStateForCap?.max_active_signals;
    const botMax =
      typeof botMaxRaw === 'number'
        ? botMaxRaw
        : typeof botMaxRaw === 'string'
          ? Number.parseInt(botMaxRaw, 10)
          : null;

    // A fail-closed path can pause/disarm the relay during this tick. Never
    // reuse the status captured at tick start when deciding whether entries
    // may run.
    const currentInstanceState = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instance.id },
      select: { status: true },
    });
    if (!currentInstanceState) return;
    const freshForExitGate = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instance.id },
      select: { expiresAt: true },
    });
    const exitOnlyNow =
      forceExitOnly ||
      (!simActive &&
        (currentInstanceState.status !== TradingAgentInstanceStatus.ACTIVE ||
          hireExpiryRequiresExitOnlyProcessing(freshForExitGate?.expiresAt, simActive)));

    // Phase 0 — shadow-diff observability. Compares the showcase book (from
    // the state already fetched above) against the copy's ledger. Pure
    // observability: no exchange calls, no behavior change, never throws.
    this.currentStage = 'RECORD_MIRROR_DIFF';
    await this.recordMirrorDiff(
      agentId,
      instance,
      canonicalSignedBook,
      openParticipantAfter,
      execMetaById,
      !exitOnlyNow,
    ).catch((err) => {
      this.logger.warn(
        `[MIRROR-DIFF] snapshot failed ${instance.userId}: ${err instanceof Error ? err.message : err}`,
      );
    });

    // Backend-owned sustained fidelity stop. This runs in the existing
    // executor tick (no browser/localStorage and no second monitor loop), only
    // after exact exchange-ledger reconciliation and a fresh canonical Fly
    // snapshot are both available.
    this.currentStage = 'ENFORCE_LIVE_FIDELITY_GUARD';
    const fidelityGuardTripped = await this.enforceSustainedLiveFidelityGuard(
      agentId,
      instance,
      canonicalSignedBook,
      simActive,
    ).catch((err) => {
      // Unknown/stale evidence must never be converted into a false breach.
      this.logger.warn(
        `[LIVE-FIDELITY-GUARD] observation skipped ${instance.userId}: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
      return false;
    });
    if (fidelityGuardTripped) {
      await this.persistCapacityState(instance, lotSummary, maxConcurrent, botMax);
      return;
    }

    if (exitOnlyNow) {
      this.currentStage = 'PERSIST_PAUSED_CAPACITY';
      await this.persistCapacityState(instance, lotSummary, maxConcurrent, botMax);
      if (managedOpenTrade) {
        await this.prisma.tradingAgentInstance.update({
          where: { id: instance.id },
          data: {
            lastError:
              'Relay stopped — no new entries; Scenario C risk monitor still active on OPEN lots.',
          },
        });
      }
      return;
    }

    this.currentStage = 'PERSIST_ACTIVE_CAPACITY';
    await this.persistCapacityState(instance, lotSummary, maxConcurrent, botMax);

    await this.attemptMirrorCatchupEntries(
      agentId,
      instance,
      creds,
      botStateForCap,
      openParticipantAfter,
      lotSummary,
      managedOrderIds,
      marginCap,
      maxConcurrent,
      exitOnlyNow,
      simActive,
    ).catch((err) => {
      this.logger.warn(
        `[MIRROR-CATCHUP] ${instance.userId}: ${err instanceof Error ? err.message : err}`,
      );
    });

    const botState = botStateForCap;
    if (botState?.execution_paused) {
      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: {
          lastError:
            'Showcase bot paused on dashboard — no new copy entries until admin resumes trading.',
        },
      });
      return;
    }

    // Pass 2 — virtual lot ledger: multiple same-direction legs on merged Bitfinex position.
    const canonicalEntryBook = canonicalSignedBook;
    const intentCycles = canonicalPendingIntentCycles(
      entryCycles.filter((c) => c.status === SignalCycleStatus.INTENT),
      canonicalEntryBook,
    );
    let entriesThisTick = 0;
    let entryDirectionThisTick: 'LONG' | 'SHORT' | null = null;
    for (const cycle of intentCycles) {
      if (cycle.expiresAt && cycle.expiresAt < new Date()) continue;
      if (
        cycle.expiresAt &&
        cycle.expiresAt.getTime() - Date.now() < MIN_INTENT_TTL_MS
      ) {
        continue;
      }

      const existing = await this.prisma.signalCycleParticipant.findUnique({
        where: { cycleId_userId: { cycleId: cycle.id, userId: instance.userId } },
      });
      if (existing) {
        // C5 stale-claim reclaim: an INTENT claim older than 120s with no transition is a
        // crashed replica's orphan (placeEntry should complete in seconds). Reclaim it so
        // the cycle isn't blocked forever. A fresh INTENT claim (<120s) is respected as
        // "another replica is placing this order" — do not touch.
        if (
          existing.status === SignalCycleStatus.INTENT &&
          existing.createdAt.getTime() < Date.now() - 120_000
        ) {
          await this.prisma.signalCycleParticipant
            .delete({ where: { id: existing.id } })
            .catch(() => {
              /* may have been transitioned concurrently */
            });
          this.logger.log(
            `Hire reclaim ${instance.userId} cycle=${cycle.id}: stale INTENT claim (age>120s) cleared`,
          );
        } else {
          continue;
        }
      }
      const intent = cycle.intentEnvelope as SignalIntentEnvelope;
      if (!intent?.direction) continue;
      if (!mergedDirectionCompatible(entryDirectionThisTick, intent.direction)) {
        this.logger.warn(
          `[MERGED-POSITION-GATE] skipped opposing ${intent.direction} trade=${cycle.tradeId}; ` +
            `${entryDirectionThisTick} was already submitted in this tick.`,
        );
        continue;
      }

      const eligibility = await this.evaluateEntryEligibility(
        creds,
        {
          open: lotSummary.open,
          pending: lotSummary.pending + entriesThisTick,
          direction: lotSummary.direction,
        },
        managedOrderIds,
        marginCap,
        maxConcurrent,
        intent.direction,
        instance,
      );
      if (!eligibility.canEnter) {
        this.cycleAudit.stage('CAPACITY_REJECT', {
          userId: instance.userId,
          agentId,
          cycleId: cycle.id,
          tradeId: cycle.tradeId,
          detail: eligibility.reason ?? 'capacity',
          meta: {
            open: lotSummary.open,
            pending: lotSummary.pending + entriesThisTick,
            limit: maxConcurrent,
          },
        });
        await this.persistCapacityState(instance, lotSummary, maxConcurrent, botMax, {
          reason: eligibility.reason,
        });
        if (entriesThisTick === 0) {
          await this.prisma.tradingAgentInstance.update({
            where: { id: instance.id },
            data: { lastError: eligibility.reason },
          });
        }
        break;
      }

      const placed = await this.placeEntry(
        agentId,
        instance,
        cycle.id,
        cycle.intentEnvelope,
        creds,
        marginCap,
        cycle.tradeId,
        venue,
      );
      if (!placed) continue;
      entriesThisTick += 1;
      entryDirectionThisTick = intent.direction;
    }

    if (entriesThisTick > 0) {
      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: { lastError: null },
      });
    } else if (!managedOpenTrade) {
      const lotSummary = await this.buildVirtualLotSummary(openParticipantAfter);
      const eligibility = await this.evaluateEntryEligibility(
        creds,
        { open: lotSummary.open, pending: lotSummary.pending, direction: lotSummary.direction },
        managedOrderIds,
        marginCap,
        maxConcurrent,
        undefined,
        instance,
      );
      const preserveMismatchAlert =
        instance.status === TradingAgentInstanceStatus.PAUSED &&
        Boolean(instance.lastError);
      if (!eligibility.canEnter) {
        await this.prisma.tradingAgentInstance.update({
          where: { id: instance.id },
          data: { lastError: eligibility.reason },
        });
      } else if (!preserveMismatchAlert) {
    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: { lastError: null },
    });
      }
    }

    // Part B (intent-mirror) — when the legacy fill-based path placed nothing
    // this tick AND the instance is ACTIVE (not sim, not PAUSED exit-only),
    // try to enter directly from an approved showcase-lane INTENT cycle.
    // core fix for "dashboard active, Bitfinex flat" when the showcase runs
    // paper-only. The kill switch (N3) and dry-run flag (N4) are honored
    // inside maybeEnterFromIntent. The new path ADDS guards (N6 lane
    // whitelist, G5 paper block, G11/G12 eligibility+cap); it does not relax
    // any existing guard.
    if (
      entriesThisTick === 0 &&
      !simActive &&
      instance.status === TradingAgentInstanceStatus.ACTIVE
    ) {
      try {
        const intentEntries = await this.maybeEnterFromIntent(
          agentId,
          instance,
          creds,
          entryCycles,
          lotSummary,
          managedOrderIds,
          marginCap,
          maxConcurrent,
          venue,
          canonicalSignedBook,
        );
        if (intentEntries > 0) {
          await this.prisma.tradingAgentInstance.update({
            where: { id: instance.id },
            data: { lastError: null },
          });
        }
      } catch (err) {
        this.logger.warn(
          `[INTENT-MIRROR] ${instance.userId}: ${err instanceof Error ? err.message : err}`,
        );
      }
    }
  }

  /**
   * Showcase state for the execution path. With MIRROR_CONVERGENCE_ENABLED a
   * 1s memo lets every call site in the same tick share ONE fresh pull
   * (placeEntry defer check, monitorEntry abandon check, applyLimitChase
   * anchor, capacity cap) instead of each paying a full tunnel round-trip —
   * this is the main lever that cuts effective reprice lag. With the flag OFF
   * the behavior is byte-identical to the legacy per-call fresh fetch.
   */
  private async fetchExecutionBotState(): Promise<BotApiState | null> {
    if (!this.botBridge.isEnabled()) return null;
    if (mirrorConvergenceEnabled()) {
      const now = Date.now();
      if (this.execStateMemo && now - this.execStateMemo.at < MIRROR_EXEC_STATE_MEMO_MS) {
        return this.execStateMemo.state;
      }
    }
    const state = await this.botBridge.fetchStateForExecution(true).catch(() => null);
    this.execStateMemo = { at: Date.now(), state };
    return state;
  }

  /** Resolve showcase bot pending limit for chase sync. */
  private resolveBotLimitPrice(
    bot: BotApiState | null,
    tradeId: string,
  ): number | null {
    if (!bot) return null;
    const order = this.showcasePendingOrder(bot, tradeId);
    if (order?.limit_price && order.limit_price > 0) return order.limit_price;
    return null;
  }

  private showcasePendingOrder(
    bot: import('./bot-state.mapper').BotApiState | null,
    tradeId: string,
  ) {
    return (bot?.orders ?? []).find(
      (o) =>
        o.trade_id === tradeId
        && isExecutableStructuralShowcaseOrder(o),
    );
  }

  private showcaseSignalForTrade(
    bot: import('./bot-state.mapper').BotApiState | null,
    tradeId: string,
  ) {
    return (bot?.signal_info?.signals ?? []).find(
      (s) => String(s.trade_id ?? '') === tradeId,
    );
  }

  /**
   * Bitfinex relay must not place before global showcase has a resting limit —
   * mirrors virtual defer / chase-bucket gating on :7002.
   */
  private showcaseCopyEntryReady(
    bot: import('./bot-state.mapper').BotApiState | null,
    tradeId: string | undefined,
  ): { ready: boolean; reason?: string } {
    if (!tradeId || !bot) return { ready: true };
    const pending = this.showcasePendingOrder(bot, tradeId);
    if (pending?.limit_price && pending.limit_price > 0) return { ready: true };
    const showcasePosition = (bot.positions ?? []).find(
      (position) => String(position.trade_id ?? '') === tradeId,
    );
    if (showcasePosition) {
      return {
        ready: false,
        reason: 'Showcase filled before copy entry; market catch-up is prohibited.',
      };
    }
    const sig = this.showcaseSignalForTrade(bot, tradeId);
    const st = String(sig?.status ?? '').toUpperCase();
    if (
      st === 'AWAITING_DASHBOARD_CHASE' ||
      st === 'AWAITING_CHASE_3PLUS' ||
      st === 'AWAITING_MICRO' ||
      st === 'AWAITING_5M' ||
      st === 'AWAITING_MIN_AGE'
    ) {
      return {
        ready: false,
        reason:
          'Showcase bot has not placed a limit yet (virtual defer / chase bucket) — relay waiting.',
      };
    }
    return {
      ready: false,
      reason: sig
        ? 'Waiting for the showcase to publish its exact resting limit.'
        : 'Showcase trade is not present in the current canonical book.',
    };
  }

  /** Showcase cancelled or blocked entry — relay must drop its resting limit too. */
  private showcaseEntryAbandoned(
    bot: import('./bot-state.mapper').BotApiState | null,
    tradeId: string,
  ): { abandoned: boolean; reason?: string } {
    if (!bot || !tradeId) return { abandoned: false };

    const disposition = pendingCopyShowcaseDisposition(bot, tradeId);
    if (disposition === 'SHOWCASE_PENDING') return { abandoned: false };
    if (disposition === 'MISSED_SHOWCASE_FILL') {
      return { abandoned: true, reason: 'MISSED_SHOWCASE_FILL' };
    }

    const expired = (bot.expired_orders ?? []).find((e) => e.trade_id === tradeId);
    if (expired) {
      return { abandoned: true, reason: expired.reason ?? 'SHOWCASE_EXPIRED' };
    }

    const sig = this.showcaseSignalForTrade(bot, tradeId);
    // No pending, no position, no active signal, and no expired_orders hit.
    // Showcase fully dropped this trade — common when the expiry row rotated
    // out of the bot's MAX_EXPIRED_ORDERS ring (20). Fail-closed so the copy
    // does not rest until cycle TTL and risk an orphan fill (COPY_ORDER_NO_SHOWCASE).
    if (!sig) {
      return { abandoned: true, reason: 'SHOWCASE_ABSENT' };
    }

    const st = String(sig.status ?? '').toUpperCase();
    if (
      st === 'AWAITING_DASHBOARD_CHASE' ||
      st === 'AWAITING_CHASE_3PLUS' ||
      st === 'AWAITING_MICRO' ||
      st === 'AWAITING_5M' ||
      st === 'AWAITING_MIN_AGE'
    ) {
      return { abandoned: false };
    }

    const outcome = String(sig.outcome ?? sig.exit_reason ?? '').toUpperCase();
    const terminal =
      st === 'EXPIRED' ||
      st === 'BLOCKED' ||
      st === 'CLOSED' ||
      /CHASE_BUCKET|EXPIRED|BLOCKED|TTL|CLOSED/.test(outcome);
    if (terminal) {
      return { abandoned: true, reason: sig.exit_reason ?? sig.outcome ?? st };
    }

    return { abandoned: false };
  }

  /**
   * Phase 0 — shadow-diff observability (MIRROR_DIFF). Compares desired
   * (showcase book: pending limits + open positions, from the bot state the
   * tick already fetched) vs actual (the copy's ledger: PENDING_ENTRY resting
   * orders + OPEN lots, metas already loaded by the caller). Divergences are
   * persisted compactly:
   *  - dashboardState.mirrorDiff — latest snapshot + rolling counters
   *    (divergent ticks, showcase fills seen vs captured, reprice-lag EMA).
   *  - a MIRROR_DIFF SignalCycleEvent per diverged participant, throttled to
   *    max 1 per participant per 60s.
   * Cheap by design: no exchange API calls, DB-only. Never throws (caller
   * catches). No behavior change — observability only.
   */
  private async recordMirrorDiff(
    agentId: string,
    instance: TradingAgentInstance,
    botState: BotApiState | null,
    participants: Array<{
      id: string;
      status: SignalCycleStatus;
      cycleId: string;
      cycle?: { tradeId: string | null } | null;
    }>,
    metaById: Map<string, ExecutionPayload>,
    entryEnabled: boolean,
  ) {
    if (!mirrorDiffEnabled() || !botState) return;
    const now = Date.now();

    const showcaseOrders = (botState.orders ?? []).filter(
      (o) =>
        (o.status === 'PENDING' || o.status === 'ORDERED') &&
        typeof o.trade_id === 'string' &&
        (o.limit_price ?? 0) > 0,
    );
    const showcasePositions = (botState.positions ?? []).filter(
      (p) => typeof p.trade_id === 'string' && p.trade_id.length > 0,
    );

    const copyPending = participants.filter((p) => p.status === SignalCycleStatus.PENDING_ENTRY);
    const copyOpen = participants.filter((p) => p.status === SignalCycleStatus.OPEN);
    // Cancel-race fills and adopted exchange positions intentionally rewrite
    // cycle.tradeId to an audit identifier (`relink:*` / `adopt:*`). Mirror
    // comparison must use the canonical showcase identity, otherwise an
    // exactly matched live position is reported both missing and orphaned.
    const mirrorTradeIdFor = (participant: (typeof participants)[number]) =>
      resolveShowcaseMirrorTradeIdFromInputs(
        participant.cycle?.tradeId,
        metaById.get(participant.id)?.originTradeId,
      );
    const copyTradeIds = new Set(
      participants
        .map(mirrorTradeIdFor)
        .filter((t): t is string => typeof t === 'string' && t.length > 0),
    );
    const copyOpenTradeIds = new Set(
      copyOpen
        .map(mirrorTradeIdFor)
        .filter((t): t is string => typeof t === 'string' && t.length > 0),
    );
    const copyPendingTradeIds = new Set(
      copyPending
        .map(mirrorTradeIdFor)
        .filter((t): t is string => typeof t === 'string' && t.length > 0),
    );

    type MirrorDiffDivergence = {
      type:
        | 'PRICE_DELTA'
        | 'QTY_DELTA'
        | 'COPY_ORDER_NO_SHOWCASE'
        | 'SHOWCASE_ORDER_NOT_MIRRORED'
        | 'SHOWCASE_FILLED_COPY_PENDING'
        | 'SHOWCASE_POSITION_NOT_MIRRORED'
        | 'COPY_POSITION_NO_SHOWCASE';
      tradeId?: string;
      participantId?: string;
      cycleId?: string;
      copyLimit?: number;
      showcaseLimit?: number;
      deltaUsd?: number;
      showcaseQty?: number;
      copyQty?: number;
      deltaQty?: number;
      showcaseDir?: string;
      copyDir?: string;
      sourceCreatedAtMs?: number;
    };
    const divergences: MirrorDiffDivergence[] = [];

    // Copy resting orders vs showcase pending limits (per-order price delta).
    for (const p of copyPending) {
      const meta = metaById.get(p.id);
      const tradeId = mirrorTradeIdFor(p) ?? undefined;
      const copyLimit = meta?.limitPrice;
      if (!tradeId || !copyLimit || copyLimit <= 0 || !meta?.bitfinexOrderId) continue;
      const showcaseOrder = showcaseOrders.find((o) => o.trade_id === tradeId);
      if (showcaseOrder?.limit_price) {
        const delta = copyLimit - showcaseOrder.limit_price;
        if (Math.abs(delta) >= MIRROR_DIFF_PRICE_EPSILON_USD) {
          divergences.push({
            type: 'PRICE_DELTA',
            tradeId,
            participantId: p.id,
            cycleId: p.cycleId,
            copyLimit,
            showcaseLimit: showcaseOrder.limit_price,
            deltaUsd: Math.round(delta * 100) / 100,
          });
          if (!this.mirrorDivergenceSince.has(p.id)) {
            this.mirrorDivergenceSince.set(p.id, now);
          }
        } else {
          // Converged — close any open divergence window (reprice-lag sample).
          this.mirrorDivergenceSince.delete(p.id);
        }
      } else {
        const showcasePos = showcasePositions.find((x) => x.trade_id === tradeId);
        if (showcasePos) {
          const partialQty = meta?.partialFillQty ?? 0;
          const showcaseQty = Number(showcasePos.qty ?? 0);
          if (partialQty > 0) {
            divergences.push({
              type: 'QTY_DELTA',
              tradeId,
              participantId: p.id,
              cycleId: p.cycleId,
              showcaseQty,
              copyQty: partialQty,
              deltaQty: mirrorPositionQuantityDelta(showcaseQty, partialQty) ?? 0,
              showcaseDir: showcasePos.dir ?? showcasePos.side,
              copyDir: meta?.direction,
            });
            continue;
          }
          // Showcase already filled this trade while the copy is still pending.
          divergences.push({
            type: 'SHOWCASE_FILLED_COPY_PENDING',
            tradeId,
            participantId: p.id,
            cycleId: p.cycleId,
            copyLimit,
            showcaseDir: showcasePos.dir ?? showcasePos.side,
          });
        } else {
          // Copy holds a resting order with no showcase counterpart (order or
          // position) — showcase abandoned/expired this trade.
          divergences.push({
            type: 'COPY_ORDER_NO_SHOWCASE',
            tradeId,
            participantId: p.id,
            cycleId: p.cycleId,
            copyLimit,
          });
        }
      }
    }

    // Showcase pending limits with no copy counterpart. Mirror the BOOK, not
    // the per-lane spawns: dedupe showcase orders by limit price first (the
    // showcase book expires same-price duplicates as DUPLICATE_LIMIT_PRICE).
    const seenShowcasePrices = new Set<string>();
    for (const o of showcaseOrders) {
      const priceKey = (o.limit_price ?? 0).toFixed(2);
      if (seenShowcasePrices.has(priceKey)) continue;
      seenShowcasePrices.add(priceKey);
      if (o.trade_id && copyTradeIds.has(o.trade_id)) continue;
      // Another lane spawn of the same book entry may be the one mirrored.
      const mirroredAtPrice = copyPending.some((p) => {
        const meta = metaById.get(p.id);
        return (
          meta?.limitPrice != null &&
          Math.abs(meta.limitPrice - (o.limit_price ?? 0)) < DUPLICATE_LIMIT_EPSILON_USD
        );
      });
      if (mirroredAtPrice) continue;
      divergences.push({
        type: 'SHOWCASE_ORDER_NOT_MIRRORED',
        tradeId: o.trade_id,
        showcaseLimit: o.limit_price,
        showcaseDir: o.side,
        sourceCreatedAtMs:
          sourceEntityCreatedAtMs(o as Record<string, unknown>) ?? undefined,
      });
    }

    // Showcase open positions vs copy OPEN lots (present/missing).
    for (const pos of showcasePositions) {
      const tid = pos.trade_id as string;
      if (copyOpenTradeIds.has(tid)) continue;
      if (copyPendingTradeIds.has(tid)) continue; // reported as SHOWCASE_FILLED_COPY_PENDING
      divergences.push({
        type: 'SHOWCASE_POSITION_NOT_MIRRORED',
        tradeId: tid,
        showcaseDir: pos.dir ?? pos.side,
        sourceCreatedAtMs:
          sourceEntityCreatedAtMs(pos as Record<string, unknown>) ?? undefined,
      });
    }
    // Identity/count parity is insufficient: one OPEN row on each side can
    // still hide a materially under-filled copy. Compare source quantity with
    // the durable real lot quantity for the matching canonical trade.
    for (const pos of showcasePositions) {
      const tradeId = pos.trade_id as string;
      const copy = copyOpen.find((row) => mirrorTradeIdFor(row) === tradeId);
      if (!copy) continue;
      const showcaseQty = Number(pos.qty ?? 0);
      const copyQty = Number(metaById.get(copy.id)?.qty ?? 0);
      const deltaQty = mirrorPositionQuantityDelta(showcaseQty, copyQty);
      if (deltaQty == null) continue;
      divergences.push({
        type: 'QTY_DELTA',
        tradeId,
        participantId: copy.id,
        cycleId: copy.cycleId,
        showcaseQty,
        copyQty,
        deltaQty,
        showcaseDir: pos.dir ?? pos.side,
        copyDir: metaById.get(copy.id)?.direction,
      });
    }
    for (const p of copyOpen) {
      const tradeId = mirrorTradeIdFor(p) ?? undefined;
      if (!tradeId) continue;
      if (showcasePositions.some((x) => x.trade_id === tradeId)) continue;
      const meta = metaById.get(p.id);
      divergences.push({
        type: 'COPY_POSITION_NO_SHOWCASE',
        tradeId,
        participantId: p.id,
        cycleId: p.cycleId,
        copyDir: meta?.direction,
      });
    }

    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instance.id },
      select: { dashboardState: true, status: true },
    });
    if (!fresh) return;
    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;

    const copyDirections = new Set(
      [...copyPending, ...copyOpen]
        .map((participant) => metaById.get(participant.id)?.direction)
        .filter(
          (direction): direction is 'LONG' | 'SHORT' =>
            direction === 'LONG' || direction === 'SHORT',
        ),
    );
    const copyDirection = copyDirections.size === 1 ? [...copyDirections][0] : null;
    const sourceOnlyPositionTradeIds = divergences
      .filter((d) => d.type === 'SHOWCASE_POSITION_NOT_MIRRORED' && d.tradeId)
      .map((d) => d.tradeId as string);
    const expectedMissedShowcaseTradeIds = new Set<string>();
    if (sourceOnlyPositionTradeIds.length > 0) {
      const missedFillEvents = await this.prisma.signalCycleEvent.findMany({
        where: {
          eventType: 'EXPIRED',
          cycle: {
            agentId,
            tradeId: { in: sourceOnlyPositionTradeIds },
          },
          participant: {
            userId: instance.userId,
            status: SignalCycleStatus.EXPIRED,
          },
        },
        select: {
          payload: true,
          cycle: { select: { tradeId: true } },
        },
      });
      for (const event of missedFillEvents) {
        const payload =
          event.payload && typeof event.payload === 'object' && !Array.isArray(event.payload)
            ? (event.payload as Record<string, unknown>)
            : null;
        if (
          payload?.event === 'MISSED_SHOWCASE_FILL' ||
          payload?.reason === 'MISSED_SHOWCASE_FILL'
        ) {
          expectedMissedShowcaseTradeIds.add(event.cycle.tradeId);
        }
      }
    }
    const reportableDivergences = reportableMirrorDiffsForRelayMode(
      divergences,
      entryEnabled,
      copyDirection,
      relayArmTimestampMs(dash),
      expectedMissedShowcaseTradeIds,
    );

    // Rolling counters (per instance, persisted in dashboardState.mirrorDiff.rolling).
    const prev = (dash.mirrorDiff ?? {}) as {
      rolling?: {
        ticks?: number;
        divergentTicks?: number;
        lastDivergenceAt?: string | null;
        showcaseFillsSeen?: number;
        copyFillsCaptured?: number;
        avgRepriceLagMs?: number | null;
        repriceSamples?: number;
      };
    };
    const rolling = {
      ticks: (prev.rolling?.ticks ?? 0) + 1,
      divergentTicks:
        (prev.rolling?.divergentTicks ?? 0) +
        (reportableDivergences.length > 0 ? 1 : 0),
      lastDivergenceAt:
        reportableDivergences.length > 0
          ? new Date(now).toISOString()
          : (prev.rolling?.lastDivergenceAt ?? null),
      showcaseFillsSeen: prev.rolling?.showcaseFillsSeen ?? 0,
      copyFillsCaptured: prev.rolling?.copyFillsCaptured ?? 0,
      avgRepriceLagMs: prev.rolling?.avgRepriceLagMs ?? null,
      repriceSamples: prev.rolling?.repriceSamples ?? 0,
    };

    // Fill-capture counter: a showcase position trade_id NEW since the last
    // snapshot is a showcase fill; captured when the copy has an OPEN lot for it.
    const seenSet = this.mirrorSeenShowcasePositions.get(instance.id) ?? new Set<string>();
    const nextSeen = new Set<string>();
    for (const pos of showcasePositions) {
      const tid = pos.trade_id as string;
      nextSeen.add(tid);
      if (seenSet.has(tid)) continue;
      rolling.showcaseFillsSeen += 1;
      if (copyOpenTradeIds.has(tid)) rolling.copyFillsCaptured += 1;
    }
    this.mirrorSeenShowcasePositions.set(instance.id, nextSeen);

    // Reprice-lag EMA: participants whose PRICE_DELTA divergence window just
    // closed contribute a lag sample (first-divergence → convergence).
    for (const [pid, since] of [...this.mirrorDivergenceSince.entries()]) {
      const stillPending = copyPending.some((p) => p.id === pid);
      const stillDiverged = reportableDivergences.some(
        (d) => d.type === 'PRICE_DELTA' && d.participantId === pid,
      );
      if (stillDiverged) continue;
      // Converged (or participant left PENDING_ENTRY) — close the window.
      this.mirrorDivergenceSince.delete(pid);
      if (!stillPending) continue; // fill/expiry, not a reprice — no sample
      const lagMs = now - since;
      const prevEma = rolling.avgRepriceLagMs;
      rolling.avgRepriceLagMs =
        prevEma != null && Number.isFinite(prevEma)
          ? Math.round(prevEma * 0.8 + lagMs * 0.2)
          : lagMs;
      rolling.repriceSamples += 1;
    }

    const byType: Record<string, number> = {};
    for (const d of reportableDivergences) {
      byType[d.type] = (byType[d.type] ?? 0) + 1;
    }

    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        dashboardState: applyInstanceDashboardPatch(fresh.status, dash, {
          mirrorDiff: {
            at: new Date(now).toISOString(),
            botStateSource: botState.snapshot_source ?? 'live_bot',
            showcasePendingOrders: showcaseOrders.length,
            showcaseOpenPositions: showcasePositions.length,
            copyPendingOrders: copyPending.length,
            copyOpenLots: copyOpen.length,
            entryPolicy: entryEnabled ? 'ACTIVE' : 'EXIT_ONLY',
            suppressedExpectedSourceOnly:
              divergences.length - reportableDivergences.length,
            divergences: reportableDivergences.slice(0, 20),
            counts: { total: reportableDivergences.length, byType },
            rolling,
          },
        }) as unknown as Prisma.InputJsonValue,
      },
    });

    // Throttled MIRROR_DIFF event per diverged participant (max 1/60s each).
    for (const d of reportableDivergences) {
      if (!d.participantId || !d.cycleId) continue;
      const lastAt = this.mirrorDiffEventAt.get(d.participantId) ?? 0;
      if (now - lastAt < MIRROR_DIFF_EVENT_THROTTLE_MS) continue;
      this.mirrorDiffEventAt.set(d.participantId, now);
      await this.cycles
        .recordHireExecutionEvent(instance.userId, agentId, d.cycleId, 'MIRROR_DIFF', {
          venue: 'bitfinex',
          source: 'hire',
          diff_type: d.type,
          trade_id: d.tradeId,
          copy_limit: d.copyLimit,
          showcase_limit: d.showcaseLimit,
          delta_usd: d.deltaUsd,
          showcase_qty: d.showcaseQty,
          copy_qty: d.copyQty,
          delta_qty: d.deltaQty,
          showcase_dir: d.showcaseDir,
          copy_dir: d.copyDir,
        })
        .catch(() => {
          /* observability only — never abort the tick */
        });
    }

    // Bound the in-memory throttle maps.
    if (this.mirrorDiffEventAt.size > 500) {
      for (const [pid, at] of this.mirrorDiffEventAt) {
        if (now - at > 60 * 60_000) this.mirrorDiffEventAt.delete(pid);
      }
    }
  }

  private async enforceSustainedLiveFidelityGuard(
    agentId: string,
    instance: TradingAgentInstance,
    botState: BotApiState | null,
    simActive: boolean,
  ): Promise<boolean> {
    const nowMs = Date.now();
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instance.id },
      select: {
        status: true,
        exchangeProvider: true,
        dashboardState: true,
      },
    });
    if (!fresh) return false;

    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;
    const relayArmedAt =
      typeof dash.relayArmedAt === 'string' ? dash.relayArmedAt : null;
    const activeLive =
      !simActive &&
      fresh.exchangeProvider === 'bitfinex' &&
      fresh.status === TradingAgentInstanceStatus.ACTIVE &&
      dash.relayExecutionMode === 'LIVE' &&
      relayArmedAt != null;
    const previous = readLiveFidelityGuardState(dash.liveFidelityGuard);

    if (!isLiveFidelityGuardEnabled()) {
      const disabledState: LiveFidelityGuardState = {
        ...liveFidelityGuardBase(previous, relayArmedAt),
        enabled: false,
        status: 'IDLE',
        lastObservedAt: new Date(nowMs).toISOString(),
        lastResetReason: 'LIVE_FIDELITY_GUARD_DISABLED',
        action: previous?.action ?? null,
        lastTrippedAt: previous?.lastTrippedAt ?? null,
      };
      if (
        previous?.relayArmedAt !== relayArmedAt ||
        previous.enabled !== false ||
        previous.lastResetReason !== 'LIVE_FIDELITY_GUARD_DISABLED'
      ) {
        await this.persistLiveFidelityGuardState(instance.id, disabledState);
        this.logger.log(
          `[LIVE-FIDELITY-GUARD] skipped ${instance.userId}: LIVE_FIDELITY_GUARD_ENABLED kill-switch off`,
        );
      }
      return false;
    }

    const sourceFresh =
      isFreshCanonicalFidelityBotState(botState, nowMs) &&
      botState?.execution_paused !== true;
    const reconcileFresh = isFreshExactFidelityReconcile(
      dash.copyRelayReconcile,
      nowMs,
    );

    if (!activeLive || !sourceFresh || !reconcileFresh) {
      const resetReason = !activeLive
        ? 'LIVE_RELAY_INACTIVE'
        : !sourceFresh
          ? 'CANONICAL_SOURCE_STALE_UNKNOWN_OR_PAUSED'
          : 'EXCHANGE_RECONCILE_STALE_OR_UNKNOWN';
      if (
        previous?.relayArmedAt === relayArmedAt &&
        previous.status === 'IDLE' &&
        previous.lowObservationCount === 0 &&
        previous.lastResetReason === resetReason
      ) {
        return false;
      }
      const decision = advanceLiveFidelityGuard({
        previous,
        nowMs,
        activeLive,
        relayArmedAt,
        evidenceFresh: sourceFresh && reconcileFresh,
        scorePct: null,
        comparisonCount: 0,
        resetReason,
      });
      await this.persistLiveFidelityGuardState(instance.id, decision.state);
      return false;
    }

    // Do not repeat the history query every 2-second execution tick. The guard
    // observes at most every 30s inside this existing loop; the 90s persistence
    // requirement is therefore durable without creating another timer.
    if (
      previous?.relayArmedAt === relayArmedAt &&
      previous.lastObservedAt &&
      Number.isFinite(Date.parse(previous.lastObservedAt)) &&
      nowMs - Date.parse(previous.lastObservedAt) <
        LIVE_FIDELITY_GUARD_OBSERVATION_INTERVAL_MS
    ) {
      return false;
    }

    const sessionStartedAt = new Date(relayArmedAt);
    if (!Number.isFinite(sessionStartedAt.getTime())) {
      const decision = advanceLiveFidelityGuard({
        previous,
        nowMs,
        activeLive,
        relayArmedAt,
        evidenceFresh: false,
        scorePct: null,
        comparisonCount: 0,
        resetReason: 'RELAY_EPOCH_INVALID',
      });
      await this.persistLiveFidelityGuardState(instance.id, decision.state);
      return false;
    }

    const recentParticipants = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId: instance.userId,
        cycle: { agentId },
      },
      include: {
        cycle: {
          select: {
            id: true,
            tradeId: true,
            showcaseExitReason: true,
            closedAt: true,
          },
        },
        events: { orderBy: { createdAt: 'asc' } },
      },
      orderBy: { updatedAt: 'desc' },
      take: 512,
    });
    const sessionParticipants = recentParticipants.filter((participant) =>
      participantTouchesSession(participant, sessionStartedAt),
    );
    const fidelity = buildRelayFidelitySnapshot({
      bot: botState,
      participants: sessionParticipants,
      limit: 50,
      sessionStartedAt,
    });
    const observation = liveRelayFidelityObservation(fidelity);
    const decision = advanceLiveFidelityGuard({
      previous,
      nowMs,
      activeLive,
      relayArmedAt,
      evidenceFresh: true,
      scorePct: observation.scorePct,
      comparisonCount: observation.comparisonCount,
      resetReason: 'NO_MEANINGFUL_COMPARISON_DATA',
    });

    if (!decision.shouldTrip) {
      await this.persistLiveFidelityGuardState(instance.id, decision.state);
      return false;
    }

    await this.pauseRelayForSustainedFidelityBreach(
      agentId,
      instance,
      decision.state,
    );
    return true;
  }

  private async persistLiveFidelityGuardState(
    instanceId: string,
    state: LiveFidelityGuardState,
  ): Promise<void> {
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instanceId },
      select: { dashboardState: true },
    });
    if (!fresh) return;
    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;
    await this.prisma.tradingAgentInstance.update({
      where: { id: instanceId },
      data: {
        dashboardState: applyDashboardPatch(dash, {
          liveFidelityGuard: state,
        }) as unknown as Prisma.InputJsonValue,
      },
    });
  }

  private async resetLiveFidelityGuardWithoutEvidence(
    instance: TradingAgentInstance,
    reason: string,
  ): Promise<void> {
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instance.id },
      select: { dashboardState: true },
    });
    if (!fresh) return;
    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;
    const relayArmedAt =
      typeof dash.relayArmedAt === 'string' ? dash.relayArmedAt : null;
    const previous = readLiveFidelityGuardState(dash.liveFidelityGuard);
    if (
      previous?.relayArmedAt === relayArmedAt &&
      previous.status === 'IDLE' &&
      previous.lowObservationCount === 0 &&
      previous.lastResetReason === reason
    ) {
      return;
    }
    const decision = advanceLiveFidelityGuard({
      previous,
      nowMs: Date.now(),
      activeLive: true,
      relayArmedAt,
      evidenceFresh: false,
      scorePct: null,
      comparisonCount: 0,
      resetReason: reason,
    });
    await this.persistLiveFidelityGuardState(instance.id, decision.state);
  }

  private async pauseRelayForSustainedFidelityBreach(
    agentId: string,
    instance: TradingAgentInstance,
    state: LiveFidelityGuardState,
  ): Promise<void> {
    const scorePct = state.lastScorePct ?? 0;
    const spanSec = state.breachStartedAt && state.lastObservedAt
      ? Math.max(
          0,
          Math.round(
            (Date.parse(state.lastObservedAt) - Date.parse(state.breachStartedAt)) /
              1000,
          ),
        )
      : 0;
    const reason =
      `LIVE_FIDELITY_GUARD: ${scorePct.toFixed(2)}% below ` +
      `${LIVE_FIDELITY_GUARD_THRESHOLD_PCT}% for ${state.lowObservationCount} ` +
      `observations spanning ${spanSec}s; relay paused and pending entries cancelled. ` +
      'Open positions remain under exit/risk management; auto-flatten was not configured.';
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instance.id },
      select: { dashboardState: true },
    });
    const dash = (
      fresh?.dashboardState ??
      instance.dashboardState ??
      {}
    ) as Record<string, unknown>;
    const trippedState: LiveFidelityGuardState = {
      ...state,
      status: 'TRIPPED',
      action: {
        relayPaused: true,
        pendingEntriesCancelled: null,
        pendingEntryCleanupError: null,
        openPositionsFlattened: false,
        // There is currently no durable backend user consent for automatic
        // flattening. Browser localStorage is intentionally not treated as
        // permission to market-close real positions.
        flattenPolicy: 'NOT_CONFIGURED',
      },
    };
    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        status: TradingAgentInstanceStatus.PAUSED,
        lastError: reason.slice(0, 500),
        dashboardState: applyDashboardPatch(dash, {
          relayExecutionMode: 'PAUSED',
          relayArmedAt: null,
          realTradingConfirmedAt: null,
          liveFidelityGuard: trippedState,
        }) as unknown as Prisma.InputJsonValue,
      },
    });

    let pendingEntriesCancelled = 0;
    let pendingEntryCleanupError: string | null = null;
    try {
      pendingEntriesCancelled =
        await this.cancelVerifiedUnfilledPendingEntries(
          agentId,
          instance,
          'LIVE_FIDELITY_GUARD_CANCELLED_UNFILLED',
          'Live fidelity guard',
        );
    } catch (err) {
      pendingEntryCleanupError =
        err instanceof Error ? err.message : String(err);
      this.logger.error(
        `[LIVE-FIDELITY-GUARD] pending cleanup incomplete ${instance.userId}: ${pendingEntryCleanupError}`,
      );
    }

    const finalState: LiveFidelityGuardState = {
      ...trippedState,
      action: {
        ...trippedState.action!,
        pendingEntriesCancelled,
        pendingEntryCleanupError,
      },
    };
    const after = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instance.id },
      select: { dashboardState: true },
    });
    const afterDash = (
      after?.dashboardState ??
      dash
    ) as Record<string, unknown>;
    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        lastError: pendingEntryCleanupError
          ? `${reason} Pending cleanup incomplete: ${pendingEntryCleanupError}`.slice(0, 500)
          : reason.slice(0, 500),
        dashboardState: applyDashboardPatch(afterDash, {
          liveFidelityGuard: finalState,
        }) as unknown as Prisma.InputJsonValue,
      },
    });

    await this.notifications
      .notifyUser(instance.userId, {
        type: NotificationType.TRADING_AGENT_UPDATE,
        title: 'Conservative BTC live copy auto-stopped',
        body:
          `Relay fidelity stayed at ${scorePct.toFixed(1)}% (below 60%) for ` +
          `${spanSec}s. New entries are paused and ${pendingEntriesCancelled} ` +
          'unfilled pending order(s) were cancelled. Open positions were not auto-flattened.',
        link: `/agent-hub/${AGENT_SLUG}`,
      })
      .catch(() => {
        /* notification is best-effort after durable fail-closed persistence */
      });
  }

  private async buildVirtualLotSummary(
    participants: Array<{ id: string; status: SignalCycleStatus }>,
  ): Promise<VirtualLotSummary> {
    let open = 0;
    let pending = 0;
    let openQtySats = 0;
    let signedOpenQtySats = 0;
    let direction: 'LONG' | 'SHORT' | null = null;
    let directionConflict = false;

    for (const p of participants) {
      const meta = await this.loadExecutionMeta(p.id);
      let qty = meta.qty ?? 0;
      if (btcToSats(qty) === 0 && meta.margin_usd && meta.limitPrice) {
        qty = computeQty(
          meta.margin_usd,
          resolveSubscriberLeverage(),
          meta.limitPrice,
          MIN_QTY_BTC,
        );
      }
      if (!meta.direction) continue;
      if (direction != null && meta.direction !== direction) {
        directionConflict = true;
      } else if (direction == null) {
        direction = meta.direction;
      }
      if (p.status === SignalCycleStatus.OPEN) {
        open += 1;
        const qtySats = Math.abs(btcToSats(qty));
        openQtySats += qtySats;
        signedOpenQtySats += meta.direction === 'LONG' ? qtySats : -qtySats;
      } else if (p.status === SignalCycleStatus.PENDING_ENTRY) {
        pending += 1;
      }
    }

    return {
      open,
      pending,
      direction,
      openQty: satsToBtc(openQtySats),
      signedOpenQty: satsToBtc(signedOpenQtySats),
      directionConflict,
    };
  }

  private async persistCapacityState(
    instance: TradingAgentInstance,
    lotSummary: VirtualLotSummary,
    maxConcurrent: number,
    botMax: number | null,
    reject?: { reason: string | null } | null,
  ) {
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instance.id },
      select: { dashboardState: true, status: true },
    });
    if (!fresh) return;
    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;
    const prev = dash.copyRelayCapacity as CopyRelayCapacitySnapshot | undefined;
    const capacity = buildCopyRelayCapacity({
      open: lotSummary.open,
      pending: lotSummary.pending,
      capacityLimit: maxConcurrent,
      source:
        botMax != null && Number.isFinite(botMax)
          ? 'showcase_dashboard'
          : process.env.SUBSCRIBER_MAX_CONCURRENT_SIGNALS
            ? 'env_override'
            : 'default',
      showcaseMaxActiveSignals: botMax,
      lastRejectReason: reject?.reason ?? prev?.lastRejectReason ?? null,
      lastRejectAt: reject?.reason ? new Date().toISOString() : (prev?.lastRejectAt ?? null),
    });
    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        dashboardState: applyInstanceDashboardPatch(fresh.status, dash, {
          copyRelayCapacity: capacity,
          // Fix F — tick liveness watchdog. persistCapacityState already runs
          // once per processInstance (both exit-only and normal paths), so this
          // piggybacks on an existing per-tick dashboardState write — no extra
          // DB round-trip. A stale lastTickAt means the executor loop is dead.
          lastTickAt: new Date().toISOString(),
          relayExecutor: this.getHealthSnapshot(),
          // Option A — surface the dynamic-stops circuit-breaker state so the
          // operator sees which participants have tripped it (map of
          // participantId → lastError). Empty object when feature is off or no
          // participant has tripped the threshold.
          stopManagerCircuitOpen:
            this.stopManagerCircuitOpen.size > 0
              ? Object.fromEntries(this.stopManagerCircuitOpen)
              : {},
          exchangeDynamicStopsEnabled: exchangeDynamicStopsEnabled(),
        }) as unknown as Prisma.InputJsonValue,
      },
    });
  }

  /**
   * Phase 6 fix 3 — per-tick orphan surfacer. Lists active Bitfinex orders,
   * filters to foreign (not in `managedOrderIds`), and persists/clears
   * `dashboardState.orphanOrderIds` so the dashboard stays current even when
   * the bot is idle and no new signal arrives (the legacy path only surfaced
   * inside `evaluateEntryEligibility`, leaving orphans `undefined` for hours).
   * Returns the foreign orders so `cleanupOrphanCopyOrders` (fix 4) can reuse
   * them without a second `listActiveOrders` round-trip. Best-effort: never
   * aborts the tick on a Bitfinex lookup failure.
   */
  private async surfaceOrphanOrders(
    instance: TradingAgentInstance,
    creds: ExchangeCredentials,
    managedOrderIds: Set<number>,
    freshOrders?: BitfinexActiveOrder[],
  ): Promise<Array<{ id: number; amount?: number; amountOrig?: number; price?: number; status?: string; orderType?: string; cid?: number; createdAtMs?: number }>> {
    if (instance.exchangeProvider !== 'bitfinex') return [];
    let orders: Awaited<ReturnType<ExecutionTradingClient['listActiveOrders']>>;
    try {
      // The tick already refreshes this exact Bitfinex book immediately before
      // ledger reconciliation. Reusing that successful snapshot removes a
      // redundant authenticated request from the serialized nonce lane. Under
      // exchange latency, that third read could sit queued long enough to trip
      // the 60s watchdog at SURFACE_ORPHAN_ORDERS and unnecessarily disarm an
      // otherwise flat, healthy relay. If the refresh failed, retain the
      // original fail-closed read here instead of trusting stale data.
      orders = freshOrders ?? (await this.activeTrading.listActiveOrders(creds));
    } catch (err) {
      await this.persistExchangeOrderAudit(instance.id, {
        known: false,
        activeOrderCount: null,
        managedActiveOrderCount: null,
        foreignActiveOrderCount: null,
        checkedAt: new Date().toISOString(),
      }).catch(() => {
        /* the relay pause below remains the authoritative failure signal */
      });
      const message =
        `BITFINEX_ACTIVE_ORDER_READ_FAILED: unmanaged-order state is unknown; relay paused. ` +
        `${err instanceof Error ? err.message : String(err)}`;
      this.logger.warn(
        `Orphan surfacer ${instance.userId}: ${message}`,
      );
      await this.pauseRelayForPositionMismatch(instance, message);
      throw err;
    }
    const foreign = orders.filter((o) => !managedOrderIds.has(o.id));
    await this.persistExchangeOrderAudit(instance.id, {
      known: true,
      activeOrderCount: orders.length,
      managedActiveOrderCount: orders.length - foreign.length,
      foreignActiveOrderCount: foreign.length,
      checkedAt: new Date().toISOString(),
    }).catch(() => {
      /* a missing/stale audit makes the deployment flat gate fail closed */
    });
    if (foreign.length > 0) {
      await this.persistOrphanOrderIds(instance.id, foreign).catch(() => {
        /* dashboard surfacing is best-effort */
      });
    } else {
      await this.clearOrphanOrderIds(instance.id).catch(() => {
        /* best-effort */
      });
    }
    return foreign;
  }

  private async persistExchangeOrderAudit(
    instanceId: string,
    exchangeOrderAudit: {
      known: boolean;
      activeOrderCount: number | null;
      managedActiveOrderCount: number | null;
      foreignActiveOrderCount: number | null;
      checkedAt: string;
    },
  ) {
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instanceId },
      select: { dashboardState: true, status: true },
    });
    if (!fresh) return;
    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;
    await this.prisma.tradingAgentInstance.update({
      where: { id: instanceId },
      data: {
        dashboardState: applyInstanceDashboardPatch(fresh.status, dash, {
          exchangeOrderAudit,
        }) as unknown as Prisma.InputJsonValue,
      },
    });
  }

  /**
   * Surface foreign (unmanaged) Bitfinex order ids into dashboardState.orphanOrderIds
   * so the operator can see WHICH order to cancel on the exchange UI. Surface-only —
   * does NOT auto-cancel. dashboardState is JSON so no schema migration is required.
   */
  private async persistOrphanOrderIds(
    instanceId: string,
    foreignOrders: Array<{ id: number; amount?: number; amountOrig?: number; price?: number; status?: string; orderType?: string; cid?: number; createdAtMs?: number }>,
  ) {
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instanceId },
      select: { dashboardState: true },
    });
    if (!fresh) return;
    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;
    const orphanOrderIds = foreignOrders.map((o) => ({
      id: o.id,
      // Phase 4: parser now surfaces `cid` (Bitfinex v2 index [2]) so the
      // operator can cross-reference against ExecutionPayload.clientOrderId
      // shown in the participant timeline. Absent for manual / pre-Phase-2 orders.
      cid: o.cid ?? null,
      // amountRemaining: remaining qty on the exchange (signed).
      amountRemaining: o.amount ?? null,
      amountOrig: o.amountOrig ?? null,
      price: o.price ?? null,
      status: o.status ?? null,
      orderType: o.orderType ?? null,
    }));
    await this.prisma.tradingAgentInstance.update({
      where: { id: instanceId },
      data: {
        dashboardState: applyDashboardPatch(dash, {
          orphanOrderIds,
          orphanOrderDetectedAt: new Date().toISOString(),
        }) as unknown as Prisma.InputJsonValue,
      },
    });
  }

  /** Clear stale orphanOrderIds once no foreign orders remain. */
  private async clearOrphanOrderIds(instanceId: string) {
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instanceId },
      select: { dashboardState: true },
    });
    if (!fresh) return;
    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;
    if (!Array.isArray(dash.orphanOrderIds) && dash.orphanOrderDetectedAt == null) return;
    await this.prisma.tradingAgentInstance.update({
      where: { id: instanceId },
      data: {
        dashboardState: applyDashboardPatch(dash, {
          orphanOrderIds: [],
          orphanOrderDetectedAt: null,
        }) as unknown as Prisma.InputJsonValue,
      },
    });
  }

  private async reconcileLotLedger(
    agentId: string,
    instance: TradingAgentInstance,
    participants: Array<{ id: string; status: SignalCycleStatus }>,
    creds: ExchangeCredentials,
    managedOrderIds: Set<number>,
    activeOrders: BitfinexActiveOrder[],
    execMetaById: Map<string, ExecutionPayload>,
    simActive: boolean,
  ): Promise<boolean> {
    const summary = await this.buildVirtualLotSummary(participants);
    let position: Awaited<ReturnType<ExecutionTradingClient['getOpenPositionDetail']>>;
    try {
      position = await this.activeTrading.getOpenPositionDetail(creds);
    } catch (err) {
      const message =
        `EXCHANGE_POSITION_READ_FAILED: Bitfinex position is unknown; relay paused. ` +
        `${err instanceof Error ? err.message : String(err)}`;
      await this.pauseRelayForPositionMismatch(instance, message);
      return false;
    }

    const signedExchangeAmount = satsToBtc(btcToSats(position?.amount ?? 0));
    const rawExchangeQty = satsToBtc(Math.abs(btcToSats(signedExchangeAmount)));
    const ledgerOpenQty = summary.openQty;
    const signedLedgerOpenAmount = summary.signedOpenQty;
    const delta = satsToBtc(
      relayPositionDeltaSats(signedExchangeAmount, signedLedgerOpenAmount),
    );
    const mark = await this.activeTrading.getMarkPrice().catch(() => null);

    const reconcile = this.relaySim.buildReconcileSnapshot({
      exchangePositionQty: rawExchangeQty,
      exchangePositionAmount: signedExchangeAmount,
      ledgerOpenQty,
      ledgerOpenAmount: signedLedgerOpenAmount,
      openLots: summary.open,
      pendingLots: summary.pending,
      markPrice: mark,
    });

    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instance.id },
      select: { dashboardState: true, status: true },
    });
    if (!fresh) return false;
    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;
    const simActiveNow = isCopyRelaySimActive(dash);
    const priorGraceRaw = dash.pendingFillReconcileGrace;
    const priorGraceObject =
      priorGraceRaw && typeof priorGraceRaw === 'object' && !Array.isArray(priorGraceRaw)
        ? (priorGraceRaw as Record<string, unknown>)
        : null;
    const priorGrace =
      priorGraceObject &&
      typeof priorGraceObject.firstObservedAt === 'string' &&
      (priorGraceObject.direction === 'LONG' || priorGraceObject.direction === 'SHORT') &&
      Array.isArray(priorGraceObject.ownerParticipantIds)
        ? {
            firstObservedAtMs: Date.parse(priorGraceObject.firstObservedAt),
            direction: priorGraceObject.direction as 'LONG' | 'SHORT',
            ownerParticipantIds: priorGraceObject.ownerParticipantIds
              .filter((id): id is string => typeof id === 'string')
              .sort(),
          }
        : null;
    const pending = participants
      .filter((row) => row.status === SignalCycleStatus.PENDING_ENTRY)
      .map((row) => {
        const meta = execMetaById.get(row.id);
        return {
          participantId: row.id,
          direction: meta?.direction,
          qty: meta?.qty,
          bitfinexOrderId: meta?.bitfinexOrderId,
        };
      });
    const fillGrace = pendingFillReconcileDecision({
      nowMs: Date.now(),
      signedDeltaBtc: summary.directionConflict ? 0 : delta,
      pending,
      managedOrderIds,
      activeOrders,
      prior:
        priorGrace != null && Number.isFinite(priorGrace.firstObservedAtMs)
          ? priorGrace
          : null,
    });
    const pendingFillReconcileGrace = fillGrace.defer
      ? {
          firstObservedAt: new Date(fillGrace.firstObservedAtMs!).toISOString(),
          expiresAt: new Date(
            fillGrace.firstObservedAtMs! + PENDING_FILL_RECONCILE_GRACE_MS,
          ).toISOString(),
          direction: fillGrace.direction,
          deltaBtc: delta,
          ownerParticipantIds: fillGrace.ownerParticipantIds,
          reason: fillGrace.reason,
        }
      : null;
    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        dashboardState: applyInstanceDashboardPatch(fresh.status, dash, {
          copyRelayReconcile: reconcile,
          pendingFillReconcileGrace,
          ...(simActiveNow
            ? {
                copyRelaySim: {
                  ...readCopyRelaySimState(dash),
                  reconcile,
                },
              }
            : {}),
        }) as unknown as Prisma.InputJsonValue,
      },
    });

    if (summary.directionConflict) {
      const message =
        'LEDGER_DIRECTION_CONFLICT: simultaneous LONG and SHORT virtual lots cannot be ' +
        'represented by one merged BTC-PERP position; relay paused.';
      await this.pauseRelayForPositionMismatch(instance, message);
      return false;
    }

    if (btcToSats(delta) === 0) return true;

    if (fillGrace.defer) {
      this.logger.warn(
        `Pending fill reconcile grace ${instance.userId}: exchange ${signedExchangeAmount.toFixed(8)} BTC vs ` +
          `ledger ${signedLedgerOpenAmount.toFixed(8)} BTC (delta ${delta.toFixed(8)}); ` +
          `owners=${fillGrace.ownerParticipantIds.join(',')} — deferring this tick without new entries`,
      );
      return false;
    }

    // A managed stop/manual close may flatten Bitfinex after the early
    // immediate-flat read but before this later ledger read. Re-run the
    // existing full immediate-flat proof only for that exact race. It reads
    // every managed order, cancels any stale remainder, re-confirms exchange
    // zero, and records EXIT. Unknown exchange orders remain fail-closed.
    if (
      btcToSats(signedExchangeAmount) === 0 &&
      btcToSats(signedLedgerOpenAmount) !== 0 &&
      summary.open > 0 &&
      summary.pending === 0 &&
      !summary.directionConflict
    ) {
      let freshActiveOrders: BitfinexActiveOrder[];
      try {
        freshActiveOrders = await this.activeTrading.listActiveOrders(creds);
      } catch (err) {
        const message =
          `IMMEDIATE_FLAT_RETRY_ORDER_READ_FAILED: cannot prove active orders are managed; ` +
          `${err instanceof Error ? err.message : String(err)}`;
        await this.pauseRelayForPositionMismatch(instance, message);
        return false;
      }
      const foreignActiveOrders = freshActiveOrders.filter(
        (order) => !managedOrderIds.has(order.id),
      ).length;
      if (
        shouldRetryImmediateFlatReconcile({
          signedExchangeAmount,
          signedLedgerOpenAmount,
          openLots: summary.open,
          pendingLots: summary.pending,
          directionConflict: summary.directionConflict,
          foreignActiveOrders,
        })
      ) {
        this.logger.warn(
          `Managed exit race ${instance.userId}: Bitfinex flattened after the early read; ` +
          'retrying immediate-flat reconciliation before mismatch pause.',
        );
        const reconciled = await this.reconcileImmediateExchangeFlat(
          agentId,
          instance,
          creds,
          {},
          simActive,
        );
        // The summary above is stale after a successful EXIT. End the tick and
        // require the next one to rebuild all ledger/order state.
        if (reconciled) return false;
      }
    }

    this.logger.warn(
      `Virtual lot drift ${instance.userId}: exchange ${signedExchangeAmount.toFixed(8)} BTC vs ledger ${signedLedgerOpenAmount.toFixed(8)} BTC (Δ ${delta.toFixed(8)}, ${summary.open} open)`,
    );

    if (reconcile.alert) {
      this.cycleAudit.stage('RECONCILE', {
        userId: instance.userId,
        agentId,
        detail: `exchange ${signedExchangeAmount.toFixed(8)} ledger ${signedLedgerOpenAmount.toFixed(8)} Δ ${delta.toFixed(8)}`,
        meta: { reconcile },
      });
    }

    if (
      simActive &&
      position &&
      summary.open === 0 &&
      summary.pending === 0
    ) {
      try {
        await this.activeTrading.submitMarketClose(creds, {
          symbol: position.symbol,
          positionDirection: position.direction,
          qty: rawExchangeQty,
        });
        this.logger.warn(
          `Sim orphan exchange heal ${instance.userId}: flattened ${rawExchangeQty.toFixed(8)} BTC paper position (ledger empty)`,
        );
        await this.persistSimTickState(agentId, instance);
      } catch (err) {
        this.logger.error(
          `Sim orphan flatten failed ${instance.userId}: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }
      return false;
    }

    const message =
      fillGrace.reason === 'GRACE_EXPIRED'
        ? `PENDING_FILL_RECONCILE_GRACE_EXPIRED: exchange ${signedExchangeAmount.toFixed(8)} BTC ≠ ` +
          `ledger ${signedLedgerOpenAmount.toFixed(8)} BTC (Δ ${delta.toFixed(8)}). ` +
          'Managed fill attribution did not converge within 60 seconds; relay paused.'
        : `RECONCILE ALERT: exchange ${signedExchangeAmount.toFixed(8)} BTC ≠ ` +
          `ledger ${signedLedgerOpenAmount.toFixed(8)} BTC (Δ ${delta.toFixed(8)}). ` +
          'Relay paused; no blind close or ledger rewrite was attempted.';
    await this.pauseRelayForPositionMismatch(instance, message);
    return false;
  }

  private async closeOrphanLedgerLots(
    agentId: string,
    userId: string,
    excessBtc: number,
    participants: Array<{ id: string; status: SignalCycleStatus }>,
    creds: ExchangeCredentials,
    venue = 'bitfinex',
  ) {
    await Promise.reject(
      new Error(
        'PARTIAL_LEDGER_EXIT_RECONCILIATION_DISABLED: a partial exchange deficit cannot be safely attributed to a whole virtual lot.',
      ),
    );

    let remaining = excessBtc;
    const openRows = participants.filter((p) => p.status === SignalCycleStatus.OPEN);
    for (const row of openRows) {
      if (remaining < MIN_QTY_BTC) break;
      // Fix 5b — EXIT idempotency: recordHireExecutionEvent has no EXIT
      // dedupe, so a race with another reconcile path (or a second replica)
      // would double-record the EXIT. Skip rows that already exited.
      if (await this.hasParticipantExited(row.id)) continue;
      const meta = await this.loadExecutionMeta(row.id);
      if (!meta.qty || !meta.direction) continue;
      const participant = await this.prisma.signalCycleParticipant.findUnique({
        where: { id: row.id },
        include: { cycle: true },
      });
      if (!participant) continue;

      const mark = await this.activeTrading.getMarkPrice().catch(() => meta.limitPrice ?? 0);
      const fillPrice =
        participant.fillPrice != null
          ? Number(participant.fillPrice)
          : meta.limitPrice ?? mark;
      const leverage = DEFAULT_SUBSCRIBER_LEVERAGE;

      // Already-flat path: the exchange position is smaller than the ledger (or flat),
      // meaning Bitfinex's own stop or a partial external close fired before the relay
      // could reconcile this orphan lot. Attribute the real realized P&L instead of $0.
      const flat = await this.resolveAlreadyFlatPnl(
        agentId,
        userId,
        creds,
        row.id,
        meta,
        fillPrice,
        mark || fillPrice,
      );
      const pnlMarginPct =
        fillPrice && fillPrice > 0
          ? (flat.pnlUsd / (fillPrice * meta.qty)) * 100 * leverage
          : 0;

      // Money-path safety: cancel any leftover Bitfinex orders on this orphan lot
      // before recording the EXIT. Without this, a resting limit or stop left on
      // the exchange freezes new Live Copy entries (real incident: orphaned
      // Bitfinex order blocked entries for 60+ min until manual UI cancel).
      // Mirror the normal exit path (~L2391) but with a pre-fill guard: if the
      // order has already executed any quantity, leave it alone so
      // reconcileFilledParticipants can heal it as a fill instead of double-counting.
      const cancelledOids: number[] = [];
      let cancelStillLive = false;
      for (const oid of [meta.bitfinexOrderId, meta.stopOrderId]) {
        if (oid == null) continue;
        let orderResting: { amount: number; amountOrig: number } | null = null;
        try {
          orderResting = await this.activeTrading.findOrder(creds, oid).catch(() => null);
        } catch {
          orderResting = null;
        }
        if (!orderResting) {
          // Already gone (filled, cancelled, or expired) — nothing to cancel.
          this.logger.log(
            `[RECONCILE-ADOPT] orphan oid=${oid} not resting (gone) — skip cancel ${userId} participant=${row.id}`,
          );
          continue;
        }
        const filledQty = satsToBtc(exchangeOrderFilledQtySats(orderResting));
        if (btcToSats(filledQty) > 0) {
          // Partial/full fill — let reconcileFilledParticipants heal as a fill.
          this.logger.warn(
            `[RECONCILE-ADOPT] orphan oid=${oid} has filled qty=${filledQty.toFixed(5)} — NOT cancelling (defer to fill reconcile) ${userId} participant=${row.id}`,
          );
          continue;
        }
        // Fix 5a — fail-loud cancel. The prior bare cancelOrder inside
        // try/catch swallowed failures and recorded the EXIT anyway, leaving
        // a live order orphaned on the exchange behind a "closed" ledger row.
        const cancel = await this.cancelManagedOrderGone(
          creds,
          oid,
          `[RECONCILE-ADOPT] cancel orphan oid=${oid} reason=ORPHAN_LEDGER_RECONCILE ${userId} participant=${row.id}`,
        );
        if (cancel.gone) {
          cancelledOids.push(oid);
          this.logger.log(
            `[RECONCILE-ADOPT] cancelled orphan oid=${oid} ${userId} participant=${row.id}`,
          );
        } else {
          cancelStillLive = true;
          this.logger.error(
            `[RECONCILE-ADOPT] cancel orphan oid=${oid} FAILED and order still live (attempts=${cancel.attempts}, reason=${cancel.reason ?? 'unknown'}) — deferring EXIT to next tick ${userId} participant=${row.id}`,
          );
          await this.cycles
            .recordHireExecutionEvent(userId, agentId, participant.cycleId, 'RECONCILE_CANCEL_FAILED', {
              venue,
              source: 'hire',
              event: 'ORPHAN_LEDGER_RECONCILE',
              bitfinex_order_id: oid,
              participant_id: row.id,
              cancel_attempts: cancel.attempts,
              cancel_reason: cancel.reason ?? 'unknown',
            })
            .catch(() => {
              /* audit-best-effort */
            });
        }
      }

      if (cancelStillLive) {
        // Fix 5a — an order for this lot is confirmed still live on the
        // exchange. Recording the EXIT now would orphan it behind a closed
        // ledger row. Defer the whole lot to the next tick (idempotent —
        // this loop re-runs while ledger > exchange).
        continue;
      }

      await this.cycles.recordHireExecutionEvent(userId, agentId, participant.cycleId, 'EXIT', {
        venue,
        exit_price: mark || fillPrice,
        exit_reason: 'MANUAL_PARTIAL_CLOSE',
        pnl_usd: Math.round(flat.pnlUsd * 100) / 100,
        pnl_margin_pct: Math.round(pnlMarginPct * 100) / 100,
        qty_closed: meta.qty,
        pnl_source: flat.pnlSource,
        source: 'hire',
        event: 'ORPHAN_LEDGER_RECONCILE',
        cancelled_order_ids: cancelledOids,
      });

      if (cancelledOids.length > 0) {
        // Dedicated audit event so operators can see exactly which oids were
        // cancelled at orphan-exit time (separate from the EXIT row).
        await this.cycles.recordHireExecutionEvent(
          userId,
          agentId,
          participant.cycleId,
          'EXIT',
          {
            venue,
            source: 'hire',
            event: 'ORPHAN_LEDGER_RECONCILE_CANCEL',
            cancelled_order_ids: cancelledOids,
            participant_id: row.id,
          },
        );
      }

      this.logger.warn(
        `Orphan ledger lot closed ${userId} participant=${row.id} qty=${meta.qty} cancelledOids=[${cancelledOids.join(
          ',',
        )}] (ledger > exchange)`,
      );
      remaining -= meta.qty;
    }
  }

  private async persistSimTickState(agentId: string, instance: TradingAgentInstance) {
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instance.id },
      select: { dashboardState: true },
    });
    if (!fresh || !isCopyRelaySimActive(fresh.dashboardState)) return;
    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;
    const sim = readCopyRelaySimState(dash);
    const reconcile =
      (dash.copyRelayReconcile as ReturnType<CopyRelaySimService['buildReconcileSnapshot']>) ??
      sim.reconcile;
    const bot = this.botBridge.isEnabled() ? await this.botBridge.fetchStateForExecution(true).catch(() => null) : null;
    if (bot && typeof bot === 'object') {
      const stats = mapBotStateToAgentStats(bot);
      // Only carry forward the trade count here; persistSimState computes the
      // showcasePnlUsd delta from the sim-start baseline authoritatively so we
      // do NOT clobber it with the raw cumulative.
      sim.showcaseTradeCount = stats.tradeCount;
    }
    await this.relaySim.persistSimState(instance.id, instance.userId, sim, reconcile);
  }

  /**
   * Virtual lot ledger — same-direction legs on merged Bitfinex position.
   * Each lot exits via partial market close of its exact qty (Scenario C rules).
   */
  private async evaluateEntryEligibility(
    creds: ExchangeCredentials,
    managed: {
      open: number;
      pending: number;
      direction: 'LONG' | 'SHORT' | null;
    },
    managedOrderIds: Set<number>,
    marginCap: number,
    maxConcurrent: number,
    newDirection?: 'LONG' | 'SHORT',
    instance?: { id: string; dashboardState?: unknown },
    availableUsdOverride?: number,
    skipOrphanDashboardClear = false,
  ): Promise<EntryEligibility> {
    let available = availableUsdOverride;
    if (available == null) try {
      available = await this.activeTrading.getDerivativesAvailableUsd(creds);
    } catch (err) {
      this.logger.warn(
        `Could not read Bitfinex Derivatives balance: ${err instanceof Error ? err.message : err}`,
      );
      return {
        canEnter: false,
        reason: 'Could not read Bitfinex Derivatives balance — skipping new entries this tick.',
        availableUsd: null,
        slotsRemaining: 0,
      };
    }

    // Phase 6 fix 3 — read the already-persisted orphanOrderIds instead of
    // re-querying listActiveOrders. The per-tick `surfaceOrphanOrders` pass in
    // processInstance keeps this field current (even when idle), so the entry
    // gate stays accurate without a second exchange round-trip per entry
    // evaluation. `managedOrderIds` is retained for caller compatibility and
    // as a secondary cross-check when the persisted state is missing.
    const dashState = (instance?.dashboardState ?? {}) as Record<string, unknown>;
    const persistedOrphanIds = Array.isArray(dashState.orphanOrderIds)
      ? (dashState.orphanOrderIds as Array<{ id?: number; cid?: number | null }>)
      : [];
    const foreignPending = persistedOrphanIds.filter((o) => o && typeof o.id === 'number');
    if (foreignPending.length > 0) {
      return {
        canEnter: false,
        reason: `${foreignPending.length} unmanaged Bitfinex order(s) — cancel manually or wait before new copy entries. (ids: ${foreignPending
          .map((o) => o.id)
          .join(', ')})`,
        availableUsd: available,
        slotsRemaining: 0,
      };
    }

    // No foreign orders — clear any stale orphanOrderIds so the dashboard
    // doesn't show a ghost warning after the operator cancels. The per-tick
    // surfacer already does this, but keep the clear here as a belt-and-braces
    // fallback for the case where evaluateEntryEligibility runs without a
    // preceding surfacer (e.g. the idle `entriesThisTick === 0` branch below).
    if (instance?.id && !skipOrphanDashboardClear) {
      await this.clearOrphanOrderIds(instance.id).catch(() => {
        /* best-effort */
      });
    }
    void managedOrderIds;

    if (
      newDirection &&
      managed.direction &&
      newDirection !== managed.direction
    ) {
      return {
        canEnter: false,
        reason: `Copy ledger is ${managed.direction} — cannot add ${newDirection} on merged BTC-PERP until flat.`,
        availableUsd: available,
        slotsRemaining: 0,
      };
    }

    const totalLegs = managed.open + managed.pending;

    if (totalLegs >= maxConcurrent) {
      return {
        canEnter: false,
        reason: `Capacity cap: OPEN(${managed.open}) + PENDING(${managed.pending}) = ${totalLegs} >= limit ${maxConcurrent} (showcase max_active_signals).`,
        availableUsd: available,
        slotsRemaining: 0,
      };
    }

    const maxByMargin = Math.max(0, Math.floor((available * 0.95) / marginCap));
    const maxLegs = Math.min(maxConcurrent, maxByMargin);
    const minRequired = marginCap * 0.9;

    if (totalLegs >= maxLegs) {
      if (maxLegs <= 0 || available < minRequired) {
        return {
          canEnter: false,
          reason: `Insufficient Derivatives margin ($${available.toFixed(2)} available, need ~$${marginCap} per leg).`,
          availableUsd: available,
          slotsRemaining: 0,
        };
      }
      return {
        canEnter: false,
        reason: `Capacity cap: OPEN(${managed.open}) + PENDING(${managed.pending}) = ${totalLegs} >= limit ${maxLegs} (showcase max_active_signals).`,
        availableUsd: available,
        slotsRemaining: 0,
      };
    }

    if (managed.open >= maxConcurrent) {
      return {
        canEnter: false,
        reason: `Max ${maxConcurrent} open copy lots (showcase dashboard) — waiting for Scenario C exits.`,
        availableUsd: available,
        slotsRemaining: 0,
      };
    }

    if (managed.pending >= maxConcurrent) {
      return {
        canEnter: false,
        reason: `Max ${maxConcurrent} pending limits (showcase dashboard) — waiting for fills/chase.`,
        availableUsd: available,
        slotsRemaining: 0,
      };
    }

    if (available < minRequired) {
      return {
        canEnter: false,
        reason: `Insufficient Derivatives margin ($${available.toFixed(2)} available, need ~$${marginCap} per leg).`,
        availableUsd: available,
        slotsRemaining: 0,
      };
    }

    return {
      canEnter: true,
      reason: null,
      availableUsd: available,
      slotsRemaining: maxLegs - totalLegs,
    };
  }

  /**
   * Showcase filled OPEN while copy still holds a resting limit for the same
   * trade_id. Cancel the stale limit so mirror catch-up can market-enter.
   */
  private async clearPendingForShowcaseCatchup(
    agentId: string,
    userId: string,
    participant: { id: string; cycleId: string },
    creds: ExchangeCredentials,
  ): Promise<boolean> {
    const release = await this.acquireParticipantMoneyLane(participant.id);
    try {
    const [freshParticipant, freshCycle, meta] = await Promise.all([
      this.prisma.signalCycleParticipant.findUnique({
        where: { id: participant.id },
        select: { status: true, cycleId: true },
      }),
      this.prisma.signalCycle.findUnique({
        where: { id: participant.cycleId },
        select: { id: true },
      }),
      this.loadExecutionMeta(participant.id),
    ]);
    if (
      !freshParticipant
      || !freshCycle
      || freshParticipant.status !== SignalCycleStatus.PENDING_ENTRY
      || freshParticipant.cycleId !== participant.cycleId
    ) return false;
    const orderId = meta.bitfinexOrderId;
    if (orderId) {
      const fill = await this.detectEntryFillBeforeCancel(creds, meta).catch(() => null);
      if (fill) {
        this.logger.warn(
          `[MIRROR-CATCHUP] pending ${participant.id} trade had real fill before clear — deferring to fill path`,
        );
        return false;
      }
      const cancel = await this.cancelManagedOrderGone(
        creds,
        orderId,
        `Showcase catch-up clear ${userId} cycle=${participant.cycleId} cancel stale limit ${orderId}`,
      );
      if (!cancel.gone) {
        this.logger.warn(
          `[MIRROR-CATCHUP] could not clear pending ${participant.id} order ${orderId} — cancel failed`,
        );
        return false;
      }
      if (
        cancel.reason === 'NOT_FOUND'
        && !(await this.replacementTerminalUnfilledProof(creds, meta))
      ) return false;
    }

    await this.cycles.recordHireExecutionEvent(userId, agentId, participant.cycleId, 'EXPIRED', {
      venue: 'bitfinex',
      source: 'hire',
      event: 'SHOWCASE_FILLED_CATCHUP_CLEAR',
      pnl_usd: 0,
      bitfinex_order_id: orderId ?? undefined,
    });
    this.logger.warn(
      `[MIRROR-CATCHUP] cleared stale pending ${participant.id} cycle=${participant.cycleId} for showcase catch-up`,
    );
    return true;
    } finally {
      release();
    }
  }

  /**
   * Phase 1 book-state dedupe (MIRROR_CONVERGENCE_ENABLED). The showcase bot
   * spawns the same signal into multiple research lanes (cont-/vc603-) but
   * its own paper book holds only ONE order per limit price (duplicates are
   * expired as DUPLICATE_LIMIT_PRICE). The copy must mirror the BOOK, not the
   * spawns: if any other PENDING_ENTRY participant already owns a real
   * resting order at (epsilon-)the same limit price — either its current
   * copy-side limit or its showcase anchor — the new entry is a duplicate.
   * Returns the mirror-owner participant, or null when the price is unique.
   */
  private async findDuplicateRestingLimit(
    userId: string,
    agentId: string,
    cycleId: string,
    limitPrice: number,
    botState: BotApiState | null,
  ): Promise<{ participantId: string; cycleId: string; tradeId: string | null; price: number } | null> {
    const pendings = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId },
        NOT: { cycleId },
      },
      include: { cycle: { select: { tradeId: true } } },
    });
    for (const p of pendings) {
      const meta = await this.loadExecutionMeta(p.id);
      // Only a participant with a REAL resting order can be the mirror owner.
      if (!meta.bitfinexOrderId) continue;
      const anchor = p.cycle?.tradeId
        ? this.resolveBotLimitPrice(botState, p.cycle.tradeId)
        : null;
      const prices = [meta.limitPrice, anchor].filter(
        (x): x is number => x != null && Number.isFinite(x) && x > 0,
      );
      if (prices.some((price) => Math.abs(price - limitPrice) < DUPLICATE_LIMIT_EPSILON_USD)) {
        return {
          participantId: p.id,
          cycleId: p.cycleId,
          tradeId: p.cycle?.tradeId ?? null,
          price: meta.limitPrice ?? limitPrice,
        };
      }
    }
    return null;
  }

  private async placeEntry(
    agentId: string,
    instance: TradingAgentInstance,
    cycleId: string,
    envelopeJson: unknown,
    creds: ExchangeCredentials,
    marginCap: number,
    tradeId: string,
    venue = 'bitfinex',
    fastPreflight?: {
      availableUsd: number;
      markPrice: number;
      exchangeBookProvenEmpty?: boolean;
    },
  ): Promise<boolean> {
    const intent = envelopeJson as SignalIntentEnvelope & {
      entry: SignalIntentEnvelope['entry'] & { exact_qty_btc?: number };
    };
    if (
      !tradeId
      || !intent?.direction
      || intent.action !== 'ENTER'
      || intent.signalId !== tradeId
      || intent.entry?.mode !== 'EXACT_LIMIT'
      || intent.entry?.reference !== 'SHOWCASE_EXACT_LIMIT'
      || typeof intent.entry?.exact_qty_btc !== 'number'
      || !isExecutableEntryPolicy(intent.context?.entry_limit_policy)
    ) {
      this.logger.warn(
        `Hire reject ${instance.userId} cycle=${cycleId}: exact structural envelope identity/policy missing`,
      );
      return false;
    }
    const signedExactLimit = readFreshSignedShowcaseExactLimit(tradeId, envelopeJson);

    // F1 — Showcase-unreachable safe mode. Refuse new entries while the
    // showcase has been unreachable past ENTRY_BLOCK_MS. The 2026-07-07
    // incident showed the relay would otherwise keep trading against a stale
    // intent while the operator's tunnel was down, producing orphan fills
    // with no showcase counterpart to mirror the exit. Fail closed for new
    // money; F2 separately handles OPEN lots past ORPHAN_KILL_MS.
    if (showcaseUnreachableSafeModeEnabled() && !signedExactLimit) {
      const block = this.entryBlockedByShowcaseOutage(instance.id);
      if (block.blocked) {
        this.logger.warn(
          `[F1] Entry blocked ${instance.userId} cycle=${cycleId}: showcase unreachable for ${Math.round(block.elapsedMs / 1000)}s (≥ SHOWCASE_UNREACHABLE_ENTRY_BLOCK_MS=${SHOWCASE_UNREACHABLE_ENTRY_BLOCK_MS}ms)`,
        );
        const msg = `Showcase unreachable for ${Math.round(block.elapsedMs / 1000)}s — live copy in safe mode (no new entries). Restoring the tunnel will resume copy automatically.`;
        await this.prisma.tradingAgentInstance
          .update({ where: { id: instance.id }, data: { lastError: msg } })
          .catch(() => {});
        this.cycleAudit.stage('ENTRY_BLOCKED_SHOWCASE_OUTAGE', {
          userId: instance.userId,
          agentId,
          cycleId,
          tradeId,
          detail: msg,
          meta: {
            elapsed_ms: block.elapsedMs,
            threshold_ms: SHOWCASE_UNREACHABLE_ENTRY_BLOCK_MS,
          },
        });
        return false;
      }
    }

    // Resolve the one authoritative exact price before creating an atomic
    // participant claim. Visibility-only approvals and legacy percentage
    // envelopes are never allowed to reach the exchange write below.
    let botStateForEntry: BotApiState | null = null;
    let rawLimit: number | null = signedExactLimit?.limitPrice ?? null;
    if (!signedExactLimit) {
      if (!(await this.botBridge.isEnabledAsync())) {
        await this.prisma.tradingAgentInstance.update({
          where: { id: instance.id },
          data: { lastError: 'Showcase bridge unavailable - exact-copy entry blocked.' },
        });
        return false;
      }
      botStateForEntry = await this.fetchExecutionBotState();
      if (!botStateForEntry) {
        await this.prisma.tradingAgentInstance.update({
          where: { id: instance.id },
          data: { lastError: 'Showcase state unavailable - exact-copy entry blocked.' },
        });
        return false;
      }
      const defer = this.showcaseCopyEntryReady(botStateForEntry, tradeId);
      if (!defer.ready) {
        const reason = defer.reason ?? 'Waiting for showcase limit.';
        this.cycleAudit.stage('SIGNAL', {
          userId: instance.userId,
          agentId,
          cycleId,
          tradeId,
          detail: reason,
        });
        return false;
      }
      const canonicalOrder = this.showcasePendingOrder(botStateForEntry, tradeId);
      const canonicalDirectionRaw = String(
        canonicalOrder?.signal_dir ?? canonicalOrder?.side ?? '',
      ).toUpperCase();
      const canonicalDirection =
        canonicalDirectionRaw === 'BUY' || canonicalDirectionRaw.includes('LONG')
          ? 'LONG'
          : canonicalDirectionRaw === 'SELL' || canonicalDirectionRaw.includes('SHORT')
            ? 'SHORT'
            : null;
      if (
        !canonicalOrder
        || canonicalDirection !== intent.direction
        || !Number.isFinite(canonicalOrder.limit_price)
        || Number(canonicalOrder.limit_price) <= 0
      ) {
        this.logger.warn(
          `Hire reject ${instance.userId} cycle=${cycleId}: canonical exact order direction/price mismatch`,
        );
        return false;
      }
      rawLimit = Number(canonicalOrder.limit_price);
    } else {
      botStateForEntry = mergeSignedShowcaseOrders(null, [signedExactLimit]);
      this.logger.log(
        `[SIGNED-FAST] exact showcase order accepted trade=${tradeId} limit=${signedExactLimit.limitPrice.toFixed(2)} qty=${signedExactLimit.exactQtyBtc.toFixed(8)} ageMs=${Date.now() - signedExactLimit.receivedAtMs}`,
      );
    }

    const mark = fastPreflight?.markPrice ?? await this.activeTrading.getMarkPrice();
    const limitPrice =
      rawLimit != null
        ? sanitizeLimitPrice(mark, rawLimit, intent.direction)
        : null;
    if (rawLimit == null || limitPrice == null) {
      this.logger.error(
        `Hire reject ${instance.userId} cycle=${cycleId}: exact limit ${rawLimit ?? 'missing'} failed sanity vs mark ${mark.toFixed(2)}`,
      );
      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: {
          lastError: `Exact showcase limit rejected - price sanity check failed (mark ~$${mark.toFixed(0)}).`,
        },
      });
      return false;
    }

    const intentCap = intent.risk?.max_margin_usd;
    const effectiveCap =
      intentCap != null && Number.isFinite(intentCap) && intentCap > 0
        ? Math.min(marginCap, intentCap)
        : marginCap;
    const leverage = resolveSubscriberLeverage(intent);
    const exactQty = resolveExactShowcaseEntryQty({
      exactQtyBtc: intent.entry.exact_qty_btc,
      maxMarginUsd: effectiveCap,
      leverage,
      limitPrice,
      minQtyBtc: MIN_QTY_BTC,
    });
    if (!exactQty.ok) {
      this.logger.error(
        `Hire reject ${instance.userId} cycle=${cycleId}: exact showcase quantity rejected reason=${exactQty.reason}`,
      );
      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: {
          lastError: exactQty.reason === 'SOURCE_QTY_EXCEEDS_SUBSCRIBER_CAP'
            ? 'Showcase quantity exceeds your configured per-trade margin cap; exact copy was blocked.'
            : 'Signed showcase order is missing a valid exact quantity; entry was blocked.',
        },
      });
      return false;
    }
    const qty = exactQty.qty;
    const marginUsd = exactQty.requiredMarginUsd;

    let available = fastPreflight?.availableUsd ?? 0;
    if (!fastPreflight) {
      try {
        available = await this.activeTrading.getDerivativesAvailableUsd(creds);
      } catch (err) {
        this.logger.warn(
          `Hire skip ${instance.userId} cycle=${cycleId}: Derivatives balance check failed — ${err instanceof Error ? err.message : err}`,
        );
        return false;
      }
    }

    if (available * 0.95 < marginUsd) {
      this.logger.log(
        `Hire skip ${instance.userId} cycle=${cycleId}: free margin $${available.toFixed(2)} < $${marginUsd.toFixed(2)} exact-copy requirement`,
      );
      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: {
          lastError: `Insufficient Derivatives margin ($${available.toFixed(2)} available, need ~$${marginUsd.toFixed(2)}). Move USDT to Derivatives in Bitfinex.`,
        },
      });
      return false;
    }

    // C5 multi-replica atomic claim: declared outside the try so the catch can release it
    // if order placement fails (otherwise the cycle gets stuck on an INTENT claim with no
    // order, blocking all future retries for this user/cycle).
    let claimParticipantId: string | null = null;

    try {
    // C5 multi-replica atomic claim: create the participant row with INTENT status BEFORE
    // placing the order. If a second API replica is running placeEntry for the same
    // cycle/user concurrently, its create hits the (cycleId, userId) unique constraint
    // (P2002) and it bails out — preventing a DUPLICATE order on Bitfinex. The claim is
    // released (deleted) if order placement fails, so a future tick can retry.
    try {
      const claimed = await this.prisma.signalCycleParticipant.create({
        data: {
          cycleId,
          userId: instance.userId,
          venue,
          status: SignalCycleStatus.INTENT,
        },
      });
      claimParticipantId = claimed.id;
    } catch (err) {
      if (err instanceof PrismaClientKnownRequestError && err.code === 'P2002') {
        this.logger.log(
          `Hire claim-lost ${instance.userId} cycle=${cycleId} — an existing participant owns it`,
        );
        return false;
      }
      throw err;
    }
      // Phase 1 — book-state dedupe (flag-gated). If the copy already has a
      // real resting order at this limit price (any lane/participant), do NOT
      // place a second: the earlier participant is the mirror owner of this
      // book entry. Expire this claim ledger-side WITHOUT touching the
      // exchange (mirrors the showcase book's own DUPLICATE_LIMIT_PRICE).
      if (mirrorConvergenceEnabled() && !fastPreflight?.exchangeBookProvenEmpty) {
        const dup = await this.findDuplicateRestingLimit(
          instance.userId,
          agentId,
          cycleId,
          limitPrice,
          botStateForEntry,
        );
        if (dup) {
          this.logger.log(
            `Hire dedupe ${instance.userId} cycle=${cycleId}: limit ${limitPrice.toFixed(2)} duplicates mirror-owner participant=${dup.participantId} (trade=${dup.tradeId ?? 'n/a'}) — expiring ledger-side, no order placed`,
          );
          await this.cycles.recordHireExecutionEvent(
            instance.userId,
            agentId,
            cycleId,
            'DUPLICATE_LIMIT_SKIPPED',
            {
              venue,
              source: 'hire',
              limit_price: limitPrice,
              duplicate_of_participant: dup.participantId,
              duplicate_of_cycle: dup.cycleId,
              duplicate_trade_id: dup.tradeId,
              mirror_owner_price: dup.price,
            },
          );
          await this.cycles.recordHireExecutionEvent(instance.userId, agentId, cycleId, 'EXPIRED', {
            venue,
            pnl_usd: 0,
            source: 'hire',
            event: 'DUPLICATE_LIMIT_SKIPPED',
            duplicate_of_participant: dup.participantId,
          });
          return false;
        }
      }

    let prePosition: Awaited<ReturnType<BitfinexTradingClient['getOpenPositionDetail']>>;
    let latestGate: { status: TradingAgentInstanceStatus; dashboardState: unknown } | null = null;
    let latestCycle: { createdAt: Date } | null = null;
    try {
      [prePosition, [latestGate, latestCycle]] = await Promise.all([
        this.activeTrading.getOpenPositionDetail(creds),
        venue === 'bitfinex'
          ? Promise.all([
              this.prisma.tradingAgentInstance.findUnique({
                where: { id: instance.id },
                select: { status: true, dashboardState: true },
              }),
              this.prisma.signalCycle.findUnique({
                where: { id: cycleId },
                select: { createdAt: true },
              }),
            ])
          : Promise.resolve([null, null] as const),
      ]);
    } catch (err) {
      if (claimParticipantId) {
        await this.prisma.signalCycleParticipant
          .delete({ where: { id: claimParticipantId } })
          .catch(() => {});
        claimParticipantId = null;
      }
      this.logger.warn(
        `Hire entry blocked ${instance.userId} cycle=${cycleId}: final exchange/gate preflight failed — ${
          err instanceof Error ? err.message : err
        }`,
      );
      return false;
    }
    const exchangeQtyAtOrder = prePosition ? Math.abs(prePosition.amount) : 0;

    // Re-read the account-holder gate immediately before the exchange write.
    // A Stop click racing an already-running executor tick must win; the
    // atomic participant claim is released so a later explicit Start can
    // consume only a newly-created post-arm cycle.
    if (venue === 'bitfinex') {
      if (
        !latestGate ||
        !latestCycle ||
        latestGate.status !== TradingAgentInstanceStatus.ACTIVE ||
        !isCycleFreshForRelayArm(latestGate.dashboardState, latestCycle.createdAt)
      ) {
        if (claimParticipantId) {
          await this.prisma.signalCycleParticipant
            .delete({ where: { id: claimParticipantId } })
            .catch(() => {});
          claimParticipantId = null;
        }
        this.logger.warn(
          `Hire entry stopped before submit ${instance.userId} cycle=${cycleId}: relay no longer armed`,
        );
        return false;
      }
    }

    // Phase 2: deterministic Bitfinex `cid` so a future reconcile-adopt pass
    // can match this order even if bitfinexOrderId was not persisted in the
    // ORDER_PLACED event payload. claimParticipantId is set above after the
    // atomic claim succeeded.
    const clientOrderId = claimParticipantId
      ? computeClientOrderId(cycleId, claimParticipantId, tradeId)
      : undefined;

    const orderId = await this.activeTrading.submitLimitOrder(creds, {
      direction: intent.direction,
      qty,
      price: limitPrice,
        leverage,
      ...(clientOrderId != null ? { clientOrderId } : {}),
    });
    const exchangeAckAtMs = Date.now();

    const payload: ExecutionPayload = {
      bitfinexOrderId: orderId,
      limitPrice,
        originalLimitPrice: limitPrice,
      localMark: mark,
      qty,
      direction: intent.direction,
      exchangeQtyAtOrder,
      margin_usd: marginUsd,
      source: 'hire',
        lastChaseAtMs: 0,
      limitChaseCount: 0,
      ...(signedExactLimit
        ? {
            platformReceivedAt: new Date(signedExactLimit.receivedAtMs).toISOString(),
            platformToExchangeAckMs: Math.max(
              0,
              exchangeAckAtMs - signedExactLimit.receivedAtMs,
            ),
            ...(signedExactLimit.sourceEventAtMs != null
              ? {
                  sourceEventAt: new Date(
                    signedExactLimit.sourceEventAtMs,
                  ).toISOString(),
                  sourceToPlatformMs: Math.max(
                    0,
                    signedExactLimit.receivedAtMs - signedExactLimit.sourceEventAtMs,
                  ),
                  sourceToExchangeAckMs: Math.max(
                    0,
                    exchangeAckAtMs - signedExactLimit.sourceEventAtMs,
                  ),
                }
              : {}),
          }
        : {}),
      ...(clientOrderId != null ? { clientOrderId } : {}),
    };

    await this.cycles.recordHireExecutionEvent(instance.userId, agentId, cycleId, 'ORDER_PLACED', {
      venue,
      local_mark_at_signal: mark,
      limit_price: limitPrice,
        original_limit_price: limitPrice,
      qty,
        source_exact_qty_btc: intent.entry.exact_qty_btc,
        venue_qty_btc: qty,
        margin_usd: marginUsd,
        margin_cap_usd: effectiveCap,
        leverage,
      ...payload,
    });

    this.cycleAudit.stage('ORDER_PLACED', {
      userId: instance.userId,
      agentId,
      cycleId,
      tradeId,
      venue,
      meta: { qty, limitPrice, orderId },
    });

      const participant = await this.prisma.signalCycleParticipant.findUnique({
        where: { cycleId_userId: { cycleId, userId: instance.userId } },
      });
      if (participant) {
        await this.applyLimitChase(
          agentId,
          instance.userId,
          cycleId,
          participant.id,
          payload,
          creds,
          intent,
          tradeId,
          true,
        );
      }

    this.logger.log(
        `Hire entry ${instance.userId} cycle=${cycleId} ${intent.direction} limit=${limitPrice.toFixed(2)} qty=${qty} margin=$${marginUsd.toFixed(2)} lev=${leverage}x`,
      );
      return true;
    } catch (err) {
      // C5: order placement (or pre-order checks) failed — release the atomic claim so a
      // future tick can retry this cycle instead of being permanently blocked.
      if (claimParticipantId) {
        await this.prisma.signalCycleParticipant
          .delete({ where: { id: claimParticipantId } })
          .catch(() => {
            /* claim may already be gone (e.g. concurrently transitioned) */
          });
      }
      const msg = err instanceof Error ? err.message : String(err);
      const lowMargin =
        /insufficient|balance|margin|not enough|funds/i.test(msg) ||
        msg.includes('balance');
      if (lowMargin) {
        this.logger.log(`Hire skip ${instance.userId} cycle=${cycleId}: exchange rejected entry — ${msg}`);
        await this.prisma.tradingAgentInstance.update({
          where: { id: instance.id },
          data: {
            lastError: `Bitfinex declined new entry (margin in use or balance too low). Open trades still managed automatically.`,
          },
        });
        return false;
      }
      throw err;
    }
  }

  /**
   * Money-path cancel helper. Retries the cancel with backoff via
   * {@link cancelOrderWithRetry}, then — only if the cancel API reported a
   * response, always verifies with {@link confirmOrderGone} whether the order
   * is actually still on the active book. Returns `gone: true` only when a
   * follow-up `findOrder` confirms the order is no longer active; ONLY in that
   * case may the caller record an EXPIRED ledger event. Returns `gone: false`
   * when the order is still live or the verification read fails — caller must
   * leave the participant PENDING_ENTRY,
   * set `instance.lastError = 'CANCEL_FAILED_ORDER_STILL_LIVE'`, audit
   * `RECONCILE_CANCEL_FAILED`, and let the next tick retry.
   */
  private async cancelManagedOrderGone(
    creds: ExchangeCredentials,
    orderId: number,
    label: string,
    onTiming?: (timing: { submitStartedAtMs: number; exchangeAckAtMs: number; confirmedAtMs: number }) => void,
  ): Promise<{ gone: boolean; reason?: string; attempts: number }> {
    const client = this.activeTrading as CancelCapableClient;
    const submitStartedAtMs = Date.now();
    const result = await cancelOrderWithRetry(client, creds, orderId, {
      logger: this.logger,
      label,
    });
    const exchangeAckAtMs = Date.now();
    // Bitfinex can acknowledge the HTTP request while the v2 notification is
    // stale or reports an application-level error. Treat the cancel response
    // as intent only; every outcome must be followed by an active-book read.
    const gone = await confirmOrderGone(client, creds, orderId);
    const confirmedAtMs = Date.now();
    onTiming?.({ submitStartedAtMs, exchangeAckAtMs, confirmedAtMs });
    return {
      gone,
      reason:
        result.reason
        ?? (result.ok && !gone ? 'CANCEL_ACK_NOT_CONFIRMED' : undefined),
      attempts: result.attempts,
    };
  }

  private async replacementTerminalUnfilledProof(
    creds: ExchangeCredentials,
    meta: ExecutionPayload,
  ): Promise<boolean> {
    const orderId = meta.bitfinexOrderId;
    if (!orderId || !meta.replacementExchangeAckAtMs) return true;
    try {
      const [trades, history] = await Promise.all([
        this.bitfinex.fetchOrderTrades(creds, orderId),
        this.bitfinex.fetchOrderHistoryEvidence(creds, orderId),
      ]);
      return (
        !trades.some((trade) => Math.abs(trade.execAmount) > 0)
        && history?.terminal === true
        && history.filledQty === 0
      );
    } catch {
      return false;
    }
  }

  /**
   * Cancel-race fill check (always on — NOT gated by
   * MIRROR_CONVERGENCE_ENABLED; misclassification is a bug, not a feature).
   * Before the copy cancels its own entry order — or classifies a vanished
   * order as cancelled-by-exchange — verify the order did not actually
   * (partially) fill:
   *  - Order still resting: `|amountOrig| - |amount|` > MIN_QTY_BTC means a
   *    partial execution the cancel would silently discard.
   *  - Order gone entirely: the merged position grew by ~the lot qty vs the
   *    `exchangeQtyAtOrder` baseline (same heuristic monitorEntry's fill
   *    classifier uses) — the order FILLED, it was not cancelled.
   * Returns null when it is safe to treat the order as unfilled.
   */
  private async detectEntryFillBeforeCancel(
    creds: ExchangeCredentials,
    meta: ExecutionPayload,
    failClosedOnExchangeError = false,
  ): Promise<{
    filledQty: number;
    fillPrice: number;
    source: 'ORDER_PARTIAL' | 'POSITION_DELTA';
    orderResting: boolean;
  } | null> {
    const orderId = meta.bitfinexOrderId;
    if (!orderId || !meta.direction) return null;

    const order = failClosedOnExchangeError
      ? await this.activeTrading.findOrder(creds, orderId)
      : await this.activeTrading.findOrder(creds, orderId).catch(() => null);
    if (order) {
      const filled = satsToBtc(exchangeOrderFilledQtySats(order));
      if (btcToSats(filled) > 0) {
        return {
          filledQty: filled,
          fillPrice: order.price > 0 ? order.price : (meta.limitPrice ?? 0),
          source: 'ORDER_PARTIAL',
          orderResting: true,
        };
      }
      return null; // resting, unfilled — safe to cancel
    }

    // Order gone from the active book — filled or cancelled. Discriminate via
    // the merged-position delta against the at-order baseline.
    const position = failClosedOnExchangeError
      ? await this.activeTrading.getOpenPositionDetail(creds)
      : await this.activeTrading.getOpenPositionDetail(creds).catch(() => null);
    const expectedLong = meta.direction === 'LONG';
    const hasPosition =
      position &&
      ((expectedLong && position.amount > 0) || (!expectedLong && position.amount < 0));
    if (!hasPosition) return null;

    const baseline = meta.exchangeQtyAtOrder ?? 0;
    const lotQty = meta.qty ?? MIN_QTY_BTC;
    const currentQty = Math.abs(position!.amount);
    if (currentQty + MIN_QTY_BTC < baseline + lotQty * 0.85) return null;

    return {
      filledQty: lotQty,
      fillPrice:
        meta.limitPrice && meta.limitPrice > 0 ? meta.limitPrice : position!.basePrice,
      source: 'POSITION_DELTA',
      orderResting: false,
    };
  }

  /**
   * Fix D — authoritative fill price from Bitfinex per-order trade history
   * (POST /v2/auth/r/order/{symbol}:{orderId}/trades). Returns the
   * volume-weighted real execution price across the order's executions, or
   * null on error/empty so callers keep their existing approximation
   * (meta.limitPrice / merged basePrice). Cheap by design: only invoked at
   * fill-RECORDING time (never per tick) and always best-effort.
   */
  private async resolveExchangeTradesFillEvidence(
    creds: ExchangeCredentials,
    orderId: number | null | undefined,
  ): Promise<{
    price: number;
    qty: number;
    firstExecutedAtMs: number | null;
    lastExecutedAtMs: number | null;
  } | null> {
    if (!orderId) return null;
    try {
      const trades = await this.bitfinex.fetchOrderTrades(creds, orderId);
      let qtySum = 0;
      let notional = 0;
      const executionTimes: number[] = [];
      for (const t of trades) {
        const qty = Math.abs(t.execAmount);
        if (!(qty > 0) || !(t.execPrice > 0)) continue;
        qtySum += qty;
        notional += qty * t.execPrice;
        if (Number.isFinite(t.mtsCreate) && t.mtsCreate > 0) executionTimes.push(t.mtsCreate);
      }
      if (qtySum <= 0) return null;
      return {
        price: notional / qtySum,
        qty: qtySum,
        firstExecutedAtMs: executionTimes.length ? Math.min(...executionTimes) : null,
        lastExecutedAtMs: executionTimes.length ? Math.max(...executionTimes) : null,
      };
    } catch (err) {
      this.logger.warn(
        `fetchOrderTrades ${orderId} failed (falling back to approximate fill price): ${err instanceof Error ? err.message : err}`,
      );
      return null;
    }
  }

  /** Compatibility price-only view for existing adoption/healing paths. */
  private async resolveExchangeTradesFillPrice(
    creds: ExchangeCredentials,
    orderId: number | null | undefined,
  ): Promise<number | null> {
    return (await this.resolveExchangeTradesFillEvidence(creds, orderId))?.price ?? null;
  }

  /**
   * Phase 1 — record a fill detected by {@link detectEntryFillBeforeCancel}
   * as a REAL fill instead of closing the participant at $0. Cancels any
   * resting remainder first (never leaves a partial order live), arms the
   * protective stop, records FILLED + STOP_LOSS_ARMED, and heals the
   * participant to OPEN. When the enclosing cycle was already CLOSED/EXPIRED
   * (showcase exited), the cycle status is restored after the FILLED handler
   * force-sets it to OPEN, so monitorExit picks the lot up next tick and
   * closes it mirror-side. Returns false when the resting remainder could not
   * be cancelled — the caller must leave the participant PENDING_ENTRY and
   * retry next tick.
   */
  private async recordCancelRaceFill(
    agentId: string,
    userId: string,
    // tradeId is deliberately required. A cancel-race caller once passed a
    // narrowed cycle shape without it; Cure 1 then treated the already-
    // canonical cycle as `unknown` and rewrote it to
    // relink:unknown:<same-trade>:*. Requiring the field makes that identity
    // loss a compile-time error at every future call site.
    cycle: { id: string; status: SignalCycleStatus; tradeId: string | null },
    participantId: string,
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    intent: SignalIntentEnvelope | null,
    fill: {
      filledQty: number;
      fillPrice: number;
      source: 'ORDER_PARTIAL' | 'POSITION_DELTA';
      orderResting: boolean;
    },
    cancelContext: string,
    sourceFillAt?: string,
  ): Promise<boolean> {
    if (this.cancelRaceFillInFlight.has(participantId)) return false;
    this.cancelRaceFillInFlight.add(participantId);
    try {
      return await this.recordCancelRaceFillOwned(
        agentId,
        userId,
        cycle,
        participantId,
        meta,
        creds,
        intent,
        fill,
        cancelContext,
        sourceFillAt,
      );
    } finally {
      this.cancelRaceFillInFlight.delete(participantId);
    }
  }

  private async recordCancelRaceFillOwned(
    agentId: string,
    userId: string,
    cycle: { id: string; status: SignalCycleStatus; tradeId?: string | null },
    participantId: string,
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    intent: SignalIntentEnvelope | null,
    fill: {
      filledQty: number;
      fillPrice: number;
      source: 'ORDER_PARTIAL' | 'POSITION_DELTA';
      orderResting: boolean;
    },
    cancelContext: string,
    sourceFillAt?: string,
  ): Promise<boolean> {
    if (!meta.direction) return false;
    // Capture at funnel entry, before any remainder cancellation or exchange
    // enrichment, so detection->stop ACK measures the whole protection path.
    const fillDetectedAtMs = Date.now();

    const intendedQty = meta.qty ?? fill.filledQty;
    const mustTerminatePartial = new Set([
      'SHOWCASE_CYCLE_CLOSED',
      'SHOWCASE_ABANDONED',
      'SHOWCASE_ORDER_EXPIRED',
      'SIGNAL_TTL_EXPIRED',
      'EXIT_ONLY_PENDING_CANCEL',
    ]).has(cancelContext);
    if (
      partialEntryFillDisposition({
        intendedQty,
        filledQty: fill.filledQty,
        orderResting: fill.orderResting,
        terminalSource: mustTerminatePartial,
      }) === 'RETAIN_PROTECTED_REMAINDER'
    ) {
      // Every discovery path (poll, reconcile, source POSITION_OPENED, chase)
      // funnels through here. A partial execution is not a completed entry:
      // retain the exact exchange remainder and protect only the cumulative
      // filled slice. This prevents non-chase reconciliation from recreating
      // the cont-9e9 undercopy bug.
      await this.protectPartialFillAndRetainRemainder(
        agentId,
        userId,
        cycle.id,
        participantId,
        meta,
        creds,
        intent,
        fill.filledQty,
      );
      return true;
    }

    if (fill.orderResting && meta.bitfinexOrderId) {
      const cancel = await this.cancelManagedOrderGone(
        creds,
        meta.bitfinexOrderId,
        `Cancel-race fill ${userId} cycle=${cycle.id}: cancel partial-fill remainder ${meta.bitfinexOrderId} (${cancelContext})`,
      );
      if (!cancel.gone) {
        this.logger.error(
          `Cancel-race fill ${userId} cycle=${cycle.id}: remainder cancel failed (attempts=${cancel.attempts}, reason=${cancel.reason}) — leaving PENDING_ENTRY for next tick`,
        );
        await this.setInstanceLastError(userId, agentId, 'CANCEL_FAILED_ORDER_STILL_LIVE');
        await this.cycles
          .recordHireExecutionEvent(userId, agentId, cycle.id, 'RECONCILE_CANCEL_FAILED', {
            venue: 'bitfinex',
            source: 'hire',
            event: cancelContext,
            reason: 'CANCEL_RACE_FILL_REMAINDER_CANCEL_FAILED',
            bitfinex_order_id: meta.bitfinexOrderId,
            cancel_attempts: cancel.attempts,
            cancel_reason: cancel.reason ?? 'unknown',
          })
          .catch(() => {});
        return false;
      }
    }

    // Protection is the first write after exchange-verified fill detection.
    // Do not put the optional private trade-history enrichment ahead of the
    // stop ACK: that extra round trip previously consumed most of the <3s
    // protection budget. The already verified order/position price is safe for
    // stop placement; authoritative VWAP/MTS is enriched immediately after.
    const fallbackMarkPrice =
      fill.fillPrice > 0 || (meta.limitPrice != null && meta.limitPrice > 0)
        ? 0
        : await this.activeTrading.getMarkPrice().catch(() => 0);
    const stopReferencePrice = protectiveStopReferencePrice(
      fill.fillPrice,
      meta.limitPrice,
      fallbackMarkPrice,
    );
    if (!stopReferencePrice || stopReferencePrice <= 0) return false;
    const qty = finalizedEntryFillQty({
      intendedQty,
      filledQty: fill.filledQty,
      orderResting: fill.orderResting,
    });
    const leverage = resolveSubscriberLeverage(intent);
    const stopLossMarginPct = resolveEffectiveStopLossMarginPct(intent?.risk?.stop_loss_margin_pct, {
      mirrorMode: isShowcaseMirrorOnlyMode(),
    });
    const stopPrice = computeStopPrice(
      stopReferencePrice,
      meta.direction,
      stopLossMarginPct,
      leverage,
    );

    const stopSubmitStartedAtMs = Date.now();
    const stopOrderId = await this.activeTrading
      .submitStopOrder(creds, {
        positionDirection: meta.direction,
        qty,
        stopPrice,
        leverage,
      })
      .catch((err) => {
        this.logger.warn(
          `Cancel-race fill stop placement ${userId} cycle=${cycle.id}: ${err instanceof Error ? err.message : err}`,
        );
        return null;
      });
    const stopExchangeAckAtMs = stopOrderId != null ? Date.now() : null;

    const exchangeFillEvidence = await this.resolveExchangeTradesFillEvidence(
      creds,
      meta.bitfinexOrderId,
    );
    // VWAP is accounting enrichment only. Do not replace the acknowledged
    // stop afterward: the limit/order reference is already a no-worse risk
    // boundary for a valid limit execution, and a second stop handoff would
    // add exchange churn without improving protection.
    const fillPrice = exchangeFillEvidence?.price ?? stopReferencePrice;

    let supersededPartialStopOrderId: number | undefined;
    if (meta.partialFillStopOrderId) {
      if (stopOrderId == null) {
        const message = `FULL_FILL_STOP_HANDOFF_FAILED cycle=${cycle.id}; partial stop remains active`;
        await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
        await this.setInstanceLastError(userId, agentId, message);
        return false;
      }
      // Persist exchange ACK before cancelling the prior partial stop. A
      // restart between these operations can therefore recover both ids.
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'UPDATE_STOPS', {
        venue: 'bitfinex',
        source: 'hire',
        event: 'FULL_FILL_STOP_REPLACEMENT_ACKNOWLEDGED',
        stopOrderId,
        partialFillStopOrderId: stopOrderId,
        partialFillQty: qty,
        supersededPartialStopOrderId: meta.partialFillStopOrderId,
      });
      const oldGone = await this.cancelManagedOrderGone(
        creds,
        meta.partialFillStopOrderId,
        `FULL-FILL supersede partial stop ${meta.partialFillStopOrderId} with ${stopOrderId}`,
      );
      if (!oldGone.gone) {
        // The new stop already covers the exact full quantity. The old stop is
        // also reduce-only and therefore cannot reverse the position; retain
        // its id for loud retry on the OPEN tick and pause new entries.
        supersededPartialStopOrderId = meta.partialFillStopOrderId;
        const message = `FULL_FILL_SUPERSEDED_STOP_STILL_LIVE cycle=${cycle.id}; old=${meta.partialFillStopOrderId} new=${stopOrderId}`;
        await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
        await this.setInstanceLastError(userId, agentId, message);
      }
    }

    const priorCycleStatus = cycle.status;

    const staleExitCount = await this.prisma.signalCycleEvent.count({
      where: { participantId, eventType: 'EXIT' },
    });
    if (staleExitCount > 0) {
      this.logger.warn(
        `Cancel-race fill ${userId} cycle=${cycle.id}: superseding ${staleExitCount} stale EXIT event(s) (${cancelContext}) — participant will reopen via FILLED`,
      );
      await this.cycles
        .recordHireExecutionEvent(userId, agentId, cycle.id, 'STALE_EXIT_SUPERSEDED', {
          venue: 'bitfinex',
          source: 'hire',
          participant_id: participantId,
          stale_exit_count: staleExitCount,
          cancel_context: cancelContext,
        })
        .catch(() => {});
    }

    await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'FILLED', {
      venue: 'bitfinex',
      fill_price: fillPrice,
      fill_price_source: exchangeFillEvidence != null ? 'exchange_trades' : undefined,
      exchange_fill_first_at: exchangeFillEvidence?.firstExecutedAtMs
        ? new Date(exchangeFillEvidence.firstExecutedAtMs).toISOString()
        : undefined,
      exchange_fill_last_at: exchangeFillEvidence?.lastExecutedAtMs
        ? new Date(exchangeFillEvidence.lastExecutedAtMs).toISOString()
        : undefined,
      exchange_fill_mts: exchangeFillEvidence?.lastExecutedAtMs
        ? new Date(exchangeFillEvidence.lastExecutedAtMs).toISOString()
        : undefined,
      exchange_fill_detected_at: new Date(fillDetectedAtMs).toISOString(),
      fill_detected_at: new Date(fillDetectedAtMs).toISOString(),
      fill_detection_path: fill.source,
      fill_detection_context: cancelContext,
      exchange_fill_detection_lag_ms: exchangeFillEvidence?.lastExecutedAtMs
        ? Math.max(0, fillDetectedAtMs - exchangeFillEvidence.lastExecutedAtMs)
        : undefined,
      stop_reference_price: stopReferencePrice,
      stop_submit_started_at: new Date(stopSubmitStartedAtMs).toISOString(),
      stop_exchange_ack_at: stopExchangeAckAtMs != null
        ? new Date(stopExchangeAckAtMs).toISOString()
        : undefined,
      stop_submit_to_ack_ms: stopExchangeAckAtMs != null
        ? Math.max(0, stopExchangeAckAtMs - stopSubmitStartedAtMs)
        : undefined,
      fill_detection_to_stop_ack_ms: stopExchangeAckAtMs != null
        ? Math.max(0, stopExchangeAckAtMs - fillDetectedAtMs)
        : undefined,
      exchange_fill_to_stop_ack_ms:
        stopExchangeAckAtMs != null && exchangeFillEvidence?.lastExecutedAtMs
          ? Math.max(0, stopExchangeAckAtMs - exchangeFillEvidence.lastExecutedAtMs)
          : undefined,
      source_fill_at: sourceFillAt,
      source_event_at: sourceFillAt,
      qty,
      stop_loss_placed: stopOrderId != null,
      stop_loss_margin_pct: stopLossMarginPct,
      stopOrderId: stopOrderId ?? undefined,
      partialFillQty: null,
      partialFillStopOrderId: null,
      supersededPartialStopOrderId,
      source: 'hire',
      event: 'CANCEL_RACE_FILL',
      cancel_context: cancelContext,
      fill_source: fill.source,
    });
    if (stopOrderId != null) {
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'STOP_LOSS_ARMED', {
        venue: 'bitfinex',
        stop_price: stopPrice,
        stopOrderId,
        qty,
        fill_detection_path: fill.source,
        stop_submit_started_at: new Date(stopSubmitStartedAtMs).toISOString(),
        stop_exchange_ack_at: new Date(stopExchangeAckAtMs!).toISOString(),
        stop_submit_to_ack_ms: Math.max(0, stopExchangeAckAtMs! - stopSubmitStartedAtMs),
        fill_detection_to_stop_ack_ms: Math.max(0, stopExchangeAckAtMs! - fillDetectedAtMs),
        exchange_fill_to_stop_ack_ms:
          exchangeFillEvidence?.lastExecutedAtMs
            ? Math.max(0, stopExchangeAckAtMs! - exchangeFillEvidence.lastExecutedAtMs)
            : undefined,
        source: 'hire',
      });
    }
    await this.healStuckPendingFill(participantId, cycle.id, fillPrice);

    // The FILLED handler force-sets the cycle OPEN. If the showcase cycle was
    // already terminal, restore it so monitorExit closes this lot next tick.
    if (
      priorCycleStatus === SignalCycleStatus.CLOSED ||
      priorCycleStatus === SignalCycleStatus.EXPIRED
    ) {
      await this.prisma.signalCycle
        .update({ where: { id: cycle.id }, data: { status: priorCycleStatus } })
        .catch(() => {
          /* best-effort restore — exit safety nets still cover the lot */
        });
    }

    this.positionRuntime.set(participantId, {
      peakMarginPct: 0,
      lastChaseAtMs: 0,
      filledRecorded: true,
      // Option A — fresh FILLED resets the circuit breaker for this participant.
      consecutiveStopFailures: 0,
    });
    this.stopManagerCircuitOpen.delete(participantId);

    if (mustTerminatePartial) {
      const closed = await this.executeShowcaseMirrorClose(
        agentId,
        userId,
        { id: cycle.id, intentEnvelope: intent },
        { id: participantId, fillPrice: { toNumber: () => fillPrice } },
        {
          ...meta,
          qty,
          fillPrice,
          stopOrderId: stopOrderId ?? undefined,
          partialFillQty: null,
          partialFillStopOrderId: null,
        },
        creds,
        { trigger: `${cancelContext}_PARTIAL_FILL`, forceMirrorExit: true },
      );
      if (!closed) {
        await this.pauseUserRelayForPositionMismatch(
          userId,
          agentId,
          `PARTIAL_FILL_TERMINAL_CLOSE_FAILED cycle=${cycle.id} context=${cancelContext}`,
        ).catch(() => {});
      }
      return true;
    }

    // Cure 1 — a real fill that landed on a different showcase signal than
    // the cycle's own tradeId (mirror-owner-of-duplicate-limit race, or a
    // chase that crossed at a later signal's price) would orphan the real
    // position. Re-link the cycle to the showcase signal whose entry
    // price+direction actually matches the real fill. Best-effort — never
    // blocks the FILLED recording; safety nets still cover the lot if the
    // showcase is unreachable or no candidate matches.
    if (fillPrice > 0 && meta.direction) {
      try {
        await this.relinkCycleToShowcaseSignalIfDrifted({
          agentId,
          userId,
          cycle: { id: cycle.id, tradeId: cycle.tradeId ?? null },
          participantId,
          realFill: { price: fillPrice, direction: meta.direction },
          reason: `CANCEL_RACE_FILL/${cancelContext}`,
        });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(
          `[CURE-1] relink threw ${userId} cycle=${cycle.id}: ${msg} — safety nets remain active`,
        );
      }
    }

    this.cycleAudit.stage('FILLED', {
      userId,
      agentId,
      cycleId: cycle.id,
      participantId,
      meta: { fillPrice, qty, cancelContext, fillSource: fill.source },
    });

    this.logger.warn(
      `Cancel-race fill ${userId} cycle=${cycle.id} (${cancelContext}): order had REAL fill qty=${qty.toFixed(5)} @ ${fillPrice.toFixed(2)} (${fill.source}) — recorded FILLED instead of $0 close; stop armed=${stopOrderId != null}`,
    );
    return true;
  }

  /** Best-effort surface of a money-path cancel failure to the operator. */
  private async setInstanceLastError(userId: string, agentId: string, msg: string) {
    await this.prisma.tradingAgentInstance
      .updateMany({
        where: { userId, agentId },
        data: { lastError: msg.slice(0, 500) },
      })
      .catch(() => {
        /* best-effort — must not abort the tick */
      });
  }

  /** Fail closed on any raw exchange-versus-ledger position mismatch. */
  private async pauseRelayForPositionMismatch(
    instance: TradingAgentInstance,
    message: string,
  ): Promise<void> {
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instance.id },
      select: { dashboardState: true, status: true },
    });
    const dash = (fresh?.dashboardState ?? instance.dashboardState ?? {}) as Record<string, unknown>;
    const alreadyPaused =
      fresh?.status === TradingAgentInstanceStatus.PAUSED ||
      dash.relayExecutionMode === 'PAUSED';
    const truncated = message.slice(0, 500);
    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        status: TradingAgentInstanceStatus.PAUSED,
        lastError: truncated,
        dashboardState: applyDashboardPatch(dash, {
          relayExecutionMode: 'PAUSED',
          // Keep liveDeskSessionStartedAt so Session P&L / completed trades
          // stay scoped to this Start even after a mismatch pause clears the arm.
          relayArmedAt: null,
          realTradingConfirmedAt: null,
          positionMismatchDetectedAt: new Date().toISOString(),
          positionMismatchAlert: truncated,
          positionMismatchAlertAcked: false,
        }) as unknown as Prisma.InputJsonValue,
      },
    });
    if (!alreadyPaused) {
      await this.notifications
        .notifyUser(instance.userId, {
          type: NotificationType.TRADING_AGENT_UPDATE,
          title: 'Live copy paused — position mismatch',
          body: truncated,
          link: '/agent-hub/conservative-btc',
        })
        .catch(() => {
          /* best-effort alert — pause itself already persisted */
        });
      this.logger.error(
        `[POSITION-MISMATCH] relay paused user=${instance.userId}: ${truncated}`,
      );
    }
  }

  private async monitorEntry(
    agentId: string,
    userId: string,
    cycle: {
      id: string;
      tradeId: string;
      intentEnvelope: unknown;
      expiresAt: Date | null;
      status: SignalCycleStatus;
    },
    participant: { id: string; status: SignalCycleStatus },
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    activeOrderIdSet: Set<number>,
    exitOnly = false,
  ) {
    let intent = cycle.intentEnvelope as SignalIntentEnvelope;
    let orderId = meta.bitfinexOrderId;
    if (!orderId || !meta.direction) return;

    if (participant.status === SignalCycleStatus.OPEN) {
      await this.ensureProtectiveStop(agentId, userId, cycle.id, participant.id, meta, creds, intent);
      return;
    }

    // Serialize every pending-entry terminal/cancel decision against exact
    // limit replacement. Reload ownership only after entering the lane so a
    // tick that began on the prior generation can never cancel/expire it while
    // the newly ACKed order is being persisted.
    const releaseTerminalLane = await this.acquireParticipantMoneyLane(participant.id);
    let chaseCurrentOrder = false;
    try {
      const observedCycleStatus = cycle.status;
      const observedParticipantStatus = participant.status;
      const canReloadOwnership = !!this.prisma?.signalCycle?.findUnique
        && !!this.prisma?.signalCycleParticipant?.findUnique;
      const [freshCycle, freshParticipant, freshMeta] = canReloadOwnership ? await Promise.all([
        this.prisma.signalCycle.findUnique({
          where: { id: cycle.id },
          select: { id: true, tradeId: true, intentEnvelope: true, expiresAt: true, status: true },
        }),
        this.prisma.signalCycleParticipant.findUnique({
          where: { id: participant.id },
          select: { id: true, status: true },
        }),
        this.loadExecutionMeta(participant.id),
      ]) : [cycle, participant, meta];
      if (!freshCycle || !freshParticipant) return;
      if (
        freshCycle.status !== observedCycleStatus
        || freshParticipant.status !== observedParticipantStatus
      ) return; // next tick must branch from one coherent durable snapshot
      if (freshParticipant.status !== SignalCycleStatus.PENDING_ENTRY) return;
      cycle = freshCycle;
      participant = freshParticipant;
      meta = freshMeta;
      intent = cycle.intentEnvelope as SignalIntentEnvelope;
      orderId = meta.bitfinexOrderId;
      if (!orderId || !meta.direction) return;

      if (meta.partialFillQty && meta.partialFillStopOrderId) {
        const stopHandled = await this.reconcilePendingPartialFillStop(
          agentId,
          userId,
          cycle,
          participant.id,
          meta,
          creds,
          intent,
        );
        if (stopHandled) return;
      }

    // C2 fix: showcase cycle already CLOSED/EXPIRED (syncShowcaseCycleClosures marked it
    // because the showcase position exited). A relay still in PENDING_ENTRY must drop its
    // resting limit — otherwise it can chase/fill up to 30m TTL AFTER the showcase exited,
    // creating an orphan position with no showcase to mirror the exit on.
    if (cycle.status === SignalCycleStatus.CLOSED || cycle.status === SignalCycleStatus.EXPIRED) {
      // Cancel-race fill check (always on — misclassifying a real fill as a
      // cancel is a bug, not a feature flag): the order may have (partially)
      // filled before the showcase closure reached us — record the REAL fill
      // instead of cancelling and closing at $0 (orphan factory).
      {
        const fill = await this.detectEntryFillBeforeCancel(creds, meta).catch(() => null);
        if (fill) {
          const recorded = await this.recordCancelRaceFill(
            agentId,
            userId,
            cycle,
            participant.id,
            meta,
            creds,
            intent,
            fill,
            'SHOWCASE_CYCLE_CLOSED',
          );
          if (recorded) return;
        }
      }
      const cancel = await this.cancelManagedOrderGone(
        creds,
        orderId,
        `Hire expire ${userId} cycle=${cycle.id} (showcase ${cycle.status}) cancel relay limit ${orderId}`,
      );
      if (!cancel.gone) {
        // CRITICAL money-path: cancel failed AND the order is still live. Do
        // NOT mark the participant EXPIRED — that would orphan the order. Leave
        // PENDING_ENTRY, surface the failure, audit, and let the next tick retry.
        this.logger.error(
          `Hire expire ${userId} cycle=${cycle.id}: cancel failed (attempts=${cancel.attempts}, reason=${cancel.reason}) and order ${orderId} still live — leaving PENDING_ENTRY for next tick`,
        );
        await this.setInstanceLastError(userId, agentId, 'CANCEL_FAILED_ORDER_STILL_LIVE');
        await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'RECONCILE_CANCEL_FAILED', {
          venue: 'bitfinex',
          source: 'hire',
          event: 'SHOWCASE_CYCLE_CLOSED',
          reason: cycle.status,
          bitfinex_order_id: orderId,
          cancel_attempts: cancel.attempts,
          cancel_reason: cancel.reason ?? 'unknown',
        });
        return;
      }
      this.logger.log(
        `Hire expire ${userId} cycle=${cycle.id}: showcase cycle ${cycle.status} — cancelled relay limit (${cancel.reason === 'NOT_FOUND' ? 'already gone' : 'cancelled'})`,
      );
      if (cancel.reason === 'NOT_FOUND' && !(await this.replacementTerminalUnfilledProof(creds, meta))) return;
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXPIRED', {
        venue: 'bitfinex',
        pnl_usd: 0,
        source: 'hire',
        event: 'SHOWCASE_CYCLE_CLOSED',
        reason: cycle.status,
      });
      return;
    }

    await this.cancelAbsurdPendingOrders(creds, userId);

    let retainLateEntry = false;
    if (this.botBridge.isEnabled() && cycle.tradeId) {
      const botState = await this.fetchExecutionBotState();
      const showcaseTradeId = this.resolveShowcaseMirrorTradeId(cycle, meta) ?? cycle.tradeId;
      const abandon = this.showcaseEntryAbandoned(botState, showcaseTradeId);
      const showcasePosition = (botState?.positions ?? []).find(
        (position) => tradeIdsMatch(String(position.trade_id ?? ''), showcaseTradeId),
      );
      const showcaseFill = Number(showcasePosition?.entry ?? 0);
      retainLateEntry = shouldRetainLateEntryContinuation({
        enabled: lateEntryContinuationEnabled(),
        showcaseTradeOpen: abandon.reason === 'MISSED_SHOWCASE_FILL' && !!showcasePosition,
        participantStatus: participant.status,
        hasManagedOrder: !!orderId,
      });
      if (retainLateEntry) {
        // A source position is still live.  Do not call the phantom-cancel
        // path: after the lane releases, applyLimitChase freezes/reprices the
        // same owned order at the no-worse showcase-fill boundary.  The source
        // POSITION_CLOSED/TTL path still cancels it immediately.
        if (
          !meta.lateEntryContinuation
          || Math.abs(Number(meta.lateEntryShowcaseFill ?? 0) - showcaseFill) >= 0.01
        ) {
          await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'UPDATE_STOPS', {
            venue: 'bitfinex',
            source: 'hire',
            event: 'LATE_ENTRY_BETTER_ONLY_CONTINUATION',
            trade_id: showcaseTradeId,
            bitfinex_order_id: orderId,
            lateEntryContinuation: true,
            lateEntryShowcaseFill: showcaseFill > 0 ? showcaseFill : undefined,
            lateEntryStartedAtMs: Date.now(),
            late_entry_continuation: true,
            late_entry_showcase_fill: showcaseFill > 0 ? showcaseFill : undefined,
            late_entry_started_at: new Date().toISOString(),
          });
        }
        chaseCurrentOrder = true;
      }
      if (abandon.abandoned && !retainLateEntry) {
        if (
          abandon.reason === 'SHOWCASE_ABSENT'
          && showcaseAbsentWithinOrderPropagationGrace(showcaseTradeId, intent)
        ) {
          this.logger.warn(
            `Showcase snapshot propagation grace ${userId} cycle=${cycle.id}: signed exact order ${cycle.tradeId} is not visible yet — retaining managed order ${orderId}`,
          );
          return;
        }
        if (abandon.reason === 'MISSED_SHOWCASE_FILL') {
          if (this.hasQueuedOrActiveSourceFillWake(cycle.tradeId)) {
            this.logger.log(
              `Missed showcase fill ${userId} cycle=${cycle.id}: exact signed POSITION_OPENED wake is queued/active — deferring snapshot fallback`,
            );
            return;
          }
          if (
            missedShowcaseFillWithinSettlementGrace(
              botState,
              showcaseTradeId,
              intent,
            )
          ) {
            // Reconcile an already-visible exchange fill immediately, but do
            // not cancel a managed replacement that may still be crossing the
            // exchange boundary. The next executor tick retries until the
            // source-fill settlement grace has elapsed.
            const fill = await this.detectEntryFillBeforeCancel(
              creds,
              meta,
              true,
            ).catch(() => null);
            if (fill) {
              const recorded = await this.recordCancelRaceFill(
                agentId,
                userId,
                cycle,
                participant.id,
                meta,
                creds,
                intent,
                fill,
                'MISSED_SHOWCASE_FILL_SETTLEMENT',
              );
              if (recorded) return;
            }
            this.logger.warn(
              `Missed showcase fill ${userId} cycle=${cycle.id}: source fill is within ${PENDING_FILL_RECONCILE_GRACE_MS}ms settlement grace — retaining managed order ${orderId}`,
            );
            return;
          }
          const resolution = await resolveMissedShowcaseFill({
            managedOrderId: orderId,
            detectFill: () =>
              this.detectEntryFillBeforeCancel(creds, meta, true),
            recordFill: (fill) =>
              this.recordCancelRaceFill(
                agentId,
                userId,
                cycle,
                participant.id,
                meta,
                creds,
                intent,
                fill,
                'MISSED_SHOWCASE_FILL',
              ),
            cancelManagedOrder: (managedOrderId) =>
              this.cancelManagedOrderGone(
                creds,
                managedOrderId,
                `Hire expire ${userId} cycle=${cycle.id} (missed showcase fill) cancel relay limit ${managedOrderId}`,
              ),
          });
          if (
            resolution.outcome === 'FILL_RECORDED' ||
            resolution.outcome === 'PENDING_RETRY_AFTER_FILL'
          ) {
            return;
          }
          if (resolution.outcome === 'PENDING_FILL_CHECK_FAILED') {
            this.logger.error(
              `Missed showcase fill ${userId} cycle=${cycle.id}: fill verification failed ${resolution.phase} (${resolution.error}) — leaving PENDING_ENTRY and blocking relay`,
            );
            await this.setInstanceLastError(
              userId,
              agentId,
              'MISSED_SHOWCASE_FILL_VERIFICATION_FAILED',
            );
            await this.cycles.recordHireExecutionEvent(
              userId,
              agentId,
              cycle.id,
              'RECONCILE_CANCEL_FAILED',
              {
                venue: 'bitfinex',
                source: 'hire',
                event: 'MISSED_SHOWCASE_FILL',
                reason: 'FILL_VERIFICATION_FAILED',
                verification_phase: resolution.phase,
                bitfinex_order_id: orderId,
              },
            );
            return;
          }
          if (resolution.outcome === 'PENDING_CANCEL_FAILED') {
            this.logger.error(
              `Missed showcase fill ${userId} cycle=${cycle.id}: cancel failed (attempts=${resolution.cancelAttempts}, reason=${resolution.cancelReason}) and managed order ${orderId} remains live — leaving PENDING_ENTRY and blocking relay`,
            );
            await this.setInstanceLastError(
              userId,
              agentId,
              'MISSED_SHOWCASE_FILL_CANCEL_FAILED_ORDER_STILL_LIVE',
            );
            await this.cycles.recordHireExecutionEvent(
              userId,
              agentId,
              cycle.id,
              'RECONCILE_CANCEL_FAILED',
              {
                venue: 'bitfinex',
                source: 'hire',
                event: 'MISSED_SHOWCASE_FILL',
                reason: 'CANCEL_FAILED_ORDER_STILL_LIVE',
                bitfinex_order_id: orderId,
                cancel_attempts: resolution.cancelAttempts,
                cancel_reason: resolution.cancelReason ?? 'unknown',
              },
            );
            return;
          }
          if (
            resolution.cancelReason === 'NOT_FOUND'
            && !(await this.replacementTerminalUnfilledProof(creds, meta))
          ) return;
          this.logger.warn(
            `Missed showcase fill ${userId} cycle=${cycle.id}: exchange-proven unfilled managed order ${orderId} cancelled — copy expired without market catch-up`,
          );
          await this.cycles.recordHireExecutionEvent(
            userId,
            agentId,
            cycle.id,
            'EXPIRED',
            {
              venue: 'bitfinex',
              pnl_usd: 0,
              source: 'hire',
              event: 'MISSED_SHOWCASE_FILL',
              reason: 'MISSED_SHOWCASE_FILL',
              bitfinex_order_id: orderId,
              cancel_attempts: resolution.cancelAttempts,
              cancel_reason: resolution.cancelReason ?? 'unknown',
            },
          );
          // Cure 3 — the real Bitfinex limit was confirmed UNFILLED, but Fly
          // paper may have "filled" the same signal. If so, Fly now holds a
          // phantom paper position that will corrupt its strategy state
          // (capacity, PnL, signal generation) and keep emitting exits for a
          // trade Railway can never mirror. Tell Fly to cancel the phantom.
          // Best-effort: never blocks the executor; failures are audited.
          await this.cancelPhantomShowcasePosition(
            userId,
            agentId,
            cycle.id,
            cycle.tradeId,
            'MISSED_SHOWCASE_FILL_REAL_UNFILLED',
          );
          return;
        }
        // Cancel-race fill check (always on — see SHOWCASE_CYCLE_CLOSED branch).
        {
          const fill = await this.detectEntryFillBeforeCancel(creds, meta).catch(() => null);
          if (fill) {
            const recorded = await this.recordCancelRaceFill(
              agentId,
              userId,
              cycle,
              participant.id,
              meta,
              creds,
              intent,
              fill,
              'SHOWCASE_ABANDONED',
            );
            if (recorded) return;
          }
        }
        const cancel = await this.cancelManagedOrderGone(
          creds,
          orderId,
          `Hire expire ${userId} cycle=${cycle.id} (showcase abandoned) cancel relay limit ${orderId}`,
        );
        if (!cancel.gone) {
          this.logger.error(
            `Hire expire ${userId} cycle=${cycle.id}: cancel failed (attempts=${cancel.attempts}, reason=${cancel.reason}) and order ${orderId} still live — leaving PENDING_ENTRY for next tick`,
          );
          await this.setInstanceLastError(userId, agentId, 'CANCEL_FAILED_ORDER_STILL_LIVE');
          await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'RECONCILE_CANCEL_FAILED', {
            venue: 'bitfinex',
            source: 'hire',
            event: 'SHOWCASE_ABANDONED',
            reason: abandon.reason,
            bitfinex_order_id: orderId,
            cancel_attempts: cancel.attempts,
            cancel_reason: cancel.reason ?? 'unknown',
          });
          return;
        }
        this.logger.log(
          `Hire expire ${userId} cycle=${cycle.id}: showcase abandoned (${abandon.reason ?? 'unknown'}) — cancelled relay limit (${cancel.reason === 'NOT_FOUND' ? 'already gone' : 'cancelled'})`,
        );
        if (cancel.reason === 'NOT_FOUND' && !(await this.replacementTerminalUnfilledProof(creds, meta))) return;
        await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXPIRED', {
          venue: 'bitfinex',
          pnl_usd: 0,
          source: 'hire',
          event: 'SHOWCASE_ABANDONED',
          reason: abandon.reason,
        });
        return;
      }
    }

    if (!retainLateEntry && cycle.expiresAt && cycle.expiresAt < new Date()) {
      // Cancel-race fill check (always on — see SHOWCASE_CYCLE_CLOSED branch).
      {
        const fill = await this.detectEntryFillBeforeCancel(creds, meta).catch(() => null);
        if (fill) {
          const recorded = await this.recordCancelRaceFill(
            agentId,
            userId,
            cycle,
            participant.id,
            meta,
            creds,
            intent,
            fill,
            'SIGNAL_TTL_EXPIRED',
          );
          if (recorded) return;
        }
      }
      const cancel = await this.cancelManagedOrderGone(
        creds,
        orderId,
        `Hire expire ${userId} cycle=${cycle.id} (signal TTL expired) cancel relay limit ${orderId}`,
      );
      if (!cancel.gone) {
        this.logger.error(
          `Hire expire ${userId} cycle=${cycle.id}: cancel failed (attempts=${cancel.attempts}, reason=${cancel.reason}) and order ${orderId} still live — leaving PENDING_ENTRY for next tick`,
        );
        await this.setInstanceLastError(userId, agentId, 'CANCEL_FAILED_ORDER_STILL_LIVE');
        await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'RECONCILE_CANCEL_FAILED', {
          venue: 'bitfinex',
          source: 'hire',
          event: 'SIGNAL_TTL_EXPIRED',
          bitfinex_order_id: orderId,
          cancel_attempts: cancel.attempts,
          cancel_reason: cancel.reason ?? 'unknown',
        });
        return;
      }
      if (cancel.reason === 'NOT_FOUND' && !(await this.replacementTerminalUnfilledProof(creds, meta))) return;
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXPIRED', {
        venue: 'bitfinex',
        pnl_usd: 0,
        source: 'hire',
      });
      return;
    }

    const active =
      activeOrderIdSet.size > 0
        ? activeOrderIdSet.has(orderId)
        : !!(await this.activeTrading.findOrder(creds, orderId).catch(() => null));
    if (active) {
      this.replacementMissingProbe.delete(participant.id);
      if (exitOnly) {
        // PAUSED / hire-expired exit-only must CANCEL resting entries, not
        // early-return. Returning here left cont-3d3cd2524783 live on Bitfinex
        // after showcase went flat while the hire was PAUSED.
        {
          const fill = await this.detectEntryFillBeforeCancel(creds, meta).catch(() => null);
          if (fill) {
            const recorded = await this.recordCancelRaceFill(
              agentId,
              userId,
              cycle,
              participant.id,
              meta,
              creds,
              intent,
              fill,
              'EXIT_ONLY_PENDING_CANCEL',
            );
            if (recorded) return;
          }
        }
        const cancel = await this.cancelManagedOrderGone(
          creds,
          orderId,
          `Exit-only ${userId} cycle=${cycle.id} cancel relay limit ${orderId}`,
        );
        if (!cancel.gone) {
          this.logger.error(
            `Exit-only ${userId} cycle=${cycle.id}: cancel failed (attempts=${cancel.attempts}, reason=${cancel.reason}) and order ${orderId} still live — leaving PENDING_ENTRY`,
          );
          await this.setInstanceLastError(userId, agentId, 'CANCEL_FAILED_ORDER_STILL_LIVE');
          await this.cycles.recordHireExecutionEvent(
            userId,
            agentId,
            cycle.id,
            'RECONCILE_CANCEL_FAILED',
            {
              venue: 'bitfinex',
              source: 'hire',
              event: 'EXIT_ONLY_PENDING_CANCEL',
              bitfinex_order_id: orderId,
              cancel_attempts: cancel.attempts,
              cancel_reason: cancel.reason ?? 'unknown',
            },
          );
          return;
        }
        this.logger.log(
          `Exit-only ${userId} cycle=${cycle.id}: cancelled resting relay limit (${cancel.reason === 'NOT_FOUND' ? 'already gone' : 'cancelled'})`,
        );
        if (cancel.reason === 'NOT_FOUND' && !(await this.replacementTerminalUnfilledProof(creds, meta))) return;
        await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXPIRED', {
          venue: 'bitfinex',
          pnl_usd: 0,
          source: 'hire',
          event: 'EXIT_ONLY_PENDING_CANCEL',
        });
        return;
      }
      chaseCurrentOrder = true;
    }

    } finally {
      releaseTerminalLane();
    }

    if (chaseCurrentOrder) {
      await this.applyLimitChase(agentId, userId, cycle.id, participant.id, meta, creds, intent, cycle.tradeId, false);
      return;
    }

    // Fix 7b — distinguish a transient position-fetch failure from a genuine
    // no-position state. Classifying on a failed fetch recorded EXPIRED while
    // the fill was real (orphan factory). Fail-closed: defer to next tick.
    const releaseGoneClassification = await this.acquireParticipantMoneyLane(participant.id);
    let goneClassificationReleased = false;
    const releaseGoneClassificationOnce = () => {
      if (goneClassificationReleased) return;
      goneClassificationReleased = true;
      releaseGoneClassification();
    };
    try {
    // Before any zero-position/phantom EXPIRED path, re-read authoritative
    // ownership. LIMIT_UPDATED may have committed a replacement while this
    // tick still holds the prior order id and intent revision.
    const [durableCycle, durableParticipant, durableMeta] = await Promise.all([
      this.prisma.signalCycle.findUnique({
        where: { id: cycle.id },
        select: { id: true, tradeId: true, intentEnvelope: true, expiresAt: true, status: true },
      }),
      this.prisma.signalCycleParticipant.findUnique({
        where: { id: participant.id },
        select: { id: true, status: true },
      }),
      this.loadExecutionMeta(participant.id),
    ]);
    if (!durableCycle || !durableParticipant) return;
    if (durableCycle.status !== cycle.status || durableParticipant.status !== participant.status) {
      // The active-order snapshot belongs to the stale pass too. Do not recurse
      // with it: doing so can misclassify the durable replacement even after
      // releasing the lane. The already-queued/next executor tick reloads both
      // authoritative DB ownership and the exchange active book together.
      this.logger.warn(
        `Order ${orderId} state advanced during gone-order classification for ${userId} — deferring to a fresh executor snapshot`,
      );
      return;
    }
    if (
      pendingEntryOwnershipAdvanced(
        orderId, intent, durableMeta.bitfinexOrderId, durableCycle.intentEnvelope,
      )
    ) {
      this.logger.warn(
        `Order ${orderId} is stale for ${userId}; durable pending ownership is order ${durableMeta.bitfinexOrderId ?? 'pending-persist'} revision ${showcaseIntentRevision(durableCycle.intentEnvelope) ?? 'unknown'} — deferring all order-gone expiry paths`,
      );
      return;
    }

    if (durableMeta.bitfinexOrderId === orderId && durableMeta.replacementExchangeAckAtMs) {
      let freshOrders: Array<{ id?: number }>;
      try {
        freshOrders = await this.activeTrading.listActiveOrders(creds);
      } catch {
        return; // unavailable exchange proof is never absence
      }
      if (freshOrders.some((order) => Number(order.id) === orderId)) {
        this.replacementMissingProbe.delete(participant.id);
        return;
      }
      const generation = `${orderId}:${showcaseIntentRevision(durableCycle.intentEnvelope) ?? 'unknown'}`;
      const nowMs = Date.now();
      const priorProbe = this.replacementMissingProbe.get(participant.id);
      const { probe, terminalEligible } = advanceReplacementMissingProbe(
        priorProbe,
        generation,
        durableMeta.replacementExchangeAckAtMs,
        nowMs,
      );
      this.replacementMissingProbe.set(participant.id, probe);
      if (!terminalEligible) return;
    }

    let positionFetchFailed = false;
    const position = await this.activeTrading.getOpenPositionDetail(creds).catch(() => {
      positionFetchFailed = true;
      return null;
    });
    const expectedLong = meta.direction === 'LONG';
    const hasPosition =
      position &&
      ((expectedLong && position.amount > 0) || (!expectedLong && position.amount < 0));

    if (!hasPosition) {
      if (positionFetchFailed) {
        this.logger.warn(
          `Order ${orderId} gone and position fetch FAILED for ${userId} — cannot classify fill vs cancel, leaving PENDING_ENTRY for next tick`,
        );
        return;
      }
      this.logger.warn(`Order ${orderId} gone without position for ${userId} — treating as cancelled`);
      if (!(await this.replacementTerminalUnfilledProof(creds, durableMeta))) return;
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXPIRED', {
        venue: 'bitfinex',
        pnl_usd: 0,
        source: 'hire',
      });
      return;
    }

    const exchangeQtyAtOrder = meta.exchangeQtyAtOrder ?? 0;
    const currentExchangeQty = Math.abs(position!.amount);
    const lotQty = meta.qty ?? MIN_QTY_BTC;
    const expectedMinQty = exchangeQtyAtOrder + lotQty * 0.85;
    if (currentExchangeQty + MIN_QTY_BTC < expectedMinQty) {
      // Fix 7c — the exchangeQtyAtOrder baseline is stale: another lot's exit
      // (showcase-mirror close) between this order's fill and this check
      // shrinks the merged position, failing the growth check and discarding
      // a REAL fill. Secondary check against a FRESH baseline: the qty
      // currently attributed to OTHER open participants. If the merged
      // position covers other lots + ~this lot, the fill was real.
      const otherOpenRows = await this.prisma.signalCycleParticipant.findMany({
        where: {
          userId,
          status: SignalCycleStatus.OPEN,
          cycle: { agentId },
          id: { not: participant.id },
        },
        select: { id: true },
      });
      let otherAttributedQty = 0;
      for (const other of otherOpenRows) {
        const otherMeta = await this.loadExecutionMeta(other.id);
        if (otherMeta.direction && otherMeta.direction !== meta.direction) continue;
        otherAttributedQty += otherMeta.qty ?? 0;
      }
      const freshBaselineMinQty = otherAttributedQty + lotQty * 0.85;
      if (currentExchangeQty + MIN_QTY_BTC < freshBaselineMinQty) {
      if (!(await this.replacementTerminalUnfilledProof(creds, durableMeta))) return;
      this.logger.warn(
          `Order ${orderId} gone but merged position ${currentExchangeQty.toFixed(5)} BTC did not grow for lot ${lotQty.toFixed(5)} (baseline ${exchangeQtyAtOrder.toFixed(5)}, fresh attributed ${otherAttributedQty.toFixed(5)}) — expire phantom pending`,
      );
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXPIRED', {
        venue: 'bitfinex',
        pnl_usd: 0,
        source: 'hire',
        event: 'PHANTOM_FILL_REJECTED',
      });
      return;
      }
      this.logger.warn(
        `Order ${orderId}: stale at-order baseline ${exchangeQtyAtOrder.toFixed(5)} failed growth check but FRESH attributed baseline ${otherAttributedQty.toFixed(5)} confirms the fill (merged ${currentExchangeQty.toFixed(5)}, lot ${lotQty.toFixed(5)}) — recording real fill for ${userId}`,
      );
    }

    const fillPrice =
      meta.limitPrice && meta.limitPrice > 0
        ? meta.limitPrice
        : await this.activeTrading.getMarkPrice();
    const qty = meta.qty ?? MIN_QTY_BTC;
    const filledEvents = await this.prisma.signalCycleEvent.count({
      where: { participantId: participant.id, eventType: 'FILLED' },
    });

    if (filledEvents > 0) {
      await this.healStuckPendingFill(participant.id, cycle.id, fillPrice);
      await this.ensureProtectiveStop(
        agentId,
        userId,
        cycle.id,
        participant.id,
        { ...meta, qty },
        creds,
        intent,
      fillPrice,
      );
      return;
    }

    const runtime = this.positionRuntime.get(participant.id);
    if (runtime?.filledRecorded) return;

    const leverage = resolveSubscriberLeverage(intent);
    const stopLossMarginPct = intent.risk.stop_loss_margin_pct ?? -18;
    const stopPrice = computeStopPrice(fillPrice, meta.direction, stopLossMarginPct, leverage);

    const stopOrderId = await this.activeTrading.submitStopOrder(creds, {
      positionDirection: meta.direction,
      qty,
      stopPrice,
      leverage,
    }).catch((err) => {
      this.logger.warn(
        `Stop placement ${userId} cycle=${cycle.id}: ${err instanceof Error ? err.message : err}`,
      );
      return null;
    });

    await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'FILLED', {
      venue: 'bitfinex',
      fill_price: fillPrice,
      qty,
      stop_loss_placed: stopOrderId != null,
      stop_loss_margin_pct: stopLossMarginPct,
      stopOrderId: stopOrderId ?? undefined,
      source: 'hire',
    });

    this.cycleAudit.stage('FILLED', {
      userId,
      agentId,
      cycleId: cycle.id,
      participantId: participant.id,
      tradeId: cycle.tradeId,
      meta: { fillPrice, qty },
    });
    this.cycleAudit.stage('OPEN', {
      userId,
      agentId,
      cycleId: cycle.id,
      participantId: participant.id,
      tradeId: cycle.tradeId,
    });

    await this.healStuckPendingFill(participant.id, cycle.id, fillPrice);

    if (stopOrderId != null) {
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'STOP_LOSS_ARMED', {
        venue: 'bitfinex',
        stop_price: stopPrice,
        stopOrderId,
        source: 'hire',
      });
    }

    this.positionRuntime.set(participant.id, {
      peakMarginPct: 0,
      lastChaseAtMs: 0,
      filledRecorded: true,
    });

    // Cure 1 — re-link if the real fill maps to a different showcase signal
    // than the cycle's own tradeId. Best-effort, never blocks FILLED.
    if (fillPrice > 0 && meta.direction) {
      try {
        await this.relinkCycleToShowcaseSignalIfDrifted({
          agentId,
          userId,
          cycle: { id: cycle.id, tradeId: cycle.tradeId ?? null },
          participantId: participant.id,
          realFill: { price: fillPrice, direction: meta.direction },
          reason: 'MONITOR_ENTRY_FILL',
        });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(
          `[CURE-1] relink threw ${userId} cycle=${cycle.id}: ${msg} — safety nets remain active`,
        );
      }
    }

    this.logger.log(
      `Hire fill ${userId} cycle=${cycle.id} @ ${fillPrice.toFixed(2)} qty=${qty} stop=${stopPrice.toFixed(2)} armed=${stopOrderId != null}`,
    );
    } finally {
      releaseGoneClassificationOnce();
    }
  }

  /** Prior deploys recorded FILLED events but participant stayed PENDING_ENTRY when stop failed. */
  private async reconcileUnattributedExchangeFills(
    agentId: string,
    userId: string,
    creds: ExchangeCredentials,
    activeOrderIdSet: Set<number>,
    participantScope: { createdAt?: { gte: Date } } = {},
  ) {
    const position = await this.activeTrading.getOpenPositionDetail(creds).catch(() => null);
    if (!position || Math.abs(position.amount) < MIN_QTY_BTC) return;

    const direction: 'LONG' | 'SHORT' = position.amount > 0 ? 'LONG' : 'SHORT';
    const exchangeQty = Math.abs(position.amount);

    const managed = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: { in: [SignalCycleStatus.OPEN, SignalCycleStatus.PENDING_ENTRY] },
        cycle: { agentId },
        ...participantScope,
      },
      include: { cycle: true },
      orderBy: { createdAt: 'asc' },
    });

    let attributedQty = 0;
    for (const row of managed) {
      if (row.status !== SignalCycleStatus.OPEN) continue;
      const meta = await this.loadExecutionMeta(row.id);
      if (meta.direction && meta.direction !== direction) continue;
      attributedQty += meta.qty ?? 0;
    }

    let gap = exchangeQty - attributedQty;
    if (gap < MIN_QTY_BTC) return;

    this.logger.warn(
      `Exchange fill gap ${userId}: Bitfinex ${exchangeQty.toFixed(5)} ${direction} vs ledger OPEN ${attributedQty.toFixed(5)} — attributing ${gap.toFixed(5)} BTC`,
    );

    for (const row of managed) {
      if (gap < MIN_QTY_BTC) break;
      if (row.status !== SignalCycleStatus.PENDING_ENTRY) continue;

      const meta = await this.loadExecutionMeta(row.id);
      if (!meta.direction || meta.direction !== direction) continue;

      const orderId = meta.bitfinexOrderId;
      if (orderId && activeOrderIdSet.has(orderId)) continue;

      const filledCount = await this.prisma.signalCycleEvent.count({
        where: { participantId: row.id, eventType: 'FILLED' },
      });
      if (filledCount > 0) {
        const fillPrice =
          meta.limitPrice && meta.limitPrice > 0
            ? meta.limitPrice
            : position.basePrice > 0
              ? position.basePrice
              : await this.activeTrading.getMarkPrice();
        await this.healStuckPendingFill(row.id, row.cycleId, fillPrice);
        gap -= meta.qty ?? MIN_QTY_BTC;
        continue;
      }

      const intent = row.cycle.intentEnvelope as SignalIntentEnvelope;
      // Fix D — prefer the exchange's own per-order trade history (real
      // volume-weighted fill) over the limit-price/basePrice approximation.
      const exchangeFillPrice = await this.resolveExchangeTradesFillPrice(
        creds,
        meta.bitfinexOrderId,
      );
      const fillPrice =
        exchangeFillPrice ??
        (meta.limitPrice && meta.limitPrice > 0
          ? meta.limitPrice
          : position.basePrice > 0
            ? position.basePrice
            : await this.activeTrading.getMarkPrice());
      const qty = meta.qty ?? MIN_QTY_BTC;
      const leverage = resolveSubscriberLeverage(intent);
      const stopLossMarginPct = intent?.risk?.stop_loss_margin_pct ?? -18;
      const stopPrice = computeStopPrice(fillPrice, meta.direction, stopLossMarginPct, leverage);

      const stopOrderId = await this.activeTrading.submitStopOrder(creds, {
        positionDirection: meta.direction,
        qty,
        stopPrice,
        leverage,
      }).catch(() => null);

      await this.cycles.recordHireExecutionEvent(userId, agentId, row.cycleId, 'FILLED', {
        venue: 'bitfinex',
        fill_price: fillPrice,
        fill_price_source: exchangeFillPrice != null ? 'exchange_trades' : undefined,
        qty,
        stop_loss_placed: stopOrderId != null,
        stop_loss_margin_pct: stopLossMarginPct,
        stopOrderId: stopOrderId ?? undefined,
        stop_price: stopOrderId != null ? stopPrice : undefined,
        source: 'hire',
        event: 'EXCHANGE_FILL_RECONCILE',
      });

      if (stopOrderId != null) {
        await this.cycles.recordHireExecutionEvent(userId, agentId, row.cycleId, 'STOP_LOSS_ARMED', {
          venue: 'bitfinex',
          stop_price: stopPrice,
          stopOrderId,
          qty,
          source: 'hire',
        });
      }

      await this.healStuckPendingFill(row.id, row.cycleId, fillPrice);
      gap -= qty;

      // Vigilance / Cure 1 extension — exchange-fill gap reconciliation
      // attributes a real Bitfinex position slice to an existing
      // PENDING_ENTRY participant by side+direction, WITHOUT consulting the
      // showcase book. If the participant's cycle.tradeId points at a stale
      // showcase signal (mirror-owner-of-duplicate-limit race, like the
      // cont-de8f316fd3c0 case), the now-OPEN real lot would mirror-exit
      // against the wrong showcase id. Re-link it to the showcase signal
      // whose entry price+direction actually matches the real fill.
      if (fillPrice > 0 && meta.direction) {
        try {
          await this.relinkCycleToShowcaseSignalIfDrifted({
            agentId,
            userId,
            cycle: { id: row.cycleId, tradeId: row.cycle.tradeId ?? null },
            participantId: row.id,
            realFill: { price: fillPrice, direction: meta.direction },
            reason: 'EXCHANGE_FILL_RECONCILE',
          });
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          this.logger.warn(
            `[CURE-1] exchange-fill-reconcile relink threw ${userId} cycle=${row.cycleId}: ${msg} — safety nets remain active`,
          );
        }
      }

      this.logger.log(
        `Reconciled fill ${userId} cycle=${row.cycleId} ${meta.direction} @ ${fillPrice.toFixed(2)} qty=${qty}`,
      );
    }
  }

  private async reconcileGhostOpenLots(
    agentId: string,
    userId: string,
    participantScope: { createdAt?: { gte: Date } } = {},
    marginCap: number,
  ) {
    const ghosts = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: SignalCycleStatus.OPEN,
        cycle: { agentId },
        ...participantScope,
      },
      include: { cycle: true },
    });

    for (const row of ghosts) {
      if (await this.hasParticipantExited(row.id)) continue;

      const meta = await this.resolveLotMeta(
        row.id,
        row.cycleId,
        userId,
        agentId,
        row.cycle.intentEnvelope,
        marginCap,
      );
      if (meta.qty && btcToSats(meta.qty) > 0 && meta.direction) continue;

      const ageMs = Date.now() - row.updatedAt.getTime();
      if (ageMs < 120_000) continue;

      await this.cycles.recordHireExecutionEvent(userId, agentId, row.cycleId, 'EXIT', {
        venue: 'bitfinex',
        pnl_usd: 0,
        exit_reason: 'GHOST_LOT_REPAIRED',
        event: 'GHOST_LOT_REPAIRED',
        source: 'hire',
      });
      this.positionRuntime.delete(row.id);
      this.logger.warn(
        `Closed ghost OPEN lot ${userId} participant=${row.id} cycle=${row.cycleId} (missing qty/direction)`,
      );
    }
  }

  private async reconcileFilledParticipants(
    userId: string,
    agentId: string,
    participantScope: { createdAt?: { gte: Date } } = {},
    creds?: ExchangeCredentials,
    activeOrderIdSet?: Set<number>,
  ) {
    // Scope to this user's pending rows before touching the large event
    // ledger. The legacy relation filters scanned all SignalCycleEvent rows
    // even when a flat account had no pending entry.
    const pending = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId },
        ...participantScope,
      },
      select: { id: true, cycleId: true, updatedAt: true },
    });
    if (pending.length === 0) return;

    const filledEvents = await this.prisma.signalCycleEvent.findMany({
      where: {
        participantId: { in: pending.map((row) => row.id) },
        eventType: 'FILLED',
      },
      orderBy: { createdAt: 'desc' },
      select: { participantId: true, payload: true },
    });
    const latestFillByParticipant = new Map<string, { fill_price?: number } | null>();
    for (const event of filledEvents) {
      if (!event.participantId || latestFillByParticipant.has(event.participantId)) continue;
      latestFillByParticipant.set(
        event.participantId,
        event.payload as { fill_price?: number } | null,
      );
    }

    for (const row of pending) {
      const payload = latestFillByParticipant.get(row.id);
      if (payload === undefined) continue;
      const fill =
        payload?.fill_price != null && Number.isFinite(payload.fill_price)
          ? payload.fill_price
          : 0;
      await this.healStuckPendingFill(row.id, row.cycleId, fill);
    }

    // Cancel-by-exchange detection: a PENDING_ENTRY participant with no FILLED
    // event whose resting bitfinexOrderId is no longer in listActiveOrders was
    // cancelled/expired by the exchange without ever filling. Without this, the
    // row hangs forever and consumes a capacity slot. Heal it to EXIT with $0
    // PnL (no fill = no PnL) and an audit event so operators can see why.
    if (creds && activeOrderIdSet) {
      const unfilledIds = pending
        .filter((row) => !latestFillByParticipant.has(row.id))
        .map((row) => row.id);
      if (unfilledIds.length > 0) {
        await this.reconcileCancelByExchange(
          userId,
          agentId,
          creds,
          activeOrderIdSet,
          participantScope,
          unfilledIds,
        );
      }
    }
  }

  /**
   * Heal PENDING_ENTRY participants whose bitfinexOrderId vanished from the
   * exchange's active order list without ever recording a FILLED event. The
   * exchange cancelled/expired the order; we close the ledger row at $0 PnL.
   */
  private async reconcileCancelByExchange(
    userId: string,
    agentId: string,
    creds: ExchangeCredentials,
    activeOrderIdSet: Set<number>,
    participantScope: { createdAt?: { gte: Date } } = {},
    candidateIds: string[] = [],
  ) {
    if (candidateIds.length === 0) return;
    // Only consider participants older than 120s so we don't race with order
    // placement (the entry limit may take a few seconds to land on the book).
    const candidates = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        id: { in: candidateIds },
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId },
        // NOT having a FILLED event is the discriminator — a FILLED row that
        // vanished from the book is healed by healStuckPendingFill above.
        updatedAt: { lt: new Date(Date.now() - 120_000) },
        ...participantScope,
      },
      include: {
        cycle: { select: { id: true, tradeId: true, status: true, intentEnvelope: true } },
      },
    });

    for (const row of candidates) {
      const releaseMoneyLane = await this.acquireParticipantMoneyLane(row.id);
      try {
      const [freshParticipant, freshCycle, meta] = await Promise.all([
        this.prisma.signalCycleParticipant.findUnique({
          where: { id: row.id },
          select: { status: true, cycleId: true },
        }),
        this.prisma.signalCycle.findUnique({
          where: { id: row.cycleId },
          select: { id: true, tradeId: true, status: true, intentEnvelope: true },
        }),
        this.loadExecutionMeta(row.id),
      ]);
      if (
        !freshParticipant
        || !freshCycle
        || freshParticipant.status !== SignalCycleStatus.PENDING_ENTRY
        || freshParticipant.cycleId !== row.cycleId
      ) continue;
      const oid = meta.bitfinexOrderId;
      if (oid == null) continue;
      // If the order is still resting on the exchange, it has NOT been
      // cancelled — leave it alone.
      if (activeOrderIdSet.has(oid)) continue;

      // Double-check via a direct findOrder so we don't mis-classify an order
      // that returned to the book between listActiveOrders and now. If the
      // direct lookup finds it (e.g. transient nonce gap on the list call),
      // skip — better to leave a false-positive hanging than to wrongly EXIT.
      const stillThere = await this.activeTrading.findOrder(creds, oid).catch(() => null);
      if (stillThere) continue;

      const latestMeta = await this.loadExecutionMeta(row.id);
      const latestIntent = (freshCycle.intentEnvelope ?? null) as SignalIntentEnvelope | null;
      if (
        shouldDeferCancelByExchangeForReplacement(
          freshCycle.tradeId ?? '',
          latestIntent,
          oid,
          latestMeta.bitfinexOrderId,
        )
      ) {
        this.logger.warn(
          `[RECONCILE-ADOPT] cancel-by-exchange deferred ${userId} participant=${row.id}: exact-limit replacement is settling (observed=${oid}, latest=${latestMeta.bitfinexOrderId ?? 'unknown'})`,
        );
        continue;
      }

      // Cancel-race fill check (always on — misclassification is a bug, not a
      // feature flag): a vanished order is NOT necessarily cancelled — it may
      // have FILLED. Check the merged-position delta before declaring
      // RECONCILE_CANCEL_BY_EXCHANGE; a real fill is recorded as FILLED at
      // the real price instead of a $0 close that orphans the position slice
      // (the orphan-adoption loss factory).
      {
        const fill = await this.detectEntryFillBeforeCancel(creds, latestMeta).catch(() => null);
        if (fill) {
          const recorded = await this.recordCancelRaceFill(
            agentId,
            userId,
            {
              id: row.cycleId,
              status: freshCycle.status,
              tradeId: freshCycle.tradeId ?? null,
            },
            row.id,
            latestMeta,
            creds,
            latestIntent,
            fill,
            'RECONCILE_CANCEL_BY_EXCHANGE',
          );
          if (!recorded) {
            this.logger.warn(
              `[RECONCILE-ADOPT] cancel-by-exchange ${userId} participant=${row.id}: fill detected but recording failed — leaving PENDING_ENTRY for next tick`,
            );
          }
          continue;
        }
      }

      const venue = row.venue ?? 'bitfinex';

      if (!(await this.replacementTerminalUnfilledProof(creds, latestMeta))) continue;

      await this.prisma.signalCycleParticipant.update({
        where: { id: row.id },
        data: {
          status: SignalCycleStatus.CLOSED,
          exitPrice: null,
          pnlUsd: 0,
          pnlMarginPct: 0,
        },
      });

      await this.cycles.recordHireExecutionEvent(userId, agentId, row.cycleId, 'EXIT', {
        venue,
        exit_reason: 'RECONCILE_CANCEL_BY_EXCHANGE',
        pnl_usd: 0,
        pnl_margin_pct: 0,
        source: 'hire',
        event: 'RECONCILE_CANCEL_BY_EXCHANGE',
        bitfinex_order_id: oid,
        participant_id: row.id,
      });

      this.logger.warn(
        `[RECONCILE-ADOPT] cancel-by-exchange detected ${userId} participant=${row.id} cycle=${row.cycleId} bitfinexOrderId=${oid} — order vanished with no FILLED event, marking EXIT @ $0`,
      );
      } finally {
        releaseMoneyLane();
      }
    }

    // Phase 6 fix 5 — defensive re-cancel of the inverse case: a participant
    // already marked EXPIRED (or CLOSED with no fill) whose `meta.bitfinexOrderId`
    // is STILL on the exchange's active book. This is the orphan-by-ledger state
    // the original cancel-on-expiry path created by swallowing cancel failures.
    // Re-attempt the cancel with retry + loud-fail, and only consider the orphan
    // resolved once findOrder confirms the order is gone. Audit
    // RECONCILE_RECANCEL_EXPIRED_STILL_LIVE on every re-attempt so the operator
    // can see the ledger/exchange drift being healed.
    // First account for every currently managed entry/stop order. Previously
    // this code scanned and folded the entire historical participant ledger on
    // every 250ms tick even when the only active order was the current pending
    // entry. That unbounded O(history) loop caused the production watchdog to
    // exceed 60s and cancel a healthy, unfilled canary.
    const liveParticipants = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: { in: [SignalCycleStatus.PENDING_ENTRY, SignalCycleStatus.OPEN] },
        cycle: { agentId },
        ...participantScope,
      },
      select: { id: true },
    });
    const liveLotMeta: ExecutionPayload[] = [];
    for (const participant of liveParticipants) {
      liveLotMeta.push(await this.loadExecutionMeta(participant.id));
    }
    const untrackedOrderIds = untrackedActiveOrderIds(activeOrderIdSet, liveLotMeta);
    if (untrackedOrderIds.length === 0) return;

    const recentCutoff = new Date(Date.now() - EXPIRED_STILL_LIVE_LOOKBACK_MS);
    const scopedCutoff = participantScope.createdAt?.gte;
    const effectiveCutoff =
      scopedCutoff && scopedCutoff > recentCutoff ? scopedCutoff : recentCutoff;
    const expiredCandidates = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: { in: [SignalCycleStatus.EXPIRED, SignalCycleStatus.CLOSED] },
        cycle: { agentId },
        events: { none: { eventType: 'FILLED' } },
        ...participantScope,
        createdAt: { gt: effectiveCutoff },
        updatedAt: { lt: new Date(Date.now() - 120_000) },
      },
      select: { id: true, cycleId: true, status: true, venue: true },
      orderBy: { updatedAt: 'desc' },
      take: EXPIRED_STILL_LIVE_CANDIDATE_LIMIT,
    });

    for (const row of expiredCandidates) {
      const meta = await this.loadExecutionMeta(row.id);
      const oid = meta.bitfinexOrderId;
      if (oid == null) continue;
      // Only act if the order is confirmed still on the active book.
      if (!untrackedOrderIds.includes(oid)) continue;

      const venue = row.venue ?? 'bitfinex';
      const cancel = await this.cancelManagedOrderGone(
        creds,
        oid,
        `[RECONCILE] re-cancel expired-still-live ${oid} for ${userId} participant=${row.id}`,
      );
      if (cancel.gone) {
        this.logger.warn(
          `[RECONCILE] re-cancelled expired-still-live order ${oid} for ${userId} participant=${row.id} (was ${row.status} on ledger) — order now confirmed gone`,
        );
      } else {
        this.logger.error(
          `[RECONCILE] re-cancel of expired-still-live order ${oid} for ${userId} participant=${row.id} FAILED (attempts=${cancel.attempts}, reason=${cancel.reason}) — order still live, will retry next tick`,
        );
      }
      // Audit on every attempt (success or fail) so the operator has a
      // complete trail of the ledger/exchange drift being healed.
      await this.cycles
        .recordHireExecutionEvent(userId, agentId, row.cycleId, 'RECONCILE_RECANCEL_EXPIRED_STILL_LIVE', {
          venue,
          source: 'hire',
          bitfinex_order_id: oid,
          participant_id: row.id,
          participant_status: row.status,
          cancel_attempts: cancel.attempts,
          cancel_reason: cancel.reason ?? (cancel.gone ? 'cancelled' : 'unknown'),
          order_confirmed_gone: cancel.gone,
        })
        .catch(() => {
          /* audit-best-effort — must not abort the loop */
        });
    }
  }

  /**
   * Phase 2 — Layer B (NestJS Live Copy) reconcile-adopt pass.
   *
   * The NestJS Live Copy keeps per-participant runtime state in the in-memory
   * `positionRuntime` Map (volatile, lost on API restart) while Neon
   * `SignalCycleParticipant` rows + `SignalCycleEvent` payloads persist. On
   * restart, OPEN participants whose `meta.stopOrderId` was filled or cancelled
   * by the exchange lose their protective stop and never get re-armed by the
   * normal tick (monitorOpenPosition only arms when `meta.stopOrderId` is
   * missing — it does NOT verify the persisted id is still live on the
   * exchange). This loop closes that gap.
   *
   * Per tick, for each OPEN + PENDING_ENTRY participant of an active hire:
   *  1. OPEN: verify meta.stopOrderId is still live via findOrder; if gone,
   *     re-arm via ensureProtectiveStop (gated by RECONCILE_WRITE_WINDOW).
   *     Re-hydrate positionRuntime if missing so monitorOpenPosition resumes
   *     Scenario C mirroring (the exit ladder comes from intent.risk on the
   *     cycle's intentEnvelope, preserved in Neon — no re-decision needed).
   *  2. PENDING_ENTRY: verify meta.bitfinexOrderId is still on the exchange.
   *     The existing monitorEntry/applyLimitChase path already reprices every
   *     chase window — no re-placement here. If meta.bitfinexOrderId is
   *     missing entirely, surface in orphanPositionIds for manual decision.
   *  3. Idempotent: skip participants already in positionRuntime with a
   *     verified-live stop. try/catch per participant so one failure doesn't
   *     abort the loop. Each action appends a RECONCILE_ADOPT_* audit event
   *     to the SignalCycleEvent stream.
   */
  private async reconcileAdoptLoop(
    agentId: string,
    userId: string,
    instanceId: string,
    creds: ExchangeCredentials,
    participantScope: { createdAt?: { gte: Date } } = {},
  ) {
    const writeWindow = reconcileWriteWindowEnabled();
    const participants = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: { in: [SignalCycleStatus.OPEN, SignalCycleStatus.PENDING_ENTRY] },
        cycle: { agentId },
        ...participantScope,
      },
      include: {
        cycle: { select: { id: true, intentEnvelope: true, tradeId: true } },
      },
    });

    const orphanPositionIds: Array<{
      participantId: string;
      cycleId: string;
      status: string;
      reason: string;
      bitfinexOrderId?: number;
      stopOrderId?: number;
      fillPrice?: number;
    }> = [];

    for (const p of participants) {
      try {
        const meta = await this.loadExecutionMeta(p.id);
        const cycleId = p.cycleId;
        const tradeId = p.cycle?.tradeId ?? undefined;
        const intent = p.cycle?.intentEnvelope as SignalIntentEnvelope | null;

        if (p.status === SignalCycleStatus.OPEN) {
          // A close already owns this lot. Never hydrate or re-arm its stop
          // from a concurrent reconciliation snapshot: the close path will
          // record the durable terminal event before releasing this fence.
          if (this.exitingLots.has(p.id)) {
            this.logger.log(
              `[RECONCILE-ADOPT] participant=${p.id} cycle=${cycleId} action=skip reason=showcase_close_in_progress`,
            );
            continue;
          }
          // Re-hydrate runtime if missing so monitorOpenPosition resumes
          // Scenario C mirroring (peak/profit-lock tracking + exit ladder).
          if (!this.positionRuntime.has(p.id)) {
            const runtime = this.hydrateRuntime(p.id, meta);
            this.positionRuntime.set(p.id, runtime);
            this.logger.log(
              `[RECONCILE-ADOPT] participant=${p.id} cycle=${cycleId} action=rehydrate reason=runtime_missing_after_restart`,
            );
            await this.cycles
              .recordHireExecutionEvent(userId, agentId, cycleId, 'RECONCILE_ADOPT_REHYDRATE', {
                venue: p.venue ?? 'bitfinex',
                participant_id: p.id,
                peak_margin_pct: runtime.peakMarginPct,
                profit_lock_floor: runtime.lastProfitLockFloor,
                source: 'hire',
              })
              .catch(() => {
                /* audit-only — never abort the loop on event write failure */
              });
          }

          // No stopOrderId in meta → monitorOpenPosition will arm it on the
          // next tick (line ~2269). Nothing to adopt here.
          if (!meta.stopOrderId) {
            this.logger.log(
              `[RECONCILE-ADOPT] participant=${p.id} cycle=${cycleId} action=skip reason=no_persisted_stop_order_id`,
            );
            continue;
          }

          // Verify the persisted stop is still live on the exchange.
          const liveStop = await this.activeTrading
            .findOrder(creds, meta.stopOrderId)
            .catch(() => null);

          if (liveStop) {
            // Idempotent skip — stop is live and runtime is hydrated.
            // Option A — when dynamic stops are on, also reconstruct the
            // Scenario C rung the live stop corresponds to (from peak MFE +
            // last profit-lock floor in persisted meta) so the trail block
            // has the right baseline on the next tick and never re-places a
            // stop that's already at the correct level. Emits a RECONSTRUCTED
            // audit line; does NOT write to the exchange.
            if (exchangeDynamicStopsEnabled()) {
              const rt = this.positionRuntime.get(p.id) ?? this.hydrateRuntime(p.id, meta);
              const peak = rt.peakMarginPct ?? 0;
              const rungIdx = solveScenarioCRung(peak, SCENARIO_C_LADDER);
              if (rt.currentStopOrderId == null) {
                rt.currentStopOrderId = meta.stopOrderId;
              }
              if (rt.currentRungIdx == null && rungIdx != null) {
                rt.currentRungIdx = rungIdx;
              }
              if (rt.consecutiveStopFailures == null) {
                rt.consecutiveStopFailures = 0;
              }
              this.stopManagerCircuitOpen.delete(p.id);
              this.positionRuntime.set(p.id, rt);
              this.appendExchangeStopAudit({
                cycleId,
                userId,
                participantId: p.id,
                side: (meta.direction ?? 'LONG').toUpperCase() as 'LONG' | 'SHORT',
                entry: meta.fillPrice ?? 0,
                peakMarginPct: peak,
                prevRung: null,
                newRung: rungIdx,
                oldStop: liveStop.price ?? null,
                newStop: liveStop.price ?? null,
                bitfinexOldOrderId: meta.stopOrderId,
                bitfinexNewOrderId: meta.stopOrderId,
                action: 'RECONSTRUCTED',
              });
            }
            this.logger.log(
              `[RECONCILE-ADOPT] participant=${p.id} cycle=${cycleId} action=skip reason=stop_live stopOrderId=${meta.stopOrderId}`,
            );
            continue;
          }

          // Stop is GONE (filled or cancelled). Re-arm via ensureProtectiveStop,
          // which itself checks for an existing live stop before re-submitting
          // (no double-arm). Money-path: require a verified fillPrice — refuse
          // to re-arm with a stale or zero price.
          const fillPrice =
            meta.fillPrice && meta.fillPrice > 0
              ? meta.fillPrice
              : (p.fillPrice && Number(p.fillPrice) > 0 ? Number(p.fillPrice) : 0);

          if (!fillPrice || fillPrice <= 0) {
            this.logger.warn(
              `[RECONCILE-ADOPT] participant=${p.id} cycle=${cycleId} action=refuse reason=no_verified_fill_price stopOrderId=${meta.stopOrderId}`,
            );
            await this.cycles
              .recordHireExecutionEvent(userId, agentId, cycleId, 'RECONCILE_STOP_REARM_REFUSED', {
                venue: p.venue ?? 'bitfinex',
                participant_id: p.id,
                stop_order_id: meta.stopOrderId,
                fill_price: fillPrice,
                source: 'hire',
              })
              .catch(() => {});
            orphanPositionIds.push({
              participantId: p.id,
              cycleId,
              status: p.status,
              reason: 'RECONCILE_STOP_REARM_REFUSED_NO_FILL_PRICE',
              stopOrderId: meta.stopOrderId,
              fillPrice,
            });
            continue;
          }

          if (!writeWindow) {
            this.logger.warn(
              `[RECONCILE-ADOPT] participant=${p.id} cycle=${cycleId} action=skip reason=reconcile_write_window_disabled stopOrderId=${meta.stopOrderId}`,
            );
            await this.cycles
              .recordHireExecutionEvent(userId, agentId, cycleId, 'RECONCILE_STOP_REARM_SKIPPED', {
                venue: p.venue ?? 'bitfinex',
                participant_id: p.id,
                stop_order_id: meta.stopOrderId,
                fill_price: fillPrice,
                reason: 'RECONCILE_WRITE_WINDOW_DISABLED',
                source: 'hire',
              })
              .catch(() => {});
            orphanPositionIds.push({
              participantId: p.id,
              cycleId,
              status: p.status,
              reason: 'RECONCILE_STOP_REARM_SKIPPED_WRITE_WINDOW_DISABLED',
              stopOrderId: meta.stopOrderId,
              fillPrice,
            });
            continue;
          }

          if (!intent) {
            this.logger.warn(
              `[RECONCILE-ADOPT] participant=${p.id} cycle=${cycleId} action=refuse reason=no_intent_envelope_for_stop_rearm`,
            );
            orphanPositionIds.push({
              participantId: p.id,
              cycleId,
              status: p.status,
              reason: 'RECONCILE_STOP_REARM_REFUSED_NO_INTENT',
              stopOrderId: meta.stopOrderId,
              fillPrice,
            });
            continue;
          }

          // ensureProtectiveStop uses fillPrice ?? meta.limitPrice as the entry
          // reference for stop placement; pass the verified fillPrice
          // explicitly so it never falls back to a stale limitPrice.
          // A showcase-close fast path can terminalize this participant while
          // this reconcile pass is waiting on the exchange stop lookup above.
          // Re-read its durable state immediately before a write so a stale
          // OPEN snapshot cannot re-arm a stop after a confirmed exit.
          const durableParticipant = await this.prisma.signalCycleParticipant.findUnique({
            where: { id: p.id },
            select: { status: true },
          });
          if (durableParticipant?.status !== SignalCycleStatus.OPEN) {
            this.logger.log(
              `[RECONCILE-ADOPT] participant=${p.id} cycle=${cycleId} action=skip reason=terminalized_during_stop_check`,
            );
            continue;
          }
          await this.ensureProtectiveStop(
            agentId,
            userId,
            cycleId,
            p.id,
            meta,
            creds,
            intent,
            fillPrice,
          );
          this.logger.log(
            `[RECONCILE-ADOPT] participant=${p.id} cycle=${cycleId} action=rearm_stop reason=stop_gone stopOrderId=${meta.stopOrderId} fillPrice=${fillPrice.toFixed(2)}`,
          );
          await this.cycles
            .recordHireExecutionEvent(userId, agentId, cycleId, 'RECONCILE_ADOPT_REARM', {
              venue: p.venue ?? 'bitfinex',
              participant_id: p.id,
              prior_stop_order_id: meta.stopOrderId,
              fill_price: fillPrice,
              source: 'hire',
            })
            .catch(() => {});
          continue;
        }

        // PENDING_ENTRY — verify the resting bitfinexOrderId is still on the
        // exchange. The existing monitorEntry/applyLimitChase path handles
        // repricing every chase window (cancel+replace); no re-placement here.
        if (p.status === SignalCycleStatus.PENDING_ENTRY) {
          if (!meta.bitfinexOrderId) {
            this.logger.warn(
              `[RECONCILE-ADOPT] participant=${p.id} cycle=${cycleId} action=refuse reason=pending_entry_no_bitfinex_order_id`,
            );
            orphanPositionIds.push({
              participantId: p.id,
              cycleId,
              status: p.status,
              reason: 'RECONCILE_PENDING_ENTRY_NO_ORDER_ID',
            });
            continue;
          }
          const stillThere = await this.activeTrading
            .findOrder(creds, meta.bitfinexOrderId)
            .catch(() => null);
          if (stillThere) {
            this.logger.log(
              `[RECONCILE-ADOPT] participant=${p.id} cycle=${cycleId} action=skip reason=pending_entry_order_resting bitfinexOrderId=${meta.bitfinexOrderId}`,
            );
          } else {
            // Order gone — either filled (reconcileUnattributedExchangeFills /
            // healStuckPendingFill handles that) or cancelled
            // (reconcileCancelByExchange handles that). Surface + log; do NOT
            // re-place here (would risk duplicate fills on a race).
            this.logger.warn(
              `[RECONCILE-ADOPT] participant=${p.id} cycle=${cycleId} action=skip reason=pending_entry_order_gone bitfinexOrderId=${meta.bitfinexOrderId}`,
            );
          }
          continue;
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(
          `[RECONCILE-ADOPT] participant=${p.id} cycle=${p.cycleId} action=error reason=${msg}`,
        );
      }
    }

    if (orphanPositionIds.length > 0) {
      await this.persistOrphanPositionIds(instanceId, orphanPositionIds).catch(() => {
        /* surfacing is best-effort — never abort the tick on a dashboard patch failure */
      });
    }
  }

  /**
   * Surface participants that the reconcile-adopt loop could NOT auto-heal
   * (missing fill price for stop re-arm, write window disabled, missing
   * intent envelope, PENDING_ENTRY with no order id) into
   * dashboardState.orphanPositionIds so the operator can decide. Surface-
   * only — does NOT auto-cancel or auto-close. dashboardState is JSON so no
   * schema migration is required. Sibling to persistOrphanOrderIds.
   */
  private async persistOrphanPositionIds(
    instanceId: string,
    orphans: Array<{
      participantId: string;
      cycleId: string;
      status: string;
      reason: string;
      bitfinexOrderId?: number;
      stopOrderId?: number;
      fillPrice?: number;
    }>,
  ) {
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instanceId },
      select: { dashboardState: true },
    });
    if (!fresh) return;
    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;
    await this.prisma.tradingAgentInstance.update({
      where: { id: instanceId },
      data: {
        dashboardState: applyDashboardPatch(dash, {
          orphanPositionIds: orphans,
          orphanPositionDetectedAt: new Date().toISOString(),
        }) as unknown as Prisma.InputJsonValue,
      },
    });
  }

  private async healStuckPendingFill(
    participantId: string,
    cycleId: string,
    fillPrice: number,
  ) {
    await this.prisma.signalCycleParticipant.update({
      where: { id: participantId },
      data: {
        status: SignalCycleStatus.OPEN,
        fillPrice,
      },
    });
    await this.prisma.signalCycle.update({
      where: { id: cycleId },
      data: { status: SignalCycleStatus.OPEN },
    });
    this.logger.log(`Healed stuck PENDING_ENTRY → OPEN for participant ${participantId}`);
  }

  private async ensureProtectiveStop(
    agentId: string,
    userId: string,
    cycleId: string,
    participantId: string,
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    intent: SignalIntentEnvelope,
    fillPrice?: number,
    /**
     * Phase 4 S6b adoption — explicit conservative stop price override. When
     * provided, the standard `computeStopPrice(entry, ..., stopLossMarginPct)`
     * formula is bypassed and this price is submitted directly. The caller is
     * responsible for guaranteeing it is never WIDER than the standard SL
     * (e.g. a profit-lock rung price for an in-profit orphan). Omit to keep
     * the legacy behaviour used by every other call site.
    */
    stopPriceOverride?: number,
    qtyOverride?: number,
  ) {
    if (!meta.direction) return;
    const qty = qtyOverride ?? meta.qty ?? MIN_QTY_BTC;
    const entry = fillPrice ?? meta.limitPrice;
    if (!entry || entry <= 0) return;

    const leverage = resolveSubscriberLeverage(intent);
    const stopLossMarginPct = resolveEffectiveStopLossMarginPct(
      intent.risk.stop_loss_margin_pct,
      { mirrorMode: isShowcaseMirrorOnlyMode(), simActive: false },
    );
    const stopPrice =
      stopPriceOverride != null && stopPriceOverride > 0
        ? stopPriceOverride
        : computeStopPrice(entry, meta.direction, stopLossMarginPct, leverage);

    if (meta.stopOrderId) {
      const existing = await this.activeTrading.findOrder(creds, meta.stopOrderId).catch(() => null);
      if (existing) return;
    }

    const priorStopOrderId = meta.stopOrderId;
    const stopOrderId = await this.activeTrading.submitStopOrder(creds, {
      positionDirection: meta.direction,
      qty,
      stopPrice,
      leverage,
    }).catch(() => null);

    if (stopOrderId != null) {
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycleId, 'STOP_LOSS_ARMED', {
        venue: 'bitfinex',
        stop_price: stopPrice,
        stopOrderId,
        qty,
        stop_loss_margin_pct: stopLossMarginPct,
        supersededStopOrderId:
          priorStopOrderId != null && priorStopOrderId !== stopOrderId
            ? priorStopOrderId
            : null,
        source: 'hire',
      });
      // A stop can be briefly absent from the active-order snapshot while it
      // is still live. The replacement is durably recorded first, then the
      // prior stop remains owned until cancellation is confirmed. This avoids
      // surfacing a second protective stop as a foreign/orphan order.
      if (priorStopOrderId != null && priorStopOrderId !== stopOrderId) {
        const priorGone = await this.cancelManagedOrderGone(
          creds,
          priorStopOrderId,
          `STOP_REARM supersede prior stop ${priorStopOrderId} with ${stopOrderId}`,
        );
        if (priorGone.gone) {
          await this.cycles.recordHireExecutionEvent(userId, agentId, cycleId, 'UPDATE_STOPS', {
            venue: 'bitfinex',
            source: 'hire',
            event: 'SUPERSEDED_STOP_CLEARED',
            supersededStopOrderId: null,
          });
        }
      }
      this.logger.log(`Hire stop retry ${userId} cycle=${cycleId} @ ${stopPrice.toFixed(2)}`);
    }
  }

  private async applyLimitChase(
    agentId: string,
    userId: string,
    cycleId: string,
    participantId: string,
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    intent: SignalIntentEnvelope,
    tradeId: string | undefined,
    immediate: boolean,
  ) {
    if (!meta.direction || !meta.limitPrice || !meta.bitfinexOrderId) return;

    // Fix 3 — adopted (adopt:*) resting orders are fill-or-expire ONLY. Their
    // tradeId matches nothing in showcase state, so a chase would walk the
    // limit toward market autonomously and can fill a signal the showcase
    // already abandoned. TTL expiry (cycle.expiresAt) reaps them instead.
    if (tradeId?.startsWith('adopt:')) return;

    const lastChaseAtMs = meta.lastChaseAtMs ?? 0;
    const now = Date.now();
    const mark = await this.activeTrading.getMarkPrice();
    const nearFill = isNearChaseFillZone(meta.direction, meta.limitPrice, mark);
    const signedLimit = tradeId
      ? readFreshSignedShowcaseExactLimit(tradeId, intent, now)
      : null;
    // Signed events keep the initial/reprice path fast, but canonical state is
    // still required every tick to detect a showcase fill and enforce its
    // no-worse price boundary.
    const botState = await this.fetchExecutionBotState();
    const showcasePosition = tradeId
      ? (botState?.positions ?? []).find(
          (position) => position.trade_id && tradeIdsMatch(position.trade_id, tradeId),
        )
      : null;
    const showcaseDetails = tradeId && showcasePosition
      ? resolveShowcaseTradeDetails(botState, tradeId)
      : null;
    const showcaseFill = Number(showcaseDetails?.entry ?? showcasePosition?.entry ?? 0);
    if (showcasePosition && Number.isFinite(showcaseFill) && showcaseFill > 0) {
      const cappedLimit = capRelayLimitAtShowcaseFill(
        meta.direction,
        meta.limitPrice,
        showcaseFill,
      );
      if (Math.abs(cappedLimit - meta.limitPrice) >= 0.01) {
        await this.replaceRestingLimit(
          agentId,
          userId,
          cycleId,
          participantId,
          meta,
          creds,
          intent,
          {
            newLimit: cappedLimit,
            mark,
            now,
            chaseLabel: `showcase-fill-cap=${showcaseFill.toFixed(2)}`,
            event: 'BOT_ANCHOR_CHASE',
            tradeId,
          },
        );
      }
      // Freeze here. Signal TTL or showcase exit owns cancellation.
      return;
    }
    const canonicalLimit = tradeId ? this.resolveBotLimitPrice(botState, tradeId) : null;
    const botLimit = canonicalLimit ?? (botState == null ? signedLimit?.limitPrice : null);
    // Phase 1 chase convergence (flag-gated): converge to the showcase's
    // CURRENT pending limit every tick, but clamp cancel+replace churn to max
    // 1 replacement per order per second. Legacy path keeps the raw
    // CHASE_BOT_ANCHOR_MS cadence.
    const botAnchorInterval = mirrorConvergenceEnabled()
      ? Math.max(MIRROR_CHASE_MIN_REPLACE_MS, CHASE_BOT_ANCHOR_MS)
      : CHASE_BOT_ANCHOR_MS;
    const chaseInterval =
      botLimit != null && botLimit > 0
        ? botAnchorInterval
        : nearFill
          ? CHASE_NEAR_FILL_INTERVAL_MS
          : CHASE_INTERVAL_MS;
    if (!immediate && now - lastChaseAtMs < chaseInterval) return;

    if (botLimit != null && botLimit > 0) {
      const safeBot = sanitizeLimitPrice(mark, botLimit, meta.direction);
      if (safeBot != null && Math.abs(safeBot - meta.limitPrice) >= 0.01) {
        await this.replaceRestingLimit(agentId, userId, cycleId, participantId, meta, creds, intent, {
          newLimit: safeBot,
          mark,
          now,
          chaseLabel: `bot=${botLimit.toFixed(2)}`,
          event: 'BOT_ANCHOR_CHASE',
          tradeId,
        });
        return;
      }
      if (nearFill) return;
    }

    if (nearFill) return;

    if (isShowcaseMirrorOnlyMode()) return;

    const originalLimit = meta.originalLimitPrice ?? meta.limitPrice;
    const { newLimit, reason } = computeLimitChaseTarget(
      meta.direction,
      meta.limitPrice,
      mark,
      originalLimit,
    );
    if (reason !== 'LIMIT_CHASE' || Math.abs(newLimit - meta.limitPrice) < 0.01) return;

    await this.replaceRestingLimit(agentId, userId, cycleId, participantId, meta, creds, intent, {
      newLimit,
      mark,
      now,
      chaseLabel: '',
      event: 'LIMIT_CHASE',
      tradeId,
    });
  }

  private async replaceRestingLimit(
    agentId: string,
    userId: string,
    cycleId: string,
    participantId: string,
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    intent: SignalIntentEnvelope,
    opts: {
      newLimit: number;
      mark: number;
      now: number;
      chaseLabel: string;
      event: 'LIMIT_CHASE' | 'BOT_ANCHOR_CHASE';
      tradeId?: string | null;
    },
  ) {
    const release = await this.acquireParticipantMoneyLane(participantId);
    try {
      await this.replaceRestingLimitOwned(
        agentId, userId, cycleId, participantId, meta, creds, intent, opts,
      );
    } finally {
      release();
    }
  }

  private async replaceRestingLimitOwned(
    agentId: string,
    userId: string,
    cycleId: string,
    participantId: string,
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    intent: SignalIntentEnvelope,
    opts: {
      newLimit: number;
      mark: number;
      now: number;
      chaseLabel: string;
      event: 'LIMIT_CHASE' | 'BOT_ANCHOR_CHASE';
      /** Showcase trade id — included in the cid hash so chase replacement
       *  orders share the same deterministic clientOrderId as the entry. */
      tradeId?: string | null;
    },
  ) {
    if (!meta.direction || !meta.bitfinexOrderId || !meta.limitPrice) return;

    const leverage = resolveSubscriberLeverage(intent);
    const exactReplacementQty = resolveExactShowcaseEntryQty({
      exactQtyBtc: (intent.entry as SignalIntentEnvelope['entry'] & { exact_qty_btc?: number })?.exact_qty_btc,
      maxMarginUsd: Number(intent.risk?.max_margin_usd ?? meta.margin_usd ?? 0),
      leverage,
      limitPrice: opts.newLimit,
      minQtyBtc: MIN_QTY_BTC,
    });
    const qty = meta.qty ?? (exactReplacementQty.ok ? exactReplacementQty.qty : 0);
    if (btcToSats(qty) === 0) {
      this.logger.error(
        `Hire chase ${userId} cycle=${cycleId}: exact managed quantity unavailable; replacement blocked`,
      );
      return;
    }
    const clientOrderId = computeClientOrderId(cycleId, participantId, opts.tradeId);
    const signedExactLimitCandidate = opts.tradeId
      ? readFreshSignedShowcaseExactLimit(opts.tradeId, intent, opts.now)
      : null;
    const signedExactLimit =
      signedExactLimitCandidate
      && Math.abs(signedExactLimitCandidate.limitPrice - opts.newLimit) < 0.01
        ? signedExactLimitCandidate
        : null;

    // Cancel-race fill check (always on): never cancel+replace an order that
    // already (partially) executed — record the real fill instead. If the
    // order is gone entirely, skip the replace and let monitorEntry's
    // fill/cancel classifier handle it next tick.
    {
      const resting = await this.activeTrading
        .findOrder(creds, meta.bitfinexOrderId)
        .catch(() => null);
      if (!resting) {
        this.logger.warn(
          `Hire chase ${userId} cycle=${cycleId}: order ${meta.bitfinexOrderId} gone before replace — deferring to fill/cancel classification (no replacement placed)`,
        );
        return;
      }
      const filledQty = satsToBtc(exchangeOrderFilledQtySats(resting));
      if (btcToSats(filledQty) > 0) {
        // Preserve the exact managed remainder and keep this participant
        // PENDING_ENTRY. A cumulative partial slice is protected separately;
        // only an exchange-proven full intended quantity may transition OPEN.
        await this.protectPartialFillAndRetainRemainder(
          agentId,
          userId,
          cycleId,
          participantId,
          meta,
          creds,
          intent,
          filledQty,
        );
        return;
      }
    }

    try {
      await this.activeTrading.cancelOrder(creds, meta.bitfinexOrderId);
      const newOrderId = await this.activeTrading.submitLimitOrder(creds, {
        direction: meta.direction,
        qty,
        price: opts.newLimit,
        leverage,
        clientOrderId,
      });
      const exchangeAckAtMs = Date.now();
      const chaseCount = (meta.limitChaseCount ?? 0) + 1;
      this.logger.log(
        `Hire chase ${userId} cycle=${cycleId} ${opts.event} ${meta.limitPrice.toFixed(2)} → ${opts.newLimit.toFixed(2)} (mark ${opts.mark.toFixed(2)}${opts.chaseLabel ? ` ${opts.chaseLabel}` : ''})`,
      );
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycleId, 'UPDATE_STOPS', {
        venue: 'bitfinex',
        event: opts.event,
        prior_limit: meta.limitPrice,
        new_limit: opts.newLimit,
        limitPrice: opts.newLimit,
        bitfinexOrderId: newOrderId,
        clientOrderId,
        local_mark: opts.mark,
        lastChaseAtMs: opts.now,
        replacementExchangeAckAtMs: exchangeAckAtMs,
        limitChaseCount: chaseCount,
        ...(signedExactLimit
          ? {
              platformReceivedAt: new Date(signedExactLimit.receivedAtMs).toISOString(),
              platformToExchangeAckMs: Math.max(
                0,
                exchangeAckAtMs - signedExactLimit.receivedAtMs,
              ),
              ...(signedExactLimit.sourceEventAtMs != null
                ? {
                    sourceEventAt: new Date(signedExactLimit.sourceEventAtMs).toISOString(),
                    sourceToPlatformMs: Math.max(
                      0,
                      signedExactLimit.receivedAtMs - signedExactLimit.sourceEventAtMs,
                    ),
                    sourceToExchangeAckMs: Math.max(
                      0,
                      exchangeAckAtMs - signedExactLimit.sourceEventAtMs,
                    ),
                  }
                : {}),
            }
          : {}),
        source: 'hire',
      });
      const runtime = this.hydrateRuntime(participantId, meta);
      runtime.lastChaseAtMs = opts.now;
      this.positionRuntime.set(participantId, runtime);
    } catch (err) {
      this.logger.warn(
        `Limit chase ${userId} cycle=${cycleId}: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  /**
   * Reconcile protection while a partial entry remainder is still resting.
   * A missing stop with unchanged exposure is re-armed; a triggered stop first
   * cancels the entry remainder, then durably records the filled/closed slice.
   */
  private async reconcilePendingPartialFillStop(
    agentId: string,
    userId: string,
    cycle: { id: string; status: SignalCycleStatus; tradeId: string },
    participantId: string,
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    intent: SignalIntentEnvelope,
  ): Promise<boolean> {
    const stopId = meta.partialFillStopOrderId;
    const partialQty = meta.partialFillQty ?? 0;
    if (!stopId || partialQty <= 0 || !meta.direction) return false;

    // Source terminality wins over stop/remainder maintenance. Reconcile any
    // exchange fill using the terminal context now; otherwise return to the
    // caller's normal terminal cancellation path in this same tick.
    if (cycle.status === SignalCycleStatus.CLOSED || cycle.status === SignalCycleStatus.EXPIRED) {
      // partialFillQty is durable proof that real exchange exposure exists.
      // Do not let the generic <85%-of-target fill detector turn this into a
      // zero-fill EXPIRED row when the remainder order has already vanished.
      const remainder = meta.bitfinexOrderId
        ? await this.cancelManagedOrderGone(
            creds,
            meta.bitfinexOrderId,
            `Terminal partial ${userId} cycle=${cycle.id}: cancel-confirm entry remainder`,
          )
        : { gone: true, attempts: 0, reason: 'NO_ORDER_ID' };
      if (!remainder.gone) {
        const message = `TERMINAL_PARTIAL_REMAINDER_NOT_GONE cycle=${cycle.id}`;
        await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
        return true;
      }

      const tradeEvidence = await this.resolveExchangeTradesFillEvidence(
        creds,
        meta.bitfinexOrderId,
      );
      let signedPosition: Awaited<ReturnType<ExecutionTradingClient['getOpenPositionDetail']>> | undefined;
      try {
        signedPosition = await this.activeTrading.getOpenPositionDetail(creds);
      } catch {
        signedPosition = undefined;
      }
      if (signedPosition === undefined) {
        const message = `TERMINAL_PARTIAL_POSITION_UNKNOWN cycle=${cycle.id}`;
        await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
        return true;
      }
      const knownStopIds = [
        meta.partialFillStopOrderId,
        meta.stopOrderId,
        meta.supersededPartialStopOrderId,
      ].filter((id, index, all): id is number => !!id && all.indexOf(id) === index);
      if (signedPosition === null || btcToSats(Math.abs(signedPosition.amount)) === 0) {
        for (const knownStopId of knownStopIds) {
          const stopGone = await this.cancelManagedOrderGone(
            creds,
            knownStopId,
            `Terminal partial already-flat ${userId} cycle=${cycle.id}: clear stop ${knownStopId}`,
          );
          if (!stopGone.gone) {
            const message = `TERMINAL_PARTIAL_FLAT_STOP_NOT_GONE cycle=${cycle.id} stop=${knownStopId}`;
            await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
            return true;
          }
        }
        const realizedQty = Math.max(partialQty, tradeEvidence?.qty ?? 0);
        const realizedPrice = tradeEvidence?.price ?? meta.limitPrice ?? meta.fillPrice ?? 0;
        await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'FILLED', {
          venue: 'bitfinex',
          source: 'hire',
          event: 'TERMINAL_PARTIAL_ALREADY_FLAT',
          fill_price: realizedPrice,
          qty: realizedQty,
          stopOrderId: null,
          partialFillQty: null,
          partialFillStopOrderId: null,
          supersededPartialStopOrderId: null,
        });
        await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXIT', {
          venue: 'bitfinex',
          source: 'hire',
          event: 'TERMINAL_PARTIAL_ALREADY_FLAT',
          exit_reason: 'EXCHANGE_ALREADY_FLAT',
          qty_closed: realizedQty,
          pnl_usd: 0,
        });
        return true;
      }
      const signedDirectionMatches =
        (meta.direction === 'LONG' && signedPosition.amount > 0) ||
        (meta.direction === 'SHORT' && signedPosition.amount < 0);
      if (!signedDirectionMatches) {
        const message = `TERMINAL_PARTIAL_OPPOSITE_EXPOSURE cycle=${cycle.id} amount=${signedPosition.amount}`;
        await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
        return true;
      }
      const sameDirectionQty =
        signedPosition &&
        ((meta.direction === 'LONG' && signedPosition.amount > 0) ||
          (meta.direction === 'SHORT' && signedPosition.amount < 0))
          ? Math.abs(signedPosition.amount)
          : 0;
      const attributablePositionDelta = Math.max(
        0,
        sameDirectionQty - (meta.exchangeQtyAtOrder ?? 0),
      );
      const cumulativeQty = Math.max(
        partialQty,
        tradeEvidence?.qty ?? 0,
        attributablePositionDelta,
      );
      if (btcToSats(cumulativeQty) > 0) {
        return this.recordCancelRaceFill(
          agentId,
          userId,
          cycle,
          participantId,
          meta,
          creds,
          intent,
          {
            filledQty: cumulativeQty,
            fillPrice: tradeEvidence?.price ?? meta.limitPrice ?? 0,
            source: 'POSITION_DELTA',
            orderResting: false,
          },
          'SHOWCASE_CYCLE_CLOSED',
        );
      }

      let terminalFill: Awaited<ReturnType<typeof this.detectEntryFillBeforeCancel>>;
      try {
        terminalFill = await this.detectEntryFillBeforeCancel(creds, meta);
      } catch (err) {
        const message = `TERMINAL_PARTIAL_FILL_STATUS_UNKNOWN cycle=${cycle.id}: ${
          err instanceof Error ? err.message : String(err)
        }`;
        await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
        return true;
      }
      if (!terminalFill) return false;
      return this.recordCancelRaceFill(
        agentId,
        userId,
        cycle,
        participantId,
        meta,
        creds,
        intent,
        terminalFill,
        'SHOWCASE_CYCLE_CLOSED',
      );
    }

    let stop: Awaited<ReturnType<ExecutionTradingClient['findOrder']>>;
    try {
      stop = await this.activeTrading.findOrder(creds, stopId);
    } catch (err) {
      const message = `PARTIAL_FILL_STOP_STATUS_UNKNOWN cycle=${cycle.id}: ${
        err instanceof Error ? err.message : String(err)
      }`;
      await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
      return true;
    }
    if (stop) return false;

    // The entry remainder can fill while the stop lookup is in flight. Read
    // its latest cumulative exchange fill before deciding what quantity needs
    // protection; never re-arm from stale durable partialFillQty alone.
    let entryOrder: Awaited<ReturnType<ExecutionTradingClient['findOrder']>>;
    try {
      entryOrder = meta.bitfinexOrderId
        ? await this.activeTrading.findOrder(creds, meta.bitfinexOrderId)
        : null;
    } catch (err) {
      const message = `PARTIAL_FILL_ENTRY_STATUS_UNKNOWN cycle=${cycle.id}: ${
        err instanceof Error ? err.message : String(err)
      }`;
      await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
      return true;
    }
    const goneOrderEvidence = entryOrder
      ? null
      : await this.resolveExchangeTradesFillEvidence(creds, meta.bitfinexOrderId);
    const freshCumulativeQty = entryOrder
      ? satsToBtc(exchangeOrderFilledQtySats(entryOrder))
      : (goneOrderEvidence?.qty ?? partialQty);
    const intendedQty = meta.qty ?? partialQty;
    if (
      !entryOrder &&
      btcToSats(freshCumulativeQty) > btcToSats(partialQty)
    ) {
      return this.recordCancelRaceFill(
        agentId,
        userId,
        cycle,
        participantId,
        meta,
        creds,
        intent,
        {
          filledQty: freshCumulativeQty,
          fillPrice: goneOrderEvidence?.price ?? meta.limitPrice ?? 0,
          source: 'POSITION_DELTA',
          orderResting: false,
        },
        'PARTIAL_REMAINDER_GONE_FILL_RECONCILE',
      );
    }
    if (
      !entryOrder &&
      btcToSats(freshCumulativeQty) + btcToSats(Math.max(MIN_QTY_BTC, intendedQty * 0.001)) >=
        btcToSats(intendedQty)
    ) {
      return this.recordCancelRaceFill(
        agentId,
        userId,
        cycle,
        participantId,
        meta,
        creds,
        intent,
        {
          filledQty: freshCumulativeQty,
          fillPrice: goneOrderEvidence?.price ?? meta.limitPrice ?? 0,
          source: 'POSITION_DELTA',
          orderResting: false,
        },
        'PARTIAL_REMAINDER_FULL_FILL_RECONCILE',
      );
    }
    if (entryOrder && btcToSats(freshCumulativeQty) > btcToSats(partialQty)) {
      await this.protectPartialFillAndRetainRemainder(
        agentId,
        userId,
        cycle.id,
        participantId,
        { ...meta, partialFillStopOrderId: null, stopOrderId: undefined },
        creds,
        intent,
        freshCumulativeQty,
      );
      return true;
    }

    let position: Awaited<ReturnType<ExecutionTradingClient['getOpenPositionDetail']>>;
    try {
      position = await this.activeTrading.getOpenPositionDetail(creds);
    } catch (err) {
      const message = `PARTIAL_FILL_POSITION_STATUS_UNKNOWN cycle=${cycle.id}: ${
        err instanceof Error ? err.message : String(err)
      }`;
      await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
      return true;
    }
    const sameDirectionQty =
      position &&
      ((meta.direction === 'LONG' && position.amount > 0) ||
        (meta.direction === 'SHORT' && position.amount < 0))
        ? Math.abs(position.amount)
        : 0;
    const tolerance = Math.max(MIN_QTY_BTC, partialQty * 0.01);
    if (sameDirectionQty + tolerance >= partialQty) {
      // The stop disappeared without reducing exposure (for example a manual
      // cancellation). Re-arm before allowing any further entry lifecycle.
      await this.protectPartialFillAndRetainRemainder(
        agentId,
        userId,
        cycle.id,
        participantId,
        { ...meta, partialFillStopOrderId: null, stopOrderId: undefined },
        creds,
        intent,
        partialQty,
      );
      return true;
    }

    if (meta.bitfinexOrderId) {
      const cancel = await this.cancelManagedOrderGone(
        creds,
        meta.bitfinexOrderId,
        `Partial stop triggered ${userId} cycle=${cycle.id}: cancel entry remainder`,
      );
      if (!cancel.gone) {
        const message = `PARTIAL_STOP_TRIGGERED_REMAINDER_CANCEL_FAILED cycle=${cycle.id}`;
        await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
        return true;
      }
    }

    const flatProof = await this.activeTrading.getOpenPositionDetail(creds).catch(() => undefined);
    if (flatProof === undefined || (flatProof && btcToSats(Math.abs(flatProof.amount)) > 0)) {
      const message = `PARTIAL_STOP_TRIGGER_NOT_FLAT_PROVEN cycle=${cycle.id}`;
      await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
      await this.cycles.recordHireExecutionEvent(
        userId,
        agentId,
        cycle.id,
        'RECONCILE_CANCEL_FAILED',
        {
          venue: 'bitfinex',
          source: 'hire',
          event: 'PARTIAL_FILL_STOP_MISSING',
          reason: 'RESIDUAL_FLAT_NOT_PROVEN',
          observed_amount: flatProof?.amount,
        },
      ).catch(() => {});
      return true;
    }

    const fillPrice = meta.limitPrice ?? meta.fillPrice ?? 0;
    await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'FILLED', {
      venue: 'bitfinex',
      fill_price: fillPrice,
      qty: partialQty,
      source: 'hire',
      event: 'PARTIAL_FILL_STOP_TRIGGERED',
      stopOrderId: null,
      partialFillQty: null,
      partialFillStopOrderId: null,
    });
    await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXIT', {
      venue: 'bitfinex',
      source: 'hire',
      event: 'PARTIAL_FILL_STOP_TRIGGERED',
      exit_reason: 'EXCHANGE_STOP',
      qty_closed: partialQty,
      pnl_usd: 0,
    });
    await this.pauseUserRelayForPositionMismatch(
      userId,
      agentId,
      `PARTIAL_FILL_STOP_TRIGGERED cycle=${cycle.id}; source/copy diverged`,
    ).catch(() => {});
    return true;
  }

  /**
   * Keep a partially executed managed limit on-book for its exact remainder.
   * Protection is resized submit-new-before-cancel-old: both orders are
   * reduce-only, so the brief overlap cannot reverse or over-close the
   * position, while there is never an unprotected cancellation window.
   */
  private async protectPartialFillAndRetainRemainder(
    agentId: string,
    userId: string,
    cycleId: string,
    participantId: string,
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    intent: SignalIntentEnvelope | null,
    filledQty: number,
  ): Promise<void> {
    if (!meta.direction || btcToSats(filledQty) <= 0 || !meta.bitfinexOrderId) return;
    const protectionDetectedAtMs = Date.now();
    const desiredQty = meta.qty ?? filledQty;
    const remainingQty = Math.max(0, desiredQty - filledQty);
    const priorStopId = meta.partialFillStopOrderId ?? meta.stopOrderId;
    const alreadyCovered = meta.partialFillQty ?? 0;

    if (priorStopId && btcToSats(alreadyCovered) >= btcToSats(filledQty)) {
      const existing = await this.activeTrading.findOrder(creds, priorStopId);
      if (existing) return;
    }

    // Retry cleanup from a prior acknowledged overlap before creating another
    // generation of stops.
    if (meta.supersededPartialStopOrderId) {
      const oldGone = await this.cancelManagedOrderGone(
        creds,
        meta.supersededPartialStopOrderId,
        `PARTIAL-FILL retry superseded stop ${meta.supersededPartialStopOrderId}`,
      );
      if (!oldGone.gone) {
        const message = `PARTIAL_FILL_SUPERSEDED_STOP_STILL_LIVE cycle=${cycleId}`;
        await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
        throw new Error(message);
      }
    }

    const leverage = resolveSubscriberLeverage(intent);
    const stopLossMarginPct = resolveEffectiveStopLossMarginPct(
      intent?.risk?.stop_loss_margin_pct,
      { mirrorMode: isShowcaseMirrorOnlyMode() },
    );
    const fillPrice = meta.limitPrice ?? (await this.activeTrading.getMarkPrice());
    const stopPrice = computeStopPrice(fillPrice, meta.direction, stopLossMarginPct, leverage);

    let newStopId: number;
    const stopSubmitStartedAtMs = Date.now();
    let stopExchangeAckAtMs: number | undefined;
    try {
      newStopId = await this.activeTrading.submitStopOrder(creds, {
        positionDirection: meta.direction,
        qty: filledQty,
        stopPrice,
        leverage,
      });
      stopExchangeAckAtMs = Date.now();
    } catch (err) {
      const message = `PARTIAL_FILL_PROTECTION_FAILED cycle=${cycleId}: ${
        err instanceof Error ? err.message : String(err)
      }`;
      await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
      // Fail closed: never leave an unprotected partial position while its
      // remaining entry can continue filling. Cancel the remainder first,
      // then flatten only the proven partial slice and confirm reduction.
      const remainder = await this.cancelManagedOrderGone(
        creds,
        meta.bitfinexOrderId,
        `Partial protection failed ${userId} cycle=${cycleId}: cancel entry remainder`,
      );
      let flattened = false;
      if (remainder.gone) {
        let before: Awaited<ReturnType<ExecutionTradingClient['getOpenPositionDetail']>> | undefined;
        try {
          before = await this.activeTrading.getOpenPositionDetail(creds);
        } catch {
          before = undefined;
        }
        if (before === null) {
          flattened = true;
        } else if (before !== undefined) {
          const sameDirection =
            (meta.direction === 'LONG' && before.amount > 0) ||
            (meta.direction === 'SHORT' && before.amount < 0);
          if (!sameDirection && btcToSats(Math.abs(before.amount)) > 0) {
            const mismatch = `PARTIAL_FILL_OPPOSITE_EXPOSURE cycle=${cycleId} amount=${before.amount}`;
            await this.pauseUserRelayForPositionMismatch(userId, agentId, mismatch).catch(() => {});
          } else {
            const beforeQty = Math.abs(before.amount);
            // After the entry remainder is confirmed gone, flatten the fresh
            // same-direction exchange quantity rather than the stale observed
            // partial. A concurrent final fill can otherwise survive as an orphan.
            const closeQty = beforeQty;
            if (btcToSats(closeQty) === 0) {
              flattened = true;
            } else {
          await this.activeTrading.submitMarketClose(creds, {
            positionDirection: meta.direction,
            qty: closeQty,
            leverage: resolveSubscriberLeverage(intent),
          }).catch(() => null);
          flattened = await this.waitForMarketCloseConfirmation(
            creds,
            meta.direction,
            beforeQty,
            closeQty,
          ).catch(() => false);
            }
          }
        }
      }
      if (flattened) {
        await this.cycles.recordHireExecutionEvent(userId, agentId, cycleId, 'EXPIRED', {
          venue: 'bitfinex',
          source: 'hire',
          event: 'PARTIAL_FILL_PROTECTION_FAIL_CLOSED',
          reason: 'STOP_SUBMIT_FAILED_EXCHANGE_FLAT_CONFIRMED',
          partial_fill_qty: filledQty,
          pnl_usd: 0,
        });
      } else {
        await this.cycles.recordHireExecutionEvent(
          userId,
          agentId,
          cycleId,
          'RECONCILE_CANCEL_FAILED',
          {
            venue: 'bitfinex',
            source: 'hire',
            event: 'PARTIAL_FILL_PROTECTION_FAILED',
            reason: remainder.gone
              ? 'EMERGENCY_FLAT_NOT_CONFIRMED'
              : 'ENTRY_REMAINDER_CANCEL_NOT_CONFIRMED',
          },
        ).catch(() => {});
      }
      throw new Error(message);
    }

    // Persist the new stop id before touching the old one. A process crash
    // after exchange ACK can then recover both reduce-only orders instead of
    // leaving the new protection invisible to the ledger.
    let supersededStopId: number | undefined = priorStopId && priorStopId !== newStopId
      ? priorStopId
      : undefined;
    await this.cycles.recordHireExecutionEvent(userId, agentId, cycleId, 'UPDATE_STOPS', {
      venue: 'bitfinex',
      source: 'hire',
      event: 'PARTIAL_FILL_STOP_REPLACEMENT_ACKNOWLEDGED',
      participant_id: participantId,
      bitfinexOrderId: meta.bitfinexOrderId,
      stopOrderId: newStopId,
      partialFillStopOrderId: newStopId,
      partialFillQty: filledQty,
      supersededPartialStopOrderId: supersededStopId,
      intended_qty: desiredQty,
      remaining_qty: remainingQty,
      stop_price: stopPrice,
      partial_fill_detected_at: new Date(protectionDetectedAtMs).toISOString(),
      stop_submit_started_at: new Date(stopSubmitStartedAtMs).toISOString(),
      stop_exchange_ack_at: new Date(stopExchangeAckAtMs).toISOString(),
      detection_to_stop_ack_ms: Math.max(0, stopExchangeAckAtMs - protectionDetectedAtMs),
      stop_submit_to_ack_ms: Math.max(0, stopExchangeAckAtMs - stopSubmitStartedAtMs),
    });

    if (priorStopId && priorStopId !== newStopId) {
      const oldGone = await this.cancelManagedOrderGone(
        creds,
        priorStopId,
        `PARTIAL-FILL supersede stop ${priorStopId} with ${newStopId}`,
      );
      if (oldGone.gone) supersededStopId = undefined;
    }

    await this.cycles.recordHireExecutionEvent(userId, agentId, cycleId, 'UPDATE_STOPS', {
      venue: 'bitfinex',
      source: 'hire',
      event: 'PARTIAL_FILL_REMAINDER_RETAINED',
      participant_id: participantId,
      bitfinexOrderId: meta.bitfinexOrderId,
      stopOrderId: newStopId,
      partialFillStopOrderId: newStopId,
      partialFillQty: filledQty,
      supersededPartialStopOrderId: supersededStopId,
      intended_qty: desiredQty,
      remaining_qty: remainingQty,
      stop_price: stopPrice,
      partial_fill_detected_at: new Date(protectionDetectedAtMs).toISOString(),
      stop_submit_started_at: new Date(stopSubmitStartedAtMs).toISOString(),
      stop_exchange_ack_at: new Date(stopExchangeAckAtMs).toISOString(),
      detection_to_stop_ack_ms: Math.max(0, stopExchangeAckAtMs - protectionDetectedAtMs),
      stop_submit_to_ack_ms: Math.max(0, stopExchangeAckAtMs - stopSubmitStartedAtMs),
    });

    if (supersededStopId) {
      const message = `PARTIAL_FILL_SUPERSEDED_STOP_STILL_LIVE cycle=${cycleId}; old=${supersededStopId} new=${newStopId}`;
      await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
      throw new Error(message);
    }
    this.logger.warn(
      `Partial entry protected and retained ${userId} cycle=${cycleId}: ` +
      `filled=${filledQty.toFixed(8)} remaining=${remainingQty.toFixed(8)} ` +
      `entry=${meta.bitfinexOrderId} stop=${newStopId}`,
    );
  }


  /** Cancel resting limits whose price is far from market (e.g. $904 on $64k BTC). */
  private async cancelAbsurdPendingOrders(creds: ExchangeCredentials, userId: string) {
    const mark = await this.activeTrading.getMarkPrice().catch(() => null);
    if (!mark) return;
    const orders = await this.activeTrading.listActiveOrders(creds).catch(() => []);
    for (const order of orders) {
      const deviationPct = Math.abs((order.price - mark) / mark) * 100;
      if (deviationPct > 8) {
        try {
          await this.activeTrading.cancelOrder(creds, order.id);
          this.logger.warn(
            `Cancelled absurd order ${order.id} for ${userId}: price ${order.price} vs mark ${mark.toFixed(2)}`,
          );
        } catch {
          /* already gone */
        }
      }
    }
  }

  /**
   * Phase 2/3 — resolve the showcase trade_id to mirror for exit convergence.
   * Adopted participants (`adopt:*`) re-link to the origin showcase trade.
   */
  private resolveShowcaseMirrorTradeId(
    cycle: { tradeId?: string | null },
    meta: ExecutionPayload,
  ): string | null {
    return resolveShowcaseMirrorTradeIdFromInputs(
      cycle.tradeId ?? null,
      meta.originTradeId,
    );
  }

  /**
   * Cure 3 — best-effort call to Fly's /api/reconcile/phantom-cancel to
   * cancel a phantom paper position left behind when Railway's real limit
   * was confirmed UNFILLED but Fly paper "filled".
   *
   * Uses botBridge.proxyBotPost (already adds X-Bot-Admin-Token, retries
   * across endpoints, 8s timeout). Never throws — Fly being unreachable
   * must NOT block the executor or stall the tick. Every call (success or
   * failure) is audit-logged via SignalCycleEvent so ops has a paper trail.
   *
   * Idempotency rests on the Fly endpoint (which no-ops an already-cancelled
   * trade_id), so a retry on the next tick for the same trade is safe.
   */
  private async cancelPhantomShowcasePosition(
    userId: string,
    agentId: string,
    cycleId: string,
    showcaseTradeId: string,
    reason: string,
  ): Promise<void> {
    if (!showcaseTradeId) return;
    const startedAt = Date.now();
    let outcome: 'OK' | 'NO_CONTENT' | 'ERROR' = 'ERROR';
    let httpStatus: number | null = null;
    let flyMsg: string | undefined;
    let alreadyCancelled = false;
    try {
      const result = await this.botBridge.proxyBotPost('/api/reconcile/phantom-cancel', {
        trade_id: showcaseTradeId,
        reason,
      });
      httpStatus = typeof result.status === 'number' ? result.status : null;
      const data = (result.data ?? {}) as Record<string, unknown>;
      alreadyCancelled = Boolean(data.already_cancelled);
      if (result.ok) {
        outcome = alreadyCancelled ? 'NO_CONTENT' : 'OK';
      } else {
        flyMsg =
          typeof data.error === 'string'
            ? data.error
            : typeof result.error === 'string'
              ? result.error
              : 'unknown fly error';
      }
    } catch (err) {
      flyMsg = err instanceof Error ? err.message : String(err);
      outcome = 'ERROR';
    }

    const elapsedMs = Date.now() - startedAt;
    const eventPayload: Record<string, unknown> = {
      venue: 'bitfinex',
      source: 'hire',
      event: 'PHANTOM_CANCEL_RELAY',
      reason,
      showcase_trade_id: showcaseTradeId,
      outcome,
      http_status: httpStatus,
      elapsed_ms: elapsedMs,
      already_cancelled: alreadyCancelled || undefined,
    };
    if (flyMsg) eventPayload.fly_error = flyMsg;

    // Audit log — fire-and-forget so a DB blip never blocks the executor.
    this.cycles
      .recordHireExecutionEvent(userId, agentId, cycleId, 'UPDATE_STOPS', eventPayload)
      .catch(() => {
        /* audit-best-effort */
      });

    if (outcome === 'OK') {
      this.logger.warn(
        `[CURE-3] Phantom paper position cancelled on Fly ${userId} trade=${showcaseTradeId} reason=${reason} (http=${httpStatus}, ${elapsedMs}ms)`,
      );
    } else if (outcome === 'NO_CONTENT') {
      this.logger.log(
        `[CURE-3] Phantom paper position already cancelled on Fly ${userId} trade=${showcaseTradeId} (idempotent no-op, ${elapsedMs}ms)`,
      );
    } else {
      this.logger.warn(
        `[CURE-3] Phantom paper position cancel FAILED ${userId} trade=${showcaseTradeId} reason=${reason} http=${httpStatus} err=${flyMsg ?? 'n/a'} —Fly may still hold the phantom; next tick will retry (idempotent)`,
      );
    }
  }

  /**
   * Cure 1 — relink a real-filled cycle to the showcase signal that
   * actually corresponds to the real fill, when they differ.
   *
   * Why this exists: the cycle.tradeId is fixed at creation time from the
   * showcase signal_id. When a real Bitfinex limit lands on a price that
   * corresponds to a DIFFERENT signal than the cycle's own (the
   * mirror-owner-of-duplicate-limit race, or a chase order that crossed at
   * the new signal's price), the participant's cycle.tradeId is stale. Every
   * mirror-exit path keys off cycle.tradeId and never fires for the real
   * fill's signal; the real position rots unmanaged.
   *
   * This method is idempotent: if cycle.tradeId already starts with `relink:`
   * and meta.originTradeId matches the resolved candidate, it returns the
   * already-relinked tradeId without writing. If the candidate differs from
   * the existing re-link target, it re-links again to the new candidate
   * (rare; only if multiple signals shared the fill price band).
   *
   * Returns the showcase tradeId the cycle now mirrors (existing, new, or
   * null when no re-link occurred — e.g. showcase unreachable, sim mode, or
   * no candidate matched).
   */
  private async relinkCycleToShowcaseSignalIfDrifted(input: {
    agentId: string;
    userId: string;
    cycle: { id: string; tradeId?: string | null };
    participantId: string;
    realFill: { price: number; direction: 'LONG' | 'SHORT' };
    reason: string;
  }): Promise<string | null> {
    const bot = await this.fetchExecutionBotState().catch(() => null);
    if (!bot) return null;

    // Some reconciliation paths historically carried only cycle.id/status.
    // Resolve the durable identity before candidate matching so an already-
    // canonical fill cannot be mistaken for a drift merely because the
    // caller's narrowed snapshot omitted tradeId.
    let currentTradeId = input.cycle.tradeId ?? null;
    if (!currentTradeId || currentTradeId === 'unknown') {
      const persistedCycle = await this.prisma.signalCycle
        .findUnique({
          where: { id: input.cycle.id },
          select: { tradeId: true },
        })
        .catch(() => null);
      currentTradeId = persistedCycle?.tradeId ?? currentTradeId;
    }

    // Build the set of showcase tradeIds already claimed by other OPEN
    // participants via re-link, so two real fills can't both point at the
    // same showcase position.
    const claimedByOthers = new Set<string>();
    try {
      const openRows = await this.prisma.signalCycleParticipant.findMany({
        where: {
          userId: input.userId,
          status: SignalCycleStatus.OPEN,
          cycle: { agentId: input.agentId },
          id: { not: input.participantId },
        },
        select: { id: true },
      });
      for (const row of openRows) {
        const m = await this.loadExecutionMeta(row.id);
        if (m.originTradeId) claimedByOthers.add(m.originTradeId);
      }
    } catch {
      /* best-effort — fail-open (no exclusions) */
    }

    const candidate = resolveShowcaseRelinkForRealFill({
      showcasePositions: bot.positions ?? [],
      realFill: input.realFill,
      currentTradeId,
      nowMs: Date.now(),
      alreadyRelinkedTo: claimedByOthers,
    });
    if (!candidate) return null;

    // Already re-linked to this exact signal — nothing to do.
    const existingMeta = await this.loadExecutionMeta(input.participantId);
    if (
      (input.cycle.tradeId ?? '').startsWith('relink:') &&
      existingMeta.originTradeId === candidate.tradeId
    ) {
      return candidate.tradeId;
    }

    const originTradeId = currentTradeId ?? 'unknown';
    const relinkedTradeId = `relink:${originTradeId}:${candidate.tradeId}:${Date.now()}`;

    try {
      await this.prisma.signalCycle.update({
        where: { id: input.cycle.id },
        data: { tradeId: relinkedTradeId },
      });
    } catch (err) {
      // Unique constraint collision (extremely unlikely — Date.now() is in
      // the id) or cycle gone. Log and bail; mirror-exit safety nets still
      // cover the lot.
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.warn(
        `[CURE-1] relink failed ${input.userId} cycle=${input.cycle.id}: ${msg}`,
      );
      return null;
    }

    // Persist the new showcase trade id on the participant via a META event
    // so loadExecutionMeta folds originTradeId into every later tick. The
    // event payload also carries the relink audit context.
    await this.cycles.recordHireExecutionEvent(
      input.userId,
      input.agentId,
      input.cycle.id,
      'UPDATE_STOPS',
      {
        venue: 'bitfinex',
        source: 'hire',
        event: 'CYCLE_TRADE_ID_RELINK',
        reason: input.reason,
        // CRITICAL — origin_trade_id is the field loadExecutionMeta folds into
        // meta.originTradeId, which resolveShowcaseMirrorTradeId returns for
        // any `relink:`/`adopt:` cycle. It MUST be the NEW showcase trade id
        // (the one the real fill actually corresponds to), NOT the stale
        // original. The pre-relink origin is preserved in prior_origin_trade_id
        // for audit only.
        origin_trade_id: candidate.tradeId,
        origin_trade_id_key: 'origin_trade_id',
        prior_origin_trade_id: originTradeId,
        new_showcase_trade_id: candidate.tradeId,
        relinked_cycle_trade_id: relinkedTradeId,
        real_fill_price: input.realFill.price,
        real_fill_direction: input.realFill.direction,
        showcase_entry_price: candidate.entryPrice,
        showcase_price_band_pct: Math.round(candidate.priceBandPct * 10000) / 10000,
        showcase_time_delta_ms: candidate.timeDeltaMs,
        participant_id: input.participantId,
      },
    );

    this.logger.warn(
      `[CURE-1] Relinked orphan real fill ${input.userId} cycle=${input.cycle.id} ${input.realFill.direction} @ ${input.realFill.price.toFixed(2)} from stale trade=${originTradeId} to live showcase trade=${candidate.tradeId} (entry=${candidate.entryPrice.toFixed(2)}, band=${candidate.priceBandPct.toFixed(4)}%, dt=${candidate.timeDeltaMs ?? 'n/a'}ms) — reason=${input.reason}`,
    );

    return candidate.tradeId;
  }

  /** Detect showcase trade closure from bot state (trades / trades_map / positions). */
  private detectShowcaseTradeClosed(
    bot: BotApiState | null,
    tradeId: string | null,
  ): { closed: boolean; exitPrice?: number; exitReason?: string } {
    if (!bot || !tradeId) return { closed: false };

    const inPositions = (bot.positions ?? []).some(
      (p) => p.trade_id && tradeIdsMatch(p.trade_id, tradeId),
    );
    if (inPositions) return { closed: false };

    const inOrders = (bot.orders ?? []).some(
      (o) =>
        o.trade_id &&
        tradeIdsMatch(o.trade_id, tradeId) &&
        (o.status === 'PENDING' || o.status === 'ORDERED'),
    );
    if (inOrders) return { closed: false };

    const details = resolveShowcaseTradeDetails(bot, tradeId);
    if (details?.exit != null && Number.isFinite(details.exit) && details.exit > 0) {
      return {
        closed: true,
        exitPrice: details.exit,
        exitReason: details.exitReason,
      };
    }

    for (const [mapKey, entry] of Object.entries(bot.trades_map ?? {})) {
      const sig = entry?.signal_ref as Record<string, unknown> | undefined;
      if (!sig) continue;
      const refId = String(sig.trade_id ?? mapKey);
      if (!tradeIdsMatch(refId, tradeId) && !tradeIdsMatch(mapKey, tradeId)) continue;
      if (
        String(sig.status ?? '') === 'CLOSED' &&
        (sig.exit_price != null || sig.closed_ts != null)
      ) {
        const exitCtx = sig.exit_context as Record<string, unknown> | undefined;
        const exitPrice = Number(exitCtx?.exit_price ?? sig.exit_price ?? 0);
        return {
          closed: true,
          exitPrice: exitPrice > 0 ? exitPrice : undefined,
          exitReason: String(sig.exit_reason ?? ''),
        };
      }
    }

    const trades = normalizeBotSessionTrades(bot);
    const trade = trades.find((t) => t.trade_id && tradeIdsMatch(t.trade_id, tradeId));
    if (trade?.exit != null && trade.pnl != null) {
      return {
        closed: true,
        exitPrice: trade.exit ?? undefined,
        exitReason: trade.exit_reason ?? undefined,
      };
    }

    return { closed: false };
  }

  /**
   * Phase 2c/3a — immediate showcase mirror exit before independent exit ladder.
   * Returns true when the lot was closed or exit is in progress.
   */
  private async tryImmediateShowcaseMirrorExit(
    agentId: string,
    userId: string,
    cycle: {
      id: string;
      status: SignalCycleStatus;
      tradeId?: string | null;
      intentEnvelope?: unknown;
      showcasePnlUsd?: { toNumber?: () => number } | null;
    },
    participant: { id: string; fillPrice: { toNumber?: () => number } | null },
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    simActive = false,
    preWakeSignedClose?: RelayExecutorWakeRequest['signedClose'],
  ): Promise<boolean> {
    if (simActive) return false;
    if (!mirrorExitConvergenceEnabled() || !isShowcaseMirrorOnlyMode()) return false;

    const showcaseTradeId = this.resolveShowcaseMirrorTradeId(cycle, meta);
    const mirrorRelinked = (cycle.tradeId ?? '').startsWith('adopt:');

    let closed =
      preWakeSignedClose != null
      || cycle.status === SignalCycleStatus.CLOSED
      || cycle.status === SignalCycleStatus.EXPIRED;
    let showcaseExitPrice: number | undefined;
    let showcaseExitReason: string | undefined;
    let mirrorTrigger = mirrorRelinked ? 'ORIGIN_SHOWCASE_CLOSED' : 'SHOWCASE_CLOSED';
    // The private prewake is emitted only after owner + HMAC verification. Carry
    // that exact terminal evidence into the worker so it can close the already-
    // owned lot while the canonical Neon transaction is still committing,
    // instead of polling Fly and losing the intended overlap.
    const signedClose = preWakeSignedClose ?? readSignedShowcaseClose(cycle.intentEnvelope);
    if (closed && signedClose) {
      showcaseExitPrice = signedClose.exitPrice;
      showcaseExitReason = signedClose.exitReason;
      mirrorTrigger = mirrorRelinked
        ? 'ORIGIN_SHOWCASE_CLOSED_WEBHOOK'
        : 'SHOWCASE_CLOSED_WEBHOOK';
    }

    if (!closed && showcaseTradeId) {
      const bot = await this.fetchExecutionBotState();
      const det = this.detectShowcaseTradeClosed(bot, showcaseTradeId);
      if (det.closed) {
        closed = true;
        showcaseExitPrice = det.exitPrice;
        showcaseExitReason = det.exitReason;
        // F7 — tag the wake source. Webhook wakes mean the showcase just
        // pushed POSITION_CLOSED and we should be exiting within ~2s; poll
        // wakes mean we discovered the closure on the regular 2s tick. Both
        // are valid; the tag is for ops telemetry.
        const wakeSource = this.consumeWakeTrigger();
        mirrorTrigger = mirrorRelinked
          ? `ORIGIN_SHOWCASE_CLOSED_${wakeSource}`
          : `SHOWCASE_CLOSED_${wakeSource}`;
      } else if (
        bot &&
        this.trackShowcasePositionAbsent(participant.id, showcaseTradeId, bot)
      ) {
        // Copy OPEN but this trade_id is not in showcase open positions.
        // Covers cross-ID ghost fills (showcase trade still PENDING in trades_map)
        // where SHOWCASE_VANISHED never fires because the trade is still "known".
        closed = true;
        showcaseExitReason = 'SHOWCASE_POSITION_ABSENT';
        mirrorTrigger = 'SHOWCASE_POSITION_ABSENT';
        this.consumeWakeTrigger();
        this.logger.warn(
          `Showcase position absent ${userId} cycle=${cycle.id} trade=${showcaseTradeId} — ` +
            `market-closing copy lot after ${SHOWCASE_POSITION_ABSENT_CONSECUTIVE_MISSES} ` +
            `consecutive fresh states and ${SHOWCASE_POSITION_ABSENT_GRACE_MS}ms convergence grace`,
        );
      } else {
        this.consumeWakeTrigger();
      }
    } else {
      this.consumeWakeTrigger();
    }

    if (!closed && mirrorRelinked && meta.originCycleId) {
      const originCycle = await this.prisma.signalCycle.findUnique({
        where: { id: meta.originCycleId },
        select: { status: true },
      });
      if (
        originCycle?.status === SignalCycleStatus.CLOSED ||
        originCycle?.status === SignalCycleStatus.EXPIRED
      ) {
        closed = true;
        mirrorTrigger = 'ORIGIN_SHOWCASE_CLOSED';
      }
    }

    if (!closed) return false;

    if (showcaseExitPrice == null && showcaseTradeId && signedClose == null) {
      const bot = await this.fetchExecutionBotState();
      const det = this.detectShowcaseTradeClosed(bot, showcaseTradeId);
      showcaseExitPrice = det.exitPrice;
      showcaseExitReason = det.exitReason ?? showcaseExitReason;
    }

    return this.executeShowcaseMirrorClose(
      agentId,
      userId,
      cycle,
      participant,
      meta,
      creds,
      {
        showcaseExitPrice,
        showcaseExitReason,
        sourceEventAtMs: signedClose?.sourceEventAtMs,
        platformReceivedAtMs: signedClose?.platformReceivedAtMs,
        mirrorRelinked,
        trigger: mirrorTrigger,
      },
    );
  }

  /**
   * Fail-safe when showcase is flat/closed but a copy lot remains OPEN after the
   * normal mirror path did not close it (e.g. stale EXIT before hasParticipantExited
   * fix, or a transient bot-state miss). Surfaces operator alert at 120s and forces
   * market-close via executeShowcaseMirrorClose.
   */
  private async enforceShowcaseFlatOpenFailsafe(
    agentId: string,
    userId: string,
    cycle: {
      id: string;
      status: SignalCycleStatus;
      tradeId?: string | null;
      showcasePnlUsd?: { toNumber?: () => number } | null;
      intentEnvelope?: unknown;
    },
    participant: { id: string; fillPrice: { toNumber?: () => number } | null },
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    simActive = false,
  ): Promise<boolean> {
    if (simActive) return false;
    if (!mirrorExitConvergenceEnabled() || !isShowcaseMirrorOnlyMode()) return false;

    const showcaseTradeId = this.resolveShowcaseMirrorTradeId(cycle, meta);
    const cycleTerminal =
      cycle.status === SignalCycleStatus.CLOSED || cycle.status === SignalCycleStatus.EXPIRED;

    let showcaseFlat = cycleTerminal;
    let showcaseExitPrice: number | undefined;
    let showcaseExitReason: string | undefined;

    if (!showcaseFlat && showcaseTradeId) {
      const bot = await this.fetchExecutionBotState();
      if (!bot) return false;
      const det = this.detectShowcaseTradeClosed(bot, showcaseTradeId);
      if (det.closed) {
        showcaseFlat = true;
        showcaseExitPrice = det.exitPrice;
        showcaseExitReason = det.exitReason;
      } else {
        const inPositions = (bot.positions ?? []).some(
          (p) => p.trade_id && tradeIdsMatch(p.trade_id, showcaseTradeId),
        );
        if (!inPositions && (bot.positions ?? []).length === 0) {
          showcaseFlat = true;
          showcaseExitReason = 'SHOWCASE_BOOK_FLAT';
        } else {
          this.showcaseFlatOpenSince.delete(participant.id);
          return false;
        }
      }
    }

    if (!showcaseFlat) {
      this.showcaseFlatOpenSince.delete(participant.id);
      return false;
    }

    const now = Date.now();
    const since = this.showcaseFlatOpenSince.get(participant.id) ?? now;
    if (!this.showcaseFlatOpenSince.has(participant.id)) {
      this.showcaseFlatOpenSince.set(participant.id, since);
    }
    const elapsed = now - since;

    if (elapsed < SHOWCASE_FLAT_OPEN_FAILSAFE_MS) {
      return false;
    }

    const staleExitCount = await this.prisma.signalCycleEvent.count({
      where: { participantId: participant.id, eventType: 'EXIT' },
    });
    const alertMsg = `MIRROR EXIT FAIL-SAFE: showcase flat but copy OPEN ${Math.round(elapsed / 1000)}s (participant=${participant.id.slice(0, 8)}… stale_exit_events=${staleExitCount}) — forcing market close`;
    this.logger.error(alertMsg);
    await this.setInstanceLastError(userId, agentId, alertMsg);
    await this.cycles
      .recordHireExecutionEvent(userId, agentId, cycle.id, 'MIRROR_EXIT_FAILSAFE_ALERT', {
        venue: 'bitfinex',
        source: 'hire',
        participant_id: participant.id,
        trade_id: showcaseTradeId ?? cycle.tradeId,
        elapsed_ms: elapsed,
        stale_exit_events: staleExitCount,
        showcase_exit_reason: showcaseExitReason,
      })
      .catch(() => {});

    return this.executeShowcaseMirrorClose(agentId, userId, cycle, participant, meta, creds, {
      showcaseExitPrice,
      showcaseExitReason: showcaseExitReason ?? 'SHOWCASE_FLAT_FAILSAFE',
      mirrorRelinked: (cycle.tradeId ?? '').startsWith('adopt:'),
      trigger: 'SHOWCASE_FLAT_FAILSAFE',
      forceMirrorExit: true,
    });
  }

  private async expectedRemainingLedgerAmount(
    agentId: string,
    userId: string,
    exitingParticipantId: string,
  ): Promise<number | null> {
    const rows = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        id: { not: exitingParticipantId },
        status: SignalCycleStatus.OPEN,
        cycle: { agentId },
      },
      select: { id: true },
    });
    let signedSats = 0;
    for (const row of rows) {
      if (await this.hasParticipantExited(row.id)) continue;
      const meta = await this.loadExecutionMeta(row.id);
      if (!meta.direction || !meta.qty || btcToSats(meta.qty) === 0) return null;
      const qtySats = Math.abs(btcToSats(meta.qty));
      signedSats += meta.direction === 'LONG' ? qtySats : -qtySats;
    }
    return satsToBtc(signedSats);
  }

  private async pauseUserRelayForPositionMismatch(
    userId: string,
    agentId: string,
    message: string,
  ): Promise<void> {
    const instances = await this.prisma.tradingAgentInstance.findMany({
      where: { userId, agentId, exchangeProvider: 'bitfinex' },
    });
    for (const instance of instances) {
      await this.pauseRelayForPositionMismatch(instance, message);
    }
  }

  /**
   * A close acknowledgement can time out after Bitfinex has partially reduced
   * the position. Re-arm only the exchange-verified remainder owned by the
   * exiting lot; never restore the lot's original full-size stop.
   */
  private async ensureProtectiveStopForVerifiedExitResidual(
    agentId: string,
    userId: string,
    cycleId: string,
    participantId: string,
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    intent: SignalIntentEnvelope,
    fillPrice: number | undefined,
    stopOrderIds: Iterable<number>,
  ): Promise<void> {
    if (!meta.direction) return;

    for (const stopOrderId of new Set(stopOrderIds)) {
      const liveStop = await this.activeTrading.findOrder(creds, stopOrderId);
      if (liveStop) return;
    }

    const remainingLedgerAmount = await this.expectedRemainingLedgerAmount(
      agentId,
      userId,
      participantId,
    );
    if (remainingLedgerAmount == null) return;
    const position = await this.activeTrading.getOpenPositionDetail(creds);
    const target = relayLotExitTarget({
      currentAmount: position?.amount ?? 0,
      remainingLedgerAmount,
      exitingLedgerQty: meta.qty ?? 0,
      direction: meta.direction,
    });
    if (!target.ok || btcToSats(target.closeQty) === 0) return;

    await this.ensureProtectiveStop(
      agentId,
      userId,
      cycleId,
      participantId,
      { ...meta, stopOrderId: undefined },
      creds,
      intent,
      fillPrice,
      undefined,
      target.closeQty,
    );
  }

  private async closeParticipantPositionToLedgerTarget(
    agentId: string,
    userId: string,
    participantId: string,
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    leverage: number,
    stopOrderIds: Iterable<number>,
    prepared?: {
      remainingLedgerAmount: number | null;
    },
  ): Promise<RelayLotExitTarget> {
    if (!meta.direction) {
      throw new Error('EXIT_TARGET_MISSING_DIRECTION');
    }
    const remainingLedgerAmount = prepared
      ? prepared.remainingLedgerAmount
      : await this.expectedRemainingLedgerAmount(agentId, userId, participantId);
    if (remainingLedgerAmount == null) {
      throw new Error('EXIT_TARGET_OTHER_LOT_METADATA_INCOMPLETE');
    }
    // Always read the position immediately before stop cancellation and close
    // submission. An earlier observation may be stale if the protective stop
    // filled while the public mark / ledger target was being resolved.
    const position = await this.activeTrading.getOpenPositionDetail(creds);
    const target = relayLotExitTarget({
      currentAmount: position?.amount ?? 0,
      remainingLedgerAmount,
      exitingLedgerQty: meta.qty ?? 0,
      direction: meta.direction,
    });
    if (!target.ok) {
      const message =
        `${target.reason}: exchange ${target.currentAmount.toFixed(8)} BTC, ` +
        `post-exit ledger target ${target.targetAmount.toFixed(8)} BTC.`;
      await this.pauseUserRelayForPositionMismatch(userId, agentId, message);
      throw new Error(message);
    }
    if (btcToSats(target.closeQty) === 0) {
      // No market action is required, so there is no close-SLA critical path
      // to protect. Retire any stale reduce-only stops before reporting the
      // ledger-target result.
      for (const stopOrderId of new Set(stopOrderIds)) {
        const result = await this.cancelManagedOrderGone(
          creds,
          stopOrderId,
          `EXIT-TARGET already-flat cancel stop oid=${stopOrderId} ${userId} participant=${participantId}`,
        );
        if (!result.gone) {
          throw new Error(
            `EXIT_TARGET_STOP_CANCEL_FAILED oid=${stopOrderId} reason=${result.reason ?? 'unknown'}`,
          );
        }
      }
      return target;
    }

    const closeSubmitStartedAtMs = Date.now();
    if (target.finalAccountFlatten) {
      await this.activeTrading.submitPositionFlatten(creds, {
        positionDirection: meta.direction,
        qty: target.closeQty,
        leverage,
      });
    } else {
      await this.activeTrading.submitMarketClose(creds, {
        positionDirection: meta.direction,
        qty: target.closeQty,
        leverage,
      });
    }
    const closeExchangeAckAtMs = Date.now();
    const confirmed = await this.waitForMarketCloseConfirmation(
      creds,
      meta.direction,
      Math.abs(target.currentAmount),
      target.closeQty,
    );
    if (!confirmed) {
      const message =
        `MARKET_CLOSE_NOT_CONFIRMED_BY_EXCHANGE_POSITION: expected ` +
        `${target.targetAmount.toFixed(8)} BTC after closing ${target.closeQty.toFixed(8)} BTC.`;
      await this.pauseUserRelayForPositionMismatch(userId, agentId, message);
      throw new Error(message);
    }
    // Keep reduce-only protection live until the exchange has confirmed the
    // market reduction. Cancelling a stop before submitting the close made the
    // stop-cancel round trip part of the critical path (2.493s in cont-9b),
    // even though a reduce-only stop cannot reverse a flat position. Cleanup
    // is now post-confirmation and best effort; orphan reconciliation retains
    // it as a backstop if Bitfinex has a transient cancel-read failure.
    for (const stopOrderId of new Set(stopOrderIds)) {
      try {
        const result = await this.cancelManagedOrderGone(
          creds,
          stopOrderId,
          `EXIT-TARGET post-close cancel stop oid=${stopOrderId} ${userId} participant=${participantId}`,
        );
        if (!result.gone) {
          this.logger.warn(
            `EXIT_TARGET_POST_CLOSE_STOP_CANCEL_PENDING oid=${stopOrderId} reason=${result.reason ?? 'unknown'}`,
          );
        }
      } catch (err) {
        this.logger.warn(
          `EXIT_TARGET_POST_CLOSE_STOP_CANCEL_ERROR oid=${stopOrderId}: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }
    }
    return {
      ...target,
      closeSubmitStartedAtMs,
      closeExchangeAckAtMs,
      closeConfirmedAtMs: Date.now(),
    };
  }

  private async waitForMarketCloseConfirmation(
    creds: ExchangeCredentials,
    direction: 'LONG' | 'SHORT',
    beforeQty: number,
    closeQty: number,
  ): Promise<boolean> {
    for (const delayMs of [0, 200, 400, 800, 1_200]) {
      if (delayMs > 0) {
        await new Promise<void>((resolve) => setTimeout(resolve, delayMs));
      }
      try {
        const after = await this.activeTrading.getOpenPositionDetail(creds);
        if (
          marketCloseReductionConfirmed({
            direction,
            beforeQty,
            closeQty,
            afterAmount: after?.amount ?? 0,
          })
        ) {
          return true;
        }
      } catch {
        // A failed observation is not confirmation; retry within this tick.
      }
    }
    return false;
  }

  /**
   * Phase 2c — market-close copy lot on showcase closure with observability fields.
   * Idempotent via exitingLots + hasParticipantExited (unless forceMirrorExit).
   */
  private async executeShowcaseMirrorClose(
    agentId: string,
    userId: string,
    cycle: {
      id: string;
      showcasePnlUsd?: { toNumber?: () => number } | null;
      intentEnvelope?: unknown;
    },
    participant: { id: string; fillPrice: { toNumber?: () => number } | null },
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    opts?: {
      showcaseExitPrice?: number;
      showcaseExitReason?: string;
      sourceEventAtMs?: number;
      platformReceivedAtMs?: number;
      mirrorRelinked?: boolean;
      trigger?: string;
      /** Bypass hasParticipantExited (stale RECONCILE_CANCEL EXIT events). */
      forceMirrorExit?: boolean;
    },
  ): Promise<boolean> {
    const closePreflightStartedAtMs = Date.now();
    if (!opts?.forceMirrorExit && (await this.hasParticipantExited(participant.id))) return true;
    if (this.exitingLots.has(participant.id)) return true;

    this.exitingLots.add(participant.id);
    try {
      try {
        await this.cancelLinkedPendingLimits(creds, meta);
      } catch (err) {
        const message =
          `LINKED_ENTRY_REMAINDER_UNCERTAIN cycle=${cycle.id}: ${
            err instanceof Error ? err.message : String(err)
          }`;
        await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
        this.logger.warn(`Market close ${userId} cycle=${cycle.id}: ${message}`);
        return false;
      }

      const fillPrice =
        participant.fillPrice != null
          ? Number(participant.fillPrice)
          : meta.limitPrice ?? meta.fillPrice ?? 0;
      if (!meta.qty || !meta.direction) return false;
      const leverage =
        resolveSubscriberLeverage(cycle.intentEnvelope as SignalIntentEnvelope);

      let position: Awaited<ReturnType<ExecutionTradingClient['getOpenPositionDetail']>>;
      let remainingLedgerAmount: number | null;
      // Public mark is audit/PnL enrichment, not a prerequisite for a
      // reduce-only close. Start it concurrently but never hold the exchange
      // close submission behind it; consume it only after the close ACK (or in
      // the already-flat accounting branch).
      const preparedExitPricePromise = this.activeTrading
        .getMarkPrice()
        .catch(() => fillPrice || 0);
      try {
        // Once any linked entry remainder is confirmed gone, overlap the
        // independent ledger and position reads. The target path deliberately
        // performs one final fresh position read immediately before cancelling
        // the stop and submitting the close.
        [position, remainingLedgerAmount] = await Promise.all([
          this.activeTrading.getOpenPositionDetail(creds),
          this.expectedRemainingLedgerAmount(agentId, userId, participant.id),
        ]);
      } catch (err) {
        const message =
          `EXIT_POSITION_READ_FAILED cycle=${cycle.id}: Bitfinex position is unknown; ` +
          `${err instanceof Error ? err.message : String(err)}`;
        await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
        this.logger.warn(`Market close ${userId} cycle=${cycle.id}: ${message}`);
        return false;
      }
      if (!position || btcToSats(position.amount) === 0) {
        try {
          await this.closeParticipantPositionToLedgerTarget(
            agentId,
            userId,
            participant.id,
            meta,
            creds,
            leverage,
            ownedStopOrderIds(meta),
            { remainingLedgerAmount },
          );
        } catch (err) {
          await this.pauseUserRelayForPositionMismatch(
            userId,
            agentId,
            `ALREADY_FLAT_RECONCILIATION_FAILED cycle=${cycle.id}: ${
              err instanceof Error ? err.message : String(err)
            }`,
          ).catch(() => {});
          this.logger.warn(
            `Already-flat target verification ${userId} cycle=${cycle.id}: ${
              err instanceof Error ? err.message : String(err)
            }`,
          );
          return false;
        }
        const exitPrice =
          opts?.showcaseExitPrice ??
          await preparedExitPricePromise;
        let pnlUsd = 0;
        let pnlMarginPct = 0;
        let pnlSource: 'exchange_realised' | 'reconstructed' = 'reconstructed';
        if (meta.qty && meta.direction) {
          const flat = await this.resolveAlreadyFlatPnl(
            agentId,
            userId,
            creds,
            participant.id,
            meta,
            fillPrice,
            exitPrice || fillPrice,
          );
          pnlUsd = flat.pnlUsd;
          pnlSource = flat.pnlSource;
          pnlMarginPct =
            fillPrice && fillPrice > 0
              ? (pnlUsd / (fillPrice * meta.qty)) *
                100 *
                resolveSubscriberLeverage(cycle.intentEnvelope as SignalIntentEnvelope)
              : 0;
        }
        const exitSlippageUsd =
          opts?.showcaseExitPrice != null && exitPrice > 0 && meta.qty
            ? Math.round(Math.abs(exitPrice - opts.showcaseExitPrice) * meta.qty * 100) / 100
            : undefined;
        await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXIT', {
          venue: 'bitfinex',
          exit_price: exitPrice || fillPrice,
          exit_reason: 'SHOWCASE_MIRROR_ALREADY_FLAT',
          showcase_exit_price: opts?.showcaseExitPrice,
          exit_slippage_usd: exitSlippageUsd,
          showcase_exit_reason: opts?.showcaseExitReason,
          mirror_relinked: opts?.mirrorRelinked ?? false,
          mirror_trigger: opts?.trigger,
          pnl_usd: Math.round(pnlUsd * 100) / 100,
          pnl_margin_pct: Math.round(pnlMarginPct * 100) / 100,
          pnl_source: pnlSource,
          source: 'hire',
        });
        this.showcaseVanishedMisses.delete(participant.id);
        this.showcasePositionAbsentMisses.delete(participant.id);
        this.showcasePositionAbsentSince.delete(participant.id);
        this.showcaseFlatOpenSince.delete(participant.id);
        this.logger.log(
          `Exit already flat ${userId} cycle=${cycle.id} — showcase mirror recorded pnl=$${pnlUsd.toFixed(2)} source=${pnlSource}`,
        );
        return true;
      }

      let closeTarget: RelayLotExitTarget;
      try {
        closeTarget = await this.closeParticipantPositionToLedgerTarget(
          agentId,
          userId,
          participant.id,
          meta,
          creds,
          leverage,
          ownedStopOrderIds(meta),
          { remainingLedgerAmount },
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`Market close ${userId} cycle=${cycle.id}: ${msg}`);
        await this.pauseUserRelayForPositionMismatch(
          userId,
          agentId,
          `EXIT_RECONCILIATION_FAILED cycle=${cycle.id}: ${msg}`,
        ).catch(() => {});
        await this.ensureProtectiveStopForVerifiedExitResidual(
          agentId,
          userId,
          cycle.id,
          participant.id,
          meta,
          creds,
          cycle.intentEnvelope as SignalIntentEnvelope,
          fillPrice || undefined,
          ownedStopOrderIds(meta),
        ).catch(() => {});
        return false;
      }
      const exitPrice = await preparedExitPricePromise;
      const closeQty = closeTarget.closeQty;

      const direction = meta.direction;
      const pnlUsd =
        fillPrice && exitPrice
          ? direction === 'LONG'
            ? (exitPrice - fillPrice) * meta.qty
            : (fillPrice - exitPrice) * meta.qty
          : cycle.showcasePnlUsd != null
            ? Number(cycle.showcasePnlUsd)
            : 0;

      const pnlMarginPct =
        fillPrice && fillPrice > 0 ? (pnlUsd / (fillPrice * meta.qty)) * 100 * leverage : 0;

      const exitSlippageUsd =
        opts?.showcaseExitPrice != null && exitPrice > 0 && meta.qty
          ? Math.round(Math.abs(exitPrice - opts.showcaseExitPrice) * meta.qty * 100) / 100
          : undefined;
      const closeExchangeAckAtMs = closeTarget.closeExchangeAckAtMs;
      const closeConfirmedAtMs = closeTarget.closeConfirmedAtMs;

      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXIT', {
        venue: 'bitfinex',
        exit_price: exitPrice,
        exit_reason: 'SHOWCASE_MIRROR',
        showcase_exit_price: opts?.showcaseExitPrice,
        exit_slippage_usd: exitSlippageUsd,
        showcase_exit_reason: opts?.showcaseExitReason,
        close_submit_started_at: closeTarget.closeSubmitStartedAtMs != null
          ? new Date(closeTarget.closeSubmitStartedAtMs).toISOString()
          : undefined,
        close_preflight_started_at: new Date(closePreflightStartedAtMs).toISOString(),
        close_exchange_ack_at: closeExchangeAckAtMs != null
          ? new Date(closeExchangeAckAtMs).toISOString()
          : undefined,
        close_confirmed_at: closeConfirmedAtMs != null
          ? new Date(closeConfirmedAtMs).toISOString()
          : undefined,
        source_to_close_ack_ms:
          opts?.sourceEventAtMs != null && closeExchangeAckAtMs != null
            ? Math.max(0, closeExchangeAckAtMs - opts.sourceEventAtMs)
            : undefined,
        platform_to_close_ack_ms:
          opts?.platformReceivedAtMs != null && closeExchangeAckAtMs != null
            ? Math.max(0, closeExchangeAckAtMs - opts.platformReceivedAtMs)
            : undefined,
        platform_to_close_submit_ms:
          opts?.platformReceivedAtMs != null && closeTarget.closeSubmitStartedAtMs != null
            ? Math.max(0, closeTarget.closeSubmitStartedAtMs - opts.platformReceivedAtMs)
            : undefined,
        close_preflight_to_submit_ms:
          closeTarget.closeSubmitStartedAtMs != null
            ? Math.max(0, closeTarget.closeSubmitStartedAtMs - closePreflightStartedAtMs)
            : undefined,
        close_ack_to_confirm_ms:
          closeExchangeAckAtMs != null && closeConfirmedAtMs != null
            ? Math.max(0, closeConfirmedAtMs - closeExchangeAckAtMs)
            : undefined,
        mirror_relinked: opts?.mirrorRelinked ?? false,
        mirror_trigger: opts?.trigger,
        pnl_usd: Math.round(pnlUsd * 100) / 100,
        pnl_margin_pct: Math.round(pnlMarginPct * 100) / 100,
        pnl_source: 'open_position',
        source: 'hire',
      });

      this.logger.log(
        `Hire showcase mirror exit ${userId} cycle=${cycle.id} pnl=$${pnlUsd.toFixed(2)} showcase_exit=${opts?.showcaseExitPrice?.toFixed(2) ?? 'n/a'} slip=$${exitSlippageUsd ?? 0}`,
      );
      this.showcaseVanishedMisses.delete(participant.id);
      this.showcasePositionAbsentMisses.delete(participant.id);
      this.showcasePositionAbsentSince.delete(participant.id);
      this.showcaseFlatOpenSince.delete(participant.id);
      return true;
    } finally {
      this.exitingLots.delete(participant.id);
    }
  }

  private async countMirrorCatchupEntriesLast24h(userId: string, agentId: string): Promise<number> {
    const since = new Date(Date.now() - 24 * 60 * 60_000);
    return this.prisma.signalCycleEvent.count({
      where: {
        eventType: 'MIRROR_CATCHUP_ENTRY',
        createdAt: { gte: since },
        participant: { userId, cycle: { agentId } },
      },
    });
  }

  /**
   * Phase 3b — action-match catch-up market entry when showcase is OPEN but copy
   * has no OPEN lot and no working order for that trade_id.
   * Policy v4: no daily budget / slip cap by default (fill price may differ).
   * Still fail-closed on: no intent cycle, direction mismatch, capacity/orphans,
   * insufficient margin, exchange errors.
   */
  private async attemptMirrorCatchupEntries(
    agentId: string,
    instance: TradingAgentInstance,
    creds: ExchangeCredentials,
    botState: BotApiState | null,
    openParticipants: Array<{
      id: string;
      status: SignalCycleStatus;
      cycleId: string;
      cycle?: { tradeId: string | null } | null;
    }>,
    lotSummary: VirtualLotSummary,
    managedOrderIds: Set<number>,
    marginCap: number,
    maxConcurrent: number,
    exitOnly: boolean,
    simActive: boolean,
  ): Promise<void> {
    if (exitOnly || simActive) return;
    if (!mirrorCatchupEnabled() || !isShowcaseMirrorOnlyMode()) return;
    if (!this.botBridge.isEnabled() || !botState) return;

    const budget = mirrorCatchupBudgetPerDay();
    const used =
      budget != null ? await this.countMirrorCatchupEntriesLast24h(instance.userId, agentId) : 0;
    if (budget != null && used >= budget) {
      this.logger.warn(
        `[ACTION-MISS] ENTRY catch-up budget exhausted ${instance.userId}: ${used}/${budget} in 24h`,
      );
      return;
    }

    const showcasePositions = (botState.positions ?? []).filter(
      (pos) => typeof pos.trade_id === 'string' && pos.trade_id.length > 0,
    );
    if (!showcasePositions.length) return;

    const mark = await this.activeTrading.getMarkPrice().catch(() => 0);
    if (!mark || mark <= 0) return;

    const maxSlip = mirrorCatchupMaxSlipUsd();
    let catchupsThisTick = 0;
    const budgetRemaining = budget != null ? budget - used : Number.POSITIVE_INFINITY;

    for (const pos of showcasePositions) {
      if (catchupsThisTick >= budgetRemaining) {
        this.logger.warn(
          `[ACTION-MISS] ENTRY catch-up budget hit mid-tick ${instance.userId}: placed=${catchupsThisTick} remaining was ${budgetRemaining}`,
        );
        break;
      }
      const tradeId = pos.trade_id!;

      // F7 (2026-07-08 real-money hotfix) — whitelist-only mirroring. Only
      // only explicitly allow-listed showcase lanes may be mirrored
      // Bitfinex money. This inverts the legacy F6 blocklist, which silently
      // failed-open for every newly-added research lane (vc603-/szdc1-/slav1-
      // were auto-mirrored until manually blocklisted, putting real money on
      // trades that exist only in the showcase bot's paper book). Defense-in-
      // depth: the F6 paper check below is retained as a belt-and-suspenders
      // guard in case a future bot change leaks paper trades into an allowed
      // position (it would be caught by both the lane-prefix check and the
      // paper-book check).
      if (!isMirrorableLaneTradeId(tradeId)) {
        this.logger.warn(
          `[F7] mirror-catchup skipped non-mirrorable lane trade=${tradeId} user=${instance.userId}`,
        );
        continue;
      }
      // F6 (defense-in-depth) — even within the production allowlist, refuse
      // explicit paper-lane trade_ids if a future bot change leaks them.
      if (isPaperLaneTradeId(tradeId)) {
        this.logger.warn(
          `[F6] mirror-catchup skipped paper-lane trade=${tradeId} user=${instance.userId}`,
        );
        continue;
      }

      // Resolve and freshness-check the source cycle before touching any
      // existing participant/order. Start is a strict arming watermark:
      // pre-existing showcase positions must never be caught up.
      const liveCycles = await this.prisma.signalCycle.findMany({
        where: {
          agentId,
          status: {
            in: [
              SignalCycleStatus.INTENT,
              SignalCycleStatus.PENDING_ENTRY,
              SignalCycleStatus.OPEN,
            ],
          },
        },
        orderBy: { createdAt: 'desc' },
        take: 100,
      });
      let matchedCycle = liveCycles.find((c) => tradeIdsMatch(c.tradeId, tradeId)) ?? null;
      if (!matchedCycle) {
        matchedCycle = await this.prisma.signalCycle.findFirst({
          where: { agentId, tradeId },
          orderBy: { createdAt: 'desc' },
        });
      }
      if (!matchedCycle) {
        this.logger.warn(
          `[ACTION-MISS] ENTRY no Neon cycle for showcase OPEN trade=${tradeId} user=${instance.userId}`,
        );
        await this.recordActionMissEntry(agentId, instance.userId, null, tradeId, 'NO_CYCLE', {
          showcase_entry: typeof pos.entry === 'number' ? pos.entry : null,
          mark,
        });
        continue;
      }
      if (!isCycleFreshForRelayArm(instance.dashboardState, matchedCycle.createdAt)) {
        this.logger.log(
          `[NEXT-FRESH-ONLY] skipped pre-arm showcase position trade=${tradeId} ` +
            `cycle_created=${matchedCycle.createdAt.toISOString()}`,
        );
        continue;
      }

      const matching = openParticipants.filter(
        (p) => p.cycle?.tradeId && tradeIdsMatch(p.cycle.tradeId, tradeId),
      );
      if (matching.some((p) => p.status === SignalCycleStatus.OPEN)) continue;
      if (matching.some((p) => p.status === SignalCycleStatus.INTENT)) continue;

      const pendingMatch = matching.find((p) => p.status === SignalCycleStatus.PENDING_ENTRY);
      if (pendingMatch) {
        const cleared = await this.clearPendingForShowcaseCatchup(
          agentId,
          instance.userId,
          pendingMatch,
          creds,
        );
        if (!cleared) continue;
        lotSummary.pending = Math.max(0, lotSummary.pending - 1);
      }

      const showcaseEntry = typeof pos.entry === 'number' && pos.entry > 0 ? pos.entry : mark;
      const showcaseQty = typeof pos.qty === 'number' ? pos.qty : Number.NaN;
      if (!Number.isFinite(showcaseQty) || showcaseQty <= 0) {
        await this.recordActionMissEntry(
          agentId,
          instance.userId,
          matchedCycle.id,
          tradeId,
          'NO_EXACT_QTY',
          { showcase_entry: showcaseEntry, showcase_qty: pos.qty ?? null, mark },
        );
        continue;
      }
      const slipUsd = Math.abs(mark - showcaseEntry);
      if (maxSlip != null && slipUsd > maxSlip) {
        this.logger.warn(
          `[ACTION-MISS] ENTRY catch-up slip cap ${instance.userId} trade=${tradeId}: slip $${slipUsd.toFixed(2)} > cap $${maxSlip}`,
        );
        await this.recordActionMissEntry(agentId, instance.userId, null, tradeId, 'SLIP_CAP', {
          slip_usd: Math.round(slipUsd * 100) / 100,
          max_slip_usd: maxSlip,
          showcase_entry: showcaseEntry,
          mark,
        });
        continue;
      }

      if (
        matchedCycle.status !== SignalCycleStatus.INTENT &&
        matchedCycle.status !== SignalCycleStatus.PENDING_ENTRY &&
        matchedCycle.status !== SignalCycleStatus.OPEN
      ) {
        // A fresh post-arm cycle may have terminal status drift while the
        // canonical showcase position remains OPEN; reopen only that cycle.
        matchedCycle = await this.prisma.signalCycle.update({
          where: { id: matchedCycle.id },
          data: { status: SignalCycleStatus.OPEN, closedAt: null },
        });
        this.logger.warn(
          `[MIRROR-CATCHUP] reopened terminal cycle ${matchedCycle.id} trade=${tradeId} for action-match entry`,
        );
      }

      const intent = matchedCycle.intentEnvelope as SignalIntentEnvelope;
      if (!intent?.direction || !intent?.risk) {
        this.logger.warn(
          `[ACTION-MISS] ENTRY cycle ${matchedCycle.id} trade=${tradeId} missing intent envelope`,
        );
        await this.recordActionMissEntry(
          agentId,
          instance.userId,
          matchedCycle.id,
          tradeId,
          'NO_INTENT',
          { showcase_entry: showcaseEntry, mark },
        );
        continue;
      }

      const posDir = String(pos.dir ?? pos.side ?? '').toUpperCase();
      const showcaseDir =
        posDir.includes('LONG') || posDir === 'BUY'
          ? 'LONG'
          : posDir.includes('SHORT') || posDir === 'SELL'
            ? 'SHORT'
            : intent.direction;
      if (showcaseDir !== intent.direction) {
        this.logger.warn(
          `[ACTION-MISS] ENTRY direction mismatch trade=${tradeId} showcase=${showcaseDir} intent=${intent.direction}`,
        );
        await this.recordActionMissEntry(
          agentId,
          instance.userId,
          matchedCycle.id,
          tradeId,
          'DIRECTION_MISMATCH',
          { showcase_dir: showcaseDir, intent_dir: intent.direction },
        );
        continue;
      }

      const existingParticipant = await this.prisma.signalCycleParticipant.findUnique({
        where: {
          cycleId_userId: { cycleId: matchedCycle.id, userId: instance.userId },
        },
      });
      if (
        existingParticipant &&
        (existingParticipant.status === SignalCycleStatus.OPEN ||
          existingParticipant.status === SignalCycleStatus.PENDING_ENTRY)
      ) {
        continue;
      }

      const eligibility = await this.evaluateEntryEligibility(
        creds,
        {
          open: lotSummary.open + catchupsThisTick,
          pending: lotSummary.pending,
          direction: lotSummary.direction,
        },
        managedOrderIds,
        marginCap,
        maxConcurrent,
        intent.direction,
        instance,
      );
      if (!eligibility.canEnter) {
        this.logger.warn(
          `[ACTION-MISS] ENTRY eligibility blocked trade=${tradeId}: ${eligibility.reason}`,
        );
        await this.recordActionMissEntry(
          agentId,
          instance.userId,
          matchedCycle.id,
          tradeId,
          'ELIGIBILITY',
          { reason: eligibility.reason },
        );
        continue;
      }

      const placed = await this.placeMirrorCatchupEntry(
        agentId,
        instance,
        matchedCycle.id,
        intent,
        creds,
        marginCap,
        tradeId,
        showcaseEntry,
        showcaseQty,
        mark,
        slipUsd,
      );
      if (placed) catchupsThisTick += 1;
    }
  }

  /** Throttled ACTION_MISS_ENTRY audit (max 1 per trade_id per 60s). */
  private async recordActionMissEntry(
    _agentId: string,
    userId: string,
    cycleId: string | null,
    tradeId: string,
    reason: string,
    extra?: Record<string, unknown>,
  ): Promise<void> {
    const key = `${userId}:${tradeId}:${reason}`;
    const now = Date.now();
    const prev = this.actionMissEntryThrottle.get(key) ?? 0;
    if (now - prev < 60_000) return;
    this.actionMissEntryThrottle.set(key, now);

    // Bound the in-memory throttle map — same pattern as mirrorDiffEventAt.
    // Without this sweep, keys accumulate forever (~1.8MB/year worst case at
    // 50 trades/day across all users).
    if (this.actionMissEntryThrottle.size > 500) {
      for (const [k, at] of this.actionMissEntryThrottle) {
        if (now - at > 60 * 60_000) this.actionMissEntryThrottle.delete(k);
      }
    }

    this.logger.warn(`[ACTION-MISS] ENTRY ${reason} trade=${tradeId} user=${userId}`);
    if (!cycleId) return;

    // Write audit events directly — do NOT use recordHireExecutionEvent, which
    // auto-creates a PENDING_ENTRY participant and would block real catch-up.
    const participant = await this.prisma.signalCycleParticipant.findUnique({
      where: { cycleId_userId: { cycleId, userId } },
      select: { id: true },
    });
    const payload = {
      venue: 'bitfinex',
      source: 'hire',
      trade_id: tradeId,
      reason,
      ...extra,
    };
    try {
      await this.prisma.signalCycleEvent.create({
        data: {
          cycleId,
          participantId: participant?.id ?? null,
          eventType: 'ACTION_MISS_ENTRY',
          payload,
        },
      });
      await this.prisma.signalCycleEvent.create({
        data: {
          cycleId,
          participantId: participant?.id ?? null,
          eventType: 'MIRROR_CATCHUP_SKIPPED',
          payload,
        },
      });
    } catch (err) {
      this.logger.warn(
        `[ACTION-MISS] failed to persist entry miss trade=${tradeId}: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  private async placeMirrorCatchupEntry(
    agentId: string,
    instance: TradingAgentInstance,
    cycleId: string,
    intent: SignalIntentEnvelope,
    creds: ExchangeCredentials,
    marginCap: number,
    tradeId: string,
    showcaseEntry: number,
    showcaseQty: number,
    mark: number,
    slipUsd: number,
  ): Promise<boolean> {
    // F1 — Same safe-mode gate as placeEntry. The catch-up path is also a new
    // entry — it must respect the showcase-unreachable block. attemptMirrorCatchupEntries
    // is itself gated on botState != null, but a race between the catchup's
    // own fetch and the per-instance fetch can leave a window. Belt + suspenders.
    if (showcaseUnreachableSafeModeEnabled()) {
      const block = this.entryBlockedByShowcaseOutage(instance.id);
      if (block.blocked) {
        this.logger.warn(
          `[F1] Mirror-catchup entry blocked ${instance.userId} cycle=${cycleId}: showcase unreachable for ${Math.round(block.elapsedMs / 1000)}s`,
        );
        return false;
      }
    }
    const existingClaim = await this.prisma.signalCycleParticipant.findUnique({
      where: { cycleId_userId: { cycleId, userId: instance.userId } },
    });

    let claimParticipantId: string | null = null;
    let revivedTerminal = false;

    if (existingClaim) {
      // Active claim — another path owns this trade (or a concurrent replica).
      if (
        existingClaim.status === SignalCycleStatus.OPEN ||
        existingClaim.status === SignalCycleStatus.PENDING_ENTRY
      ) {
        return false;
      }
      // Fresh INTENT claim (<120s) — another replica is placing; do not steal.
      if (
        existingClaim.status === SignalCycleStatus.INTENT &&
        existingClaim.createdAt.getTime() >= Date.now() - 120_000
      ) {
        return false;
      }
      // Terminal (CLOSED/EXPIRED) or stale INTENT — revive for action-match entry.
      // Unique(cycleId, userId) blocks create; reset the row instead.
      if (
        TERMINAL_PARTICIPANT_STATUSES.has(existingClaim.status) ||
        existingClaim.status === SignalCycleStatus.INTENT
      ) {
        await this.prisma.signalCycleParticipant.update({
          where: { id: existingClaim.id },
          data: {
            status: SignalCycleStatus.INTENT,
            fillPrice: null,
            exitPrice: null,
            pnlUsd: null,
            pnlMarginPct: null,
            stopLossConfirmedAt: null,
          },
        });
        claimParticipantId = existingClaim.id;
        revivedTerminal = true;
        this.logger.warn(
          `[MIRROR-CATCHUP] revived ${existingClaim.status} participant ${existingClaim.id} cycle=${cycleId} trade=${tradeId}`,
        );
      } else {
        return false;
      }
    } else {
      try {
        const claimed = await this.prisma.signalCycleParticipant.create({
          data: {
            cycleId,
            userId: instance.userId,
            venue: 'bitfinex',
            status: SignalCycleStatus.INTENT,
          },
        });
        claimParticipantId = claimed.id;
      } catch (err) {
        if (err instanceof PrismaClientKnownRequestError && err.code === 'P2002') return false;
        throw err;
      }
    }

    const releaseClaim = async () => {
      if (!claimParticipantId) return;
      if (revivedTerminal) {
        // Preserve the row (history / unique key) — mark EXPIRED so next tick can revive again.
        await this.prisma.signalCycleParticipant
          .update({
            where: { id: claimParticipantId },
            data: { status: SignalCycleStatus.EXPIRED },
          })
          .catch(() => {});
      } else {
        await this.prisma.signalCycleParticipant
          .delete({ where: { id: claimParticipantId } })
          .catch(() => {});
      }
    };

    const intentCap = intent.risk?.max_margin_usd;
    const effectiveCap =
      intentCap != null && Number.isFinite(intentCap) && intentCap > 0
        ? Math.min(marginCap, intentCap)
        : marginCap;
    let available = 0;
    try {
      available = await this.activeTrading.getDerivativesAvailableUsd(creds);
    } catch {
      await releaseClaim();
      return false;
    }
    const leverage = resolveSubscriberLeverage(intent);
    const exactQty = resolveExactShowcaseEntryQty({
      exactQtyBtc: showcaseQty,
      maxMarginUsd: effectiveCap,
      leverage,
      limitPrice: mark,
      minQtyBtc: MIN_QTY_BTC,
    });
    if (!exactQty.ok) {
      await releaseClaim();
      await this.recordActionMissEntry(
        agentId,
        instance.userId,
        cycleId,
        tradeId,
        exactQty.reason,
        {
          source_exact_qty_btc: showcaseQty,
          margin_cap_usd: effectiveCap,
          mark,
        },
      );
      return false;
    }
    const marginUsd = exactQty.requiredMarginUsd;
    if (available * 0.95 < marginUsd) {
      await releaseClaim();
      return false;
    }

    const qty = exactQty.qty;
    const clientOrderId = computeClientOrderId(cycleId, claimParticipantId!, tradeId);

    let marketOrderId: number;
    try {
      marketOrderId = await this.activeTrading.submitMarketEntry(creds, {
        direction: intent.direction,
        qty,
        leverage,
        clientOrderId,
      });
    } catch (err) {
      await releaseClaim();
      this.logger.warn(
        `[MIRROR-CATCHUP] market entry failed ${instance.userId} cycle=${cycleId}: ${err instanceof Error ? err.message : err}`,
      );
      return false;
    }

    const fillPrice = mark;
    const stopLossMarginPct = resolveEffectiveStopLossMarginPct(intent.risk.stop_loss_margin_pct, {
      mirrorMode: true,
    });
    const stopPrice = computeStopPrice(fillPrice, intent.direction, stopLossMarginPct, leverage);
    const stopOrderId = await this.activeTrading
      .submitStopOrder(creds, {
        positionDirection: intent.direction,
        qty,
        stopPrice,
        leverage,
      })
      .catch(() => null);

    await this.cycles.recordHireExecutionEvent(
      instance.userId,
      agentId,
      cycleId,
      'MIRROR_CATCHUP_ENTRY',
      {
        venue: 'bitfinex',
        source: 'hire',
        trade_id: tradeId,
        showcase_entry: showcaseEntry,
        mark_at_entry: mark,
        slip_usd: Math.round(slipUsd * 100) / 100,
        qty,
        source_exact_qty_btc: showcaseQty,
        venue_qty_btc: qty,
        direction: intent.direction,
        leverage,
        margin_usd: marginUsd,
        margin_cap_usd: effectiveCap,
        limitPrice: fillPrice,
        originalLimitPrice: showcaseEntry,
        fillPrice,
        bitfinexOrderId: marketOrderId,
        bitfinex_order_id: marketOrderId,
        clientOrderId,
        stopOrderId: stopOrderId ?? undefined,
        fill_price: fillPrice,
        action_match: true,
        revived_terminal: revivedTerminal,
      },
    );

    await this.cycles.recordHireExecutionEvent(instance.userId, agentId, cycleId, 'FILLED', {
      venue: 'bitfinex',
      fill_price: fillPrice,
      qty,
      source_exact_qty_btc: showcaseQty,
      venue_qty_btc: qty,
      direction: intent.direction,
      leverage,
      margin_usd: marginUsd,
      margin_cap_usd: effectiveCap,
      limitPrice: fillPrice,
      originalLimitPrice: showcaseEntry,
      fillPrice,
      bitfinexOrderId: marketOrderId,
      bitfinex_order_id: marketOrderId,
      stop_loss_placed: stopOrderId != null,
      stop_loss_margin_pct: stopLossMarginPct,
      stopOrderId: stopOrderId ?? undefined,
      source: 'hire',
      event: 'MIRROR_CATCHUP_ENTRY',
      clientOrderId,
    });

    if (stopOrderId != null) {
      await this.cycles.recordHireExecutionEvent(instance.userId, agentId, cycleId, 'STOP_LOSS_ARMED', {
        venue: 'bitfinex',
        stop_price: stopPrice,
        stopOrderId,
        qty,
        stop_loss_margin_pct: stopLossMarginPct,
        source: 'hire',
      });
    }

    await this.healStuckPendingFill(claimParticipantId!, cycleId, fillPrice);
    this.positionRuntime.set(claimParticipantId!, {
      peakMarginPct: 0,
      lastChaseAtMs: 0,
      filledRecorded: true,
    });

    this.logger.warn(
      `[MIRROR-CATCHUP] ${instance.userId} cycle=${cycleId} trade=${tradeId} ${intent.direction} qty=${qty.toFixed(5)} @ ${fillPrice.toFixed(2)} slip=$${slipUsd.toFixed(2)} vs showcase ${showcaseEntry.toFixed(2)}`,
    );
    return true;
  }

  /**
    * Part B (intent-mirror) — Place a hire's copy order directly from an
   * approved `cont-` or `tbhv1-` INTENT cycle. Entry is fail-closed until
   * canonical showcase state contains the exact matching resting limit.
   * The intent wakes the relay; the :7002 order book supplies the authoritative
   * price and lifecycle so a user account cannot get ahead of the showcase.
   *
   * Guards (ALL must pass; this path ADDS guards, never relaxes G1–G14):
   *   - N3: INTENT_MIRROR_KILL_SWITCH env (panic button — reverts to fill-only).
   *   - Only INTENT cycles (decision §8 #2) — NOT PENDING_ENTRY (duplicate risk).
   *   - N6: re-affirm isMirrorableLaneTradeId (explicit showcase allowlist).
   *   - G5: isPaperLaneTradeId belt-and-suspenders block.
   *   - F1: showcase-unreachable safe mode (delegated to placeEntry via the
   *     eligibility + venue guard).
   *   - G11: evaluateEntryEligibility (margin, slots, foreign orders, direction).
   *   - G12: subscriberMaxMarginUsd cap (platform ceiling always wins, §8 #3).
   *   - N4: INTENT_MIRROR_DRY_RUN — logs + audit row, no submitLimitOrder.
   *   - N5: SignalCycleEvent "INTENT_MIRROR_ENTER" / "INTENT_MIRROR_ENTER_DRY".
   *
   * Limit price source: the signed/canonical `SHOWCASE_EXACT_LIMIT` produced
   * only after the selected virtual chase bucket activates. There is no
   * subscriber-mark, percentage-offset, or market-price fallback.
   *
   * Returns the number of entries placed (0 or 1 per call; at most one intent
   * per tick per instance to preserve the atomic-claim contract).
   */
  private async maybeEnterFromIntent(
    agentId: string,
    instance: TradingAgentInstance,
    creds: ExchangeCredentials,
    cycles: Array<{
      id: string;
      tradeId: string;
      intentEnvelope: unknown;
      status: SignalCycleStatus;
      expiresAt: Date | null;
      createdAt: Date;
    }>,
    lotSummary: VirtualLotSummary,
    managedOrderIds: Set<number>,
    marginCap: number,
    maxConcurrent: number,
    venue: string,
    botState: BotApiState | null,
  ): Promise<number> {
    // N3 — panic button.
    if (intentMirrorKillSwitchActive()) return 0;

    // §8 #2 — only INTENT cycles. PENDING_ENTRY means a hire limit is already
    // resting; re-entering risks a duplicate order on top of it.
    const intentCycles = canonicalPendingIntentCycles(
      cycles
        .filter((c) => c.status === SignalCycleStatus.INTENT)
        .filter((c) => !c.expiresAt || c.expiresAt.getTime() > Date.now()),
      botState,
    );

    for (const cycle of intentCycles) {
      const tid = cycle.tradeId;
      // N6 / G4 — re-affirm the explicit showcase lane allowlist.
      if (!isMirrorableLaneTradeId(tid)) {
        this.logger.warn(
          `[INTENT-MIRROR] skip non-mirrorable lane trade=${tid} user=${instance.userId}`,
        );
        continue;
      }
      // G5 — belt-and-suspenders paper-lane block.
      if (isPaperLaneTradeId(tid)) {
        this.logger.warn(
          `[INTENT-MIRROR] skip paper-lane trade=${tid} user=${instance.userId}`,
        );
        continue;
      }

      // Already has a participant for this cycle? (fills, prior intent, etc.)
      const existing = await this.prisma.signalCycleParticipant.findUnique({
        where: { cycleId_userId: { cycleId: cycle.id, userId: instance.userId } },
      });
      if (existing) {
        // Stale INTENT claim (>120s, crashed replica) — reclaim, else skip.
        if (
          existing.status === SignalCycleStatus.INTENT &&
          existing.createdAt.getTime() < Date.now() - 120_000
        ) {
          await this.prisma.signalCycleParticipant
            .delete({ where: { id: existing.id } })
            .catch(() => {});
          this.logger.log(
            `[INTENT-MIRROR] reclaimed stale INTENT claim ${instance.userId} cycle=${cycle.id}`,
          );
        } else {
          continue;
        }
      }

      const intent = cycle.intentEnvelope as SignalIntentEnvelope & {
        intent_source?: 'paper' | 'live';
        entry?: { exact_limit_price?: number; exact_qty_btc?: number };
        margin_usdt?: number;
      };
      if (!intent?.direction || intent.action !== 'ENTER') continue;

      // G11 — margin / slots / foreign orders / direction gate.
      const eligibility = await this.evaluateEntryEligibility(
        creds,
        {
          open: lotSummary.open,
          pending: lotSummary.pending,
          direction: lotSummary.direction,
        },
        managedOrderIds,
        marginCap,
        maxConcurrent,
        intent.direction,
        instance,
      );
      if (!eligibility.canEnter) {
        this.logger.log(
          `[INTENT-MIRROR] eligibility blocked trade=${tid} user=${instance.userId}: ${eligibility.reason}`,
        );
        continue;
      }

      // Exact-copy contract: this path is never allowed to reconstruct a
      // percentage pullback from signal/mark. Both signed fast-path and polling
      // backstop must carry the canonical executable resting limit (deterministic
      // 0.1% offset policy, or the legacy structural policy for in-flight intents).
      const exactLimit = Number(intent.entry?.exact_limit_price ?? 0);
      if (
        intent.entry?.mode !== 'EXACT_LIMIT'
        || intent.entry?.reference !== 'SHOWCASE_EXACT_LIMIT'
        || typeof intent.entry?.exact_qty_btc !== 'number'
        || !isExecutableEntryPolicy(intent.context?.entry_limit_policy)
        || !Number.isFinite(exactLimit)
        || exactLimit <= 0
      ) {
        this.logger.warn(
          `[INTENT-MIRROR] rejected non-executable exact entry trade=${tid} user=${instance.userId}`,
        );
        continue;
      }
      const mark = await this.activeTrading.getMarkPrice().catch(() => 0);
      if (!mark || mark <= 0) continue;
      let limitPrice = exactLimit;
      const sanitized = sanitizeLimitPrice(mark, limitPrice, intent.direction);
      if (sanitized == null) {
        this.logger.warn(
          `[INTENT-MIRROR] rejected absurd limit ${limitPrice.toFixed(2)} trade=${tid} user=${instance.userId}`,
        );
        continue;
      }
      limitPrice = sanitized;

      // Exact showcase quantity is authoritative; the subscriber margin is a
      // hard safety ceiling and never a replacement sizing instruction.
      const leverage = resolveSubscriberLeverage(intent);
      const exactQty = resolveExactShowcaseEntryQty({
        exactQtyBtc: intent.entry.exact_qty_btc,
        maxMarginUsd: marginCap,
        leverage,
        limitPrice,
        minQtyBtc: MIN_QTY_BTC,
      });
      if (!exactQty.ok) {
        this.logger.warn(
          `[INTENT-MIRROR] exact quantity blocked trade=${tid} user=${instance.userId} reason=${exactQty.reason}`,
        );
        continue;
      }
      const qty = exactQty.qty;
      const exactMarginUsd = exactQty.requiredMarginUsd;

      // N4 — dry-run mode. Log the would-be order + audit row, no exchange call.
      if (intentMirrorDryRunActive(instance)) {
        const dryPayload = {
          venue,
          source: 'hire',
          trade_id: tid,
          direction: intent.direction,
          limit_price: Math.round(limitPrice * 100) / 100,
          qty,
          source_exact_qty_btc: intent.entry.exact_qty_btc,
          margin_usd: exactMarginUsd,
          margin_cap_usd: marginCap,
          leverage,
          intent_source: intent.intent_source ?? 'unknown',
          cycle_id: cycle.id,
          entry_mode: 'EXACT_LIMIT',
          entry_limit_policy:
            intent.context?.entry_limit_policy ?? SHOWCASE_DETERMINISTIC_ENTRY_POLICY_VERSION,
          reason: 'INTENT_MIRROR_DRY_RUN',
        };
        this.logger.log(
          `[INTENT-MIRROR][DRY-RUN] would place user=${instance.userId} cycle=${cycle.id} trade=${tid} ${intent.direction} qty=${qty.toFixed(5)} @ ${limitPrice.toFixed(2)} exactMargin=$${exactMarginUsd.toFixed(2)} cap=$${marginCap.toFixed(2)} lev=${leverage}x intent_source=${intent.intent_source ?? 'unknown'}`,
        );
        try {
          await this.prisma.signalCycleEvent.create({
            data: {
              cycleId: cycle.id,
              eventType: 'INTENT_MIRROR_ENTER_DRY',
              payload: dryPayload as unknown as import('@prisma/client').Prisma.InputJsonValue,
            },
          });
        } catch (err) {
          this.logger.warn(
            `[INTENT-MIRROR] DRY audit persist failed trade=${tid}: ${err instanceof Error ? err.message : err}`,
          );
        }
        // Do not place a real order. Continue scanning remaining INTENT cycles.
        return 0;
      }

      // Live path — re-use placeEntry so the C5 atomic-claim, F1 safe-mode,
      // price-sanity, ORDER_PLACED audit, and applyLimitChase wiring all run
      // identically. placeEntry consumes the same exact structural envelope.
      // We additionally emit the N5 audit tag here so the event stream
      // distinguishes mirror-from-intent from mirror-from-showcase-fill.
      const placed = await this.placeEntry(
        agentId,
        instance,
        cycle.id,
        cycle.intentEnvelope,
        creds,
        marginCap,
        tid,
        venue,
      ).catch((err) => {
        this.logger.warn(
          `[INTENT-MIRROR] placeEntry failed trade=${tid} user=${instance.userId}: ${err instanceof Error ? err.message : err}`,
        );
        return false;
      });
      if (placed) {
        // N5 — audit trail tag distinguishing this entry as intent-mirror.
        try {
          await this.prisma.signalCycleEvent.create({
            data: {
              cycleId: cycle.id,
              eventType: 'INTENT_MIRROR_ENTER',
              payload: {
                venue,
                source: 'hire',
                trade_id: tid,
                direction: intent.direction,
                limit_price: Math.round(limitPrice * 100) / 100,
                qty,
                source_exact_qty_btc: intent.entry.exact_qty_btc,
                margin_usd: exactMarginUsd,
                margin_cap_usd: marginCap,
                leverage,
                intent_source: intent.intent_source ?? 'unknown',
                entry_mode: 'EXACT_LIMIT',
                entry_limit_policy:
                  intent.context?.entry_limit_policy ?? SHOWCASE_DETERMINISTIC_ENTRY_POLICY_VERSION,
              } as unknown as import('@prisma/client').Prisma.InputJsonValue,
            },
          });
        } catch (err) {
          this.logger.warn(
            `[INTENT-MIRROR] ENTER audit persist failed trade=${tid}: ${err instanceof Error ? err.message : err}`,
          );
        }
        return 1;
      }
      continue;
    }
    return 0;
  }

  /**
   * F1/F3 — Mark the showcase as unreachable for this instance and return the
   * elapsed ms since the first null fetch in this dark streak. Clears on the
   * next successful fetch via {@link clearShowcaseUnreachable}. Idempotent.
   */
  private markShowcaseUnreachable(instanceId: string): number {
    const now = Date.now();
    const since = this.showcaseUnreachableSince.get(instanceId) ?? now;
    if (!this.showcaseUnreachableSince.has(instanceId)) {
      this.showcaseUnreachableSince.set(instanceId, since);
    }
    // F8 — a failed fetch resets the recovery counter; the next 3 consecutive
    // successful fetches must restart from scratch before safe mode clears.
    this.showcaseRecoveryHits.delete(instanceId);
    return now - since;
  }

  /**
   * F1/F3 — Clear the unreachable streak on a successful fetch.
   *
   * F8 (2026-07-08 hotfix) — debounced: requires
   * {@link SHOWCASE_RECOVERY_HITS_REQUIRED} consecutive successful fetches
   * before actually clearing the entry block. A single lucky ping during a
   * tunnel flap no longer re-arms live copy. The unreachable-since timestamp
   * is preserved until the threshold is met so F1/F2 keep gating entries and
   * orphans throughout the flap.
   */
  private clearShowcaseUnreachable(instanceId: string): boolean {
    if (!this.showcaseUnreachableSince.has(instanceId)) {
      // Already healthy — keep the counter clean.
      this.showcaseRecoveryHits.delete(instanceId);
      return false;
    }
    const hits = (this.showcaseRecoveryHits.get(instanceId) ?? 0) + 1;
    if (hits >= this.SHOWCASE_RECOVERY_HITS_REQUIRED) {
      this.showcaseUnreachableSince.delete(instanceId);
      this.showcaseSafeModeNoticeAt.delete(instanceId);
      this.showcaseRecoveryHits.delete(instanceId);
      return true;
    } else {
      this.showcaseRecoveryHits.set(instanceId, hits);
      return false;
    }
  }

  /**
   * F1 — Should this instance refuse new entries right now?
   * True when the showcase has been unreachable for ≥ ENTRY_BLOCK_MS.
   * Caller must first invoke markShowcaseUnreachable() with the latest fetch
   * outcome so the streak is tracked correctly.
   */
  private entryBlockedByShowcaseOutage(instanceId: string): { blocked: boolean; elapsedMs: number } {
    if (!showcaseUnreachableSafeModeEnabled()) return { blocked: false, elapsedMs: 0 };
    const since = this.showcaseUnreachableSince.get(instanceId);
    if (!since) return { blocked: false, elapsedMs: 0 };
    const elapsed = Date.now() - since;
    return { blocked: elapsed >= SHOWCASE_UNREACHABLE_ENTRY_BLOCK_MS, elapsedMs: elapsed };
  }

  /**
   * F2 — Should an OPEN copy lot be force-closed right now because the
   * showcase has been unreachable for ≥ ORPHAN_KILL_MS? This is the orphan
   * counterpart to {@link enforceShowcaseFlatOpenFailsafe} — that one needs a
   * successfully fetched flat book to fire; this one fires precisely when the
   * fetch keeps failing, which is the case the existing failsafe cannot cover.
   */
  private openLotOrphanedByShowcaseOutage(instanceId: string): {
    orphan: boolean;
    elapsedMs: number;
  } {
    if (!showcaseUnreachableSafeModeEnabled()) return { orphan: false, elapsedMs: 0 };
    const since = this.showcaseUnreachableSince.get(instanceId);
    if (!since) return { orphan: false, elapsedMs: 0 };
    const elapsed = Date.now() - since;
    return { orphan: elapsed >= SHOWCASE_UNREACHABLE_ORPHAN_KILL_MS, elapsedMs: elapsed };
  }

  /**
   * F2 — Market-close an OPEN copy lot because the showcase has been
   * unreachable past the orphan-kill threshold. We do NOT know the
   * showcase_exit_price here (no fetch succeeded) — that's fine; the
   * alternative is letting the orphan marinade while real money is on the
   * line. Reuses {@link executeShowcaseMirrorClose} for idempotency and
   * accounting. Always surfaces an operator alert + lastError.
   */
  private async enforceShowcaseOutageOrphanKill(
    agentId: string,
    userId: string,
    instanceId: string,
    cycle: {
      id: string;
      tradeId?: string | null;
      showcasePnlUsd?: { toNumber?: () => number } | null;
      intentEnvelope?: unknown;
    },
    participant: { id: string; fillPrice: { toNumber?: () => number } | null },
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    elapsedMs: number,
  ): Promise<boolean> {
    const alertMsg = `SHOWCASE OUTAGE ORPHAN KILL: copy OPEN lot for trade=${
      cycle.tradeId ?? '?'
    } (participant=${participant.id.slice(0, 8)}…) market-closing — showcase unreachable for ${Math.round(
      elapsedMs / 1000,
    )}s (≥ SHOWCASE_UNREACHABLE_ORPHAN_KILL_MS=${SHOWCASE_UNREACHABLE_ORPHAN_KILL_MS}ms). F2 safe-mode.`;
    this.logger.error(alertMsg);
    await this.setInstanceLastError(userId, agentId, alertMsg);
    await this.cycles
      .recordHireExecutionEvent(userId, agentId, cycle.id, 'SHOWCASE_OUTAGE_ORPHAN_KILL', {
        venue: 'bitfinex',
        source: 'hire',
        participant_id: participant.id,
        trade_id: cycle.tradeId ?? null,
        elapsed_ms: elapsedMs,
        threshold_ms: SHOWCASE_UNREACHABLE_ORPHAN_KILL_MS,
        instance_id: instanceId,
      })
      .catch(() => {});

    return this.executeShowcaseMirrorClose(agentId, userId, cycle, participant, meta, creds, {
      showcaseExitReason: 'SHOWCASE_UNREACHABLE_OPEN_LOT',
      mirrorRelinked: (cycle.tradeId ?? '').startsWith('adopt:'),
      trigger: 'SHOWCASE_UNREACHABLE_OPEN_LOT',
      forceMirrorExit: true,
    });
  }

  /**
   * Cross-ID / ghost-fill tracker. Returns true when the participant's
   * showcase trade_id is absent from open positions for
   * {@link SHOWCASE_POSITION_ABSENT_CONSECUTIVE_MISSES} consecutive fresh bot
   * states and {@link SHOWCASE_POSITION_ABSENT_GRACE_MS} has elapsed. Unlike
   * {@link trackShowcaseVanished}, this does NOT require the trade to be wiped
   * from trades_map — a PENDING/VIRTUAL_CHASE entry that never became an open
   * showcase position still counts as absent.
   * Fail-closed: caller must pass a successfully-fetched bot state.
   */
  private trackShowcasePositionAbsent(
    participantId: string,
    tradeId: string,
    bot: BotApiState,
  ): boolean {
    if (!tradeId || tradeId.startsWith('adopt:')) {
      this.showcasePositionAbsentMisses.delete(participantId);
      this.showcasePositionAbsentSince.delete(participantId);
      return false;
    }
    const inPositions = (bot.positions ?? []).some(
      (p) => p.trade_id && tradeIdsMatch(p.trade_id, tradeId),
    );
    if (inPositions) {
      this.showcasePositionAbsentMisses.delete(participantId);
      this.showcasePositionAbsentSince.delete(participantId);
      return false;
    }
    const nowMs = Date.now();
    const firstAbsentAtMs =
      this.showcasePositionAbsentSince.get(participantId) ?? nowMs;
    this.showcasePositionAbsentSince.set(participantId, firstAbsentAtMs);
    const misses = (this.showcasePositionAbsentMisses.get(participantId) ?? 0) + 1;
    this.showcasePositionAbsentMisses.set(participantId, misses);
    if (
      !showcasePositionAbsenceActionable({
        misses,
        firstAbsentAtMs,
        nowMs,
      })
    ) {
      this.logger.warn(
        `Showcase position absent ${tradeId} (participant=${participantId}) — ` +
          `miss ${misses}/${SHOWCASE_POSITION_ABSENT_CONSECUTIVE_MISSES}, ` +
          `elapsed=${nowMs - firstAbsentAtMs}ms/${SHOWCASE_POSITION_ABSENT_GRACE_MS}ms`,
      );
      return false;
    }
    return true;
  }

  /**
   * Fix B — showcase-vanished tracker. Returns true when the participant's
   * showcase trade_id has been absent from the canonical showcase state's
   * open positions AND closed trades (trades / trades_map) AND pending orders
   * for {@link SHOWCASE_VANISHED_CONSECUTIVE_MISSES} consecutive FRESH,
   * successfully-fetched canonical states. Fail-closed:
   *  - unreachable/failed fetch → no verdict, counter unchanged (never counts);
   *  - trade present anywhere → counter reset (normal mirror machinery owns it);
   *  - `adopt:*` trades have no showcase counterpart by design → never counted.
   * Counter is in-memory (Map keyed by participantId) and resets on restart.
   */
  private async trackShowcaseVanished(
    participantId: string,
    tradeId: string | null,
  ): Promise<boolean> {
    if (!tradeId || tradeId.startsWith('adopt:')) {
      this.showcaseVanishedMisses.delete(participantId);
      return false;
    }
    const bot = await this.fetchExecutionBotState();
    if (!bot) return false; // canonical unreachable — do not count failed fetches

    const inPositions = (bot.positions ?? []).some(
      (p) => p.trade_id != null && tradeIdsMatch(p.trade_id, tradeId),
    );
    const inTrades = (bot.trades ?? []).some(
      (t) => t.trade_id != null && tradeIdsMatch(t.trade_id, tradeId),
    );
    const inTradesMap =
      bot.trades_map != null &&
      Object.keys(bot.trades_map).some((key) => tradeIdsMatch(key, tradeId));
    // Defensive: a trade still pending/known as a signal is NOT vanished.
    const inOrders = (bot.orders ?? []).some(
      (o) => o.trade_id != null && tradeIdsMatch(o.trade_id, tradeId),
    );
    const inSignals = (bot.signal_info?.signals ?? []).some(
      (s) => tradeIdsMatch(String(s.trade_id ?? ''), tradeId),
    );

    if (inPositions || inTrades || inTradesMap || inOrders || inSignals) {
      this.showcaseVanishedMisses.delete(participantId);
      return false;
    }

    const misses = (this.showcaseVanishedMisses.get(participantId) ?? 0) + 1;
    this.showcaseVanishedMisses.set(participantId, misses);
    if (misses < SHOWCASE_VANISHED_CONSECUTIVE_MISSES) {
      this.logger.warn(
        `Showcase trade missing ${tradeId} (participant=${participantId}) — miss ${misses}/${SHOWCASE_VANISHED_CONSECUTIVE_MISSES}`,
      );
      return false;
    }
    return true;
  }

  private async monitorOpenPosition(
    agentId: string,
    userId: string,
    cycle: { id: string; intentEnvelope: unknown; tradeId?: string | null },
    participant: { id: string; fillPrice: { toNumber?: () => number } | null },
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    simActive = false,
  ) {
    if (!meta.qty || !meta.direction) return;
    if (await this.hasParticipantExited(participant.id)) return;

    const supersededStops = [
      { id: meta.supersededPartialStopOrderId, kind: 'partial' },
      { id: meta.supersededStopOrderId, kind: 'full' },
    ].filter((stop): stop is { id: number; kind: 'partial' | 'full' } => stop.id != null);
    for (const stop of supersededStops) {
      const gone = await this.cancelManagedOrderGone(
        creds,
        stop.id,
        `OPEN cleanup superseded ${stop.kind} stop ${stop.id}`,
      );
      if (!gone.gone) {
        const message = `SUPERSEDED_STOP_STILL_LIVE cycle=${cycle.id} kind=${stop.kind}`;
        await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
        return;
      }
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'UPDATE_STOPS', {
        venue: 'bitfinex',
        source: 'hire',
        event: stop.kind === 'partial' ? 'SUPERSEDED_PARTIAL_STOP_CLEARED' : 'SUPERSEDED_STOP_CLEARED',
        ...(stop.kind === 'partial'
          ? { supersededPartialStopOrderId: null }
          : { supersededStopOrderId: null }),
      });
    }

    const intent = cycle.intentEnvelope as SignalIntentEnvelope;
    const position = await this.activeTrading.getOpenPositionDetail(creds);
    const expectedLong = meta.direction === 'LONG';
    const hasExpected =
      position &&
      ((expectedLong && position.amount > 0) || (!expectedLong && position.amount < 0));
    if (!hasExpected) return;

    const fillPrice =
      participant.fillPrice != null
        ? Number(participant.fillPrice)
        : meta.limitPrice && meta.limitPrice > 0
          ? meta.limitPrice
          : 0;
    if (!fillPrice || fillPrice <= 0) return;

    const leverage = resolveSubscriberLeverage(intent);
    const stopLossMarginPct = resolveEffectiveStopLossMarginPct(
      intent.risk.stop_loss_margin_pct,
      { mirrorMode: isShowcaseMirrorOnlyMode(), simActive },
    );
    const mark = await this.activeTrading.getMarkPrice();
    const unrealMarginPct = computeUnrealizedMarginPct(fillPrice, mark, meta.direction, leverage);

    const runtime = this.hydrateRuntime(participant.id, meta);
    const priorPeak = Math.max(runtime.peakMarginPct, meta.peakMarginPct ?? 0);
    runtime.peakMarginPct = Math.max(priorPeak, unrealMarginPct);
    if (meta.profitLockFloor != null) {
      runtime.lastProfitLockFloor = Math.max(runtime.lastProfitLockFloor ?? 0, meta.profitLockFloor);
    }
    this.positionRuntime.set(participant.id, runtime);

    if (runtime.peakMarginPct > priorPeak + 0.25) {
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'UPDATE_STOPS', {
        venue: 'bitfinex',
        event: 'PEAK_MARGIN_UPDATE',
        peak_margin_pct: Math.round(runtime.peakMarginPct * 100) / 100,
        unreal_margin_pct: Math.round(unrealMarginPct * 100) / 100,
        mark,
        source: 'hire',
      });
    }

    // Fix B — showcase-vanished close rule. When the showcase state no longer
    // contains this trade AT ALL (not an open position, not a closed trade —
    // e.g. a Fresh Collection wipe) for N consecutive fresh canonical states,
    // nothing else ever closes the copy position (the mirror-exit path needs
    // the cycle to transition CLOSED, which never happens for a wiped trade).
    // Market-close it through the same machinery as the Scenario C exits.
    // CRITICAL: use resolveShowcaseMirrorTradeId — cycle.tradeId may be
    // `relink:…` / `adopt:…` after Cure 1. Exact === against the raw string
    // false-vanished cont-e549 at noon 2026-08-08 (COPY_POSITION_NO_SHOWCASE
    // → SHOWCASE_VANISHED $0 while Fly still held the short until +$1.18).
    const showcaseTradeIdForVanish = this.resolveShowcaseMirrorTradeId(cycle, meta);
    if (await this.trackShowcaseVanished(participant.id, showcaseTradeIdForVanish)) {
      this.logger.warn(
        `Showcase trade VANISHED ${userId} cycle=${cycle.id} trade=${showcaseTradeIdForVanish} (cycle.tradeId=${cycle.tradeId}) — absent from canonical positions/trades for ${SHOWCASE_VANISHED_CONSECUTIVE_MISSES} consecutive fresh states; market-closing copy lot`,
      );
      const closed = await this.closeVirtualLot(agentId, userId, cycle.id, participant.id, meta, creds, {
        reason: 'SHOWCASE_VANISHED',
        mark,
        fillPrice,
        leverage,
        peakMarginPct: runtime.peakMarginPct,
        unrealMarginPct,
        stopLossMarginPct,
      });
      if (closed) this.positionRuntime.delete(participant.id);
      return;
    }

    const { reason: exitReason, lockFloor } = evaluateSubscriberLotExit({
      unrealMarginPct,
      peakMarginPct: runtime.peakMarginPct,
      stopLossMarginPct,
      // Relay sim: per-lot Scenario C exits (profit lock / thesis) for realistic soak tests.
      showcaseMirrorOnly: simActive ? false : isShowcaseMirrorOnlyMode(),
    });

    if (exitReason) {
      const closed = await this.closeVirtualLot(agentId, userId, cycle.id, participant.id, meta, creds, {
        reason: exitReason,
        mark,
        fillPrice,
        leverage,
        lockFloor,
        peakMarginPct: runtime.peakMarginPct,
        unrealMarginPct,
        stopLossMarginPct,
      });
      if (closed) this.positionRuntime.delete(participant.id);
      return;
    }

    // Real-side protective safety net — independent of showcase mirror exits.
    // Fires on adverse moves the showcase mirror path missed (paper/real desync,
    // missed maker fill that left a real position, or a fill on a different
    // signal than the one tracked by this cycle). Uses the REAL fill price +
    // REAL mark, runs the SAME Scenario C math the showcase bot uses. This is
    // the last line of defense for real money; it never replaces the showcase
    // mirror exit for profitable exits, only catches adverse ones.
    if (
      realSideSafetyNetEnabled() &&
      shouldRunLocalRealSideSafetyNet({
        simActive,
        showcaseMirrorOnly: isShowcaseMirrorOnlyMode(),
        mirrorExitConvergence: mirrorExitConvergenceEnabled(),
      })
    ) {
      const intentStopLoss =
        intent?.risk?.stop_loss_margin_pct != null
          ? Number(intent.risk.stop_loss_margin_pct)
          : undefined;
      const safetyNet = evaluateRealSideSafetyNetExit({
        unrealMarginPct,
        peakMarginPct: runtime.peakMarginPct,
        hardStopMarginPct: realSideSafetyNetHardStopMarginPct(intentStopLoss),
      });
      if (safetyNet.reason) {
        const closed = await this.closeVirtualLot(
          agentId,
          userId,
          cycle.id,
          participant.id,
          meta,
          creds,
          {
            reason: safetyNet.reason,
            mark,
            fillPrice,
            leverage,
            lockFloor: safetyNet.lockFloor,
            peakMarginPct: runtime.peakMarginPct,
            unrealMarginPct,
            stopLossMarginPct,
          },
        );
        if (closed) {
          this.logger.warn(
            `Real-side safety net ${userId} cycle=${cycle.id} closed lot: reason=${safetyNet.reason} unreal=${unrealMarginPct.toFixed(2)}% peak=${runtime.peakMarginPct.toFixed(2)}% mark=${mark.toFixed(2)} fill=${fillPrice.toFixed(2)}`,
          );
          await this.cycles
            .recordHireExecutionEvent(userId, agentId, cycle.id, 'UPDATE_STOPS', {
              venue: 'bitfinex',
              source: 'hire',
              event: 'REAL_SIDE_SAFETY_NET',
              reason: safetyNet.reason,
              unreal_margin_pct: Math.round(unrealMarginPct * 100) / 100,
              peak_margin_pct: Math.round(runtime.peakMarginPct * 100) / 100,
              lock_floor_margin_pct: safetyNet.lockFloor,
              mark,
              fill_price: fillPrice,
              showcase_trade_id: cycle.tradeId ?? null,
            })
            .catch(() => {
              /* audit is best-effort; the close itself already recorded EXIT */
            });
          this.positionRuntime.delete(participant.id);
          return;
        }
      }
    }

    if (!meta.stopOrderId && fillPrice > 0) {
      const stopPrice = computeStopPrice(fillPrice, meta.direction, stopLossMarginPct, leverage);
      const stopOrderId = await this.activeTrading.submitStopOrder(creds, {
        positionDirection: meta.direction,
      qty: meta.qty,
        stopPrice,
        leverage,
      }).catch(() => null);
      if (stopOrderId != null) {
        await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'STOP_LOSS_ARMED', {
          venue: 'bitfinex',
          stop_price: stopPrice,
      stopOrderId,
          qty: meta.qty,
      source: 'hire',
    });
        // Option A — seed tracked stop state so the never-loosen / SKIP_SAME
        // checks have a baseline on the very first trail tick. Initial rung
        // is the disaster stop (no Scenario C rung yet).
        runtime.currentStopOrderId = stopOrderId;
        runtime.currentStopPrice = stopPrice;
        runtime.currentRungIdx = undefined;
        runtime.consecutiveStopFailures = 0;
        this.stopManagerCircuitOpen.delete(participant.id);
        this.positionRuntime.set(participant.id, runtime);
        this.logger.log(`Hire stop ${userId} lot cycle=${cycle.id} @ ${stopPrice.toFixed(2)} qty=${meta.qty}`);
      }
    } else if (meta.stopOrderId && fillPrice > 0) {
      const armed = await this.prisma.signalCycleEvent.count({
        where: { participantId: participant.id, eventType: 'STOP_LOSS_ARMED' },
      });
      if (armed === 0) {
        const stopPrice = computeStopPrice(fillPrice, meta.direction, stopLossMarginPct, leverage);
        await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'STOP_LOSS_ARMED', {
          venue: 'bitfinex',
          stop_price: stopPrice,
          stopOrderId: meta.stopOrderId,
          qty: meta.qty,
          source: 'hire',
          event: 'STOP_LOSS_EVENT_HEAL',
        });
        // Option A — seed tracked stop state from the healed entry too.
        if (runtime.currentStopOrderId == null) {
          runtime.currentStopOrderId = meta.stopOrderId;
          runtime.currentStopPrice = stopPrice;
          runtime.currentRungIdx = undefined;
          runtime.consecutiveStopFailures = 0;
          this.positionRuntime.set(participant.id, runtime);
        }
        this.logger.warn(
          `Healed missing STOP_LOSS_ARMED ${userId} cycle=${cycle.id} stop=${stopPrice.toFixed(2)}`,
        );
      } else if (runtime.currentStopOrderId == null && meta.stopOrderId) {
        // Stop already armed on a previous tick but we never seeded tracked
        // state (e.g. option A flag was off, now on). Adopt without a write.
        runtime.currentStopOrderId = meta.stopOrderId;
        runtime.consecutiveStopFailures = 0;
        this.positionRuntime.set(participant.id, runtime);
      }
    }

    const lockFloorTrail = getProfitLockFloor(runtime.peakMarginPct);
    // Option A — when EXCHANGE_DYNAMIC_STOPS_ENABLED=true, force the trail to
    // fire even in showcase-mirror-only + exit-convergence mode, so the
    // protective stop advances through the Scenario C ladder rungs on the
    // exchange. Showcase bot remains the EXIT decision maker; this only
    // manages the protective stop between entry and that EXIT.
    const skipProfitLockTrail =
      !simActive &&
      isShowcaseMirrorOnlyMode() &&
      mirrorExitConvergenceEnabled() &&
      !exchangeDynamicStopsEnabled();
    if (lockFloorTrail != null && fillPrice > 0 && meta.stopOrderId && !skipProfitLockTrail) {
      const trailStop = computeProfitLockStopPrice(fillPrice, meta.direction, lockFloorTrail, leverage);
      const priorFloor = runtime.lastProfitLockFloor ?? 0;
      // Option A — rung index for audit + SKIP_SAME. solveScenarioCRung is the
      // pure solver over SCENARIO_C_LADDER (0-based, null when below first rung).
      const newRungIdx = solveScenarioCRung(runtime.peakMarginPct, SCENARIO_C_LADDER);
      // Tracked previous stop (authoritative — meta.stopOrderId can lag a tick).
      const prevStopOrderId = runtime.currentStopOrderId ?? meta.stopOrderId;
      const prevStopPrice = runtime.currentStopPrice;
      const prevRungIdx = runtime.currentRungIdx ?? null;
      const failures = runtime.consecutiveStopFailures ?? 0;

      // Circuit breaker — stop attempting replacements for this participant
      // after STOP_MANAGER_CIRCUIT_THRESHOLD consecutive failures. Reset to 0
      // on any successful replace or on the next FILLED event.
      if (exchangeDynamicStopsEnabled() && failures >= STOP_MANAGER_CIRCUIT_THRESHOLD) {
        this.appendExchangeStopAudit({
          cycleId: cycle.id,
          userId,
          participantId: participant.id,
          side: meta.direction,
          entry: fillPrice,
          peakMarginPct: runtime.peakMarginPct,
          prevRung: prevRungIdx,
          newRung: newRungIdx,
          oldStop: prevStopPrice ?? null,
          newStop: trailStop,
          bitfinexOldOrderId: prevStopOrderId ?? null,
          bitfinexNewOrderId: null,
          action: 'SKIP_CIRCUIT_OPEN',
        });
      } else if (lockFloorTrail > priorFloor + 0.5) {
        // Option A — SKIP_SAME: same rung already live, no exchange write.
        // (Only meaningful when dynamic stops are on; legacy path didn't
        // track rung index so we only short-circuit there.)
        const sameRung =
          exchangeDynamicStopsEnabled() &&
          prevRungIdx != null &&
          newRungIdx != null &&
          prevRungIdx === newRungIdx;
        // Option A — never loosen. For SHORT the stop price must DECREASE to
        // tighten (trail down). For LONG it must INCREASE. If the new stop
        // would be wider than the live one, skip with SKIP_LOOSEN. This is
        // the single most important safety property of the dynamic stops.
        const wouldLoosen =
          exchangeDynamicStopsEnabled() &&
          prevStopPrice != null &&
          prevStopPrice > 0 &&
          ((meta.direction === 'SHORT' && trailStop > prevStopPrice + 0.01) ||
            (meta.direction === 'LONG' && trailStop < prevStopPrice - 0.01));

        if (sameRung) {
          this.appendExchangeStopAudit({
            cycleId: cycle.id,
            userId,
            participantId: participant.id,
            side: meta.direction,
            entry: fillPrice,
            peakMarginPct: runtime.peakMarginPct,
            prevRung: prevRungIdx,
            newRung: newRungIdx,
            oldStop: prevStopPrice ?? null,
            newStop: trailStop,
            bitfinexOldOrderId: prevStopOrderId ?? null,
            bitfinexNewOrderId: prevStopOrderId ?? null,
            action: 'SKIP_SAME',
          });
        } else if (wouldLoosen) {
          this.logger.warn(
            `SKIP_LOOSEN ${userId} cycle=${cycle.id} side=${meta.direction} new=${trailStop.toFixed(2)} old=${prevStopPrice!.toFixed(2)} — refusing to widen protective stop`,
          );
          this.appendExchangeStopAudit({
            cycleId: cycle.id,
            userId,
            participantId: participant.id,
            side: meta.direction,
            entry: fillPrice,
            peakMarginPct: runtime.peakMarginPct,
            prevRung: prevRungIdx,
            newRung: newRungIdx,
            oldStop: prevStopPrice ?? null,
            newStop: trailStop,
            bitfinexOldOrderId: prevStopOrderId ?? null,
            bitfinexNewOrderId: null,
            action: 'SKIP_LOOSEN',
          });
        } else {
          const isInitial = prevStopOrderId == null;
          try {
            // Cancel-then-replace (skip the cancel on INITIAL — nothing to remove).
            if (!isInitial && prevStopOrderId != null) {
              await this.activeTrading.cancelOrder(creds, prevStopOrderId);
            }
            const newStopId = await this.activeTrading.submitStopOrder(creds, {
              positionDirection: meta.direction,
              qty: meta.qty,
              stopPrice: trailStop,
              leverage,
            });
            // Option A — update tracked state (per-account via participantId key).
            runtime.lastProfitLockFloor = lockFloorTrail;
            runtime.currentStopOrderId = newStopId;
            runtime.currentStopPrice = trailStop;
            runtime.currentRungIdx = newRungIdx ?? undefined;
            runtime.consecutiveStopFailures = 0;
            this.stopManagerCircuitOpen.delete(participant.id);
            this.positionRuntime.set(participant.id, runtime);
            await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'UPDATE_STOPS', {
              venue: 'bitfinex',
              event: 'PROFIT_LOCK_TRAIL',
              lock_floor_margin_pct: lockFloorTrail,
              profitLockFloor: lockFloorTrail,
              peak_margin_pct: runtime.peakMarginPct,
              stop_price: trailStop,
              stopOrderId: newStopId,
              qty: meta.qty,
              source: 'hire',
            });
            this.appendExchangeStopAudit({
              cycleId: cycle.id,
              userId,
              participantId: participant.id,
              side: meta.direction,
              entry: fillPrice,
              peakMarginPct: runtime.peakMarginPct,
              prevRung: prevRungIdx,
              newRung: newRungIdx,
              oldStop: prevStopPrice ?? null,
              newStop: trailStop,
              bitfinexOldOrderId: prevStopOrderId ?? null,
              bitfinexNewOrderId: newStopId,
              action: isInitial ? 'INITIAL' : 'REPLACE',
            });
            this.logger.log(
              `Hire trail stop lot ${userId} cycle=${cycle.id} floor=${lockFloorTrail}% stop=${trailStop.toFixed(2)} qty=${meta.qty} action=${isInitial ? 'INITIAL' : 'REPLACE'}`,
            );
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            // Option A — circuit breaker increment. When this participant has
            // now failed STOP_MANAGER_CIRCUIT_THRESHOLD times in a row, open
            // the circuit (surface on dashboard) and stop retrying until the
            // next FILLED resets the counter.
            const newFailures = failures + 1;
            runtime.consecutiveStopFailures = newFailures;
            this.positionRuntime.set(participant.id, runtime);
            if (newFailures >= STOP_MANAGER_CIRCUIT_THRESHOLD) {
              this.stopManagerCircuitOpen.set(participant.id, msg);
              this.appendExchangeStopAudit({
                cycleId: cycle.id,
                userId,
                participantId: participant.id,
                side: meta.direction,
                entry: fillPrice,
                peakMarginPct: runtime.peakMarginPct,
                prevRung: prevRungIdx,
                newRung: newRungIdx,
                oldStop: prevStopPrice ?? null,
                newStop: trailStop,
                bitfinexOldOrderId: prevStopOrderId ?? null,
                bitfinexNewOrderId: null,
                action: 'CIRCUIT_OPEN',
                error: msg,
              });
              this.logger.error(
                `STOP_MANAGER_CIRCUIT_OPEN ${userId} cycle=${cycle.id} participant=${participant.id} — ${newFailures} consecutive failures, lastError=${msg}`,
              );
            }
            this.logger.warn(`Trail stop update ${userId}: ${msg}`);
          }
        }
      }
    }
  }

  private async closeVirtualLot(
    agentId: string,
    userId: string,
    cycleId: string,
    participantId: string,
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
    opts: {
      /** Scenario C exits plus Fix B's showcase-vanished close (same machinery). */
      reason: NonNullable<VirtualLotExitReason> | 'SHOWCASE_VANISHED';
      mark: number;
      fillPrice: number;
      leverage: number;
      peakMarginPct: number;
      unrealMarginPct: number;
      lockFloor?: number;
      stopLossMarginPct: number;
    },
  ): Promise<boolean> {
    if (!meta.qty || !meta.direction || !opts.reason) return false;
    if (this.exitingLots.has(participantId)) return false;
    if (await this.hasParticipantExited(participantId)) return true;

    this.exitingLots.add(participantId);
    try {
    let closeQty = 0;
    const stopIdsToCancel = new Set<number>();
    if (meta.stopOrderId) stopIdsToCancel.add(meta.stopOrderId);

    try {
      // Option A — cancel BOTH the persisted meta.stopOrderId AND any tracked
      // currentStopOrderId before market-closing. With dynamic stops on, the
      // tracked id is the authoritative live stop (meta.stopOrderId can lag by
      // a tick). Showcase EXIT is authoritative — never let a stale protective
      // stop fire mid-close and realize the wrong rung.
      const runtimeTrack = this.positionRuntime.get(participantId);
      const trackedStopId = runtimeTrack?.currentStopOrderId;
      if (trackedStopId) stopIdsToCancel.add(trackedStopId);
      const closeTarget = await this.closeParticipantPositionToLedgerTarget(
        agentId,
        userId,
        participantId,
        meta,
        creds,
        opts.leverage,
        stopIdsToCancel,
      );
      closeQty = closeTarget.closeQty;
    } catch (err) {
      this.logger.warn(
        `Lot close ${userId} cycle=${cycleId} ${opts.reason}: ${err instanceof Error ? err.message : err}`,
      );
      await this.pauseUserRelayForPositionMismatch(
        userId,
        agentId,
        `EXIT_RECONCILIATION_FAILED cycle=${cycleId}: ${
          err instanceof Error ? err.message : String(err)
        }`,
      ).catch(() => {});
      const cycleRecord = await this.prisma.signalCycle.findUnique({
        where: { id: cycleId },
        select: { intentEnvelope: true },
      }).catch(() => null);
      if (cycleRecord) {
        await this.ensureProtectiveStopForVerifiedExitResidual(
          agentId,
          userId,
          cycleId,
          participantId,
          meta,
          creds,
          cycleRecord.intentEnvelope as SignalIntentEnvelope,
          opts.fillPrice,
          stopIdsToCancel,
        ).catch(() => {});
      }
      return false;
    }

    const direction = meta.direction;
    // Already-flat path: the exchange position is gone (protective stop fired or
    // showcase-mirror exit closed it first), so closeQty=0 and the mark-vs-fill formula
    // would record $0 — even though Bitfinex realized a real result. Attribute the real
    // P&L from the Bitfinex margin wallet ledger when only this lot was open, else
    // reconstruct from this lot's own fill price vs the exit mark using the FULL lot qty
    // (what the exchange would have realized on this lot's share of the merged position).
    let pnlUsd: number;
    let pnlSource: 'open_position' | 'exchange_realised' | 'reconstructed';
    if (btcToSats(closeQty) === 0) {
      const flat = await this.resolveAlreadyFlatPnl(
        agentId,
        userId,
        creds,
        participantId,
        meta,
        opts.fillPrice,
        opts.mark,
      );
      pnlUsd = flat.pnlUsd;
      pnlSource = flat.pnlSource;
    } else {
      pnlUsd =
      direction === 'LONG'
        ? (opts.mark - opts.fillPrice) * closeQty
        : (opts.fillPrice - opts.mark) * closeQty;
      pnlSource = 'open_position';
    }
    const pnlMarginPct =
      opts.reason === 'PROFIT_LOCK' && opts.lockFloor != null
        ? opts.lockFloor
        : opts.unrealMarginPct;

    const exitReasonMap: Record<NonNullable<VirtualLotExitReason> | 'SHOWCASE_VANISHED', string> = {
      PROFIT_LOCK: 'PROFIT_LOCK',
      THESIS_FAST_CUT: 'THESIS_FAST_CUT',
      HARD_STOP: 'HARD_STOP',
      SHOWCASE_VANISHED: 'SHOWCASE_VANISHED',
    };

    await this.cycles.recordHireExecutionEvent(userId, agentId, cycleId, 'EXIT', {
      venue: 'bitfinex',
      exit_price: opts.mark,
      exit_reason: exitReasonMap[opts.reason],
      peak_margin_pct: opts.peakMarginPct,
      lock_floor_margin_pct: opts.lockFloor,
      unreal_margin_pct: opts.unrealMarginPct,
      pnl_usd: Math.round(pnlUsd * 100) / 100,
      pnl_margin_pct: Math.round(pnlMarginPct * 100) / 100,
      qty_closed: closeQty,
      pnl_source: pnlSource,
      source: 'hire',
    });

    this.logger.log(
      `Hire lot exit ${userId} cycle=${cycleId} ${opts.reason} peak=${opts.peakMarginPct.toFixed(2)}% unreal=${opts.unrealMarginPct.toFixed(2)}% qty=${closeQty} exit=${opts.mark.toFixed(2)} pnl_source=${pnlSource}`,
    );
    return true;
    } finally {
      this.exitingLots.delete(participantId);
      this.showcaseVanishedMisses.delete(participantId);
      this.showcasePositionAbsentMisses.delete(participantId);
      this.showcasePositionAbsentSince.delete(participantId);
      this.showcaseFlatOpenSince.delete(participantId);
      // Option A — clear dynamic-stops circuit state on full exit.
      this.stopManagerCircuitOpen.delete(participantId);
    }
  }

  /**
   * Resolve the realized P&L for a lot whose exchange position is ALREADY flat when the
   * relay gets to the close path. Priority (per approved fix):
   *  1. Bitfinex margin wallet ledger (`v2/auth/r/ledgers/hist` wallet=margin) since the
   *     lot's entry — when only one lot was open, the position-close ledger entry IS this
   *     lot's exchange-realized P&L. (`exchange_realised`)
   *  2. Reconstruct from the lot's own entry fill price vs the relay-observed exit price
   *     using the FULL lot qty — what the exchange would have realized on this lot's share.
   *     (`reconstructed`)
   * Never writes $0 for an already-flat close: that under-attributes P&L and breaks
   * reconciliation against the user's real wallet.
   */
  private async resolveAlreadyFlatPnl(
    agentId: string,
    userId: string,
    creds: ExchangeCredentials,
    participantId: string,
    meta: ExecutionPayload,
    fillPrice: number,
    exitPrice: number,
  ): Promise<{ pnlUsd: number; pnlSource: 'exchange_realised' | 'reconstructed' }> {
    const qty = meta.qty ?? 0;
    const direction = meta.direction;
    if (!qty || !direction || !fillPrice || fillPrice <= 0 || !exitPrice || exitPrice <= 0) {
      return { pnlUsd: 0, pnlSource: 'reconstructed' };
    }

    const reconstructed =
      direction === 'LONG'
        ? (exitPrice - fillPrice) * qty
        : (fillPrice - exitPrice) * qty;

    // Only attribute via the exchange ledger when a single lot was open — otherwise the
    // margin wallet's position-close entry is the merged-position P&L, not this lot's
    // share, and reconstruction is more accurate per-lot.
    try {
      const openLotCount = await this.prisma.signalCycleParticipant.count({
        where: {
          userId,
          status: SignalCycleStatus.OPEN,
          cycle: { agentId },
        },
      });
      if (openLotCount <= 1) {
        const entryMs = await this.resolveLotEntryMs(participantId);
        if (entryMs > 0) {
          const realised = await this.activeTrading.getRealizedPnlSince(creds, entryMs);
          if (Math.abs(realised) > 0.0001) {
            return { pnlUsd: realised, pnlSource: 'exchange_realised' };
          }
        }
      }
    } catch (err) {
      this.logger.warn(
        `Already-flat PnL ledger lookup failed ${participantId}: ${err instanceof Error ? err.message : err}`,
      );
    }

    return { pnlUsd: reconstructed, pnlSource: 'reconstructed' };
  }

  /** Lot entry timestamp — from the FILLED event, falling back to the participant claim time. */
  private async resolveLotEntryMs(participantId: string): Promise<number> {
    const filled = await this.prisma.signalCycleEvent.findFirst({
      where: { participantId, eventType: 'FILLED' },
      orderBy: { createdAt: 'asc' },
      select: { createdAt: true },
    });
    if (filled) return filled.createdAt.getTime();
    const participant = await this.prisma.signalCycleParticipant.findUnique({
      where: { id: participantId },
      select: { createdAt: true },
    });
    return participant?.createdAt.getTime() ?? 0;
  }

  /**
   * Scenario 1 — exchange flat while ledger OPEN: close virtual lots immediately (manual UI close).
   */
  private async reconcileImmediateExchangeFlat(
    agentId: string,
    instance: TradingAgentInstance,
    creds: ExchangeCredentials,
    participantScope: { createdAt?: { gte: Date } } = {},
    simActive = false,
  ): Promise<boolean> {
    const userId = instance.userId;
    let position: Awaited<ReturnType<ExecutionTradingClient['getOpenPositionDetail']>>;
    try {
      position = await this.activeTrading.getOpenPositionDetail(creds);
    } catch (err) {
      const message =
        `EXCHANGE_POSITION_READ_FAILED (${simActive ? 'simulation' : 'live'}): ` +
        `cannot prove Bitfinex flat; relay paused. ${
          err instanceof Error ? err.message : String(err)
        }`;
      await this.pauseRelayForPositionMismatch(instance, message);
      return false;
    }
    if (position && btcToSats(position.amount) !== 0) return true;

    const openRows = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: SignalCycleStatus.OPEN,
        cycle: { agentId },
        ...participantScope,
      },
      include: { cycle: true },
    });
    if (!openRows.length) return true;

    let safeToContinue = true;
    for (const row of openRows) {
      if (await this.hasParticipantExited(row.id)) continue;
      const meta = await this.loadExecutionMeta(row.id);
      if (!meta.qty || !meta.direction) continue;

      const mark = await this.activeTrading.getMarkPrice().catch(() => meta.limitPrice ?? 0);
      const fillPrice =
        row.fillPrice != null ? Number(row.fillPrice) : meta.limitPrice ?? mark;
      const exitPrice = mark;
      // Fix 6 — realized P&L. Fill-vs-current-mark reconstruction misstates
      // the outcome when the exchange's own stop (or a manual close) fired at
      // a different price than the current mark. resolveAlreadyFlatPnl walks
      // the exchange's realized ledger first (single-lot case) and only falls
      // back to reconstruction — same path closeOrphanLedgerLots uses.
      const flat = await this.resolveAlreadyFlatPnl(
        agentId,
        userId,
        creds,
        row.id,
        meta,
        fillPrice,
        exitPrice || fillPrice,
      );
      const pnlUsd = flat.pnlUsd;
      const pnlMarginPct =
        fillPrice && fillPrice > 0
          ? (pnlUsd / (fillPrice * meta.qty)) * 100 * DEFAULT_SUBSCRIBER_LEVERAGE
          : 0;

      // Money-path safety: cancel any leftover Bitfinex orders on this lot
      // before recording the EXIT. Without this, the protective STOP and the
      // entry limit survive on Bitfinex as orphans after the position is flat —
      // real incident: a SELL STOP (oid 240154119117) was left ACTIVE and would
      // have opened an untracked SHORT if price had tagged it. Mirror
      // closeOrphanLedgerLots (~L1186) pre-fill guard: if the order has already
      // executed any quantity, leave it alone so reconcileFilledParticipants
      // heals it as a fill. Use the retry helper, not a bare cancelOrder.
      const cancelledOids: number[] = [];
      let cancelStillLive = false;
      let orderLookupFailed = false;
      for (const oid of [meta.bitfinexOrderId, meta.stopOrderId]) {
        if (oid == null) continue;
        let orderResting: { amount: number; amountOrig: number } | null = null;
        try {
          orderResting = await this.activeTrading.findOrder(creds, oid);
        } catch (err) {
          orderLookupFailed = true;
          safeToContinue = false;
          const message =
            `IMMEDIATE_FLAT_ORDER_READ_FAILED cycle=${row.cycleId} oid=${oid}: ` +
            `cannot prove the managed order is gone; ${
              err instanceof Error ? err.message : String(err)
            }`;
          await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
          this.logger.warn(`[IMMEDIATE-FLAT] ${message}`);
          break;
        }
        if (!orderResting) {
          // Already gone (filled, cancelled, or expired) — nothing to cancel.
          this.logger.log(
            `[IMMEDIATE-FLAT] oid=${oid} not resting (gone) — skip cancel ${userId} participant=${row.id}`,
          );
          continue;
        }
        const filledQty = satsToBtc(exchangeOrderFilledQtySats(orderResting));
        if (btcToSats(filledQty) > 0) {
          // The account is already exchange-flat. Cancel the still-resting
          // managed remainder even when this order previously filled in part;
          // leaving it active could reopen exposure after we record EXIT.
          this.logger.warn(
            `[IMMEDIATE-FLAT] oid=${oid} has filled qty=${filledQty.toFixed(8)}; cancelling the active remainder before EXIT ${userId} participant=${row.id}`,
          );
        }
        this.logger.log(
          `[IMMEDIATE-FLAT] cancelling oid=${oid} reason=IMMEDIATE_EXCHANGE_FLAT ${userId} participant=${row.id}`,
        );
        const result = await this.cancelManagedOrderGone(
          creds,
          oid,
          `IMMEDIATE-FLAT cancel oid=${oid} ${userId} participant=${row.id}`,
        );
        if (result.gone) {
          cancelledOids.push(oid);
        } else {
          cancelStillLive = true;
          this.logger.warn(
            `[IMMEDIATE-FLAT] cancel oid=${oid} FAILED and order still live (reason=${result.reason ?? 'unknown'} attempts=${result.attempts}) — deferring to next tick ${userId} participant=${row.id}`,
          );
        }
      }
      if (orderLookupFailed) continue;
      if (cancelStillLive) {
        await this.pauseUserRelayForPositionMismatch(
          userId,
          agentId,
          `IMMEDIATE_FLAT_CANCEL_FAILED cycle=${row.cycleId}; managed order remains live.`,
        ).catch(() => {});
        safeToContinue = false;
        continue;
      }

      let postCancelPosition: Awaited<
        ReturnType<ExecutionTradingClient['getOpenPositionDetail']>
      >;
      try {
        postCancelPosition = await this.activeTrading.getOpenPositionDetail(creds);
      } catch (err) {
        const message =
          `IMMEDIATE_FLAT_POST_CANCEL_POSITION_READ_FAILED cycle=${row.cycleId}: ` +
          `cannot reconfirm Bitfinex flat; ${err instanceof Error ? err.message : String(err)}`;
        await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
        this.logger.warn(`[IMMEDIATE-FLAT] ${message}`);
        safeToContinue = false;
        continue;
      }
      if (postCancelPosition && btcToSats(postCancelPosition.amount) !== 0) {
        const message =
          `IMMEDIATE_FLAT_CANCEL_RACE_FILL cycle=${row.cycleId}: Bitfinex position changed to ` +
          `${postCancelPosition.amount.toFixed(8)} BTC; EXIT deferred for fill reconciliation.`;
        await this.pauseUserRelayForPositionMismatch(userId, agentId, message).catch(() => {});
        this.logger.warn(`[IMMEDIATE-FLAT] ${message}`);
        safeToContinue = false;
        continue;
      }

      await this.cycles.recordHireExecutionEvent(userId, agentId, row.cycleId, 'EXIT', {
        venue: 'bitfinex',
        exit_price: exitPrice,
        exit_reason: 'MANUAL_OR_EXCHANGE_CLOSE',
        pnl_usd: Math.round(pnlUsd * 100) / 100,
        pnl_margin_pct: Math.round(pnlMarginPct * 100) / 100,
        pnl_source: flat.pnlSource,
        source: 'hire',
        event: 'IMMEDIATE_EXCHANGE_FLAT',
        cancelled_order_ids: cancelledOids,
      });
      this.logger.warn(
        `Immediate flat reconcile ${userId} cycle=${row.cycleId} — exchange 0, ledger OPEN closed (cancelled_oids=[${cancelledOids.join(',')}])`,
      );
    }
    return safeToContinue;
  }

  /** Scenario 5 — cancel resting entry limits linked to a showcase cycle before exit. */
  private async cancelLinkedPendingLimits(
    creds: ExchangeCredentials,
    meta: ExecutionPayload,
  ) {
    const orderId = meta.bitfinexOrderId;
    if (!orderId) return;

    // `findOrder` reads the active book. A read failure is uncertainty, not
    // proof that the entry remainder is gone. Cancel any live managed
    // remainder before reducing the position so it cannot refill after EXIT.
    const resting = await this.activeTrading.findOrder(creds, orderId);
    if (!resting) return;

    const cancel = await this.cancelManagedOrderGone(
      creds,
      orderId,
      `SHOWCASE-CLOSE cancel linked entry oid=${orderId}`,
    );
    if (!cancel.gone) {
      throw new Error(
        `LINKED_ENTRY_CANCEL_FAILED oid=${orderId} reason=${cancel.reason ?? 'unknown'}`,
      );
    }
  }

  /**
   * User closed on Bitfinex (or stop filled) while our cycle still shows OPEN — reconcile without crashing.
   */
  private async reconcileManualClose(
    agentId: string,
    userId: string,
    cycle: {
      id: string;
      status: SignalCycleStatus;
      showcasePnlUsd: { toNumber?: () => number } | null;
    },
    participant: { id: string; fillPrice: { toNumber?: () => number } | null; status: SignalCycleStatus },
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
  ): Promise<boolean> {
    if (participant.status !== SignalCycleStatus.OPEN || !meta.qty || !meta.direction) {
      return false;
    }

    const position = await this.activeTrading.getOpenPositionDetail(creds);
    const expectedLong = meta.direction === 'LONG';
    const hasExpected =
      position &&
      ((expectedLong && position.amount > 0) || (!expectedLong && position.amount < 0));

    if (hasExpected) {
      const exchangeQty = Math.abs(position!.amount);
      if (meta.qty && exchangeQty + MIN_QTY_BTC < meta.qty) {
        this.logger.warn(
          `Lot qty drift ${userId} cycle=${cycle.id}: ledger lot ${meta.qty.toFixed(5)} > exchange ${exchangeQty.toFixed(5)} — partial external close?`,
        );
      }
      // This may be a legitimate minimum-size filled lot or a residual on top
      // of another merged lot. Exit reconciliation owns cleanup; never sweep
      // a live position merely because its size is below the new-entry minimum.
      return false;
    }

    if (meta.stopOrderId) {
      try {
        await this.activeTrading.cancelOrder(creds, meta.stopOrderId);
      } catch {
        /* stop may have filled */
      }
    }

    const fillPrice =
      participant.fillPrice != null
        ? Number(participant.fillPrice)
        : meta.limitPrice ?? (await this.activeTrading.getMarkPrice());
    const exitPrice = await this.activeTrading.getMarkPrice().catch(() => fillPrice ?? 0);
    const direction = meta.direction;
    const pnlUsd =
      fillPrice && exitPrice
        ? direction === 'LONG'
          ? (exitPrice - fillPrice) * meta.qty
          : (fillPrice - exitPrice) * meta.qty
        : 0;
    const pnlMarginPct =
      fillPrice && fillPrice > 0
        ? (pnlUsd / (fillPrice * meta.qty)) * 100 * DEFAULT_SUBSCRIBER_LEVERAGE
        : 0;

    await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXIT', {
      venue: 'bitfinex',
      exit_price: exitPrice,
      exit_reason: meta.stopOrderId ? 'EXCHANGE_STOP' : 'MANUAL_OR_EXCHANGE_CLOSE',
      pnl_usd: Math.round(pnlUsd * 100) / 100,
      pnl_margin_pct: Math.round(pnlMarginPct * 100) / 100,
      pnl_source: 'reconstructed',
      source: 'hire',
    });

    await this.prisma.tradingAgentInstance.updateMany({
      where: { agentId, userId },
      data: {
        lastError:
          'Position closed on your exchange (manual or stop) — copy session synced. Ready for next showcase signal.',
      },
    });

    this.logger.log(
      `Hire reconcile manual close ${userId} cycle=${cycle.id} pnl=$${pnlUsd.toFixed(2)}`,
    );
    return true;
  }

  private async monitorExit(
    agentId: string,
    userId: string,
    cycle: {
      id: string;
      status: SignalCycleStatus;
      tradeId?: string | null;
      showcasePnlUsd: { toNumber?: () => number } | null;
      closedAt: Date | null;
      intentEnvelope?: unknown;
    },
    participant: { id: string; fillPrice: { toNumber?: () => number } | null },
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
  ) {
    if (cycle.status !== SignalCycleStatus.CLOSED && cycle.status !== SignalCycleStatus.EXPIRED) {
      return;
    }

    const showcaseTradeId = this.resolveShowcaseMirrorTradeId(cycle, meta);
    const bot = await this.fetchExecutionBotState();
    const det = showcaseTradeId ? this.detectShowcaseTradeClosed(bot, showcaseTradeId) : { closed: false };
    const mirrorRelinked = (cycle.tradeId ?? '').startsWith('adopt:');

    await this.executeShowcaseMirrorClose(agentId, userId, cycle, participant, meta, creds, {
      showcaseExitPrice: det.exitPrice,
      showcaseExitReason: det.exitReason,
      mirrorRelinked,
      trigger: 'CYCLE_CLOSED',
    });
  }

  private async cleanupOrphanCopyOrders(
    userId: string,
    instanceId: string,
    agentId: string,
    creds: ExchangeCredentials,
    managedOrderIds: Set<number>,
    preFetchedForeign?: Array<{
      id: number;
      amount?: number;
      amountOrig?: number;
      price?: number;
      status?: string;
      orderType?: string;
      cid?: number;
      createdAtMs?: number;
    }>,
  ) {
    // REAL-MONEY SAFETY: never blanket-cancel every unmanaged order on the symbol.
    // The previous behavior cancelled a user's MANUAL orders/stops each tick because
    // they were not in `managedOrderIds` (which only tracks copy-relay orders placed
    // this session). Now we only cancel when aggressive cleanup is explicitly opted-in
    // (RELAY_AGGRESSIVE_ORPHAN_CLEANUP=1) OR the foreign order's `cid` matches a
    // bot-computed `ExecutionPayload.clientOrderId` in the DB (Phase 6 fix 4 — those
    // are definitively the bot's OWN orphaned orders, never user manual orders, so
    // self-heal is default-on for them without the blanket aggressive flag).
    const aggressive =
      (this.config.get<string>('RELAY_AGGRESSIVE_ORPHAN_CLEANUP') ?? '').trim() === '1';

    const orders = preFetchedForeign
      ? preFetchedForeign.filter((o) => !managedOrderIds.has(o.id))
      : (await this.activeTrading.listActiveOrders(creds).catch(() => [])).filter(
          (o) => !managedOrderIds.has(o.id),
        );

    if (orders.length === 0) return;

    // Phase 6 fix 4 — build the cid → participant map for this user/agent by
    // reading one bounded set of recent ORDER_PLACED / UPDATE_STOPS payloads.
    // This avoids the historical participant N+1 scan that can exceed the
    // executor watchdog. A foreign
    // active order whose `cid` matches one of these is, by construction, the
    // bot's own orphaned limit (the participant is now terminal so its
    // bitfinexOrderId left `managedOrderIds`, but the exchange order is still
    // on the book). Auto-cancel those with retry + loud-fail + audit. cid-less
    // / unknown-cid foreign orders are left for manual review (current
    // behavior, gated by the aggressive flag).
    const ownershipEvents = await this.prisma.signalCycleEvent.findMany({
      where: {
        participantId: { not: null },
        eventType: { in: ['ORDER_PLACED', 'UPDATE_STOPS'] },
        createdAt: { gte: new Date(Date.now() - 90 * 24 * 60 * 60 * 1_000) },
        participant: { userId, cycle: { agentId } },
      },
      orderBy: { createdAt: 'desc' },
      take: 2_000,
      select: {
        participantId: true,
        cycleId: true,
        payload: true,
        participant: { select: { status: true } },
      },
    });
    const cidToParticipant = new Map<number, { participantId: string; cycleId: string }>();
    for (const event of ownershipEvents) {
      if (!event.participantId || !event.payload || typeof event.payload !== 'object') continue;
      // A freshly placed order can appear in Bitfinex's active-order snapshot
      // before the same tick's execution metadata is visible to the bounded
      // managedOrderIds read.  Its cid is already present in ORDER_PLACED, so
      // treating every cid match as an orphan creates a placement/cleanup race
      // and cancels a legitimate live relay order one second after submission.
      // Only terminal participants can own a genuinely orphaned resting order.
      if (!event.participant || !participantCanOwnOrphanOrder(event.participant.status)) {
        continue;
      }
      const cid = Number((event.payload as { clientOrderId?: unknown }).clientOrderId);
      if (!Number.isInteger(cid) || cid <= 0) continue;
      // First-seen wins — a re-placement (applyLimitChase) reuses the same cid
      // hash for the same (cycle, participant, tradeId) triple, so duplicates
      // map to the same participant anyway.
      if (!cidToParticipant.has(cid)) {
        cidToParticipant.set(cid, {
          participantId: event.participantId,
          cycleId: event.cycleId,
        });
      }
    }

    const client = this.activeTrading as CancelCapableClient;
    let autoCancelled = 0;
    for (const order of orders) {
      const matched = order.cid != null ? cidToParticipant.get(order.cid) : undefined;
      if (!matched) {
        if (aggressive) {
          const cancel = await cancelOrderWithRetry(client, creds, order.id, {
            logger: this.logger,
            label: `Aggressive orphan cleanup cancel ${order.id} for ${userId}`,
          });
          if (cancel.ok) {
            this.logger.warn(
              `Cancelled orphan copy order ${order.id} (${order.orderType}) for ${userId} (aggressive cleanup)`,
            );
            autoCancelled += 1;
          } else {
            this.logger.error(
              `Aggressive orphan cleanup: order ${order.id} cancel failed (attempts=${cancel.attempts}, reason=${cancel.reason}) — leaving for next tick`,
            );
          }
        } else {
        // Log only; do NOT cancel — could be a user's own manual order.
        this.logger.debug(
            `Unmanaged order ${order.id} (cid=${order.cid ?? 'none'}, ${order.orderType}) present for ${userId}; leaving untouched (no cid match, aggressive cleanup off)`,
        );
        }
        continue;
      }
      // cid matched one of OUR participants — definitively the bot's own
      // orphan. Auto-cancel with retry + audit, regardless of `aggressive`.
      const cancel = await cancelOrderWithRetry(client, creds, order.id, {
        logger: this.logger,
        label: `Auto-cancel own orphan ${order.id} (cid=${order.cid}) for ${userId}`,
      });
      if (cancel.ok) {
        autoCancelled += 1;
        this.logger.warn(
          `[RECONCILE] auto-cancelled own orphan order ${order.id} (cid=${order.cid}) for ${userId} — matched participant ${matched.participantId}`,
        );
        await this.cycles
          .recordHireExecutionEvent(userId, agentId, matched.cycleId, 'RECONCILE_AUTO_CANCELLED_OWN_ORPHAN', {
            venue: 'bitfinex',
            source: 'hire',
            bitfinex_order_id: order.id,
            client_order_id: order.cid,
            matched_participant_id: matched.participantId,
            cancel_attempts: cancel.attempts,
            cancel_reason: cancel.reason ?? 'cancelled',
          })
          .catch(() => {
            /* audit-best-effort — must not abort cleanup */
          });
        // Re-verify the order is actually gone; if it still shows on the book
        // (cancel API said NOT_FOUND but a stale listActiveOrders cached it),
        // leave it — the next tick's surfacer will re-list and re-attempt.
        const gone = await confirmOrderGone(client, creds, order.id);
        if (!gone) {
          this.logger.warn(
            `[RECONCILE] own orphan ${order.id} for ${userId}: cancel returned ok but findOrder still locates it — deferring to next tick`,
          );
        }
      } else {
        this.logger.error(
          `[RECONCILE] own orphan ${order.id} (cid=${order.cid}) for ${userId}: auto-cancel failed (attempts=${cancel.attempts}, reason=${cancel.reason}) — leaving for next tick`,
        );
      }
    }

    if (autoCancelled > 0) {
      // The surfacer already persisted orphanOrderIds for this tick; refresh
      // it so the dashboard reflects the auto-cancellations immediately.
      const remaining = orders
        .filter((o) => {
          if (o.cid == null) return true;
          const m = cidToParticipant.get(o.cid);
          return !m; // unmatched (manual / unknown) — keep surfaced
        })
        .map((o) => ({
          id: o.id,
          amount: o.amount,
          amountOrig: o.amountOrig,
          price: o.price,
          status: o.status,
          orderType: o.orderType,
          ...(o.cid != null ? { cid: o.cid } : {}),
          createdAtMs: o.createdAtMs,
        }));
      if (remaining.length === 0) {
        await this.clearOrphanOrderIds(instanceId).catch(() => {
          /* best-effort */
        });
      } else {
        await this.persistOrphanOrderIds(instanceId, remaining).catch(() => {
          /* best-effort */
        });
      }
    }
  }

  /**
   * Exchange stop filled for this lot qty — position shrank but other lots remain open.
   */
  private async reconcileAttributedLotClose(
    agentId: string,
    userId: string,
    cycle: { id: string },
    participant: { id: string; fillPrice: { toNumber?: () => number } | null },
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
  ): Promise<boolean> {
    if (!meta.qty || !meta.direction) return false;
    if (await this.hasParticipantExited(participant.id)) return false;

    const position = await this.activeTrading.getOpenPositionDetail(creds);
    if (!position) return false;

    const expectedLong = meta.direction === 'LONG';
    const hasExpected =
      (expectedLong && position.amount > 0) || (!expectedLong && position.amount < 0);
    if (!hasExpected) return false;

    const openParticipants = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: SignalCycleStatus.OPEN,
        cycle: { agentId },
      },
    });
    let ledgerQty = 0;
    for (const p of openParticipants) {
      const m = await this.loadExecutionMeta(p.id);
      ledgerQty += m.qty ?? 0;
    }

    const exchangeQty = Math.abs(position.amount);
    const deficit = ledgerQty - exchangeQty;
    if (deficit < meta.qty * 0.85 || deficit > meta.qty * 1.15) return false;

    if (!meta.stopOrderId) return false;
    const stop = await this.activeTrading.findOrder(creds, meta.stopOrderId).catch(() => null);
    if (stop) return false;

    const fillPrice =
      participant.fillPrice != null
        ? Number(participant.fillPrice)
        : meta.limitPrice ?? position.basePrice;
    const exitPrice = await this.activeTrading.getMarkPrice().catch(() => fillPrice);
    const direction = meta.direction;
    const closeQty = Math.min(meta.qty, deficit);
    const pnlUsd =
      fillPrice && exitPrice
        ? direction === 'LONG'
          ? (exitPrice - fillPrice) * closeQty
          : (fillPrice - exitPrice) * closeQty
        : 0;
    const pnlMarginPct =
      fillPrice && fillPrice > 0
        ? (pnlUsd / (fillPrice * closeQty)) * 100 * DEFAULT_SUBSCRIBER_LEVERAGE
        : 0;

    await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXIT', {
      venue: 'bitfinex',
      exit_price: exitPrice,
      exit_reason: 'EXCHANGE_STOP',
      pnl_usd: Math.round(pnlUsd * 100) / 100,
      pnl_margin_pct: Math.round(pnlMarginPct * 100) / 100,
      qty_closed: closeQty,
      pnl_source: 'open_position',
      source: 'hire',
    });

    this.logger.log(
      `Hire exchange stop lot ${userId} cycle=${cycle.id} qty=${closeQty} pnl=$${pnlUsd.toFixed(2)}`,
    );
    return true;
  }

  private hydrateRuntime(participantId: string, meta: ExecutionPayload): PositionRuntime {
    const cached = this.positionRuntime.get(participantId);
    if (cached) return cached;
    return {
      peakMarginPct: meta.peakMarginPct ?? 0,
      lastChaseAtMs: meta.lastChaseAtMs ?? 0,
      lastProfitLockFloor: meta.profitLockFloor,
      filledRecorded: true,
      // Option A — seed tracked stop from persisted meta so cancel-then-replace
      // has the right previous order id on the very first tick after FILLED.
      currentStopOrderId: meta.stopOrderId,
      currentStopPrice: undefined,
      currentRungIdx: undefined,
      consecutiveStopFailures: 0,
    };
  }

  /**
   * Option A — structured audit log for every protective-stop adjustment. One
   * JSON line per action, appended to logs/exchange-stop-manager.log. Fires
   * for INITIAL / REPLACE / SKIP_LOOSEN / SKIP_SAME / RECONSTRUCTED so the
   * full decision trail (not only successful writes) is reconstructable.
   * Best-effort: append errors are surfaced via logger.warn but never throw.
   */
  private appendExchangeStopAudit(entry: {
    cycleId: string;
    userId: string;
    participantId: string;
    side: 'LONG' | 'SHORT';
    entry: number;
    peakMarginPct: number;
    prevRung: number | null;
    newRung: number | null;
    oldStop: number | null;
    newStop: number | null;
    bitfinexOldOrderId: number | null;
    bitfinexNewOrderId: number | null;
    action:
      | 'INITIAL'
      | 'REPLACE'
      | 'SKIP_LOOSEN'
      | 'SKIP_SAME'
      | 'SKIP_CIRCUIT_OPEN'
      | 'RECONSTRUCTED'
      | 'CIRCUIT_OPEN';
    error?: string;
  }): void {
    try {
      // Lazy require so the unit-test path (and any non-Node main) doesn't pay
      // the cost unless the dynamic-stops feature is actually in use.
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const fs = require('fs') as typeof import('fs');
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const path = require('path') as typeof import('path');
      const logDir = path.resolve(process.cwd(), 'logs');
      try {
        fs.mkdirSync(logDir, { recursive: true });
      } catch {
        /* may already exist or be unwritable — best-effort */
      }
      const line =
        JSON.stringify({
          tag: '[SCENARIO_C]',
          cycle: entry.cycleId,
          user: entry.userId,
          participant: entry.participantId,
          side: entry.side,
          entry: entry.entry,
          peak_margin_pct: Math.round(entry.peakMarginPct * 100) / 100,
          prev_rung: entry.prevRung,
          new_rung: entry.newRung,
          old_stop: entry.oldStop,
          new_stop: entry.newStop,
          bitfinex_old_order_id: entry.bitfinexOldOrderId,
          bitfinex_new_order_id: entry.bitfinexNewOrderId,
          action: entry.action,
          error: entry.error,
          ts: new Date().toISOString(),
        }) + '\n';
      fs.appendFileSync(path.resolve(logDir, 'exchange-stop-manager.log'), line, {
        flag: 'a',
      });
    } catch (err) {
      this.logger.warn(
        `exchange-stop-manager audit write failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  /**
   * Option A — circuit breaker state, surfaced on the instance dashboard via
   * `dashboardState.stopManagerCircuitOpen` (a map of participantId → last
   * error). Keyed by participantId → per-account isolation. Reset by the
   * FILLED handler (consecutiveStopFailures = 0 + delete from this map).
   */
  private readonly stopManagerCircuitOpen = new Map<string, string>();

  private async hasParticipantExited(participantId: string): Promise<boolean> {
    const participant = await this.prisma.signalCycleParticipant.findUnique({
      where: { id: participantId },
      select: { status: true },
    });
    if (!participant) return false;
    // Cancel-race recovery can record EXIT (RECONCILE_CANCEL_BY_EXCHANGE) before a
    // real FILLED re-opens the lot — trust live participant status over event count.
    if (
      participant.status === SignalCycleStatus.OPEN ||
      participant.status === SignalCycleStatus.PENDING_ENTRY ||
      participant.status === SignalCycleStatus.INTENT
    ) {
      return false;
    }
    const exits = await this.prisma.signalCycleEvent.count({
      where: { participantId, eventType: 'EXIT' },
    });
    return exits > 0;
  }

  private async resolveLotMeta(
    participantId: string,
    cycleId: string,
    userId: string,
    agentId: string,
    intentEnvelope: unknown,
    marginCap: number,
  ): Promise<ExecutionPayload> {
    const meta = await this.loadExecutionMeta(participantId);
    if (meta.qty && btcToSats(meta.qty) > 0 && meta.direction) return meta;

    const intent = intentEnvelope as SignalIntentEnvelope;
    const direction = meta.direction ?? intent?.direction;
    let limitPrice =
      meta.limitPrice
      ?? meta.originalLimitPrice
      ?? (intent?.entry?.mode === 'EXACT_LIMIT'
        ? Number(intent.entry.exact_limit_price ?? 0)
        : undefined);
    if (
      (!limitPrice || limitPrice <= 0)
      && intent?.entry?.mode !== 'EXACT_LIMIT'
      && intent?.entry?.offset_pct != null
      && direction
    ) {
      const mark = await this.activeTrading.getMarkPrice().catch(() => null);
      if (mark && mark > 0) {
        const raw = computeLimitFromMark(mark, intent.entry.offset_pct);
        limitPrice = sanitizeLimitPrice(mark, raw, direction) ?? limitPrice;
      }
    }
    const leverage = resolveSubscriberLeverage(intent);
    const marginUsd =
      meta.margin_usd ??
      (intent?.risk?.max_margin_usd && intent.risk.max_margin_usd > 0
        ? Math.min(marginCap, intent.risk.max_margin_usd)
        : marginCap);

    let qty = meta.qty;
    if ((!qty || btcToSats(qty) === 0) && limitPrice && limitPrice > 0 && marginUsd > 0) {
      const exactRepairQty = resolveExactShowcaseEntryQty({
        exactQtyBtc: (intent?.entry as SignalIntentEnvelope['entry'] & { exact_qty_btc?: number })?.exact_qty_btc,
        maxMarginUsd: marginUsd,
        leverage,
        limitPrice,
        minQtyBtc: MIN_QTY_BTC,
      });
      if (exactRepairQty.ok) qty = exactRepairQty.qty;
    }

    if (qty && btcToSats(qty) > 0 && direction) {
      if (shouldPersistLotMetaRepair(meta, { qty, direction })) {
        await this.cycles.recordHireExecutionEvent(userId, agentId, cycleId, 'UPDATE_STOPS', {
          venue: 'bitfinex',
          event: 'META_QTY_REPAIR',
          qty,
          direction,
          margin_usd: marginUsd,
          source: 'hire',
        });
        this.logger.warn(
          `Repaired missing lot meta ${userId} participant=${participantId} qty=${qty.toFixed(5)} direction=${direction}`,
        );
      }
      return { ...meta, qty, direction, margin_usd: marginUsd };
    }

    return meta;
  }

  private async loadExecutionMeta(participantId: string): Promise<ExecutionPayload> {
    const events = await this.prisma.signalCycleEvent.findMany({
      where: { participantId },
      orderBy: { createdAt: 'asc' },
    });
    const meta: ExecutionPayload = {};
    let peakFromEvents = 0;
    let lockFloorFromEvents = 0;
    for (const e of events) {
      if (e.payload && typeof e.payload === 'object') {
        const p = e.payload as ExecutionPayload & {
          peak_margin_pct?: number;
          lock_floor_margin_pct?: number;
          origin_participant_id?: string;
          origin_cycle_id?: string;
          origin_trade_id?: string;
        };
        Object.assign(meta, p);
        if (p.origin_participant_id) meta.originParticipantId = p.origin_participant_id;
        if (p.origin_cycle_id) meta.originCycleId = p.origin_cycle_id;
        if (p.origin_trade_id) meta.originTradeId = p.origin_trade_id;
        const peak = Number(p.peak_margin_pct ?? p.peakMarginPct);
        if (Number.isFinite(peak)) peakFromEvents = Math.max(peakFromEvents, peak);
        const floor = Number(p.lock_floor_margin_pct ?? p.profitLockFloor);
        if (Number.isFinite(floor)) lockFloorFromEvents = Math.max(lockFloorFromEvents, floor);
      }
    }
    meta.peakMarginPct = Math.max(meta.peakMarginPct ?? 0, peakFromEvents);
    if (lockFloorFromEvents > 0) meta.profitLockFloor = lockFloorFromEvents;
    return meta;
  }

  /** Emergency flatten all OPEN copy lots (sync protection breach). */
  async emergencyFlattenOpenCopyLots(userId: string, agentSlug: string): Promise<{ flattened: number }> {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: agentSlug } });
    if (!agent) throw new NotFoundException('Agent not found');
    const instance = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId: agent.id, userId } },
    });
    if (!instance?.exchangeProvider || instance.exchangeProvider === 'paper') {
      return { flattened: 0 };
    }
    const creds = await this.exchanges.getUserCredentials(userId, instance.exchangeProvider);
    if (!creds) return { flattened: 0 };

    this.activeTrading = this.bitfinex;
    const mark =
      (await this.activeTrading.getMarkPrice().catch(() => null)) ??
      (await this.botBridge.fetchStateForExecution(true).catch(() => null))?.price ??
      0;

    const openRows = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: SignalCycleStatus.OPEN,
        cycle: { agentId: agent.id },
      },
    });

    let flattened = 0;
    for (const row of openRows) {
      const meta = await this.loadExecutionMeta(row.id);
      if (!meta.qty || !meta.direction) continue;
      const fillPrice = meta.fillPrice ?? mark;
      const closed = await this.closeVirtualLot(agent.id, userId, row.cycleId, row.id, meta, creds, {
        reason: 'HARD_STOP',
        mark: mark || fillPrice,
        fillPrice,
        leverage: meta.leverage ?? resolveSubscriberLeverage(),
        peakMarginPct: meta.peakMarginPct ?? 0,
        unrealMarginPct: 0,
        stopLossMarginPct: meta.stopLossMarginPct ?? 0,
      });
      if (closed) flattened += 1;
    }

    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        lastError: `Sync protection — emergency flatten closed ${flattened} open lot(s). Relay paused.`,
      },
    });

    return { flattened };
  }

  // ---------------------------------------------------------------------------
  // Phase 4 + 5 — autonomous orphan adoption (S6a pending order + S6b filled
  // position) with guardrails. See the design doc in the prompt:
  //   S6a — orphan RESTING order on the exchange with no tracking participant
  //         → re-adopt as a new PENDING_ENTRY, run the chase lifecycle.
  //   S6b — orphan FILLED position with no tracking participant
  //         → re-adopt as a new OPEN, arm a conservative protective stop,
  //           run the Scenario C exit ladder fresh via monitorOpenPosition.
  // Guardrails: env kill-switch, per-session budget cap, size sanity, conservative
  // stop distance, idempotency, and a full audit ring buffer + SignalCycleEvent
  // stream. When in doubt, default to surface-only (do NOT adopt).
  // ---------------------------------------------------------------------------

  /**
   * Main adoption entry. Called from processInstance once per tick, right after
   * reconcileAdoptLoop. Reads the exchange directly so it is independent of the
   * legacy reconcileLotLedger surface-only path. Every adoption decision is
   * audited via recordHireExecutionEvent + appended to
   * dashboardState.reconcileAdoptLog (ring buffer). Never throws — adoption
   * failures are logged and surfaced so the tick continues.
   */
  private async reconcileAdoptOrphans(
    agentId: string,
    instance: TradingAgentInstance,
    creds: ExchangeCredentials,
    participantScope: { createdAt?: { gte: Date } } = {},
    marginCap: number,
  ): Promise<void> {
    // Sim mode heals its own orphans by flattening (reconcileLotLedger L951).
    // Never adopt under sim — would double-manage the paper position.
    if (isCopyRelaySimActive(instance.dashboardState)) return;

    if (!reconcileAdoptEnabled()) {
      this.logger.log(
        `[RECONCILE-ADOPT] disabled via RECONCILE_ADOPT_ENABLED=0 ${instance.userId} — surface-only`,
      );
      // No event write here (no cycle context); the dashboard already shows
      // orphanOrderIds / orphanPositionIds from the legacy surface path.
      return;
    }

    // Fix 2 — reset-proof budget. dashboardState.reconcileAdoptCount is wiped
    // by resetAllUserCopySessions/buildFreshInstanceDashboardState on every
    // showcase fresh-collection, so derive the count from the Neon event
    // stream instead (survives resets): adoption events for this user/agent
    // in the trailing 24h. Belt-and-braces: also count synthesized `adopt:%`
    // cycles in the same window (written unconditionally by
    // synthesizeAdoptedCycle, whereas the audit events are best-effort) and
    // take the max — fail-closed, never undercounts.
    const budgetSince = new Date(Date.now() - RECONCILE_ADOPT_BUDGET_WINDOW_MS);
    const adoptEventCount = await this.prisma.signalCycleEvent.count({
      where: {
        eventType: {
          in: ['RECONCILE_ADOPT_ORPHAN_ORDER', 'RECONCILE_ADOPT_ORPHAN_POSITION'],
        },
        createdAt: { gte: budgetSince },
        cycle: { agentId },
        participant: { userId: instance.userId },
      },
    });
    const adoptCycleCount = await this.prisma.signalCycle.count({
      where: {
        agentId,
        tradeId: { startsWith: 'adopt:' },
        createdAt: { gte: budgetSince },
        participants: { some: { userId: instance.userId } },
      },
    });
    const adoptCount = Math.max(adoptEventCount, adoptCycleCount);
    const budget = reconcileAdoptBudget();
    let remaining = budget - adoptCount;
    if (remaining <= 0) {
      this.logger.warn(
        `[RECONCILE-ADOPT] budget exhausted ${instance.userId} count=${adoptCount} budget=${budget} — surface-only`,
      );
      await this.persistReconcileAdoptAudit(instance.id, {
        kind: 'BUDGET_EXHAUSTED',
        count: adoptCount,
        budget,
        at: new Date().toISOString(),
      }).catch(() => {
        /* audit-best-effort */
      });
      return;
    }

    // S6b first — a filled orphan position is the dangerous case (no stop, no
    // exit ladder). Heal it before S6a so a fill race doesn't sit unmanaged
    // while we chase a resting order.
    const s6bAdoptions = await this.adoptOrphanFilledPosition(
      agentId,
      instance,
      creds,
      participantScope,
      marginCap,
      remaining,
    ).catch((err) => {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.warn(`[RECONCILE-ADOPT] S6b error ${instance.userId}: ${msg}`);
      return 0;
    });

    // Fix 2 — decrement between S6b and S6a: passing the same `remaining` to
    // both allowed a one-tick overshoot of the budget by one adoption.
    remaining -= s6bAdoptions;
    if (remaining <= 0) return;

    // S6a — resting orphan order. Lower risk (no position yet) but still worth
    // re-adopting so the chase lifecycle runs and TTL expiry cancels cleanly.
    await this.adoptOrphanPendingOrders(
      agentId,
      instance,
      creds,
      participantScope,
      marginCap,
      remaining,
    ).catch((err) => {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.warn(`[RECONCILE-ADOPT] S6a error ${instance.userId}: ${msg}`);
    });
  }

  /**
   * S6b — orphan FILLED position. The exchange has a position but no OPEN
   * participant tracks it (the prior participant was marked terminal in a
   * fill/expiry race). Re-adopt as a fresh OPEN participant seeded from the
   * ORIGIN participant's real fill (Fix 1 — `position.basePrice` is the
   * exchange's MERGED average across all lots, not this slice's fill), carry
   * the origin's peak/floor so the profit-lock trail resumes instead of
   * restarting at 0, arm a conservative protective stop, and let the next
   * tick's monitorOpenPosition run the Scenario C exit ladder.
   *
   * Returns the number of adoptions performed (0 or 1) so the caller can
   * decrement the shared budget before running S6a.
   */
  private async adoptOrphanFilledPosition(
    agentId: string,
    instance: TradingAgentInstance,
    creds: ExchangeCredentials,
    participantScope: { createdAt?: { gte: Date } } = {},
    marginCap: number,
    budgetRemaining: number,
  ): Promise<number> {
    const position = await this.activeTrading.getOpenPositionDetail(creds).catch(() => null);
    if (!position || Math.abs(position.amount) < MIN_QTY_BTC) return 0;

    const direction: 'LONG' | 'SHORT' = position.amount > 0 ? 'LONG' : 'SHORT';
    const exchangeQty = Math.abs(position.amount);
    const mergedBasePrice = position.basePrice > 0 ? position.basePrice : 0;
    if (!mergedBasePrice || mergedBasePrice <= 0) {
      this.logger.warn(
        `[RECONCILE-ADOPT] S6b refuse ${instance.userId}: exchange position has no basePrice — cannot verify fill`,
      );
      return 0;
    }

    // A just-filled entry can appear at the exchange one tick before its
    // participant is promoted from PENDING_ENTRY to OPEN. Never let orphan
    // recovery steal that position and attach it to an older terminal trade.
    const pendingRows = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId: instance.userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId },
        ...participantScope,
      },
      select: { id: true },
    });
    const pendingMeta: ExecutionPayload[] = [];
    for (const row of pendingRows) {
      pendingMeta.push(await this.loadExecutionMeta(row.id));
    }
    if (pendingEntryMayOwnExchangePosition(direction, pendingMeta)) {
      this.logger.warn(
        `[RECONCILE-ADOPT] S6b defer ${instance.userId}: ${pendingRows.length} current pending participant(s) may own ${exchangeQty.toFixed(5)} BTC ${direction}`,
      );
      await this.persistReconcileAdoptAudit(instance.id, {
        kind: 'DEFERRED_PENDING_FILL_OWNER',
        scenario: 'S6b',
        exchangeQty,
        direction,
        pendingCandidates: pendingRows.length,
        at: new Date().toISOString(),
      }).catch(() => {});
      return 0;
    }

    // Compute currently-attributed OPEN qty for this direction so we only adopt
    // the orphan SLICE (exchange may have other legitimately-tracked lots).
    const openRows = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId: instance.userId,
        status: SignalCycleStatus.OPEN,
        cycle: { agentId },
        ...participantScope,
      },
    });
    let attributedQty = 0;
    for (const row of openRows) {
      const meta = await this.loadExecutionMeta(row.id);
      if (meta.direction && meta.direction !== direction) continue;
      attributedQty += meta.qty ?? 0;
    }
    const orphanSlice = exchangeQty - attributedQty;
    if (orphanSlice < MIN_QTY_BTC) return 0; // nothing unattributed

    // Size sanity — refuse giant orphans. Normal lot at this price/lev.
    const leverage = DEFAULT_SUBSCRIBER_LEVERAGE;
    const normalLot = this.resolveNormalLotQty(marginCap, leverage, mergedBasePrice);
    const maxSize = normalLot * RECONCILE_ADOPT_SIZE_ANOMALY_MULTIPLE;
    if (orphanSlice > maxSize + MIN_QTY_BTC) {
      this.logger.warn(
        `[RECONCILE-ADOPT] S6b refuse ${instance.userId}: orphan slice ${orphanSlice.toFixed(5)} BTC > 2x normal lot ${maxSize.toFixed(5)} BTC — size anomaly, surface-only`,
      );
      await this.persistReconcileAdoptAudit(instance.id, {
        kind: 'REFUSED_SIZE_ANOMALY',
        scenario: 'S6b',
        orphanQty: orphanSlice,
        normalLot,
        maxSize,
        direction,
        fillPrice: mergedBasePrice,
        at: new Date().toISOString(),
      }).catch(() => {});
      return 0;
    }

    // Match the orphan to a terminal participant (cid not available for
    // positions; use the side+qty+price+time fallback).
    const match = await this.matchOrphanToTerminalParticipant(
      agentId,
      instance.userId,
      {
        side: direction,
        qty: orphanSlice,
        price: mergedBasePrice,
        cid: undefined,
        createdAtMs: Date.now(),
      },
      participantScope,
    );
    if (!match) {
      this.logger.warn(
        `[RECONCILE-ADOPT] S6b refuse ${instance.userId}: no terminal participant matches orphan slice ${orphanSlice.toFixed(5)} ${direction} @ ${mergedBasePrice.toFixed(2)} — surface-only (could be a manual position)`,
      );
      await this.persistReconcileAdoptAudit(instance.id, {
        kind: 'REFUSED_NO_MATCH',
        scenario: 'S6b',
        orphanQty: orphanSlice,
        direction,
        fillPrice: mergedBasePrice,
        at: new Date().toISOString(),
      }).catch(() => {});
      return 0;
    }

    if (budgetRemaining <= 0) return 0; // re-check after async match

    // Fix 1 — adoption economics. Seed the adopted participant from the
    // ORIGIN participant's own FILLED fill price, not the exchange's merged
    // basePrice (production evidence: a 0.0324 slice of a 0.0971 BTC merged
    // position adopted at the merged 61809.74 average — worse than any real
    // fill — then stopped by noise). Fix D added per-order trade history to
    // the Bitfinex client, so the origin's REAL executions are consulted
    // before falling back to position.basePrice (tagged in the audit payload).
    const originFill = await this.resolveOriginFillPrice(match.participantId, creds);
    const fillPrice = originFill?.price ?? mergedBasePrice;
    const fillPriceSource: 'origin_filled_event' | 'exchange_trades' | 'merged_base_price' =
      originFill?.source ?? 'merged_base_price';

    // Fix 1 — carry the origin's peak/floor so the profit-lock trail resumes
    // where the origin left off instead of restarting at 0 (loadExecutionMeta
    // already folds peak_margin_pct / lock_floor_margin_pct maxima into meta).
    const carriedPeak = Math.max(0, match.meta.peakMarginPct ?? 0);
    const carriedFloor = Math.max(0, match.meta.profitLockFloor ?? 0);

    // Fix 4 — orphan-level double-adopt guard. Two replicas (or a tick race)
    // can each synthesize a DISTINCT adopted cycle for the same orphan (the
    // tradeId embeds Date.now(), so the cycle-level P2002 unique constraint
    // does not protect). Fail-closed: skip + audit when a recent non-terminal
    // adopt:* participant already covers this slice.
    const duplicate = await this.findRecentAdoptedDuplicate(
      agentId,
      instance.userId,
      direction,
      orphanSlice,
    );
    if (duplicate) {
      this.logger.warn(
        `[RECONCILE-ADOPT] S6b refuse ${instance.userId}: recent adopted participant ${duplicate.participantId} already covers ${direction} slice ${orphanSlice.toFixed(5)} — double-adopt guard, skipping`,
      );
      await this.cycles
        .recordHireExecutionEvent(
          instance.userId,
          agentId,
          duplicate.cycleId,
          'RECONCILE_ADOPT_REFUSED_DUPLICATE',
          {
            venue: 'bitfinex',
            source: 'hire',
            scenario: 'S6b',
            direction,
            orphan_qty: orphanSlice,
            duplicate_participant_id: duplicate.participantId,
          },
        )
        .catch(() => {});
      await this.persistReconcileAdoptAudit(instance.id, {
        kind: 'REFUSED_DUPLICATE',
        scenario: 'S6b',
        direction,
        orphanQty: orphanSlice,
        duplicateParticipantId: duplicate.participantId,
        duplicateCycleId: duplicate.cycleId,
        at: new Date().toISOString(),
      }).catch(() => {});
      return 0;
    }

    // Adopt: synthesise a new cycle + participant seeded from the real fill.
    // The new cycle copies the terminal cycle's intentEnvelope so the exit
    // ladder (intent.risk.take_profit_ladder / stop_loss_margin_pct) runs
    // identically to the original trade.
    const intent = match.cycle.intentEnvelope as SignalIntentEnvelope;
    if (!intent?.risk) {
      this.logger.warn(
        `[RECONCILE-ADOPT] S6b refuse ${instance.userId}: matched terminal cycle has no intent risk — cannot adopt`,
      );
      return 0;
    }

    const adoptQty = Math.min(orphanSlice, match.meta.qty ?? orphanSlice);

    // Fix 1 — stop economics. Use the origin's actual stop-loss margin pct
    // (its meta stopLossMarginPct, default -18) applied to the REAL fill
    // price. When the origin was in profit (carried peak/floor), the existing
    // profit-lock-rung logic recomputes a tighter stop from the carried
    // values instead of the hardcoded first rung.
    const mark = await this.activeTrading.getMarkPrice().catch(() => fillPrice);
    const unrealMarginPct = computeUnrealizedMarginPct(fillPrice, mark, direction, leverage);
    const stopLossMarginPct =
      match.meta.stopLossMarginPct ?? intent.risk.stop_loss_margin_pct ?? -18;
    const effectiveStopMargin = resolveEffectiveStopLossMarginPct(stopLossMarginPct, {
      mirrorMode: isShowcaseMirrorOnlyMode(),
    });
    const standardStop = computeStopPrice(fillPrice, direction, effectiveStopMargin, leverage);
    let conservativeStop = standardStop;
    let stopReason: 'STANDARD_SL' | 'PROFIT_LOCK_RUNG' | 'MIRROR_DISASTER_STOP' = 'STANDARD_SL';
    const ladderFloor = getProfitLockFloor(Math.max(carriedPeak, unrealMarginPct)) ?? 0;
    const effectiveFloor = Math.max(carriedFloor, ladderFloor);
    const mirrorDisasterOnly =
      isShowcaseMirrorOnlyMode() && mirrorExitConvergenceEnabled();
    if (mirrorDisasterOnly) {
      stopReason = 'MIRROR_DISASTER_STOP';
    } else if (effectiveFloor > 0) {
      const lockStop = computeProfitLockStopPrice(fillPrice, direction, effectiveFloor, leverage);
      const longStopTooWide = direction === 'LONG' && lockStop < standardStop;
      const shortStopTooWide = direction === 'SHORT' && lockStop > standardStop;
      if (!longStopTooWide && !shortStopTooWide && lockStop > 0) {
        conservativeStop = lockStop;
        stopReason = 'PROFIT_LOCK_RUNG';
      }
    }

    const adoptedCycle = await this.synthesizeAdoptedCycle(
      agentId,
      instance.userId,
      match,
      'OPEN',
    ).catch((err) => {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.warn(`[RECONCILE-ADOPT] S6b synthesize cycle failed ${instance.userId}: ${msg}`);
      return null;
    });
    if (!adoptedCycle) return 0;
    const { cycleId, participantId } = adoptedCycle;

    // Record FILLED so the participant transitions to OPEN with the verified
    // fill price (recordHireExecutionEvent flips status + fillPrice). The
    // carried peak/floor ride in the payload so loadExecutionMeta re-derives
    // them for every later tick (peak_margin_pct / lock_floor_margin_pct).
    await this.cycles.recordHireExecutionEvent(instance.userId, agentId, cycleId, 'FILLED', {
      venue: 'bitfinex',
      fill_price: fillPrice,
      fill_price_source: fillPriceSource,
      qty: adoptQty,
      stop_loss_placed: false, // armed below
      stop_loss_margin_pct: stopLossMarginPct,
      peak_margin_pct: carriedPeak,
      lock_floor_margin_pct: effectiveFloor > 0 ? effectiveFloor : undefined,
      source: 'hire',
      event: 'RECONCILE_ADOPT_ORPHAN_POSITION',
      adopt_kind: 'S6b_FILLED_POSITION',
      origin_participant_id: match.participantId,
      origin_cycle_id: match.cycleId,
      origin_trade_id: match.cycle.tradeId ?? undefined,
      exchange_qty: exchangeQty,
      orphan_slice: orphanSlice,
      merged_base_price: mergedBasePrice,
      clientOrderId: match.meta.clientOrderId,
    });

    // Arm the protective stop via the existing ensureProtectiveStop, passing
    // the conservative price as an explicit override so it never falls back to
    // the standard formula. ensureProtectiveStop is idempotent (skips if an
    // existing live stop is found) and emits STOP_LOSS_ARMED itself.
    const adoptMeta: ExecutionPayload = {
      bitfinexOrderId: match.meta.bitfinexOrderId,
      stopOrderId: undefined,
      limitPrice: match.meta.limitPrice ?? fillPrice,
      originalLimitPrice: match.meta.originalLimitPrice,
      qty: adoptQty,
      direction,
      fillPrice,
      margin_usd: match.meta.margin_usd,
      source: 'hire',
      peakMarginPct: carriedPeak,
      profitLockFloor: effectiveFloor > 0 ? effectiveFloor : undefined,
      leverage,
      stopLossMarginPct,
      clientOrderId: match.meta.clientOrderId,
      originParticipantId: match.participantId,
      originCycleId: match.cycleId,
      originTradeId: match.cycle.tradeId ?? undefined,
    };
    if (reconcileWriteWindowEnabled()) {
      await this.ensureProtectiveStop(
        agentId,
        instance.userId,
        cycleId,
        participantId,
        adoptMeta,
        creds,
        intent,
        fillPrice,
        conservativeStop,
      );
    } else {
      this.logger.warn(
        `[RECONCILE-ADOPT] S6b stop arming skipped ${instance.userId} — RECONCILE_WRITE_WINDOW=0`,
      );
    }

    // Hydrate runtime so monitorOpenPosition (next tick) immediately resumes
    // the Scenario C exit ladder with the CARRIED peak + profit-lock floor.
    this.positionRuntime.set(participantId, {
      peakMarginPct: carriedPeak,
      lastChaseAtMs: 0,
      lastProfitLockFloor: effectiveFloor > 0 ? effectiveFloor : undefined,
      filledRecorded: true,
    });

    await this.cycles
      .recordHireExecutionEvent(instance.userId, agentId, cycleId, 'RECONCILE_ADOPT_ORPHAN_POSITION', {
        venue: 'bitfinex',
        participant_id: participantId,
        origin_participant_id: match.participantId,
        origin_cycle_id: match.cycleId,
        origin_trade_id: match.cycle.tradeId ?? undefined,
        direction,
        qty: adoptQty,
        fill_price: fillPrice,
        fill_price_source: fillPriceSource,
        merged_base_price: mergedBasePrice,
        carried_peak_margin_pct: carriedPeak,
        carried_lock_floor_margin_pct: carriedFloor,
        effective_lock_floor_margin_pct: effectiveFloor,
        stop_price: conservativeStop,
        stop_reason: stopReason,
        stop_loss_margin_pct: stopLossMarginPct,
        standard_stop: standardStop,
        unreal_margin_pct: Math.round(unrealMarginPct * 100) / 100,
        budget_remaining: budgetRemaining - 1,
        source: 'hire',
      })
      .catch(() => {
        /* audit-only */
      });

    await this.persistReconcileAdoptAudit(instance.id, {
      kind: 'ADOPTED_POSITION',
      scenario: 'S6b',
      cycleId,
      participantId,
      originCycleId: match.cycleId,
      originParticipantId: match.participantId,
      direction,
      qty: adoptQty,
      fillPrice,
      fillPriceSource,
      mergedBasePrice,
      carriedPeak,
      carriedFloor,
      stopPrice: conservativeStop,
      stopReason,
      at: new Date().toISOString(),
    }).catch(() => {});

    // Vigilance / Cure 1 extension — S6b adopts a real Bitfinex orphan slice,
    // but synthesizeAdoptedCycle stamps the new cycle's tradeId as
    // `adopt:<ORIGIN_cycle.tradeId>:<ts>` and meta.originTradeId to the same
    // ORIGIN showcase id. If the ORIGIN cycle was the stale side of a
    // mirror-owner-of-duplicate-limit race (tonight's cont-de8f316fd3c0 case:
    // the real position corresponds to a LATER showcase signal than the cycle
    // that placed the order), every mirror-exit path would key off the wrong
    // showcase id and the adopted real slice would rot unmanaged a second
    // time. Re-link the freshly-adopted cycle to whichever showcase signal
    // actually matches the verified real fill. Best-effort — never blocks the
    // adoption (safety nets still cover the lot if no candidate matches).
    try {
      await this.relinkCycleToShowcaseSignalIfDrifted({
        agentId,
        userId: instance.userId,
        cycle: { id: cycleId, tradeId: `adopt:${match.cycle.tradeId ?? 'unknown'}` },
        participantId,
        realFill: { price: fillPrice, direction },
        reason: 'RECONCILE_ADOPT_S6b',
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.warn(
        `[CURE-1] S6b relink threw ${instance.userId} cycle=${cycleId}: ${msg} — safety nets remain active`,
      );
    }

    this.logger.warn(
      `[RECONCILE-ADOPT] S6b adopted orphan position ${instance.userId} cycle=${cycleId} ${direction} qty=${adoptQty.toFixed(5)} @ ${fillPrice.toFixed(2)} (${fillPriceSource}) stop=${conservativeStop.toFixed(2)} (${stopReason}) peak=${carriedPeak} floor=${effectiveFloor}`,
    );
    return 1;
  }

  /**
   * S6a — orphan RESTING pending order. The exchange has a limit order with no
   * tracking PENDING_ENTRY participant. Re-adopt as a fresh PENDING_ENTRY
   * seeded from the exchange open order, then let the next tick's
   * monitorEntry / applyLimitChase run the chase lifecycle. If chase budget
   * exhausts or TTL expires, monitorEntry cancels the order + marks EXPIRED.
   */
  private async adoptOrphanPendingOrders(
    agentId: string,
    instance: TradingAgentInstance,
    creds: ExchangeCredentials,
    participantScope: { createdAt?: { gte: Date } } = {},
    marginCap: number,
    budgetRemaining: number,
  ): Promise<void> {
    const orders = await this.activeTrading.listActiveOrders(creds).catch(() => []);
    if (!orders.length) return;

    // Build the managed-order-id set from CURRENT participants so we don't
    // adopt an order that's already tracked by an OPEN/PENDING participant.
    const tracked = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId: instance.userId,
        status: { in: [SignalCycleStatus.OPEN, SignalCycleStatus.PENDING_ENTRY] },
        cycle: { agentId },
        ...participantScope,
      },
    });
    const trackedOrderIds = new Set<number>();
    for (const row of tracked) {
      const meta = await this.loadExecutionMeta(row.id);
      if (meta.bitfinexOrderId) trackedOrderIds.add(meta.bitfinexOrderId);
      if (meta.stopOrderId) trackedOrderIds.add(meta.stopOrderId);
    }

    // Only LIMIT orders are adoption candidates — STOP orders belong to OPEN
    // lots (their stop), and a resting STOP with no OPEN lot is a different
    // anomaly handled by the surface path. We also exclude the absurd-price
    // orders that cancelAbsurdPendingOrders will reap.
    const candidates = orders.filter(
      (o) =>
        !trackedOrderIds.has(o.id) &&
        /LIMIT/i.test(o.orderType) &&
        Math.abs(o.amount) >= MIN_QTY_BTC,
    );
    if (!candidates.length) return;

    for (const order of candidates) {
      if (budgetRemaining <= 0) break;

      const direction: 'LONG' | 'SHORT' = order.amount > 0 ? 'LONG' : 'SHORT';
      const qty = Math.abs(order.amountOrig || order.amount);
      const limitPrice = order.price;
      if (!limitPrice || limitPrice <= 0) continue;

      // Size sanity
      const leverage = DEFAULT_SUBSCRIBER_LEVERAGE;
      const normalLot = this.resolveNormalLotQty(marginCap, leverage, limitPrice);
      const maxSize = normalLot * RECONCILE_ADOPT_SIZE_ANOMALY_MULTIPLE;
      if (qty > maxSize + MIN_QTY_BTC) {
        this.logger.warn(
          `[RECONCILE-ADOPT] S6a refuse ${instance.userId}: orphan order ${order.id} qty ${qty.toFixed(5)} > 2x normal lot ${maxSize.toFixed(5)} — size anomaly`,
        );
        await this.persistReconcileAdoptAudit(instance.id, {
          kind: 'REFUSED_SIZE_ANOMALY',
          scenario: 'S6a',
          orderId: order.id,
          orphanQty: qty,
          normalLot,
          maxSize,
          direction,
          limitPrice,
          at: new Date().toISOString(),
        }).catch(() => {});
        continue;
      }

      // Match — cid first (orders surface cid), then fallback.
      const match = await this.matchOrphanToTerminalParticipant(
        agentId,
        instance.userId,
        {
          side: direction,
          qty,
          price: limitPrice,
          cid: order.cid,
          createdAtMs: order.createdAtMs,
        },
        participantScope,
      );
      if (!match) {
        this.logger.warn(
          `[RECONCILE-ADOPT] S6a refuse ${instance.userId}: no terminal participant matches orphan order ${order.id} — surface-only`,
        );
        await this.persistReconcileAdoptAudit(instance.id, {
          kind: 'REFUSED_NO_MATCH',
          scenario: 'S6a',
          orderId: order.id,
          cid: order.cid,
          orphanQty: qty,
          direction,
          limitPrice,
          at: new Date().toISOString(),
        }).catch(() => {});
        continue;
      }

      const intent = match.cycle.intentEnvelope as SignalIntentEnvelope;
      if (!intent?.risk) continue;

      // Fix 4 — orphan-level double-adopt guard (see adoptOrphanFilledPosition).
      // Order-id first: a recent non-terminal adopt:* participant already
      // tracking THIS order id means another replica/tick won the race.
      const duplicate = await this.findRecentAdoptedDuplicate(
        agentId,
        instance.userId,
        direction,
        qty,
        order.id,
      );
      if (duplicate) {
        this.logger.warn(
          `[RECONCILE-ADOPT] S6a refuse ${instance.userId}: recent adopted participant ${duplicate.participantId} already covers order ${order.id} (${direction} qty=${qty.toFixed(5)}) — double-adopt guard, skipping`,
        );
        await this.cycles
          .recordHireExecutionEvent(
            instance.userId,
            agentId,
            duplicate.cycleId,
            'RECONCILE_ADOPT_REFUSED_DUPLICATE',
            {
              venue: 'bitfinex',
              source: 'hire',
              scenario: 'S6a',
              direction,
              orphan_qty: qty,
              order_id: order.id,
              duplicate_participant_id: duplicate.participantId,
            },
          )
          .catch(() => {});
        await this.persistReconcileAdoptAudit(instance.id, {
          kind: 'REFUSED_DUPLICATE',
          scenario: 'S6a',
          orderId: order.id,
          direction,
          orphanQty: qty,
          duplicateParticipantId: duplicate.participantId,
          duplicateCycleId: duplicate.cycleId,
          at: new Date().toISOString(),
        }).catch(() => {});
        continue;
      }

      const adoptedCycle = await this.synthesizeAdoptedCycle(
        agentId,
        instance.userId,
        match,
        'PENDING_ENTRY',
      ).catch((err) => {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`[RECONCILE-ADOPT] S6a synthesize cycle failed ${instance.userId}: ${msg}`);
        return null;
      });
      if (!adoptedCycle) continue;
      const { cycleId, participantId } = adoptedCycle;

      // Record ORDER_PLACED — this transitions the participant to PENDING_ENTRY
      // and seeds the execution meta (bitfinexOrderId, limitPrice, qty, cid).
      await this.cycles.recordHireExecutionEvent(
        instance.userId,
        agentId,
        cycleId,
        'ORDER_PLACED',
        {
          venue: 'bitfinex',
          local_mark_at_signal: limitPrice,
          limit_price: limitPrice,
          original_limit_price: match.meta.originalLimitPrice ?? limitPrice,
          qty,
          margin_usd: match.meta.margin_usd ?? marginCap,
          margin_cap_usd: marginCap,
          leverage: resolveSubscriberLeverage(intent),
          bitfinexOrderId: order.id,
          clientOrderId: order.cid ?? match.meta.clientOrderId,
          source: 'hire',
          event: 'RECONCILE_ADOPT_ORPHAN_ORDER',
          adopt_kind: 'S6a_PENDING_ORDER',
          origin_participant_id: match.participantId,
          origin_cycle_id: match.cycleId,
          origin_trade_id: match.cycle.tradeId ?? undefined,
          lastChaseAtMs: 0,
          limitChaseCount: 0,
        },
      );

      // Seed runtime so applyLimitChase treats it as a freshly-placed order
      // (chase window starts now). monitorEntry on the next tick will pick up
      // the chase / TTL expiry / fill lifecycle automatically.
      this.positionRuntime.set(participantId, {
        peakMarginPct: 0,
        lastChaseAtMs: 0,
        lastProfitLockFloor: undefined,
        filledRecorded: false,
      });

      await this.cycles
        .recordHireExecutionEvent(instance.userId, agentId, cycleId, 'RECONCILE_ADOPT_ORPHAN_ORDER', {
          venue: 'bitfinex',
          participant_id: participantId,
          origin_participant_id: match.participantId,
          origin_cycle_id: match.cycleId,
          origin_trade_id: match.cycle.tradeId ?? undefined,
          orderId: order.id,
          cid: order.cid,
          direction,
          qty,
          limitPrice,
          budget_remaining: budgetRemaining - 1,
          source: 'hire',
        })
        .catch(() => {});

      await this.persistReconcileAdoptAudit(instance.id, {
        kind: 'ADOPTED_ORDER',
        scenario: 'S6a',
        cycleId,
        participantId,
        originCycleId: match.cycleId,
        originParticipantId: match.participantId,
        orderId: order.id,
        cid: order.cid,
        direction,
        qty,
        limitPrice,
        at: new Date().toISOString(),
      }).catch(() => {});

      budgetRemaining -= 1;
      this.logger.warn(
        `[RECONCILE-ADOPT] S6a adopted orphan order ${instance.userId} cycle=${cycleId} orderId=${order.id} ${direction} qty=${qty.toFixed(5)} @ ${limitPrice.toFixed(2)}`,
      );
    }
  }

  /**
   * Match an orphan to a terminal participant. cid-first (orders), then
   * side+qty+price+time fallback (positions and pre-Phase-2 orders). Returns
   * the terminal participant + its cycle + the loaded execution meta so the
   * caller can copy intent + synthesise the adopted cycle.
   *
   * Safety: only TERMINAL participants (CLOSED / EXPIRED with an EXIT/EXPIRED
   * event) are candidates — an OPEN/PENDING participant is by definition still
   * tracking its order/position and must never be re-adopted. The fallback
   * time-window (RECONCILE_ADOPT_MATCH_WINDOW_MS) excludes stale orphans the
   * operator chose to leave flat, so the current 0.03247 BTC orphan (exited
   * long ago) is NOT matched and stays surface-only.
   */
  private async matchOrphanToTerminalParticipant(
    agentId: string,
    userId: string,
    orphan: {
      side: 'LONG' | 'SHORT';
      qty: number;
      price?: number;
      cid?: number;
      createdAtMs?: number;
    },
    participantScope: { createdAt?: { gte: Date } } = {},
  ): Promise<{
    participantId: string;
    cycleId: string;
    cycle: { id: string; intentEnvelope: unknown; tradeId: string | null };
    meta: ExecutionPayload;
    matchKind: 'cid' | 'fallback';
  } | null> {
    // Terminal participants for this user/agent. We look back further than
    // participantScope (the sim/rental window) because a race can happen at
    // the boundary of a sim session — but the time-window check below still
    // gates recency.
    const terminal = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: { in: [SignalCycleStatus.CLOSED, SignalCycleStatus.EXPIRED] },
        cycle: { agentId },
        events: { some: { eventType: { in: ['EXIT', 'EXPIRED'] } } },
      },
      include: {
        cycle: { select: { id: true, intentEnvelope: true, tradeId: true } },
        events: {
          where: { eventType: { in: ['EXIT', 'EXPIRED', 'ORDER_PLACED'] } },
          orderBy: { createdAt: 'desc' },
          take: 3,
        },
      },
      orderBy: { updatedAt: 'desc' },
      take: 40,
    });

    const now = Date.now();

    // Pass 1 — cid match (orders only). Exact int32 equality.
    if (orphan.cid != null && orphan.cid !== 0) {
      for (const row of terminal) {
        const meta = await this.loadExecutionMeta(row.id);
        if (meta.clientOrderId === orphan.cid && meta.direction === orphan.side) {
          return {
            participantId: row.id,
            cycleId: row.cycleId,
            cycle: row.cycle,
            meta,
            matchKind: 'cid',
          };
        }
      }
    }

    // Pass 2 — fallback: side + qty (tolerance) + price band + time window.
    for (const row of terminal) {
      const meta = await this.loadExecutionMeta(row.id);
      if (!meta.direction || meta.direction !== orphan.side) continue;
      const metaQty = meta.qty ?? 0;
      if (Math.abs(metaQty - orphan.qty) > RECONCILE_ADOPT_QTY_TOLERANCE) continue;

      // Price band — orphan entry within X% of the terminal participant's
      // intended limit (or its fill price if the limit was never persisted).
      const refPrice = meta.limitPrice ?? meta.fillPrice ?? meta.originalLimitPrice;
      if (orphan.price && refPrice && refPrice > 0) {
        const bandPct = Math.abs((orphan.price - refPrice) / refPrice) * 100;
        if (bandPct > RECONCILE_ADOPT_PRICE_BAND_PCT) continue;
      }

      // Time window — the terminal participant's most recent EXIT/EXPIRED
      // event must be within RECONCILE_ADOPT_MATCH_WINDOW_MS of NOW. This is
      // what excludes the stale 0.03247 BTC orphan (exited long ago).
      const lastExit = row.events.find((e) => e.eventType === 'EXIT' || e.eventType === 'EXPIRED');
      if (!lastExit) continue;
      const ageMs = now - lastExit.createdAt.getTime();
      if (ageMs > RECONCILE_ADOPT_MATCH_WINDOW_MS) continue;

      return {
        participantId: row.id,
        cycleId: row.cycleId,
        cycle: row.cycle,
        meta,
        matchKind: 'fallback',
      };
    }

    return null;
  }

  /**
   * Fix 1 — origin participant's own fill price. Source order:
   *  (a) the FILLED event's `fill_price` payload / the participant row's
   *      persisted fillPrice column (`origin_filled_event`);
   *  (b) Fix D — the exchange's per-order trade history for the origin's
   *      bitfinexOrderId (volume-weighted real fill, `exchange_trades`);
   *  then null — the caller falls back to the exchange's merged basePrice
   *  and tags the audit payload `fill_price_source: 'merged_base_price'`.
   */
  private async resolveOriginFillPrice(
    originParticipantId: string,
    creds?: ExchangeCredentials,
  ): Promise<{ price: number; source: 'origin_filled_event' | 'exchange_trades' } | null> {
    const filled = await this.prisma.signalCycleEvent.findFirst({
      where: { participantId: originParticipantId, eventType: 'FILLED' },
      orderBy: { createdAt: 'desc' },
      select: { payload: true },
    });
    const payload = (filled?.payload ?? null) as { fill_price?: unknown } | null;
    const fromEvent = Number(payload?.fill_price);
    if (Number.isFinite(fromEvent) && fromEvent > 0) {
      return { price: fromEvent, source: 'origin_filled_event' };
    }

    const row = await this.prisma.signalCycleParticipant.findUnique({
      where: { id: originParticipantId },
      select: { fillPrice: true },
    });
    const fromRow = row?.fillPrice != null ? Number(row.fillPrice) : NaN;
    if (Number.isFinite(fromRow) && fromRow > 0) {
      return { price: fromRow, source: 'origin_filled_event' };
    }

    if (creds) {
      const originMeta = await this.loadExecutionMeta(originParticipantId);
      const fromExchange = await this.resolveExchangeTradesFillPrice(
        creds,
        originMeta.bitfinexOrderId,
      );
      if (fromExchange != null && fromExchange > 0) {
        return { price: fromExchange, source: 'exchange_trades' };
      }
    }

    return null;
  }

  /**
   * Fix 4 — orphan-level double-adopt guard. Returns the most recent
   * non-terminal `adopt:%` participant for this user+agent created within
   * RECONCILE_ADOPT_DUPLICATE_WINDOW_MS that plausibly covers the same
   * orphan: same order id (S6a), same direction with qty within tolerance
   * (S6b), or an in-flight adoption with no execution meta yet (a replica
   * raced between the cycle create and the FILLED/ORDER_PLACED write —
   * fail-closed, treat as duplicate).
   */
  private async findRecentAdoptedDuplicate(
    agentId: string,
    userId: string,
    direction: 'LONG' | 'SHORT',
    qty: number,
    orderId?: number,
  ): Promise<{ participantId: string; cycleId: string } | null> {
    const since = new Date(Date.now() - RECONCILE_ADOPT_DUPLICATE_WINDOW_MS);
    const rows = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: { in: [SignalCycleStatus.OPEN, SignalCycleStatus.PENDING_ENTRY] },
        createdAt: { gte: since },
        cycle: { agentId, tradeId: { startsWith: 'adopt:' } },
      },
      select: { id: true, cycleId: true },
      orderBy: { createdAt: 'desc' },
    });

    for (const row of rows) {
      const meta = await this.loadExecutionMeta(row.id);
      if (orderId != null && meta.bitfinexOrderId === orderId) {
        return { participantId: row.id, cycleId: row.cycleId };
      }
      if (!meta.direction && !meta.qty) {
        // In-flight adoption (cycle synthesized, execution event not yet
        // written). Fail-closed — skip this tick rather than double-adopt.
        return { participantId: row.id, cycleId: row.cycleId };
      }
      if (meta.direction !== direction) continue;
      if (Math.abs((meta.qty ?? 0) - qty) <= RECONCILE_ADOPT_QTY_TOLERANCE) {
        return { participantId: row.id, cycleId: row.cycleId };
      }
    }
    return null;
  }

  /**
   * Synthesise a new SignalCycle + SignalCycleParticipant for an adoption.
   * Copies the original cycle's intentEnvelope so the exit ladder / stop
   * formula run identically. tradeId is namespaced `adopt:<origin>:<ts>` to
   * satisfy the (agentId, tradeId) unique constraint without colliding with
   * the original showcase trade.
   *
   * Idempotency: the (cycleId, userId) unique constraint on participants
   * guarantees that if two ticks race to adopt the same orphan, only one
   * create succeeds — the other throws P2002 and is caught by the caller.
   */
  private async synthesizeAdoptedCycle(
    agentId: string,
    userId: string,
    match: {
      cycleId: string;
      cycle: { id: string; intentEnvelope: unknown; tradeId: string | null };
      participantId: string;
      matchKind: 'cid' | 'fallback';
    },
    initialStatus: SignalCycleStatus,
  ): Promise<{ cycleId: string; participantId: string }> {
    const originTradeId = match.cycle.tradeId ?? 'unknown';
    const adoptedTradeId = `adopt:${originTradeId}:${Date.now()}`;
    const intentEnvelope = match.cycle.intentEnvelope as Prisma.InputJsonValue;

    // Fix 3 — adopted PENDING_ENTRY cycles are fill-or-expire: without an
    // expiresAt, monitorEntry's TTL branch never fires and the adopted
    // resting order lives forever (adopt:* tradeIds match nothing in showcase
    // state, so no showcase-closure path expires them either).
    const expiresAt =
      initialStatus === SignalCycleStatus.PENDING_ENTRY
        ? new Date(Date.now() + RECONCILE_ADOPT_ORDER_TTL_MS)
        : null;

    const cycle = await this.prisma.signalCycle.create({
      data: {
        agentId,
        tradeId: adoptedTradeId,
        status: initialStatus,
        intentEnvelope,
        researchVenue: 'bitfinex',
        expiresAt,
      },
    });

    const participant = await this.prisma.signalCycleParticipant.create({
      data: {
        cycleId: cycle.id,
        userId,
        venue: 'bitfinex',
        status: initialStatus,
      },
    });

    return { cycleId: cycle.id, participantId: participant.id };
  }

  /** Normal lot size at the current price for size-sanity guardrail. */
  private resolveNormalLotQty(marginCap: number, leverage: number, price: number): number {
    if (!price || price <= 0) return MIN_QTY_BTC;
    return computeQty(marginCap, leverage, price, MIN_QTY_BTC);
  }

  /**
   * Append an adoption audit entry to dashboardState.reconcileAdoptLog (ring
   * buffer, cap RECONCILE_ADOPT_LOG_CAP) and increment reconcileAdoptCount.
   * Best-effort — never aborts the tick on a dashboard patch failure.
   */
  private async persistReconcileAdoptAudit(
    instanceId: string,
    entry: Record<string, unknown>,
  ): Promise<void> {
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instanceId },
      select: { dashboardState: true },
    });
    if (!fresh) return;
    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;
    const priorLog = Array.isArray(dash.reconcileAdoptLog)
      ? (dash.reconcileAdoptLog as unknown[])
      : [];
    const priorCount = Number(dash.reconcileAdoptCount ?? 0);
    const adoptedKinds = new Set(['ADOPTED_POSITION', 'ADOPTED_ORDER']);
    const nextLog = [...priorLog, entry].slice(-RECONCILE_ADOPT_LOG_CAP);
    const nextCount = adoptedKinds.has(String(entry.kind))
      ? priorCount + 1
      : priorCount;
    await this.prisma.tradingAgentInstance.update({
      where: { id: instanceId },
      data: {
        dashboardState: applyDashboardPatch(dash, {
          reconcileAdoptLog: nextLog,
          reconcileAdoptCount: nextCount,
          reconcileAdoptLastAt: new Date().toISOString(),
        }) as unknown as Prisma.InputJsonValue,
      },
    });
  }
}
