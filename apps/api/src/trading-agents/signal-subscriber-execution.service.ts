import { Injectable, Logger, NotFoundException, OnModuleInit } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import {
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
  BITFINEX_COPY_POLICY_VERSION,
  resolveMaxConcurrentCopySignals,
  isCopyRelaySimActive,
  readCopyRelaySimState,
  COPY_RELAY_SIM_RECONCILE_ALERT_BTC,
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
  buildCopyRelayCapacity,
  type CopyRelayCapacitySnapshot,
  type VirtualLotExitReason,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { ExchangesService } from '../exchanges/exchanges.service';
import { BitfinexTradingClient } from '../exchanges/bitfinex-api.client';
import type { ExchangeCredentials } from '../exchanges/exchange-adapter.interface';
import { SignalCyclesService } from './signal-cycles.service';
import { BotBridgeService } from './bot-bridge.service';
import { CopyRelaySimService } from './copy-relay-sim.service';
import { TradeCycleAuditService } from './trade-cycle-audit.service';
import { BitfinexSimTradingClient } from '../exchanges/bitfinex-sim-trading.client';
import { applyDashboardPatch } from './instance-view.mapper';
import { loadSubscriberMaxMarginUsd } from './subscriber-margin.util';
import { mapBotStateToAgentStats } from './bot-state.mapper';

const AGENT_SLUG = 'conservative-btc';
const POLL_MS = resolveSubscriberExecutionPollMs();
const MIN_QTY_BTC = 0.00004;
const CHASE_INTERVAL_MS = SUBSCRIBER_CHASE_INTERVAL_MS ?? 60_000;
const CHASE_NEAR_FILL_INTERVAL_MS = SUBSCRIBER_CHASE_NEAR_FILL_INTERVAL_MS ?? 250;
const CHASE_BOT_ANCHOR_MS = SUBSCRIBER_SHOWCASE_ANCHOR_CHASE_MS ?? 250;

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
};

type PositionRuntime = {
  peakMarginPct: number;
  lastChaseAtMs: number;
  lastProfitLockFloor?: number;
  filledRecorded: boolean;
};

