import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import {
  SignalCycleStatus,
  TradingAgentInstanceStatus,
  type TradingAgentInstance,
} from '@prisma/client';
import type { SignalIntentEnvelope } from '@dcf/utils';
import {
  resolveSubscriberExecutionPollMs,
  DEFAULT_SUBSCRIBER_LEVERAGE,
  SUBSCRIBER_MAX_CONCURRENT_COPY_LEGS,
  SUBSCRIBER_CHASE_INTERVAL_MS,
  computeLimitFromMark,
  computeStopPrice,
  computeProfitLockStopPrice,
  computeQty,
  computeLimitChaseTarget,
  isNearChaseFillZone,
  sanitizeLimitPrice,
  getProfitLockFloor,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { ExchangesService } from '../exchanges/exchanges.service';
import { BitfinexTradingClient } from '../exchanges/bitfinex-api.client';
import type { ExchangeCredentials } from '../exchanges/exchange-adapter.interface';
import { SignalCyclesService } from './signal-cycles.service';
import { BotBridgeService } from './bot-bridge.service';
import { loadSubscriberMaxMarginUsd } from './subscriber-margin.util';

const AGENT_SLUG = 'conservative-btc';
const POLL_MS = resolveSubscriberExecutionPollMs();
const MIN_QTY_BTC = 0.00004;
const CHASE_INTERVAL_MS = SUBSCRIBER_CHASE_INTERVAL_MS ?? 60_000;

type ExecutionPayload = {
  bitfinexOrderId?: number;
  stopOrderId?: number;
  limitPrice?: number;
  originalLimitPrice?: number;
  localMark?: number;
  qty?: number;
  direction?: 'LONG' | 'SHORT';
  source?: 'hire';
  peakMarginPct?: number;
  profitLockFloor?: number;
  stopLossPlaced?: boolean;
  lastChaseAtMs?: number;
  limitChaseCount?: number;
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

@Injectable()
export class SignalSubscriberExecutionService implements OnModuleInit {
  private readonly logger = new Logger(SignalSubscriberExecutionService.name);
  private readonly bitfinex = new BitfinexTradingClient();
  private readonly positionRuntime = new Map<string, PositionRuntime>();
  private running = false;

  constructor(
    private readonly prisma: PrismaService,
    private readonly exchanges: ExchangesService,
    private readonly cycles: SignalCyclesService,
    private readonly botBridge: BotBridgeService,
  ) {}

  onModuleInit() {
    if (!executionEnabled()) {
      this.logger.warn('Subscriber execution disabled (SUBSCRIBER_EXECUTION_ENABLED=false)');
      return;
    }
    void loadSubscriberMaxMarginUsd(this.prisma).then((cap) => {
      this.logger.log(
        `Hire subscriber runner active — Bitfinex live copy every ${POLL_MS / 1000}s (max $${cap} margin/trade)`,
      );
    });
    setInterval(() => void this.tick(), POLL_MS);
    setTimeout(() => void this.tick(), 1_000);
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
          status: TradingAgentInstanceStatus.ACTIVE,
          exchangeProvider: { not: 'paper' },
          OR: [{ expiresAt: null }, { expiresAt: { gt: new Date() } }],
        },
      });

      for (const instance of instances) {
        if (instance.exchangeProvider !== 'bitfinex') continue;
        try {
          await this.processInstance(agent.id, instance);
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
    }
  }

  private async processInstance(agentId: string, instance: TradingAgentInstance) {
    const creds = await this.exchanges.getUserCredentials(instance.userId, instance.exchangeProvider);
    if (!creds) {
      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: { lastError: 'Exchange credentials missing — re-hire with API keys' },
      });
      return;
    }

    const marginCap = await loadSubscriberMaxMarginUsd(this.prisma);

    if (instance.exchangeProvider === 'bitfinex') {
      const funding = await this.bitfinex.ensureDerivativesMargin(creds, marginCap);
      if (funding.message && funding.transferredUsd > 0) {
        this.logger.log(`Instance ${instance.userId}: ${funding.message}`);
      }
      await this.cancelAbsurdPendingOrders(creds, instance.userId);
    }

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

    let managedOpenTrade = false;

    // Pass 1 — manage existing copy trades (fills, stops, exits) before any new entries.
    for (const cycle of allCycles) {
      const participant = await this.prisma.signalCycleParticipant.findUnique({
        where: { cycleId_userId: { cycleId: cycle.id, userId: instance.userId } },
      });
      if (!participant) continue;

      const meta = await this.loadExecutionMeta(participant.id);

      if (participant.status === SignalCycleStatus.PENDING_ENTRY) {
        managedOpenTrade = true;
        await this.monitorEntry(agentId, instance.userId, cycle, participant, meta, creds);
        continue;
      }

      if (participant.status === SignalCycleStatus.OPEN) {
        managedOpenTrade = true;
        const reconciled = await this.reconcileManualClose(
          agentId,
          instance.userId,
          cycle,
          participant,
          meta,
          creds,
        );
        if (!reconciled) {
          await this.monitorOpenPosition(
            agentId,
            instance.userId,
            cycle,
            participant,
            meta,
            creds,
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
      },
      orderBy: { createdAt: 'desc' },
    });

    const managedOrderIds = new Set<number>();
    for (const p of openParticipantAfter) {
      const m = await this.loadExecutionMeta(p.id);
      if (m.bitfinexOrderId) managedOrderIds.add(m.bitfinexOrderId);
      if (m.stopOrderId) managedOrderIds.add(m.stopOrderId);
    }

    const botState = this.botBridge.isEnabled() ? await this.botBridge.fetchState() : null;
    const botActiveLegs = this.countBotActiveLegs(botState);

    // Pass 2 — place limit entries for each INTENT cycle (isolated legs, same as showcase bot).
    let entriesThisTick = 0;
    for (const cycle of cycles) {
      if (cycle.status !== SignalCycleStatus.INTENT) continue;
      if (cycle.expiresAt && cycle.expiresAt < new Date()) continue;

      const existing = await this.prisma.signalCycleParticipant.findUnique({
        where: { cycleId_userId: { cycleId: cycle.id, userId: instance.userId } },
      });
      if (existing) continue;
      if (!this.botBridge.isEnabled()) continue;
      if (!(await this.botBridge.isReachable(true))) continue;

      const eligibility = await this.evaluateEntryEligibility(
        creds,
        openParticipantAfter.length + entriesThisTick,
        botActiveLegs,
        managedOrderIds,
        marginCap,
      );
      if (!eligibility.canEnter) {
        if (entriesThisTick === 0) {
          await this.prisma.tradingAgentInstance.update({
            where: { id: instance.id },
            data: {
              lastError: managedOpenTrade
                ? eligibility.reason?.includes('Managing') || eligibility.reason?.includes('slots')
                  ? eligibility.reason
                  : 'Managing open copy trades — new signals paused until margin or slots free.'
                : eligibility.reason,
            },
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
      const eligibility = await this.evaluateEntryEligibility(
        creds,
        openParticipantAfter.length,
        botActiveLegs,
        managedOrderIds,
        marginCap,
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

  /** Count showcase bot pending orders + open positions (target copy leg count). */
  private countBotActiveLegs(bot: { orders?: unknown[]; positions?: unknown[] } | null): number {
    if (!bot) return 1;
    const orders = (bot.orders ?? []).filter((o) => {
      const row = o as { status?: string };
      return row.status === 'PENDING' || row.status === 'ORDERED';
    });
    const positions = (bot.positions ?? []).filter((p) => {
      const row = p as { status?: string };
      return row.status === 'OPEN' || !row.status;
    });
    return Math.max(1, orders.length + positions.length);
  }

  private resolveBotLimitPrice(
    bot: { orders?: Array<{ trade_id?: string; limit_price?: number; status?: string }> } | null,
    tradeId: string,
  ): number | null {
    if (!bot) return null;
    const order = (bot.orders ?? []).find(
      (o) => o.trade_id === tradeId && (o.status === 'PENDING' || o.status === 'ORDERED'),
    );
    if (order?.limit_price && order.limit_price > 0) return order.limit_price;
    return null;
  }

  /**
   * Allow multiple isolated limit legs when margin and slots remain (matches showcase bot).
   * Does not block on our own managed pending orders — only foreign/unmanaged orders.
   */
  private async evaluateEntryEligibility(
    creds: ExchangeCredentials,
    managedLegCount: number,
    botActiveLegs: number,
    managedOrderIds: Set<number>,
    marginCap: number,
  ): Promise<EntryEligibility> {
    let available = 0;
    try {
      available = await this.bitfinex.getDerivativesAvailableUsd(creds);
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

    const pendingOrders = await this.bitfinex.listActiveOrders(creds).catch(() => []);
    const foreignPending = pendingOrders.filter((o) => !managedOrderIds.has(o.id));
    if (foreignPending.length > 0) {
      return {
        canEnter: false,
        reason: `${foreignPending.length} unmanaged Bitfinex order(s) — cancel manually or wait before new copy entries.`,
        availableUsd: available,
        slotsRemaining: 0,
      };
    }

    const maxByMargin = Math.max(0, Math.floor((available * 0.95) / marginCap));
    const maxLegs = Math.min(
      SUBSCRIBER_MAX_CONCURRENT_COPY_LEGS,
      Math.max(botActiveLegs, maxByMargin),
    );
    const slotsRemaining = Math.max(0, Math.min(maxByMargin, maxLegs - managedLegCount));

    if (slotsRemaining <= 0) {
      return {
        canEnter: false,
        reason:
          managedLegCount > 0
            ? `Managing ${managedLegCount} copy leg(s) — no free slots (bot has ${botActiveLegs} active, margin allows ${maxByMargin}).`
            : 'No copy slots available.',
        availableUsd: available,
        slotsRemaining: 0,
      };
    }

    const minRequired = marginCap * 0.9;
    if (available < minRequired) {
      return {
        canEnter: false,
        reason: `Insufficient Derivatives margin ($${available.toFixed(2)} available, need ~$${marginCap} per leg). Move USDT to Derivatives wallet.`,
        availableUsd: available,
        slotsRemaining: 0,
      };
    }

    return { canEnter: true, reason: null, availableUsd: available, slotsRemaining };
  }

  private async placeEntry(
    agentId: string,
    instance: TradingAgentInstance,
    cycleId: string,
    envelopeJson: unknown,
    creds: ExchangeCredentials,
    marginCap: number,
    tradeId?: string,
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
      available = await this.bitfinex.getDerivativesAvailableUsd(creds);
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

    try {
      const mark = await this.bitfinex.getMarkPrice();
      const rawLimit = computeLimitFromMark(mark, intent.entry.offset_pct);
      const limitPrice = sanitizeLimitPrice(mark, rawLimit, intent.direction);
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

      const leverage = intent.risk.leverage_hint ?? DEFAULT_SUBSCRIBER_LEVERAGE;
      const qty = computeQty(marginUsd, leverage, limitPrice, MIN_QTY_BTC);

      const orderId = await this.bitfinex.submitLimitOrder(creds, {
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
        source: 'hire',
        lastChaseAtMs: 0,
        limitChaseCount: 0,
      };

      await this.cycles.recordHireExecutionEvent(instance.userId, agentId, cycleId, 'ORDER_PLACED', {
        venue: 'bitfinex',
        local_mark_at_signal: mark,
        limit_price: limitPrice,
        original_limit_price: limitPrice,
        qty,
        margin_usd: marginUsd,
        margin_cap_usd: effectiveCap,
        leverage,
        ...payload,
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
  ) {
    const intent = cycle.intentEnvelope as SignalIntentEnvelope;
    const orderId = meta.bitfinexOrderId;
    if (!orderId || !meta.direction) return;

    if (participant.status === SignalCycleStatus.OPEN) {
      await this.ensureProtectiveStop(agentId, userId, cycle.id, participant.id, meta, creds, intent);
      return;
    }

    await this.cancelAbsurdPendingOrders(creds, userId);

    if (cycle.expiresAt && cycle.expiresAt < new Date()) {
      try {
        await this.bitfinex.cancelOrder(creds, orderId);
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

    const active = await this.bitfinex.findOrder(creds, orderId);
    if (active) {
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

    const position = await this.bitfinex.getOpenPositionDetail(creds);
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

    const fillPrice = position.basePrice > 0 ? position.basePrice : meta.limitPrice ?? (await this.bitfinex.getMarkPrice());
    const qty = Math.abs(position.amount);
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

    const stopOrderId = await this.bitfinex.submitStopOrder(creds, {
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
    const position = await this.bitfinex.getOpenPositionDetail(creds);
    if (!position) return;
    const qty = Math.abs(position.amount);
    const entry = fillPrice ?? position.basePrice;
    if (!entry || entry <= 0) return;

    const leverage = intent.risk.leverage_hint ?? DEFAULT_SUBSCRIBER_LEVERAGE;
    const stopLossMarginPct = intent.risk.stop_loss_margin_pct ?? -18;
    const stopPrice = computeStopPrice(entry, meta.direction, stopLossMarginPct, leverage);

    if (meta.stopOrderId) {
      const existing = await this.bitfinex.findOrder(creds, meta.stopOrderId).catch(() => null);
      if (existing) return;
    }

    const stopOrderId = await this.bitfinex.submitStopOrder(creds, {
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
    if (!immediate && now - lastChaseAtMs < CHASE_INTERVAL_MS) return;

    const mark = await this.bitfinex.getMarkPrice();
    if (isNearChaseFillZone(meta.direction, meta.limitPrice, mark)) return;

    const originalLimit = meta.originalLimitPrice ?? meta.limitPrice;
    let targetLimit = meta.limitPrice;

    const botState = this.botBridge.isEnabled() ? await this.botBridge.fetchState() : null;
    const botLimit = tradeId ? this.resolveBotLimitPrice(botState, tradeId) : null;
    if (botLimit != null && botLimit > 0) {
      const safeBot = sanitizeLimitPrice(mark, botLimit, meta.direction);
      if (safeBot != null) targetLimit = safeBot;
    }

    const { newLimit, reason } = computeLimitChaseTarget(
      meta.direction,
      targetLimit,
      mark,
      originalLimit,
    );
    if (reason !== 'LIMIT_CHASE' || Math.abs(newLimit - meta.limitPrice) < 0.01) return;

    const leverage = intent.risk.leverage_hint ?? DEFAULT_SUBSCRIBER_LEVERAGE;
    const qty = meta.qty ?? computeQty(20, leverage, newLimit, MIN_QTY_BTC);

    try {
      await this.bitfinex.cancelOrder(creds, meta.bitfinexOrderId);
      const newOrderId = await this.bitfinex.submitLimitOrder(creds, {
        direction: meta.direction,
        qty,
        price: newLimit,
        leverage,
      });
      const chaseCount = (meta.limitChaseCount ?? 0) + 1;
      this.logger.log(
        `Hire chase ${userId} cycle=${cycleId} limit ${meta.limitPrice.toFixed(2)} → ${newLimit.toFixed(2)} (mark ${mark.toFixed(2)}${botLimit != null ? ` bot=${botLimit.toFixed(2)}` : ''})`,
      );
      await this.cycles.recordHireExecutionEvent(userId, agentId, cycleId, 'UPDATE_STOPS', {
        venue: 'bitfinex',
        event: 'LIMIT_CHASE',
        prior_limit: meta.limitPrice,
        new_limit: newLimit,
        limitPrice: newLimit,
        bitfinexOrderId: newOrderId,
        local_mark: mark,
        lastChaseAtMs: now,
        limitChaseCount: chaseCount,
        source: 'hire',
      });
      const runtime = this.positionRuntime.get(participantId) ?? {
        peakMarginPct: 0,
        lastChaseAtMs: 0,
        filledRecorded: false,
      };
      runtime.lastChaseAtMs = now;
      this.positionRuntime.set(participantId, runtime);
    } catch (err) {
      this.logger.warn(
        `Limit chase ${userId} cycle=${cycleId}: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  /** Cancel resting limits whose price is far from market (e.g. $904 on $64k BTC). */
  private async cancelAbsurdPendingOrders(creds: ExchangeCredentials, userId: string) {
    const mark = await this.bitfinex.getMarkPrice().catch(() => null);
    if (!mark) return;
    const orders = await this.bitfinex.listActiveOrders(creds).catch(() => []);
    for (const order of orders) {
      const deviationPct = Math.abs((order.price - mark) / mark) * 100;
      if (deviationPct > 8) {
        try {
          await this.bitfinex.cancelOrder(creds, order.id);
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
   * Live profit-lock ladder + trailing stop while showcase position is still open.
   * Mirrors showcase bot TRAIL_LADDER (12% peak → lock 8%, etc.).
   */
  private async monitorOpenPosition(
    agentId: string,
    userId: string,
    cycle: { id: string; intentEnvelope: unknown },
    participant: { id: string; fillPrice: { toNumber?: () => number } | null },
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
  ) {
    if (!meta.qty || !meta.direction) return;

    const intent = cycle.intentEnvelope as SignalIntentEnvelope;
    const position = await this.bitfinex.getOpenPositionDetail(creds);
    const expectedLong = meta.direction === 'LONG';
    const hasExpected =
      position &&
      ((expectedLong && position.amount > 0) || (!expectedLong && position.amount < 0));
    if (!hasExpected) return;

    const unrealMarginPct = position.pnlPct;
    const runtime = this.positionRuntime.get(participant.id) ?? {
      peakMarginPct: meta.peakMarginPct ?? 0,
      lastChaseAtMs: 0,
      filledRecorded: true,
    };
    runtime.peakMarginPct = Math.max(runtime.peakMarginPct, unrealMarginPct);
    this.positionRuntime.set(participant.id, runtime);

    const lockFloor = getProfitLockFloor(runtime.peakMarginPct);
    const fillPrice =
      participant.fillPrice != null
        ? Number(participant.fillPrice)
        : position.basePrice > 0
          ? position.basePrice
          : meta.limitPrice ?? 0;
    const leverage = intent.risk.leverage_hint ?? DEFAULT_SUBSCRIBER_LEVERAGE;

    if (lockFloor != null && unrealMarginPct <= lockFloor) {
      const mark = await this.bitfinex.getMarkPrice();
      try {
        if (meta.stopOrderId) {
          try {
            await this.bitfinex.cancelOrder(creds, meta.stopOrderId);
          } catch {
            /* may have fired */
          }
        }
        await this.bitfinex.submitMarketClose(creds, {
          positionDirection: meta.direction,
          qty: meta.qty,
          leverage,
        });
      } catch (err) {
        this.logger.warn(
          `Profit-lock close ${userId} cycle=${cycle.id}: ${err instanceof Error ? err.message : err}`,
        );
      }

      const direction = meta.direction;
      const pnlUsd =
        fillPrice && mark
          ? direction === 'LONG'
            ? (mark - fillPrice) * meta.qty
            : (fillPrice - mark) * meta.qty
          : position.pnlUsd;
      const pnlMarginPct = runtime.peakMarginPct > 0 ? lockFloor : unrealMarginPct;

      await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'EXIT', {
        venue: 'bitfinex',
        exit_price: mark,
        exit_reason: 'PROFIT_LOCK',
        peak_margin_pct: runtime.peakMarginPct,
        lock_floor_margin_pct: lockFloor,
        pnl_usd: Math.round(pnlUsd * 100) / 100,
        pnl_margin_pct: Math.round(pnlMarginPct * 100) / 100,
        source: 'hire',
      });
      this.positionRuntime.delete(participant.id);
      this.logger.log(
        `Hire profit-lock ${userId} cycle=${cycle.id} peak=${runtime.peakMarginPct.toFixed(2)}% lock=${lockFloor}% exit=${mark.toFixed(2)}`,
      );
      return;
    }

    if (!meta.stopOrderId && fillPrice > 0) {
      const stopLossMarginPct = intent.risk.stop_loss_margin_pct ?? -18;
      const stopPrice = computeStopPrice(fillPrice, meta.direction, stopLossMarginPct, leverage);
      const stopOrderId = await this.bitfinex.submitStopOrder(creds, {
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
          source: 'hire',
        });
        this.logger.log(`Hire stop retry ${userId} cycle=${cycle.id} @ ${stopPrice.toFixed(2)}`);
      }
    }

    if (lockFloor != null && fillPrice > 0 && meta.stopOrderId) {
      const trailStop = computeProfitLockStopPrice(fillPrice, meta.direction, lockFloor, leverage);
      const priorFloor = runtime.lastProfitLockFloor ?? 0;
      if (lockFloor > priorFloor + 0.5) {
        try {
          await this.bitfinex.cancelOrder(creds, meta.stopOrderId);
          const newStopId = await this.bitfinex.submitStopOrder(creds, {
            positionDirection: meta.direction,
            qty: meta.qty,
            stopPrice: trailStop,
            leverage,
          });
          runtime.lastProfitLockFloor = lockFloor;
          this.positionRuntime.set(participant.id, runtime);
          await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'UPDATE_STOPS', {
            venue: 'bitfinex',
            event: 'PROFIT_LOCK_TRAIL',
            lock_floor_margin_pct: lockFloor,
            profitLockFloor: lockFloor,
            peak_margin_pct: runtime.peakMarginPct,
            stop_price: trailStop,
            stopOrderId: newStopId,
            source: 'hire',
          });
          this.logger.log(
            `Hire trail stop ${userId} cycle=${cycle.id} floor=${lockFloor}% stop=${trailStop.toFixed(2)}`,
          );
        } catch (err) {
          this.logger.warn(
            `Trail stop update ${userId}: ${err instanceof Error ? err.message : err}`,
          );
        }
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

    const position = await this.bitfinex.getOpenPositionDetail(creds);
    const expectedLong = meta.direction === 'LONG';
    const hasExpected =
      position &&
      ((expectedLong && position.amount > 0) || (!expectedLong && position.amount < 0));

    if (hasExpected) return false;

    if (meta.stopOrderId) {
      try {
        await this.bitfinex.cancelOrder(creds, meta.stopOrderId);
      } catch {
        /* stop may have filled */
      }
    }
    await this.bitfinex.cancelOrphanStopOrders(creds, meta.bitfinexOrderId);

    const fillPrice =
      participant.fillPrice != null
        ? Number(participant.fillPrice)
        : meta.limitPrice ?? (await this.bitfinex.getMarkPrice());
    const exitPrice = await this.bitfinex.getMarkPrice().catch(() => fillPrice ?? 0);
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
      exit_reason: 'MANUAL_OR_EXCHANGE_CLOSE',
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

    const position = await this.bitfinex.getOpenPosition(creds);
    if (!position) {
      this.logger.log(`Exit skip ${userId} cycle=${cycle.id}: already flat on exchange`);
      return;
    }

    if (!meta.qty || !meta.direction) return;

    const fillPrice = participant.fillPrice != null ? Number(participant.fillPrice) : meta.limitPrice;
    let exitPrice = fillPrice ?? 0;
    const leverage =
      (cycle.intentEnvelope as SignalIntentEnvelope)?.risk?.leverage_hint ?? DEFAULT_SUBSCRIBER_LEVERAGE;
    try {
      exitPrice = await this.bitfinex.getMarkPrice();
      await this.bitfinex.submitMarketClose(creds, {
        positionDirection: meta.direction,
        qty: meta.qty,
        leverage,
      });
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
      pnl_usd: Math.round(pnlUsd * 100) / 100,
      pnl_margin_pct: Math.round(pnlMarginPct * 100) / 100,
      source: 'hire',
    });

    this.logger.log(`Hire exit ${userId} cycle=${cycle.id} pnl=$${pnlUsd.toFixed(2)}`);
  }

  private async loadExecutionMeta(participantId: string): Promise<ExecutionPayload> {
    const events = await this.prisma.signalCycleEvent.findMany({
      where: { participantId },
      orderBy: { createdAt: 'asc' },
    });
    const meta: ExecutionPayload = {};
    for (const e of events) {
      if (e.payload && typeof e.payload === 'object') {
        Object.assign(meta, e.payload as ExecutionPayload);
      }
    }
    return meta;
  }
}
