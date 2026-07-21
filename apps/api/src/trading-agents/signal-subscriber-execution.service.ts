import { Injectable, Logger, NotFoundException, OnModuleInit } from '@nestjs/common';
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
  resolveMaxConcurrentCopySignals,
  resolveMirrorDisasterStopMarginPct,
  isCopyRelaySimActive,
  readCopyRelaySimState,
  COPY_RELAY_SIM_RECONCILE_ALERT_BTC,
  effectiveExchangeQtyBtc,
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
import { BitfinexTradingClient } from '../exchanges/bitfinex-api.client';
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
import { applyDashboardPatch } from './instance-view.mapper';
import { loadSubscriberMaxMarginUsd } from './subscriber-margin.util';
import { mapBotStateToAgentStats, normalizeBotSessionTrades, type BotApiState } from './bot-state.mapper';
import { resolveShowcaseTradeDetails, tradeIdsMatch } from './relay-fidelity.mapper';
import { NotificationsService } from '../notifications/notifications.service';

const AGENT_SLUG = 'conservative-btc';
const POLL_MS = resolveSubscriberExecutionPollMs();
const MIN_QTY_BTC = 0.00004;
const CHASE_INTERVAL_MS = SUBSCRIBER_CHASE_INTERVAL_MS ?? 60_000;
const CHASE_NEAR_FILL_INTERVAL_MS = SUBSCRIBER_CHASE_NEAR_FILL_INTERVAL_MS ?? 250;
const CHASE_BOT_ANCHOR_MS = SUBSCRIBER_SHOWCASE_ANCHOR_CHASE_MS ?? 250;
const SIGNED_SHOWCASE_FAST_PATH_MAX_AGE_MS = 15_000;
const DEFAULT_EXECUTOR_TICK_TIMEOUT_MS = 60_000;
const DEFAULT_EXECUTOR_HEALTH_MAX_AGE_MS = 15_000;

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
 * Restart the API only when a stuck executor may have an active live relay.
 * With zero ACTIVE Bitfinex instances, health stays fail-closed and future
 * starts are blocked, but the public API/dashboard must remain available.
 * A null count means persistence/query failed, so exposure is unknown and a
 * clean restart remains the safer choice.
 */
export function relayWatchdogShouldRestart(activeLiveInstanceCount: number | null): boolean {
  return activeLiveInstanceCount == null || activeLiveInstanceCount > 0;
}