function executionEnabled(): boolean {
  return process.env.SUBSCRIBER_EXECUTION_ENABLED !== 'false';
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
  private running = false;
  private wakeQueued = false;

  constructor(
    private readonly prisma: PrismaService,
    private readonly exchanges: ExchangesService,
    private readonly cycles: SignalCyclesService,
    private readonly botBridge: BotBridgeService,
    private readonly relaySim: CopyRelaySimService,
    private readonly cycleAudit: TradeCycleAuditService,
    private readonly config: ConfigService,
  ) {
    this.activeTrading = this.bitfinex;
  }

  onModuleInit() {
    if (!executionEnabled()) {
      this.logger.warn('Subscriber execution disabled (SUBSCRIBER_EXECUTION_ENABLED=false)');
      return;
    }
    void loadSubscriberMaxMarginUsd(this.prisma).then((cap) => {
      this.logger.log(
        `Hire subscriber runner active — Bitfinex copy policy v${BITFINEX_COPY_POLICY_VERSION}, every ${POLL_MS}ms (max $${cap}/trade)`,
      );
    });
    setInterval(() => void this.tick(), POLL_MS);
    setTimeout(() => void this.tick(), POLL_MS);
  }

  /** Immediate execution wake from showcase bot push (coalesced if tick in flight). */
  async wakeNow() {
    if (!executionEnabled()) return;
    if (this.running) {
      this.wakeQueued = true;
      return;
    }
    await this.tick();
  }

  private async tick() {
    if (!executionEnabled() || this.running) return;
    this.running = true;
    try {
      const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: AGENT_SLUG } });
      if (!agent) return;

      const instances = await this.prisma.tradingAgentInstance.findMany({
        where: {
          agentId: agent.id,
          status: {
            in: [TradingAgentInstanceStatus.ACTIVE, TradingAgentInstanceStatus.PAUSED],
          },
          exchangeProvider: { not: 'paper' },
          OR: [{ expiresAt: null }, { expiresAt: { gt: new Date() } }],
        },
      });

      for (const row of instances) {
        const instance =
          (await this.prisma.tradingAgentInstance.findUnique({ where: { id: row.id } })) ?? row;
        if (instance.exchangeProvider !== 'bitfinex') continue;
        const simActive = isCopyRelaySimActive(instance.dashboardState);

        if (simActive) {
          if (instance.status === TradingAgentInstanceStatus.ACTIVE) {
            await this.prisma.tradingAgentInstance.update({
              where: { id: instance.id },
              data: {
                status: TradingAgentInstanceStatus.PAUSED,
                lastError: 'Relay simulation active — live orders blocked.',
              },
            });
          }
          try {
            this.activeTrading = this.relaySim.getSimClient(
              instance.userId,
              readCopyRelaySimState(instance.dashboardState).ledger,
            );
            await (this.activeTrading as BitfinexSimTradingClient).processFillsOnMark();
            await this.processInstance(agent.id, instance, true);
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
    } finally {
      this.running = false;
      if (this.wakeQueued) {
        this.wakeQueued = false;
        setImmediate(() => void this.tick());
      }
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

    await this.reconcileFilledParticipants(instance.userId, agentId, participantSince);

    await this.reconcileGhostOpenLots(
      agentId,
      instance.userId,
      participantSince,
      marginCap,
    );

    await this.reconcileImmediateExchangeFlat(agentId, instance.userId, creds, participantSince);

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
    });

    const managedOrderIds = new Set<number>();
    for (const p of openParticipantAfter) {
      const m = await this.loadExecutionMeta(p.id);
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

    await this.cleanupOrphanCopyOrders(instance.userId, creds, managedOrderIds);

    const maxConcurrent = await this.resolveMaxConcurrentSignals();
    const botStateForCap = this.botBridge.isEnabled()
      ? await this.botBridge.fetchStateForExecution(true)
      : null;
    const botMaxRaw = botStateForCap?.max_active_signals;
    const botMax =
      typeof botMaxRaw === 'number'
        ? botMaxRaw
        : typeof botMaxRaw === 'string'
          ? Number.parseInt(botMaxRaw, 10)
          : null;

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
    const intentCycles = cycles
      .filter((c) => c.status === SignalCycleStatus.INTENT)
      .sort((a, b) => a.createdAt.getTime() - b.createdAt.getTime());
    let entriesThisTick = 0;
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
      if (!this.botBridge.isEnabled()) continue;
      if (!(await this.botBridge.isReachable(true))) continue;

      const intent = cycle.intentEnvelope as SignalIntentEnvelope;
      if (!intent?.direction) continue;

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
      if (!placed) break;
      entriesThisTick += 1;
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
    return { ready: true };
  }

  /** Showcase cancelled or blocked entry — relay must drop its resting limit too. */
  private showcaseEntryAbandoned(
    bot: import('./bot-state.mapper').BotApiState | null,
    tradeId: string,
  ): { abandoned: boolean; reason?: string } {
    if (!bot || !tradeId) return { abandoned: false };

    const pending = this.showcasePendingOrder(bot, tradeId);
    if (pending?.limit_price && pending.limit_price > 0) return { abandoned: false };

    const expired = (bot.expired_orders ?? []).find((e) => e.trade_id === tradeId);
    if (expired) {
      return { abandoned: true, reason: expired.reason ?? 'SHOWCASE_EXPIRED' };
    }

    const sig = this.showcaseSignalForTrade(bot, tradeId);
    if (!sig) return { abandoned: false };

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
          DEFAULT_SUBSCRIBER_LEVERAGE,
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
    const exchangeQty = position ? Math.abs(position.amount) : 0;
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

      await this.cycles.recordHireExecutionEvent(userId, agentId, participant.cycleId, 'EXIT', {
        venue,
        exit_price: mark,
        exit_reason: 'MANUAL_PARTIAL_CLOSE',
        pnl_usd: 0,
        pnl_margin_pct: 0,
        qty_closed: meta.qty,
        source: 'hire',
        event: 'ORPHAN_LEDGER_RECONCILE',
      });

      this.logger.warn(
        `Orphan ledger lot closed ${userId} participant=${row.id} qty=${meta.qty} (ledger > exchange)`,
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
      sim.showcasePnlUsd = stats.sessionPnlUsd;
      sim.showcaseTradeCount = stats.tradeCount;
    }
    await this.relaySim.persistSimState(instance.id, instance.userId, sim, reconcile);
  }

  private async resolveMaxConcurrentSignals(): Promise<number> {
    const bot = this.botBridge.isEnabled() ? await this.botBridge.fetchState() : null;
    return resolveMaxConcurrentCopySignals({
      botMaxActiveSignals: bot?.max_active_signals,
      envOverride: process.env.SUBSCRIBER_MAX_CONCURRENT_SIGNALS,
    });
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

    const pendingOrders = await this.activeTrading.listActiveOrders(creds).catch(() => []);
    const foreignPending = pendingOrders.filter((o) => !managedOrderIds.has(o.id));
    if (foreignPending.length > 0) {
      return {
        canEnter: false,
        reason: `${foreignPending.length} unmanaged Bitfinex order(s) — cancel manually or wait before new copy entries.`,
        availableUsd: available,
        slotsRemaining: 0,
      };
    }

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

    if (totalLegs >= maxLegs) {
      return {
        canEnter: false,
        reason: `Max ${maxConcurrent} concurrent signals (showcase dashboard) — ${totalLegs} active (${managed.open} open, ${managed.pending} pending).`,
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

    const minRequired = marginCap * 0.9;
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
      const rawLimit = computeLimitFromMark(mark, intent.entry.offset_pct);
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

      if (tradeId && (await this.botBridge.isEnabledAsync())) {
        const botState = await this.botBridge.fetchStateForExecution(true).catch(() => null);
        const defer = this.showcaseCopyEntryReady(botState, tradeId);
        if (!defer.ready) {
          await this.prisma.tradingAgentInstance.update({
            where: { id: instance.id },
            data: { lastError: defer.reason ?? 'Waiting for showcase limit.' },
          });
          return false;
        }
        const botLimit = this.resolveBotLimitPrice(botState, tradeId);
        if (botLimit != null && botLimit > 0) {
          const anchored = sanitizeLimitPrice(mark, botLimit, intent.direction);
          if (anchored != null) limitPrice = anchored;
        }
      }

      const leverage = intent.risk.leverage_hint ?? DEFAULT_SUBSCRIBER_LEVERAGE;
      const qty = computeQty(marginUsd, leverage, limitPrice, MIN_QTY_BTC);

    const prePosition = await this.activeTrading.getOpenPositionDetail(creds).catch(() => null);
    const exchangeQtyAtOrder = prePosition ? Math.abs(prePosition.amount) : 0;

    const orderId = await this.activeTrading.submitLimitOrder(creds, {
      direction: intent.direction,
      qty,
      price: limitPrice,
        leverage,
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
      try {
        await this.activeTrading.cancelOrder(creds, orderId);
      } catch {
        /* may already be gone */
      }
      this.logger.log(
        `Hire expire ${userId} cycle=${cycle.id}: showcase cycle ${cycle.status} — cancelled relay limit`,
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
      const botState = await this.botBridge.fetchStateForExecution(true).catch(() => null);
      const abandon = this.showcaseEntryAbandoned(botState, cycle.tradeId);
      if (abandon.abandoned) {
        try {
          await this.activeTrading.cancelOrder(creds, orderId);
        } catch {
          /* may already be gone */
        }
        this.logger.log(
          `Hire expire ${userId} cycle=${cycle.id}: showcase abandoned (${abandon.reason ?? 'unknown'}) — cancelled relay limit`,
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
      try {
        await this.activeTrading.cancelOrder(creds, orderId);
      } catch {
        /* may already be gone */
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

    const position = await this.activeTrading.getOpenPositionDetail(creds).catch(() => null);
    const expectedLong = meta.direction === 'LONG';
    const hasPosition =
      position &&
      ((expectedLong && position.amount > 0) || (!expectedLong && position.amount < 0));

    if (!hasPosition) {
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
      this.logger.warn(
        `Order ${orderId} gone but merged position ${currentExchangeQty.toFixed(5)} BTC did not grow for lot ${lotQty.toFixed(5)} (baseline ${exchangeQtyAtOrder.toFixed(5)}) — expire phantom pending`,
      );
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXPIRED', {
        venue: 'bitfinex',
        pnl_usd: 0,
        source: 'hire',
        event: 'PHANTOM_FILL_REJECTED',
      });
      return;
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

    const leverage = intent.risk.leverage_hint ?? DEFAULT_SUBSCRIBER_LEVERAGE;
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
      const fillPrice =
        meta.limitPrice && meta.limitPrice > 0
          ? meta.limitPrice
          : position.basePrice > 0
            ? position.basePrice
            : await this.activeTrading.getMarkPrice();
      const qty = meta.qty ?? MIN_QTY_BTC;
      const leverage = intent?.risk?.leverage_hint ?? DEFAULT_SUBSCRIBER_LEVERAGE;
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
  ) {
    if (!meta.direction) return;
    const qty = meta.qty ?? MIN_QTY_BTC;
    const entry = fillPrice ?? meta.limitPrice;
    if (!entry || entry <= 0) return;

    const leverage = intent.risk.leverage_hint ?? DEFAULT_SUBSCRIBER_LEVERAGE;
    const stopLossMarginPct = intent.risk.stop_loss_margin_pct ?? -18;
    const stopPrice = computeStopPrice(entry, meta.direction, stopLossMarginPct, leverage);

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

    const lastChaseAtMs = meta.lastChaseAtMs ?? 0;
    const now = Date.now();
    const mark = await this.activeTrading.getMarkPrice();
    const nearFill = isNearChaseFillZone(meta.direction, meta.limitPrice, mark);
    const botState = this.botBridge.isEnabled() ? await this.botBridge.fetchStateForExecution(true) : null;
    const botLimit = tradeId ? this.resolveBotLimitPrice(botState, tradeId) : null;
    const chaseInterval =
      botLimit != null && botLimit > 0
        ? CHASE_BOT_ANCHOR_MS
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
    },
  ) {
    if (!meta.direction || !meta.bitfinexOrderId || !meta.limitPrice) return;

    const leverage = intent.risk.leverage_hint ?? DEFAULT_SUBSCRIBER_LEVERAGE;
    const qty = meta.qty ?? computeQty(20, leverage, opts.newLimit, MIN_QTY_BTC);

    try {
      await this.activeTrading.cancelOrder(creds, meta.bitfinexOrderId);
      const newOrderId = await this.activeTrading.submitLimitOrder(creds, {
        direction: meta.direction,
        qty,
        price: opts.newLimit,
        leverage,
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
   * Scenario C per virtual lot — ladder, thesis -12% / MFE 2%, hard stop; partial close exact qty.
   */
  private async monitorOpenPosition(
    agentId: string,
    userId: string,
    cycle: { id: string; intentEnvelope: unknown },
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

    const leverage = intent.risk.leverage_hint ?? DEFAULT_SUBSCRIBER_LEVERAGE;
    const stopLossMarginPct = intent.risk.stop_loss_margin_pct ?? -18;
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
        this.logger.warn(
          `Healed missing STOP_LOSS_ARMED ${userId} cycle=${cycle.id} stop=${stopPrice.toFixed(2)}`,
        );
      }
    }

    const lockFloorTrail = getProfitLockFloor(runtime.peakMarginPct);
    if (lockFloorTrail != null && fillPrice > 0 && meta.stopOrderId) {
      const trailStop = computeProfitLockStopPrice(fillPrice, meta.direction, lockFloorTrail, leverage);
      const priorFloor = runtime.lastProfitLockFloor ?? 0;
      if (lockFloorTrail > priorFloor + 0.5) {
        try {
          await this.activeTrading.cancelOrder(creds, meta.stopOrderId);
          const newStopId = await this.activeTrading.submitStopOrder(creds, {
            positionDirection: meta.direction,
            qty: meta.qty,
            stopPrice: trailStop,
            leverage,
          });
          runtime.lastProfitLockFloor = lockFloorTrail;
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
    this.logger.log(
            `Hire trail stop lot ${userId} cycle=${cycle.id} floor=${lockFloorTrail}% stop=${trailStop.toFixed(2)} qty=${meta.qty}`,
          );
        } catch (err) {
          this.logger.warn(
            `Trail stop update ${userId}: ${err instanceof Error ? err.message : err}`,
          );
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
      reason: NonNullable<VirtualLotExitReason>;
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
    const closeQty = Math.min(meta.qty, Math.abs((await this.activeTrading.getOpenPositionDetail(creds))?.amount ?? meta.qty));

    try {
      if (meta.stopOrderId) {
        try {
          await this.activeTrading.cancelOrder(creds, meta.stopOrderId);
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
    const pnlUsd =
      direction === 'LONG'
        ? (opts.mark - opts.fillPrice) * closeQty
        : (opts.fillPrice - opts.mark) * closeQty;
    const pnlMarginPct =
      opts.reason === 'PROFIT_LOCK' && opts.lockFloor != null
        ? opts.lockFloor
        : opts.unrealMarginPct;

    const exitReasonMap: Record<NonNullable<VirtualLotExitReason>, string> = {
      PROFIT_LOCK: 'PROFIT_LOCK',
      THESIS_FAST_CUT: 'THESIS_FAST_CUT',
      HARD_STOP: 'HARD_STOP',
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
      source: 'hire',
    });

    this.logger.log(
      `Hire lot exit ${userId} cycle=${cycleId} ${opts.reason} peak=${opts.peakMarginPct.toFixed(2)}% unreal=${opts.unrealMarginPct.toFixed(2)}% qty=${closeQty} exit=${opts.mark.toFixed(2)}`,
    );
    } finally {
      this.exitingLots.delete(participantId);
    }
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
      const pnlUsd =
        fillPrice && exitPrice
          ? meta.direction === 'LONG'
            ? (exitPrice - fillPrice) * meta.qty
            : (fillPrice - exitPrice) * meta.qty
          : 0;

      await this.cycles.recordHireExecutionEvent(userId, agentId, row.cycleId, 'EXIT', {
        venue: 'bitfinex',
        exit_price: exitPrice,
        exit_reason: 'MANUAL_OR_EXCHANGE_CLOSE',
        pnl_usd: Math.round(pnlUsd * 100) / 100,
        source: 'hire',
        event: 'IMMEDIATE_EXCHANGE_FLAT',
      });
      this.logger.warn(
        `Immediate flat reconcile ${userId} cycle=${row.cycleId} — exchange 0, ledger OPEN closed`,
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

    if (await this.hasParticipantExited(participant.id)) {
      return;
    }

    await this.cancelLinkedPendingLimits(creds, meta, new Set());

    const position = await this.activeTrading.getOpenPositionDetail(creds).catch(() => null);
    if (!position || Math.abs(position.amount) < MIN_QTY_BTC) {
      const fillPrice = participant.fillPrice != null ? Number(participant.fillPrice) : meta.limitPrice ?? 0;
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXIT', {
        venue: 'bitfinex',
        exit_price: fillPrice,
        exit_reason: 'SHOWCASE_MIRROR_ALREADY_FLAT',
        pnl_usd: 0,
        pnl_margin_pct: 0,
        source: 'hire',
      });
      this.logger.log(`Exit already flat ${userId} cycle=${cycle.id} — showcase mirror recorded`);
      return;
    }

    if (!meta.qty || !meta.direction) return;

    const fillPrice = participant.fillPrice != null ? Number(participant.fillPrice) : meta.limitPrice;
    let exitPrice = fillPrice ?? 0;
    const leverage =
      (cycle.intentEnvelope as SignalIntentEnvelope)?.risk?.leverage_hint ?? DEFAULT_SUBSCRIBER_LEVERAGE;
    try {
      exitPrice = await this.activeTrading.getMarkPrice();
      const position = await this.activeTrading.getOpenPositionDetail(creds);
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
      // Still record EXIT so billing/state advances; position may close on exchange stop.
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
      fillPrice && fillPrice > 0
        ? (pnlUsd / (fillPrice * meta.qty)) * 100 * leverage
        : 0;

    await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXIT', {
      venue: 'bitfinex',
      exit_price: exitPrice,
      exit_reason: 'SHOWCASE_MIRROR',
      pnl_usd: Math.round(pnlUsd * 100) / 100,
      pnl_margin_pct: Math.round(pnlMarginPct * 100) / 100,
      source: 'hire',
    });

    this.logger.log(`Hire exit ${userId} cycle=${cycle.id} pnl=$${pnlUsd.toFixed(2)}`);
  }

  private async cleanupOrphanCopyOrders(
    userId: string,
    creds: ExchangeCredentials,
    managedOrderIds: Set<number>,
  ) {
    // REAL-MONEY SAFETY: never blanket-cancel every unmanaged order on the symbol.
    // The previous behavior cancelled a user's MANUAL orders/stops each tick because
    // they were not in `managedOrderIds` (which only tracks copy-relay orders placed
    // this session). Now we only cancel when aggressive cleanup is explicitly opted-in
    // (RELAY_AGGRESSIVE_ORPHAN_CLEANUP=1) — default is to LOG orphans and leave them,
    // so a real user's manual orders are never touched by the relay.
    const aggressive =
      (this.config.get<string>('RELAY_AGGRESSIVE_ORPHAN_CLEANUP') ?? '').trim() === '1';
    const orders = await this.activeTrading.listActiveOrders(creds).catch(() => []);
    for (const order of orders) {
      if (managedOrderIds.has(order.id)) continue;
      if (!aggressive) {
        // Log only; do NOT cancel — could be a user's own manual order.
        this.logger.debug(
          `Unmanaged order ${order.id} (${order.orderType}) present for ${userId}; leaving untouched (aggressive cleanup off)`,
        );
        continue;
      }
      try {
        await this.activeTrading.cancelOrder(creds, order.id);
        this.logger.warn(
          `Cancelled orphan copy order ${order.id} (${order.orderType}) for ${userId}`,
        );
      } catch {
        /* already gone */
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
    };
  }

  private async hasParticipantExited(participantId: string): Promise<boolean> {
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
    const leverage = intent?.risk?.leverage_hint ?? DEFAULT_SUBSCRIBER_LEVERAGE;
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
      if (!meta.qty || meta.qty <= MIN_QTY_BTC) {
        await this.cycles.recordHireExecutionEvent(userId, agentId, cycleId, 'UPDATE_STOPS', {
          venue: 'bitfinex',
          event: 'META_QTY_REPAIR',
          qty,
          direction,
          margin_usd: marginUsd,
          source: 'hire',
        });
        this.logger.warn(
          `Repaired missing lot meta ${userId} participant=${participantId} qty=${qty.toFixed(5)}`,
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
        };
        Object.assign(meta, p);
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
        leverage: meta.leverage ?? DEFAULT_SUBSCRIBER_LEVERAGE,
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
}