/** Watchdog cleanup may cancel an entry only when the exchange reports zero fill. */
export function relayEntryOrderIsCompletelyUnfilled(order: {
  amountOrig: number;
  amount: number;
}): boolean {
  const filledQty = Math.max(0, Math.abs(order.amountOrig) - Math.abs(order.amount));
  return filledQty <= MIN_QTY_BTC;
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
  limitChaseCount?: number;
  fillPrice?: number;
  leverage?: number;
  stopLossMarginPct?: number;
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
  const qtyMissing = !stored.qty || stored.qty <= MIN_QTY_BTC;
  const directionMissing = !stored.direction;
  return (
    (!!resolved.qty && resolved.qty > MIN_QTY_BTC && qtyMissing) ||
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
    if (
      order.trade_id &&
      (order.status === 'PENDING' || order.status === 'ORDERED') &&
      (order.limit_price ?? 0) > 0
    ) {
      canonicalRank.set(order.trade_id, index);
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
  receivedAtMs: number;
};

type SignedShowcaseEnvelope = SignalIntentEnvelope & {
  trade_id?: string;
  entry: SignalIntentEnvelope['entry'] & {
    exact_limit_price?: number;
  };
  context: SignalIntentEnvelope['context'] & {
    signed_showcase_event?: boolean;
    showcase_event?: string;
    platform_received_at?: string;
  };
};

export function readFreshSignedShowcaseExactLimit(
  tradeId: string,
  envelopeJson: unknown,
  nowMs = Date.now(),
): SignedShowcaseExactLimit | null {
  const intent = envelopeJson as SignedShowcaseEnvelope | null;
  const receivedAtMs = Date.parse(String(intent?.context?.platform_received_at ?? ''));
  const limitPrice = Number(intent?.entry?.exact_limit_price ?? 0);
  const direction = intent?.direction;
  const event = String(intent?.context?.showcase_event ?? '');
  if (
    intent?.action !== 'ENTER'
    || intent?.context?.signed_showcase_event !== true
    || (event !== 'ORDER_PLACED' && event !== 'LIMIT_UPDATED')
    || (direction !== 'LONG' && direction !== 'SHORT')
    || !Number.isFinite(limitPrice)
    || limitPrice <= 0
    || !Number.isFinite(receivedAtMs)
    || receivedAtMs > nowMs + 5_000
    || nowMs - receivedAtMs > SIGNED_SHOWCASE_FAST_PATH_MAX_AGE_MS
  ) {
    return null;
  }
  return { tradeId, direction, limitPrice, receivedAtMs };
}

export function readSignedShowcaseClose(envelopeJson: unknown): {
  exitPrice?: number;
  exitReason?: string;
} | null {
  const context = (
    envelopeJson as {
      context?: {
        signed_showcase_event?: boolean;
        showcase_event?: string;
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
  return {
    ...(Number.isFinite(rawExitPrice) && rawExitPrice > 0
      ? { exitPrice: rawExitPrice }
      : {}),
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
 *  open positions for this many consecutive fresh bot states is market-closed
 *  (SHOWCASE_POSITION_ABSENT). Unlike SHOWCASE_VANISHED, this only checks
 *  positions — trades_map may still list PENDING/VIRTUAL_CHASE for a trade the
 *  copy filled but showcase never opened. Fail-closed on unreachable fetch. */
const SHOWCASE_POSITION_ABSENT_CONSECUTIVE_MISSES = 2;

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
};

type ExecutionTradingClient = BitfinexTradingClient | BitfinexSimTradingClient;

@Injectable()
export class SignalSubscriberExecutionService implements OnModuleInit {
  private readonly logger = new Logger(SignalSubscriberExecutionService.name);
  private readonly bitfinex = new BitfinexTradingClient();
  private activeTrading: ExecutionTradingClient;
  private readonly positionRuntime = new Map<string, PositionRuntime>();
  private readonly exitingLots = new Set<string>();
  /**
   * Dust sweep in-flight dedupe — keyed by `${userId}:${cycleId}`. Prevents
   * stacking duplicate market-close orders for the same dust lot within a
   * single tick window (matches the cancelStillLive L2104 defer pattern: a
   * failed sweep is logged and retried on the next tick, never re-thrown).
   * Cleared after each sweep attempt (success or failure).
   */
  private readonly dustSweepInFlight = new Set<string>();
  private running = false;
  private wakeQueued = false;
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
  private lastShowcaseWakeTrigger: 'POSITION_CLOSED' | 'ORDER_PLACED' | 'APPROVE_PENDING' | 'LIMIT_UPDATED' | null = null;

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
    setInterval(() => void this.watchExecutorLiveness(), 1_000).unref();
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
      const restartRequired = relayWatchdogShouldRestart(activeLiveInstanceCount);
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
          this.cancelVerifiedUnfilledEntriesAfterWatchdog(agent.id, instance),
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
  private async cancelVerifiedUnfilledEntriesAfterWatchdog(
    agentId: string,
    instance: TradingAgentInstance,
  ): Promise<void> {
    const creds = await this.exchanges.getUserCredentials(
      instance.userId,
      instance.exchangeProvider,
    );
    if (!creds) return;
    this.activeTrading = this.bitfinex;
    const pending = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId: instance.userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId },
      },
      select: { id: true, cycleId: true },
    });
    for (const participant of pending) {
      const meta = await this.loadExecutionMeta(participant.id);
      const orderId = meta.bitfinexOrderId;
      if (!orderId) continue;
      const order = await this.activeTrading.findOrder(creds, orderId).catch(() => null);
      if (!order) continue;
      const filledQty = Math.max(0, Math.abs(order.amountOrig) - Math.abs(order.amount));
      if (!relayEntryOrderIsCompletelyUnfilled(order)) {
        this.logger.warn(
          `Relay watchdog left partially filled entry ${orderId} untouched ` +
            `participant=${participant.id} filledQty=${filledQty.toFixed(8)}; restart reconciliation required`,
        );
        continue;
      }
      const cancelled = await this.cancelManagedOrderGone(
        creds,
        orderId,
        `Relay watchdog cancel verified-unfilled entry ${orderId} participant=${participant.id}`,
      );
      if (!cancelled.gone) {
        throw new Error(
          `CANCEL_FAILED_ORDER_STILL_LIVE order=${orderId} participant=${participant.id}`,
        );
      }
      this.logger.warn(
        `Relay watchdog cancelled verified-unfilled entry ${orderId} ` +
          `participant=${participant.id} cycle=${participant.cycleId}`,
      );
    }
  }

  /** Immediate execution wake from showcase bot push (coalesced if tick in flight). */
  async wakeNow(trigger?: 'POSITION_CLOSED' | 'ORDER_PLACED' | 'APPROVE_PENDING' | 'LIMIT_UPDATED' | 'USER_RESUME' | 'USER_PAUSE') {
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

      for (const row of instances) {
        this.currentInstanceId = row.id;
        this.currentStage = 'LOAD_INSTANCE';
        const instance =
          (await this.prisma.tradingAgentInstance.findUnique({ where: { id: row.id } })) ?? row;
        if (instance.exchangeProvider !== 'bitfinex') continue;
        const simActive = isCopyRelaySimActive(instance.dashboardState);

        // Live copy requires an active (non-expired) hire. Sim runs without one.
        if (!simActive && instance.expiresAt && instance.expiresAt.getTime() < now) {
          // Fix E — do not skip silently: the user's live copy is halted and
          // they should see why. Throttled to once per hour per instance.
          await this.surfaceExpiredHire(instance).catch(() => {
            /* surfacing is best-effort — never abort the tick */
          });
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

  private async processInstance(
    agentId: string,
    instance: TradingAgentInstance,
    simActive = false,
  ) {
    const exitOnly = instance.status === TradingAgentInstanceStatus.PAUSED && !simActive;
    const venue = simActive ? 'bitfinex_sim' : 'bitfinex';
    const simState = simActive ? readCopyRelaySimState(instance.dashboardState) : null;
    const participantSince =
      simState?.startedAt != null ? { createdAt: { gte: new Date(simState.startedAt) } } : {};
    const creds = await this.exchanges.getUserCredentials(instance.userId, instance.exchangeProvider);
    if (!creds) {
      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: { lastError: 'Exchange credentials missing — re-hire with API keys' },
      });
      return;
    }

    const marginCap = await loadSubscriberMaxMarginUsd(this.prisma);

    let activeOrderIdSet = new Set<number>();
    if (instance.exchangeProvider === 'bitfinex') {
      try {
        const funding = await this.activeTrading.ensureDerivativesMargin(creds, marginCap);
        if (funding.message && funding.transferredUsd > 0) {
          this.logger.log(`Instance ${instance.userId}: ${funding.message}`);
        }
        await this.cancelAbsurdPendingOrders(creds, instance.userId);
        const activeOrders = await this.activeTrading.listActiveOrders(creds);
        activeOrderIdSet = new Set(activeOrders.map((o) => o.id));
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`Bitfinex prep ${instance.userId}: ${msg}`);
      }
    }

    await this.reconcileFilledParticipants(
      instance.userId,
      agentId,
      participantSince,
      instance.exchangeProvider === 'bitfinex' ? creds : undefined,
      instance.exchangeProvider === 'bitfinex' ? activeOrderIdSet : undefined,
    );

    await this.reconcileGhostOpenLots(
      agentId,
      instance.userId,
      participantSince,
      marginCap,
    );

    await this.reconcileImmediateExchangeFlat(agentId, instance.userId, creds, participantSince);

    // Phase 2 — Layer B (NestJS Live Copy) reconcile-adopt pass. Re-arms
    // protective stops for OPEN participants whose meta.stopOrderId died
    // (filled or cancelled) on restart, re-hydrates positionRuntime so
    // monitorOpenPosition resumes Scenario C mirroring next tick, and
    // surfaces PENDING_ENTRY / OPEN participants missing critical meta
    // into dashboardState.orphanPositionIds for manual decision. Gated
    // by RECONCILE_WRITE_WINDOW for the stop re-arm write itself.
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

    const MIN_INTENT_TTL_MS = 90_000;

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

    await this.reconcileUnattributedExchangeFills(
      agentId,
      instance.userId,
      creds,
      activeOrderIdSet,
      participantSince,
    );

    let managedOpenTrade = false;

    // Pass 1 — manage existing copy trades (fills, stops, exits) before any new entries.
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
    }

    await this.reconcileLotLedger(
      agentId,
      instance,
      openParticipantAfter,
      creds,
      managedOrderIds,
      simActive,
    );

    const lotSummary = await this.buildVirtualLotSummary(openParticipantAfter);

    // Phase 6 fix 3 — per-tick orphan surfacer. Runs every tick (after
    // reconcileLotLedger / reconcileAdoptOrphans) so dashboardState.orphanOrderIds
    // stays current even when the bot is idle and no new signal arrives. The
    // fetched foreign orders are handed to cleanupOrphanCopyOrders (fix 4) so we
    // don't double-query the exchange. evaluateEntryEligibility now READS the
    // persisted orphanOrderIds instead of re-querying listActiveOrders.
    const foreignOrphanOrders = await this.surfaceOrphanOrders(instance, creds, managedOrderIds);

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
      this.clearShowcaseUnreachable(instance.id);
      if (isBenignShowcaseEntryWait(instance.lastError)) {
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

    // Phase 0 — shadow-diff observability. Compares the showcase book (from
    // the state already fetched above) against the copy's ledger. Pure
    // observability: no exchange calls, no behavior change, never throws.
    await this.recordMirrorDiff(
      agentId,
      instance,
      canonicalSignedBook,
      openParticipantAfter,
      execMetaById,
    ).catch((err) => {
      this.logger.warn(
        `[MIRROR-DIFF] snapshot failed ${instance.userId}: ${err instanceof Error ? err.message : err}`,
      );
    });

    if (exitOnly) {
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
      exitOnly,
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
      if (!eligibility.canEnter) {
        await this.prisma.tradingAgentInstance.update({
          where: { id: instance.id },
          data: { lastError: eligibility.reason },
        });
      } else {
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
    bot: { orders?: Array<{ trade_id?: string; limit_price?: number; status?: string }> } | null,
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
      (o) => o.trade_id === tradeId && (o.status === 'PENDING' || o.status === 'ORDERED'),
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

    const pending = this.showcasePendingOrder(bot, tradeId);
    if (pending?.limit_price && pending.limit_price > 0) return { abandoned: false };

    // Showcase already filled this trade — keep the copy limit alive so it can
    // still fill and mirror; do not treat a missing pending as abandon.
    const showcasePos = (bot.positions ?? []).find(
      (p) => String(p.trade_id ?? '') === tradeId,
    );
    if (showcasePos) return { abandoned: false };

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
    const copyTradeIds = new Set(
      participants
        .map((p) => p.cycle?.tradeId)
        .filter((t): t is string => typeof t === 'string' && t.length > 0),
    );
    const copyOpenTradeIds = new Set(
      copyOpen
        .map((p) => p.cycle?.tradeId)
        .filter((t): t is string => typeof t === 'string' && t.length > 0),
    );
    const copyPendingTradeIds = new Set(
      copyPending
        .map((p) => p.cycle?.tradeId)
        .filter((t): t is string => typeof t === 'string' && t.length > 0),
    );

    type MirrorDiffDivergence = {
      type:
        | 'PRICE_DELTA'
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
      showcaseDir?: string;
      copyDir?: string;
    };
    const divergences: MirrorDiffDivergence[] = [];

    // Copy resting orders vs showcase pending limits (per-order price delta).
    for (const p of copyPending) {
      const meta = metaById.get(p.id);
      const tradeId = p.cycle?.tradeId ?? undefined;
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
      });
    }
    for (const p of copyOpen) {
      const tradeId = p.cycle?.tradeId ?? undefined;
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

    // Rolling counters (per instance, persisted in dashboardState.mirrorDiff.rolling).
    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instance.id },
      select: { dashboardState: true },
    });
    if (!fresh) return;
    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;
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
      divergentTicks: (prev.rolling?.divergentTicks ?? 0) + (divergences.length > 0 ? 1 : 0),
      lastDivergenceAt:
        divergences.length > 0
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
      const stillDiverged = divergences.some(
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
    for (const d of divergences) byType[d.type] = (byType[d.type] ?? 0) + 1;

    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        dashboardState: applyDashboardPatch(dash, {
          mirrorDiff: {
            at: new Date(now).toISOString(),
            botStateSource: botState.snapshot_source ?? 'live_bot',
            showcasePendingOrders: showcaseOrders.length,
            showcaseOpenPositions: showcasePositions.length,
            copyPendingOrders: copyPending.length,
            copyOpenLots: copyOpen.length,
            divergences: divergences.slice(0, 20),
            counts: { total: divergences.length, byType },
            rolling,
          },
        }) as unknown as Prisma.InputJsonValue,
      },
    });

    // Throttled MIRROR_DIFF event per diverged participant (max 1/60s each).
    for (const d of divergences) {
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

  private async buildVirtualLotSummary(
    participants: Array<{ id: string; status: SignalCycleStatus }>,
  ): Promise<VirtualLotSummary> {
    let open = 0;
    let pending = 0;
    let openQty = 0;
    let direction: 'LONG' | 'SHORT' | null = null;

    for (const p of participants) {
      const meta = await this.loadExecutionMeta(p.id);
      let qty = meta.qty ?? 0;
      if (qty <= MIN_QTY_BTC && meta.margin_usd && meta.limitPrice) {
        qty = computeQty(
          meta.margin_usd,
          resolveSubscriberLeverage(),
          meta.limitPrice,
          MIN_QTY_BTC,
        );
      }
      if (!meta.direction) continue;
      if (direction != null && meta.direction !== direction) continue;
      direction = meta.direction;
      if (p.status === SignalCycleStatus.OPEN) {
        open += 1;
        openQty += qty;
      } else if (p.status === SignalCycleStatus.PENDING_ENTRY) {
        pending += 1;
      }
    }

    return { open, pending, direction, openQty };
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
      select: { dashboardState: true },
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
        dashboardState: applyDashboardPatch(dash, {
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
  ): Promise<Array<{ id: number; amount?: number; amountOrig?: number; price?: number; status?: string; orderType?: string; cid?: number; createdAtMs?: number }>> {
    if (instance.exchangeProvider !== 'bitfinex') return [];
    const orders = await this.activeTrading.listActiveOrders(creds).catch((err) => {
      this.logger.warn(
        `Orphan surfacer ${instance.userId}: listActiveOrders failed: ${err instanceof Error ? err.message : err}`,
      );
      return [] as Awaited<ReturnType<typeof this.activeTrading.listActiveOrders>>;
    });
    const foreign = orders.filter((o) => !managedOrderIds.has(o.id));
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
    simActive: boolean,
  ) {
    const venue = simActive ? 'bitfinex_sim' : 'bitfinex';
    const summary = await this.buildVirtualLotSummary(participants);
    const position = await this.activeTrading.getOpenPositionDetail(creds).catch(() => null);
    const exchangeQty = position ? effectiveExchangeQtyBtc(position.amount) : 0;
    const ledgerOpenQty = summary.openQty;
    const delta = exchangeQty - ledgerOpenQty;
    const mark = await this.activeTrading.getMarkPrice().catch(() => null);

    const reconcile = this.relaySim.buildReconcileSnapshot({
      exchangePositionQty: exchangeQty,
      ledgerOpenQty,
      openLots: summary.open,
      pendingLots: summary.pending,
      markPrice: mark,
    });

    const fresh = await this.prisma.tradingAgentInstance.findUnique({
      where: { id: instance.id },
      select: { dashboardState: true },
    });
    if (!fresh) return;
    const dash = (fresh.dashboardState ?? {}) as Record<string, unknown>;
    const simActiveNow = isCopyRelaySimActive(dash);
    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        dashboardState: applyDashboardPatch(dash, {
          copyRelayReconcile: reconcile,
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

    // Dust sweep — aggregate position check. If the merged exchange position is
    // below MIN_QTY_BTC but non-zero, sweep it via market close so the user
    // doesn't have to manually flatten a sub-threshold sliver. This catches
    // dust that the per-participant reconcileManualClose path missed (e.g. all
    // lots already CLOSED on the ledger but the exchange still shows residual
    // qty from rounding / partial fills). Per-participant dust is handled in
    // reconcileManualClose; this is the aggregate backstop. Wrapped in
    // try/catch — never throws out of the reconcile loop.
    if (exchangeQty > 0 && exchangeQty < MIN_QTY_BTC) {
      try {
        await this.sweepAggregateDust(
          agentId,
          instance,
          participants,
          creds,
          position,
          exchangeQty,
        );
      } catch (err) {
        this.logger.warn(
          `Aggregate dust sweep ${instance.userId}: unexpected error — ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    if (Math.abs(delta) <= MIN_QTY_BTC) return;

    this.logger.warn(
      `Virtual lot drift ${instance.userId}: exchange ${exchangeQty.toFixed(5)} BTC vs ledger ${ledgerOpenQty.toFixed(5)} BTC (Δ ${delta.toFixed(5)}, ${summary.open} open)`,
    );

    if (delta > MIN_QTY_BTC) {
      const pendingCount = summary.pending;
      if (pendingCount === 0 && summary.open === 0) {
        if (simActive && position) {
          try {
            await this.activeTrading.submitMarketClose(creds, {
              symbol: position.symbol,
              positionDirection: position.direction,
              qty: exchangeQty,
            });
            this.logger.warn(
              `Sim orphan exchange heal ${instance.userId}: flattened ${exchangeQty.toFixed(5)} BTC paper position (ledger empty)`,
            );
            await this.persistSimTickState(agentId, instance);
            await this.prisma.tradingAgentInstance.update({
              where: { id: instance.id },
              data: { lastError: null },
            });
            return;
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            this.logger.error(`Sim orphan flatten failed ${instance.userId}: ${msg}`);
          }
        }
        await this.prisma.tradingAgentInstance.update({
          where: { id: instance.id },
          data: {
            lastError: `UNATTRIBUTED_EXCHANGE_EXPOSURE: exchange ${exchangeQty.toFixed(5)} BTC with no ledger lots — manual add detected; relay will not synthesize lots.`,
          },
        });
      } else {
        await this.reconcileUnattributedExchangeFills(
          agentId,
          instance.userId,
          creds,
          managedOrderIds,
        );
      }
    }

    if (delta < -MIN_QTY_BTC) {
      await this.closeOrphanLedgerLots(
        agentId,
        instance.userId,
        Math.abs(delta),
        participants,
        creds,
        venue,
      );
    }

    if (reconcile.alert) {
      this.cycleAudit.stage('RECONCILE', {
        userId: instance.userId,
        agentId,
        detail: `exchange ${exchangeQty.toFixed(5)} ledger ${ledgerOpenQty.toFixed(5)} Δ ${delta.toFixed(5)}`,
        meta: { reconcile },
      });
      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: {
          lastError: `RECONCILE ALERT: exchange ${exchangeQty.toFixed(4)} BTC ≠ ledger ${ledgerOpenQty.toFixed(4)} BTC (Δ ${delta.toFixed(4)})`,
        },
      });
    }
  }

  private async closeOrphanLedgerLots(
    agentId: string,
    userId: string,
    excessBtc: number,
    participants: Array<{ id: string; status: SignalCycleStatus }>,
    creds: ExchangeCredentials,
    venue = 'bitfinex',
  ) {
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
        const filledQty =
          (orderResting.amountOrig ?? 0) - (orderResting.amount ?? 0);
        if (filledQty > MIN_QTY_BTC) {
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
  ): Promise<EntryEligibility> {
    let available = 0;
    try {
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
    if (instance?.id) {
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
    const meta = await this.loadExecutionMeta(participant.id);
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
    tradeId?: string,
    venue = 'bitfinex',
  ): Promise<boolean> {
    const intent = envelopeJson as SignalIntentEnvelope;
    if (!intent?.direction || intent.action !== 'ENTER') return false;
    const signedExactLimit = tradeId
      ? readFreshSignedShowcaseExactLimit(tradeId, envelopeJson)
      : null;

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

    const intentCap = intent.risk?.max_margin_usd;
    const effectiveCap =
      intentCap != null && Number.isFinite(intentCap) && intentCap > 0
        ? Math.min(marginCap, intentCap)
        : marginCap;

    let available = 0;
    try {
      available = await this.activeTrading.getDerivativesAvailableUsd(creds);
    } catch (err) {
      this.logger.warn(
        `Hire skip ${instance.userId} cycle=${cycleId}: Derivatives balance check failed — ${err instanceof Error ? err.message : err}`,
      );
      return false;
    }

    const marginUsd = Math.min(effectiveCap, available * 0.95);
    if (marginUsd < effectiveCap * 0.9) {
      this.logger.log(
        `Hire skip ${instance.userId} cycle=${cycleId}: free margin $${available.toFixed(2)} < $${effectiveCap} required`,
      );
      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: {
          lastError: `Insufficient Derivatives margin ($${available.toFixed(2)} available, need ~$${effectiveCap}). Move USDT to Derivatives in Bitfinex.`,
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
    const existingClaim = await this.prisma.signalCycleParticipant.findUnique({
      where: { cycleId_userId: { cycleId, userId: instance.userId } },
    });
    if (existingClaim) {
      // A participant already exists (race with another replica, or a prior claim) — do
      // NOT place another order; the existing claim/participant owns this cycle.
      this.logger.log(
        `Hire skip ${instance.userId} cycle=${cycleId}: participant already exists (status=${existingClaim.status})`,
      );
      return false;
    }
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
          `Hire claim-lost ${instance.userId} cycle=${cycleId} — another replica is placing it`,
        );
        return false;
      }
      throw err;
    }
    const mark = await this.activeTrading.getMarkPrice();
    const rawLimit =
      signedExactLimit?.limitPrice
      ?? computeLimitFromMark(mark, intent.entry.offset_pct);
    let limitPrice = sanitizeLimitPrice(mark, rawLimit, intent.direction);
    if (limitPrice == null) {
      this.logger.error(
          `Hire reject ${instance.userId} cycle=${cycleId}: absurd limit ${rawLimit.toFixed(2)} vs mark ${mark.toFixed(2)} offset=${intent.entry.offset_pct}%`,
        );
        await this.prisma.tradingAgentInstance.update({
          where: { id: instance.id },
          data: {
            lastError: `Entry limit ${rawLimit.toFixed(0)} rejected — price sanity check failed (mark ~$${mark.toFixed(0)}). Signal will retry on next cycle.`,
          },
        });
        return false;
    }

      let botStateForEntry: BotApiState | null = null;
      if (tradeId && !signedExactLimit && !(await this.botBridge.isEnabledAsync())) {
        await this.prisma.tradingAgentInstance.update({
          where: { id: instance.id },
          data: { lastError: 'Showcase bridge unavailable — exact-copy entry blocked.' },
        });
        if (claimParticipantId) {
          await this.prisma.signalCycleParticipant
            .delete({ where: { id: claimParticipantId } })
            .catch(() => {});
          claimParticipantId = null;
        }
        return false;
      }
      if (tradeId && !signedExactLimit) {
        botStateForEntry = await this.fetchExecutionBotState();
        if (!botStateForEntry) {
          await this.prisma.tradingAgentInstance.update({
            where: { id: instance.id },
            data: { lastError: 'Showcase state unavailable — exact-copy entry blocked.' },
          });
          if (claimParticipantId) {
            await this.prisma.signalCycleParticipant
              .delete({ where: { id: claimParticipantId } })
              .catch(() => {});
            claimParticipantId = null;
          }
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
          if (claimParticipantId) {
            await this.prisma.signalCycleParticipant
              .delete({ where: { id: claimParticipantId } })
              .catch(() => {});
            claimParticipantId = null;
          }
          return false;
        }
        const botLimit = this.resolveBotLimitPrice(botStateForEntry, tradeId);
        if (botLimit != null && botLimit > 0) {
          const anchored = sanitizeLimitPrice(mark, botLimit, intent.direction);
          if (anchored != null) limitPrice = anchored;
        }
      } else if (signedExactLimit) {
        botStateForEntry = mergeSignedShowcaseOrders(null, [signedExactLimit]);
        this.logger.log(
          `[SIGNED-FAST] exact showcase limit accepted trade=${tradeId} limit=${signedExactLimit.limitPrice.toFixed(2)} ageMs=${Date.now() - signedExactLimit.receivedAtMs}`,
        );
      }

      // Phase 1 — book-state dedupe (flag-gated). If the copy already has a
      // real resting order at this limit price (any lane/participant), do NOT
      // place a second: the earlier participant is the mirror owner of this
      // book entry. Expire this claim ledger-side WITHOUT touching the
      // exchange (mirrors the showcase book's own DUPLICATE_LIMIT_PRICE).
      if (mirrorConvergenceEnabled()) {
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

      const leverage = resolveSubscriberLeverage(intent);
      const qty = computeQty(marginUsd, leverage, limitPrice, MIN_QTY_BTC);

    const prePosition = await this.activeTrading.getOpenPositionDetail(creds).catch(() => null);
    const exchangeQtyAtOrder = prePosition ? Math.abs(prePosition.amount) : 0;

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
      ...(clientOrderId != null ? { clientOrderId } : {}),
    };

    await this.cycles.recordHireExecutionEvent(instance.userId, agentId, cycleId, 'ORDER_PLACED', {
      venue,
      local_mark_at_signal: mark,
      limit_price: limitPrice,
        original_limit_price: limitPrice,
      qty,
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
   * non-"gone" failure — verifies with {@link confirmOrderGone} whether the
   * order is actually still on the active book. Returns `gone: true` when the
   * cancel succeeded OR a follow-up `findOrder` confirms the order is no
   * longer active; ONLY in that case may the caller record an EXPIRED ledger
   * event. Returns `gone: false` when the cancel failed AND the order is
   * confirmed still live — caller must leave the participant PENDING_ENTRY,
   * set `instance.lastError = 'CANCEL_FAILED_ORDER_STILL_LIVE'`, audit
   * `RECONCILE_CANCEL_FAILED`, and let the next tick retry.
   */
  private async cancelManagedOrderGone(
    creds: ExchangeCredentials,
    orderId: number,
    label: string,
  ): Promise<{ gone: boolean; reason?: string; attempts: number }> {
    const client = this.activeTrading as CancelCapableClient;
    const result = await cancelOrderWithRetry(client, creds, orderId, {
      logger: this.logger,
      label,
    });
    if (result.ok) {
      return { gone: true, reason: result.reason, attempts: result.attempts };
    }
    const gone = await confirmOrderGone(client, creds, orderId);
    return { gone, reason: result.reason, attempts: result.attempts };
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
  ): Promise<{
    filledQty: number;
    fillPrice: number;
    source: 'ORDER_PARTIAL' | 'POSITION_DELTA';
    orderResting: boolean;
  } | null> {
    const orderId = meta.bitfinexOrderId;
    if (!orderId || !meta.direction) return null;

    const order = await this.activeTrading.findOrder(creds, orderId).catch(() => null);
    if (order) {
      const filled = Math.abs(order.amountOrig) - Math.abs(order.amount);
      if (filled > MIN_QTY_BTC) {
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
    const position = await this.activeTrading.getOpenPositionDetail(creds).catch(() => null);
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
  private async resolveExchangeTradesFillPrice(
    creds: ExchangeCredentials,
    orderId: number | null | undefined,
  ): Promise<number | null> {
    if (!orderId) return null;
    try {
      const trades = await this.bitfinex.fetchOrderTrades(creds, orderId);
      let qtySum = 0;
      let notional = 0;
      for (const t of trades) {
        const qty = Math.abs(t.execAmount);
        if (!(qty > 0) || !(t.execPrice > 0)) continue;
        qtySum += qty;
        notional += qty * t.execPrice;
      }
      if (qtySum <= 0) return null;
      return notional / qtySum;
    } catch (err) {
      this.logger.warn(
        `fetchOrderTrades ${orderId} failed (falling back to approximate fill price): ${err instanceof Error ? err.message : err}`,
      );
      return null;
    }
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
    cycle: { id: string; status: SignalCycleStatus },
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
  ): Promise<boolean> {
    if (!meta.direction) return false;

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

    // Fix D — the order (partially) executed, so its trade history now holds
    // the REAL volume-weighted fill price; the heuristic price (limit price /
    // merged basePrice) stays as fallback.
    const exchangeFillPrice = await this.resolveExchangeTradesFillPrice(
      creds,
      meta.bitfinexOrderId,
    );
    const fillPrice =
      exchangeFillPrice ??
      (fill.fillPrice > 0
        ? fill.fillPrice
        : await this.activeTrading.getMarkPrice().catch(() => meta.limitPrice ?? 0));
    if (!fillPrice || fillPrice <= 0) return false;
    const qty = fill.filledQty;
    const leverage = resolveSubscriberLeverage(intent);
    const stopLossMarginPct = resolveEffectiveStopLossMarginPct(intent?.risk?.stop_loss_margin_pct, {
      mirrorMode: isShowcaseMirrorOnlyMode(),
    });
    const stopPrice = computeStopPrice(fillPrice, meta.direction, stopLossMarginPct, leverage);

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
      fill_price_source: exchangeFillPrice != null ? 'exchange_trades' : undefined,
      qty,
      stop_loss_placed: stopOrderId != null,
      stop_loss_margin_pct: stopLossMarginPct,
      stopOrderId: stopOrderId ?? undefined,
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
    const intent = cycle.intentEnvelope as SignalIntentEnvelope;
    const orderId = meta.bitfinexOrderId;
    if (!orderId || !meta.direction) return;

    if (participant.status === SignalCycleStatus.OPEN) {
      await this.ensureProtectiveStop(agentId, userId, cycle.id, participant.id, meta, creds, intent);
      return;
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

    if (this.botBridge.isEnabled() && cycle.tradeId) {
      const botState = await this.fetchExecutionBotState();
      const abandon = this.showcaseEntryAbandoned(botState, cycle.tradeId);
      if (abandon.abandoned) {
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

    if (cycle.expiresAt && cycle.expiresAt < new Date()) {
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
      if (exitOnly) return;
      await this.applyLimitChase(
        agentId,
        userId,
        cycle.id,
        participant.id,
        meta,
        creds,
        intent,
        cycle.tradeId,
        false,
      );
      return;
    }

    // Fix 7b — distinguish a transient position-fetch failure from a genuine
    // no-position state. Classifying on a failed fetch recorded EXPIRED while
    // the fill was real (orphan factory). Fail-closed: defer to next tick.
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

    this.logger.log(
      `Hire fill ${userId} cycle=${cycle.id} @ ${fillPrice.toFixed(2)} qty=${qty} stop=${stopPrice.toFixed(2)} armed=${stopOrderId != null}`,
    );
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
      if (meta.qty && meta.qty > MIN_QTY_BTC && meta.direction) continue;

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
    const stuck = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId },
        events: { some: { eventType: 'FILLED' } },
        ...participantScope,
      },
      include: {
        events: {
          where: { eventType: 'FILLED' },
          orderBy: { createdAt: 'desc' },
          take: 1,
        },
      },
    });

    for (const row of stuck) {
      const payload = row.events[0]?.payload as { fill_price?: number } | null;
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
      await this.reconcileCancelByExchange(userId, agentId, creds, activeOrderIdSet, participantScope);
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
  ) {
    // Only consider participants older than 120s so we don't race with order
    // placement (the entry limit may take a few seconds to land on the book).
    const candidates = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId },
        // NOT having a FILLED event is the discriminator — a FILLED row that
        // vanished from the book is healed by healStuckPendingFill above.
        events: { none: { eventType: 'FILLED' } },
        updatedAt: { lt: new Date(Date.now() - 120_000) },
        ...participantScope,
      },
      include: {
        events: {
          where: { eventType: 'FILLED' },
          take: 1,
        },
        cycle: { select: { id: true, status: true, intentEnvelope: true } },
      },
    });

    for (const row of candidates) {
      // Still has a FILLED event? skip (covered by healStuckPendingFill).
      if (row.events.length > 0) continue;
      const meta = await this.loadExecutionMeta(row.id);
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

      // Cancel-race fill check (always on — misclassification is a bug, not a
      // feature flag): a vanished order is NOT necessarily cancelled — it may
      // have FILLED. Check the merged-position delta before declaring
      // RECONCILE_CANCEL_BY_EXCHANGE; a real fill is recorded as FILLED at
      // the real price instead of a $0 close that orphans the position slice
      // (the orphan-adoption loss factory).
      {
        const fill = await this.detectEntryFillBeforeCancel(creds, meta).catch(() => null);
        if (fill) {
          const intent = (row.cycle?.intentEnvelope ?? null) as SignalIntentEnvelope | null;
          const recorded = await this.recordCancelRaceFill(
            agentId,
            userId,
            { id: row.cycleId, status: row.cycle?.status ?? SignalCycleStatus.PENDING_ENTRY },
            row.id,
            meta,
            creds,
            intent,
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
    }

    // Phase 6 fix 5 — defensive re-cancel of the inverse case: a participant
    // already marked EXPIRED (or CLOSED with no fill) whose `meta.bitfinexOrderId`
    // is STILL on the exchange's active book. This is the orphan-by-ledger state
    // the original cancel-on-expiry path created by swallowing cancel failures.
    // Re-attempt the cancel with retry + loud-fail, and only consider the orphan
    // resolved once findOrder confirms the order is gone. Audit
    // RECONCILE_RECANCEL_EXPIRED_STILL_LIVE on every re-attempt so the operator
    // can see the ledger/exchange drift being healed.
    const expiredCandidates = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: { in: [SignalCycleStatus.EXPIRED, SignalCycleStatus.CLOSED] },
        cycle: { agentId },
        events: { none: { eventType: 'FILLED' } },
        updatedAt: { lt: new Date(Date.now() - 120_000) },
        ...participantScope,
      },
      select: { id: true, cycleId: true, status: true, venue: true },
    });

    for (const row of expiredCandidates) {
      const meta = await this.loadExecutionMeta(row.id);
      const oid = meta.bitfinexOrderId;
      if (oid == null) continue;
      // Only act if the order is confirmed still on the active book.
      if (!activeOrderIdSet.has(oid)) continue;

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
  ) {
    if (!meta.direction) return;
    const qty = meta.qty ?? MIN_QTY_BTC;
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
        source: 'hire',
      });
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
      /** Showcase trade id — included in the cid hash so chase replacement
       *  orders share the same deterministic clientOrderId as the entry. */
      tradeId?: string | null;
    },
  ) {
    if (!meta.direction || !meta.bitfinexOrderId || !meta.limitPrice) return;

    const leverage = resolveSubscriberLeverage(intent);
    const qty = meta.qty ?? computeQty(20, leverage, opts.newLimit, MIN_QTY_BTC);
    const clientOrderId = computeClientOrderId(cycleId, participantId, opts.tradeId);

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
      const filledQty = Math.abs(resting.amountOrig) - Math.abs(resting.amount);
      if (filledQty > MIN_QTY_BTC) {
        await this.recordCancelRaceFill(
          agentId,
          userId,
          // Chase runs only on non-terminal cycles — no status restore needed.
          { id: cycleId, status: SignalCycleStatus.PENDING_ENTRY },
          participantId,
          meta,
          creds,
          intent,
          {
            filledQty,
            fillPrice: resting.price > 0 ? resting.price : meta.limitPrice,
            source: 'ORDER_PARTIAL',
            orderResting: true,
          },
          'LIMIT_CHASE_REPLACE',
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
        limitChaseCount: chaseCount,
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
    const tid = cycle.tradeId ?? null;
    if (tid?.startsWith('adopt:')) {
      if (meta.originTradeId) return meta.originTradeId;
      const parts = tid.split(':');
      if (parts.length >= 2 && parts[1] && parts[1] !== 'unknown') return parts[1];
      return null;
    }
    return tid;
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
  ): Promise<boolean> {
    if (simActive) return false;
    if (!mirrorExitConvergenceEnabled() || !isShowcaseMirrorOnlyMode()) return false;

    const showcaseTradeId = this.resolveShowcaseMirrorTradeId(cycle, meta);
    const mirrorRelinked = (cycle.tradeId ?? '').startsWith('adopt:');

    let closed =
      cycle.status === SignalCycleStatus.CLOSED || cycle.status === SignalCycleStatus.EXPIRED;
    let showcaseExitPrice: number | undefined;
    let showcaseExitReason: string | undefined;
    let mirrorTrigger = mirrorRelinked ? 'ORIGIN_SHOWCASE_CLOSED' : 'SHOWCASE_CLOSED';
    const signedClose = readSignedShowcaseClose(cycle.intentEnvelope);
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
          `Showcase position absent ${userId} cycle=${cycle.id} trade=${showcaseTradeId} — market-closing copy lot after ${SHOWCASE_POSITION_ABSENT_CONSECUTIVE_MISSES} consecutive fresh states without an open showcase position`,
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
      mirrorRelinked?: boolean;
      trigger?: string;
      /** Bypass hasParticipantExited (stale RECONCILE_CANCEL EXIT events). */
      forceMirrorExit?: boolean;
    },
  ): Promise<boolean> {
    if (!opts?.forceMirrorExit && (await this.hasParticipantExited(participant.id))) return true;
    if (this.exitingLots.has(participant.id)) return true;

    this.exitingLots.add(participant.id);
    try {
      await this.cancelLinkedPendingLimits(creds, meta, new Set());

      const fillPrice =
        participant.fillPrice != null
          ? Number(participant.fillPrice)
          : meta.limitPrice ?? meta.fillPrice ?? 0;

      const position = await this.activeTrading.getOpenPositionDetail(creds).catch(() => null);
      if (!position || Math.abs(position.amount) < MIN_QTY_BTC) {
        const exitPrice =
          opts?.showcaseExitPrice ??
          (await this.activeTrading.getMarkPrice().catch(() => fillPrice || 0));
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
        this.showcaseFlatOpenSince.delete(participant.id);
        this.logger.log(
          `Exit already flat ${userId} cycle=${cycle.id} — showcase mirror recorded pnl=$${pnlUsd.toFixed(2)} source=${pnlSource}`,
        );
        return true;
      }

      if (!meta.qty || !meta.direction) return false;

      let exitPrice = fillPrice ?? 0;
      const leverage =
        resolveSubscriberLeverage(cycle.intentEnvelope as SignalIntentEnvelope);
      try {
        if (meta.stopOrderId) {
          try {
            await this.activeTrading.cancelOrder(creds, meta.stopOrderId);
          } catch {
            /* may have fired */
          }
        }
        exitPrice = await this.activeTrading.getMarkPrice();
        const closeQty = position
          ? Math.min(meta.qty, Math.abs(position.amount))
          : meta.qty;
        if (closeQty >= MIN_QTY_BTC) {
          await this.activeTrading.submitMarketClose(creds, {
            positionDirection: meta.direction,
            qty: closeQty,
            leverage,
          });
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(`Market close ${userId} cycle=${cycle.id}: ${msg}`);
      }

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

      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXIT', {
        venue: 'bitfinex',
        exit_price: exitPrice,
        exit_reason: 'SHOWCASE_MIRROR',
        showcase_exit_price: opts?.showcaseExitPrice,
        exit_slippage_usd: exitSlippageUsd,
        showcase_exit_reason: opts?.showcaseExitReason,
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
    const marginUsd = Math.min(effectiveCap, available * 0.95);
    if (marginUsd < effectiveCap * 0.9) {
      await releaseClaim();
      return false;
    }

    const leverage = resolveSubscriberLeverage(intent);
    const qty = computeQty(marginUsd, leverage, mark, MIN_QTY_BTC);
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
        direction: intent.direction,
        leverage,
        margin_usd: marginUsd,
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
      direction: intent.direction,
      leverage,
      margin_usd: marginUsd,
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
   * approved `cont-`, `tbhv1-`, or `srmv2s-` INTENT cycle. Entry is fail-closed until
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
   * Limit price source (decision §8 #4): the webhook payload's signal_price
   * ± pullback_pct (forwarded in the cycle's intentEnvelope). Falls back to
   * the mark only if the payload lacked signal_price — never to a stale bot
   * limit, which would diverge from the showcase's actual signal.
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
        entry?: { signal_price?: number; pullback_pct?: number };
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

      // Decision §8 #4 — limit price from the webhook's signal_price ± pullback.
      // Fall back to the live mark ONLY if the envelope lacks signal_price.
      const sigPriceRaw = intent.entry?.signal_price;
      const pullbackPctRaw = intent.entry?.pullback_pct;
      const sigPrice = typeof sigPriceRaw === 'number' && Number.isFinite(sigPriceRaw) && sigPriceRaw > 0
        ? sigPriceRaw
        : null;
      const pullback = typeof pullbackPctRaw === 'number' && Number.isFinite(pullbackPctRaw)
        ? Math.max(0, Math.min(0.05, pullbackPctRaw))
        : 0.001;

      let limitPrice: number;
      if (sigPrice != null) {
        limitPrice =
          intent.direction === 'LONG'
            ? sigPrice * (1 - pullback)
            : intent.direction === 'SHORT'
              ? sigPrice * (1 + pullback)
              : sigPrice;
      } else {
        // No signal_price in payload — use live mark (best-effort; rare since
        // emit_signal_webhook always forwards signal_price).
        const mark = await this.activeTrading.getMarkPrice().catch(() => 0);
        if (!mark || mark <= 0) continue;
        limitPrice = mark;
      }
      const sanitized = sanitizeLimitPrice(limitPrice, limitPrice, intent.direction);
      if (sanitized == null) {
        this.logger.warn(
          `[INTENT-MIRROR] rejected absurd limit ${limitPrice.toFixed(2)} trade=${tid} user=${instance.userId}`,
        );
        continue;
      }
      limitPrice = sanitized;

      // §8 #3 — platform cap always wins. marginCap (subscriberMaxMarginUsd)
      // is the ceiling; the envelope's margin_usdt is informational only.
      const leverage = resolveSubscriberLeverage(intent);
      const qty = computeQty(marginCap, leverage, limitPrice, MIN_QTY_BTC);

      // N4 — dry-run mode. Log the would-be order + audit row, no exchange call.
      if (intentMirrorDryRunActive(instance)) {
        const dryPayload = {
          venue,
          source: 'hire',
          trade_id: tid,
          direction: intent.direction,
          limit_price: Math.round(limitPrice * 100) / 100,
          qty,
          margin_usd: marginCap,
          leverage,
          intent_source: intent.intent_source ?? 'unknown',
          cycle_id: cycle.id,
          signal_price: sigPrice ?? null,
          pullback_pct: pullback,
          reason: 'INTENT_MIRROR_DRY_RUN',
        };
        this.logger.log(
          `[INTENT-MIRROR][DRY-RUN] would place user=${instance.userId} cycle=${cycle.id} trade=${tid} ${intent.direction} qty=${qty.toFixed(5)} @ ${limitPrice.toFixed(2)} margin=$${marginCap.toFixed(2)} lev=${leverage}x intent_source=${intent.intent_source ?? 'unknown'}`,
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
      // identically to the legacy entry path. placeEntry reads the cycle's
      // intentEnvelope for offset_pct; our envelope already carries signal_price
      // + pullback_pct so the legacy offset path is overridden by the bot limit
      // anchor resolution inside placeEntry when the showcase book has a price.
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
                margin_usd: marginCap,
                leverage,
                intent_source: intent.intent_source ?? 'unknown',
                signal_price: sigPrice ?? null,
                pullback_pct: pullback,
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
  private clearShowcaseUnreachable(instanceId: string): void {
    if (!this.showcaseUnreachableSince.has(instanceId)) {
      // Already healthy — keep the counter clean.
      this.showcaseRecoveryHits.delete(instanceId);
      return;
    }
    const hits = (this.showcaseRecoveryHits.get(instanceId) ?? 0) + 1;
    if (hits >= this.SHOWCASE_RECOVERY_HITS_REQUIRED) {
      this.showcaseUnreachableSince.delete(instanceId);
      this.showcaseSafeModeNoticeAt.delete(instanceId);
      this.showcaseRecoveryHits.delete(instanceId);
    } else {
      this.showcaseRecoveryHits.set(instanceId, hits);
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
   * states. Unlike {@link trackShowcaseVanished}, this does NOT require the
   * trade to be wiped from trades_map — a PENDING/VIRTUAL_CHASE entry that
   * never became an open showcase position still counts as absent.
   * Fail-closed: caller must pass a successfully-fetched bot state.
   */
  private trackShowcasePositionAbsent(
    participantId: string,
    tradeId: string,
    bot: BotApiState,
  ): boolean {
    if (!tradeId || tradeId.startsWith('adopt:')) {
      this.showcasePositionAbsentMisses.delete(participantId);
      return false;
    }
    const inPositions = (bot.positions ?? []).some(
      (p) => p.trade_id && tradeIdsMatch(p.trade_id, tradeId),
    );
    if (inPositions) {
      this.showcasePositionAbsentMisses.delete(participantId);
      return false;
    }
    const misses = (this.showcasePositionAbsentMisses.get(participantId) ?? 0) + 1;
    this.showcasePositionAbsentMisses.set(participantId, misses);
    if (misses < SHOWCASE_POSITION_ABSENT_CONSECUTIVE_MISSES) {
      this.logger.warn(
        `Showcase position absent ${tradeId} (participant=${participantId}) — miss ${misses}/${SHOWCASE_POSITION_ABSENT_CONSECUTIVE_MISSES}`,
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

    const inPositions = (bot.positions ?? []).some((p) => p.trade_id === tradeId);
    const inTrades = (bot.trades ?? []).some((t) => t.trade_id === tradeId);
    const inTradesMap =
      bot.trades_map != null &&
      Object.prototype.hasOwnProperty.call(bot.trades_map, tradeId);
    // Defensive: a trade still pending/known as a signal is NOT vanished.
    const inOrders = (bot.orders ?? []).some((o) => o.trade_id === tradeId);
    const inSignals = (bot.signal_info?.signals ?? []).some(
      (s) => String(s.trade_id ?? '') === tradeId,
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
    if (await this.trackShowcaseVanished(participant.id, cycle.tradeId ?? null)) {
      this.logger.warn(
        `Showcase trade VANISHED ${userId} cycle=${cycle.id} trade=${cycle.tradeId} — absent from canonical positions/trades for ${SHOWCASE_VANISHED_CONSECUTIVE_MISSES} consecutive fresh states; market-closing copy lot`,
      );
      await this.closeVirtualLot(agentId, userId, cycle.id, participant.id, meta, creds, {
        reason: 'SHOWCASE_VANISHED',
        mark,
        fillPrice,
        leverage,
        peakMarginPct: runtime.peakMarginPct,
        unrealMarginPct,
        stopLossMarginPct,
      });
      this.positionRuntime.delete(participant.id);
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
      await this.closeVirtualLot(agentId, userId, cycle.id, participant.id, meta, creds, {
        reason: exitReason,
        mark,
        fillPrice,
        leverage,
        lockFloor,
        peakMarginPct: runtime.peakMarginPct,
        unrealMarginPct,
        stopLossMarginPct,
      });
      this.positionRuntime.delete(participant.id);
      return;
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
  ) {
    if (!meta.qty || !meta.direction || !opts.reason) return;
    if (this.exitingLots.has(participantId)) return;
    if (await this.hasParticipantExited(participantId)) return;

    this.exitingLots.add(participantId);
    try {
    const positionAmount = (await this.activeTrading.getOpenPositionDetail(creds))?.amount ?? 0;
    const closeQty = Math.min(meta.qty, Math.abs(positionAmount));

    try {
      // Option A — cancel BOTH the persisted meta.stopOrderId AND any tracked
      // currentStopOrderId before market-closing. With dynamic stops on, the
      // tracked id is the authoritative live stop (meta.stopOrderId can lag by
      // a tick). Showcase EXIT is authoritative — never let a stale protective
      // stop fire mid-close and realize the wrong rung.
      const runtimeTrack = this.positionRuntime.get(participantId);
      const trackedStopId = runtimeTrack?.currentStopOrderId;
      const stopIdsToCancel = new Set<number>();
      if (meta.stopOrderId) stopIdsToCancel.add(meta.stopOrderId);
      if (trackedStopId) stopIdsToCancel.add(trackedStopId);
      for (const stopId of stopIdsToCancel) {
        try {
          await this.activeTrading.cancelOrder(creds, stopId);
        } catch {
          /* may have fired */
        }
      }
      if (closeQty >= MIN_QTY_BTC) {
        await this.activeTrading.submitMarketClose(creds, {
          positionDirection: meta.direction,
          qty: closeQty,
          leverage: opts.leverage,
        });
      }
    } catch (err) {
      this.logger.warn(
        `Lot close ${userId} cycle=${cycleId} ${opts.reason}: ${err instanceof Error ? err.message : err}`,
      );
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
    if (closeQty < MIN_QTY_BTC) {
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
    } finally {
      this.exitingLots.delete(participantId);
      this.showcaseVanishedMisses.delete(participantId);
      this.showcasePositionAbsentMisses.delete(participantId);
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
    userId: string,
    creds: ExchangeCredentials,
    participantScope: { createdAt?: { gte: Date } } = {},
  ) {
    const position = await this.activeTrading.getOpenPositionDetail(creds).catch(() => null);
    const exchangeQty = position ? Math.abs(position.amount) : 0;
    if (exchangeQty >= MIN_QTY_BTC) return;

    const openRows = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: SignalCycleStatus.OPEN,
        cycle: { agentId },
        ...participantScope,
      },
      include: { cycle: true },
    });
    if (!openRows.length) return;

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
            `[IMMEDIATE-FLAT] oid=${oid} not resting (gone) — skip cancel ${userId} participant=${row.id}`,
          );
          continue;
        }
        const filledQty =
          (orderResting.amountOrig ?? 0) - (orderResting.amount ?? 0);
        if (filledQty > MIN_QTY_BTC) {
          // Partial/full fill — let reconcileFilledParticipants heal as a fill.
          this.logger.warn(
            `[IMMEDIATE-FLAT] oid=${oid} has filled qty=${filledQty.toFixed(5)} — NOT cancelling (defer to fill reconcile) ${userId} participant=${row.id}`,
          );
          continue;
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
          this.logger.warn(
            `[IMMEDIATE-FLAT] cancel oid=${oid} FAILED and order still live (reason=${result.reason ?? 'unknown'} attempts=${result.attempts}) — deferring to next tick ${userId} participant=${row.id}`,
          );
        }
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
  }

  /** Scenario 5 — cancel resting entry limits linked to a showcase cycle before exit. */
  private async cancelLinkedPendingLimits(
    creds: ExchangeCredentials,
    meta: ExecutionPayload,
    activeOrderIdSet: Set<number>,
  ) {
    if (meta.bitfinexOrderId && activeOrderIdSet.has(meta.bitfinexOrderId)) {
      try {
        await this.activeTrading.cancelOrder(creds, meta.bitfinexOrderId);
      } catch {
        /* already filled or gone */
      }
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
      // Dust sweep — residual exchange qty below MIN_QTY_BTC. Hands-free flatten
      // so the user doesn't have to manually close sub-threshold slivers left
      // by partial fills / stop scraps. Returns true only if it placed a
      // market order; either way we return false here so the reconcile loop
      // continues and the next tick confirms the flatten (matches the
      // cancelStillLive L2104 defer-to-next-tick pattern).
      if (exchangeQty > 0 && exchangeQty < MIN_QTY_BTC) {
        await this.sweepDustPosition(agentId, userId, cycle, participant, meta, creds);
      }
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

  /**
   * Dust sweep — hands-free flatten of residual exchange qty below MIN_QTY_BTC.
   *
   * Called from reconcileManualClose (per-participant, when the exchange
   * position matches direction but is below threshold) and reconcileLotLedger
   * (aggregate, when the merged exchange position is dust). Emits a DUST_SWEEP
   * audit event every time; places a flatten market order only when the
   * RELAY_AUTO_DUST_SWEEP kill switch is enabled (default on).
   *
   * Safety guards mirror the existing money-path patterns:
   *  - participant must be OPEN or CLOSED (never PENDING_ENTRY)
   *  - abs amount strictly between 0 and MIN_QTY_BTC
   *  - cancel any resting stop on the lot first (L6284-6290 pattern)
   *  - in-flight dedupe via dustSweepInFlight prevents stacking duplicate
   *    market orders within the same tick window
   *  - every step wrapped in try/catch; never throws out of the reconcile path
   *
   * Returns true only when a sweep market order was placed this call.
   */
  private async sweepDustPosition(
    agentId: string,
    userId: string,
    cycle: { id: string },
    participant: {
      id: string;
      status: SignalCycleStatus;
      fillPrice?: { toNumber?: () => number } | null;
    },
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
  ): Promise<boolean> {
    const key = `${userId}:${cycle.id}`;
    if (this.dustSweepInFlight.has(key)) return false;

    const sweepEnabled =
      (this.config.get<string>('RELAY_AUTO_DUST_SWEEP') ?? '1').trim() !== '0';

    let position: { amount: number; direction?: 'LONG' | 'SHORT' } | null = null;
    try {
      position = await this.activeTrading.getOpenPositionDetail(creds);
    } catch (err) {
      this.logger.warn(
        `Dust sweep ${userId} cycle=${cycle.id}: position fetch failed — ${err instanceof Error ? err.message : err}`,
      );
      await this.recordDustSweepEvent(agentId, userId, cycle.id, {
        dustQty: 0,
        marketOrderId: undefined,
        sweepEnabled,
        reason: 'position_fetch_failed',
      });
      return false;
    }

    const dustQty = position ? Math.abs(position.amount) : 0;
    if (dustQty <= 0 || dustQty >= MIN_QTY_BTC) {
      return false;
    }

    // PENDING_ENTRY participants have no real exposure to sweep yet.
    if (
      participant.status !== SignalCycleStatus.OPEN &&
      participant.status !== SignalCycleStatus.CLOSED
    ) {
      return false;
    }

    this.dustSweepInFlight.add(key);
    try {
      // Cancel any resting stop on the lot first (reconcileManualClose L6284-6290
      // pattern). Never let a stale protective stop fire mid-sweep and realize
      // the wrong residual qty.
      if (meta.stopOrderId) {
        try {
          await this.activeTrading.cancelOrder(creds, meta.stopOrderId);
        } catch {
          /* stop may have filled — safe to proceed, market close is authoritative */
        }
      }

      if (!sweepEnabled) {
        this.logger.warn(
          `Dust detected ${userId} cycle=${cycle.id}: ${dustQty.toFixed(5)} BTC below MIN_QTY_BTC — sweep disabled (RELAY_AUTO_DUST_SWEEP=0), surfacing audit only`,
        );
        await this.recordDustSweepEvent(agentId, userId, cycle.id, {
          dustQty,
          marketOrderId: undefined,
          sweepEnabled: false,
          reason: 'auto_flatten_below_min_qty',
        });
        return false;
      }

      // Flatten via market close — negate the residual position amount. Reuses
      // the same submitMarketClose path as closeVirtualLot (L5959) and the sim
      // orphan heal (L1927). Direction is inferred from the live position sign
      // so we close the correct way even if meta.direction is stale.
      const positionDirection: 'LONG' | 'SHORT' =
        position && position.amount < 0 ? 'SHORT' : (meta.direction ?? 'LONG');
      let marketOrderId: number | undefined;
      try {
        marketOrderId = await this.activeTrading.submitMarketClose(creds, {
          positionDirection,
          qty: dustQty,
        });
      } catch (err) {
        // Idempotency: never retry aggressively — log and defer to next tick
        // (cancelStillLive L2104 pattern). The reconcile loop will re-detect
        // dust on the next pass and retry.
        this.logger.warn(
          `Dust sweep market order failed ${userId} cycle=${cycle.id}: ${err instanceof Error ? err.message : err} — deferring to next tick`,
        );
        await this.recordDustSweepEvent(agentId, userId, cycle.id, {
          dustQty,
          marketOrderId: undefined,
          sweepEnabled: true,
          reason: 'market_order_failed',
        });
        return false;
      }

      this.logger.warn(
        `Dust sweep ${userId} cycle=${cycle.id}: flattened ${dustQty.toFixed(5)} BTC via market order ${marketOrderId}`,
      );
      await this.recordDustSweepEvent(agentId, userId, cycle.id, {
        dustQty,
        marketOrderId,
        sweepEnabled: true,
        reason: 'auto_flatten_below_min_qty',
      });
      return true;
    } finally {
      this.dustSweepInFlight.delete(key);
    }
  }

  /** Best-effort DUST_SWEEP audit event — never throws. */
  private async recordDustSweepEvent(
    agentId: string,
    userId: string,
    cycleId: string,
    payload: {
      dustQty: number;
      marketOrderId?: number;
      sweepEnabled: boolean;
      reason: string;
    },
  ): Promise<void> {
    try {
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycleId, 'DUST_SWEEP', {
        venue: 'bitfinex',
        source: 'hire',
        event: 'DUST_SWEEP',
        dust_qty: Math.round(payload.dustQty * 1e8) / 1e8,
        market_order_id: payload.marketOrderId,
        reason: payload.reason,
        sweep_enabled: payload.sweepEnabled,
      });
    } catch (err) {
      this.logger.warn(
        `DUST_SWEEP audit failed ${userId} cycle=${cycleId}: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  /**
   * Aggregate dust backstop — called from reconcileLotLedger when the merged
   * exchange position is dust. If exactly one OPEN participant owns the lot,
   * delegate to sweepDustPosition (which cancels stops + places market close).
   * If no OPEN participant claims it (all CLOSED/EXPIRED on ledger), emit a
   * detection-only DUST_SWEEP event so the operator can decide — synthesizing
   * a lot from nothing is the reconcileUnattributedExchangeFills path's job.
   */
  private async sweepAggregateDust(
    agentId: string,
    instance: TradingAgentInstance,
    participants: Array<{ id: string; status: SignalCycleStatus }>,
    creds: ExchangeCredentials,
    position: { amount: number; direction?: 'LONG' | 'SHORT'; symbol?: string } | null,
    exchangeQty: number,
  ): Promise<void> {
    // Only sweep when the instance is ACTIVE — PAUSED is exit-only and a dust
    // flatten is a new market order, which we don't place on pause.
    if (instance.status !== TradingAgentInstanceStatus.ACTIVE) return;

    const openParticipants = participants.filter(
      (p) => p.status === SignalCycleStatus.OPEN,
    );

    if (openParticipants.length === 1) {
      const p = openParticipants[0];
      const meta = await this.loadExecutionMeta(p.id).catch(() => ({} as ExecutionPayload));
      const participantRow = await this.prisma.signalCycleParticipant.findUnique({
        where: { id: p.id },
        select: { id: true, status: true, fillPrice: true, cycleId: true },
      });
      if (!participantRow) return;
      await this.sweepDustPosition(
        agentId,
        instance.userId,
        { id: participantRow.cycleId },
        { id: p.id, status: p.status, fillPrice: participantRow.fillPrice },
        meta,
        creds,
      );
      return;
    }

    // No single OPEN owner — surface detection only (no synthesized lot).
    const sweepEnabled =
      (this.config.get<string>('RELAY_AUTO_DUST_SWEEP') ?? '1').trim() !== '0';
    this.logger.warn(
      `Dust detected ${instance.userId}: ${exchangeQty.toFixed(5)} BTC on exchange with ${openParticipants.length} OPEN lots — no single owner, surfacing audit only`,
    );
    // Use the first OPEN participant's cycle if available, else the instance's
    // most recent cycle — DUST_SWEEP is audit-only here so the cycleId is for
    // operator triage, not status transition.
    let cycleIdForAudit: string | undefined;
    if (openParticipants.length > 0) {
      const row = await this.prisma.signalCycleParticipant.findUnique({
        where: { id: openParticipants[0].id },
        select: { cycleId: true },
      });
      cycleIdForAudit = row?.cycleId;
    }
    if (!cycleIdForAudit) {
      const latest = await this.prisma.signalCycle.findFirst({
        where: { agentId },
        orderBy: { createdAt: 'desc' },
        select: { id: true },
      });
      cycleIdForAudit = latest?.id;
    }
    if (cycleIdForAudit) {
      await this.recordDustSweepEvent(agentId, instance.userId, cycleIdForAudit, {
        dustQty: exchangeQty,
        marketOrderId: undefined,
        sweepEnabled,
        reason: openParticipants.length === 0 ? 'no_open_owner' : 'multiple_open_owners',
      });
    }
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
    // reading every participant's merged ExecutionPayload (which surfaces
    // `clientOrderId` from ORDER_PLACED / UPDATE_STOPS events). A foreign
    // active order whose `cid` matches one of these is, by construction, the
    // bot's own orphaned limit (the participant is now terminal so its
    // bitfinexOrderId left `managedOrderIds`, but the exchange order is still
    // on the book). Auto-cancel those with retry + loud-fail + audit. cid-less
    // / unknown-cid foreign orders are left for manual review (current
    // behavior, gated by the aggressive flag).
    const allParticipants = await this.prisma.signalCycleParticipant.findMany({
      where: { userId, cycle: { agentId } },
      select: { id: true, cycleId: true, status: true },
    });
    const cidToParticipant = new Map<number, { participantId: string; cycleId: string }>();
    for (const p of allParticipants) {
      const meta = await this.loadExecutionMeta(p.id);
      if (meta.clientOrderId == null) continue;
      // First-seen wins — a re-placement (applyLimitChase) reuses the same cid
      // hash for the same (cycle, participant, tradeId) triple, so duplicates
      // map to the same participant anyway.
      if (!cidToParticipant.has(meta.clientOrderId)) {
        cidToParticipant.set(meta.clientOrderId, { participantId: p.id, cycleId: p.cycleId });
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
    if (meta.qty && meta.qty > MIN_QTY_BTC && meta.direction) return meta;

    const intent = intentEnvelope as SignalIntentEnvelope;
    const direction = meta.direction ?? intent?.direction;
    let limitPrice = meta.limitPrice ?? meta.originalLimitPrice;
    if ((!limitPrice || limitPrice <= 0) && intent?.entry?.offset_pct != null && direction) {
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
    if ((!qty || qty <= MIN_QTY_BTC) && limitPrice && limitPrice > 0 && marginUsd > 0) {
      qty = computeQty(marginUsd, leverage, limitPrice, MIN_QTY_BTC);
    }

    if (qty && qty > MIN_QTY_BTC && direction) {
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
      await this.closeVirtualLot(agent.id, userId, row.cycleId, row.id, meta, creds, {
        reason: 'HARD_STOP',
        mark: mark || fillPrice,
        fillPrice,
        leverage: meta.leverage ?? resolveSubscriberLeverage(),
        peakMarginPct: meta.peakMarginPct ?? 0,
        unrealMarginPct: 0,
        stopLossMarginPct: meta.stopLossMarginPct ?? 0,
      });
      flattened += 1;
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
