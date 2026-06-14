import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import {
  SignalCycleStatus,
  TradingAgentInstanceStatus,
  type TradingAgentInstance,
} from '@prisma/client';
import type { SignalIntentEnvelope } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { ExchangesService } from '../exchanges/exchanges.service';
import { BitfinexTradingClient } from '../exchanges/bitfinex-api.client';
import type { ExchangeCredentials } from '../exchanges/exchange-adapter.interface';
import { SignalCyclesService } from './signal-cycles.service';
import { BotBridgeService } from './bot-bridge.service';

const AGENT_SLUG = 'conservative-btc';
const POLL_MS = 30_000;
const DEFAULT_MAX_MARGIN_USD = 500;
const MIN_QTY_BTC = 0.00004;

type ExecutionPayload = {
  bitfinexOrderId?: number;
  stopOrderId?: number;
  limitPrice?: number;
  localMark?: number;
  qty?: number;
  direction?: 'LONG' | 'SHORT';
  source?: 'hire';
};

function executionEnabled(): boolean {
  return process.env.SUBSCRIBER_EXECUTION_ENABLED !== 'false';
}

function maxMarginUsd(): number {
  const raw = Number(process.env.SUBSCRIBER_MAX_MARGIN_USD ?? DEFAULT_MAX_MARGIN_USD);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_MAX_MARGIN_USD;
}

function computeLimitFromMark(mark: number, offsetPct: number): number {
  return mark * (1 + offsetPct / 100);
}

function computeStopPrice(
  fill: number,
  direction: 'LONG' | 'SHORT',
  stopLossMarginPct: number,
  leverage: number,
): number {
  const distance = Math.abs(stopLossMarginPct) / (100 * Math.max(leverage, 1));
  if (direction === 'LONG') return fill * (1 - distance);
  return fill * (1 + distance);
}

function computeQty(marginUsd: number, leverage: number, price: number): number {
  const notional = marginUsd * leverage;
  const raw = notional / price;
  return Math.max(MIN_QTY_BTC, Math.floor(raw * 1e5) / 1e5);
}

@Injectable()
export class SignalSubscriberExecutionService implements OnModuleInit {
  private readonly logger = new Logger(SignalSubscriberExecutionService.name);
  private readonly bitfinex = new BitfinexTradingClient();
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
    this.logger.log(
      `Hire subscriber runner active — Bitfinex live copy every ${POLL_MS / 1000}s (max margin $${maxMarginUsd()})`,
    );
    setInterval(() => void this.tick(), POLL_MS);
    setTimeout(() => void this.tick(), 8_000);
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

    const openParticipant = await this.prisma.signalCycleParticipant.findFirst({
      where: {
        userId: instance.userId,
        status: { in: [SignalCycleStatus.PENDING_ENTRY, SignalCycleStatus.OPEN] },
        cycle: { agentId },
      },
      orderBy: { createdAt: 'desc' },
      include: { cycle: true },
    });

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
      take: 5,
    });

    for (const cycle of cycles) {
      const participant = await this.prisma.signalCycleParticipant.findUnique({
        where: { cycleId_userId: { cycleId: cycle.id, userId: instance.userId } },
      });

      if (!participant && cycle.status === SignalCycleStatus.INTENT) {
        if (openParticipant) continue;
        if (cycle.expiresAt && cycle.expiresAt < new Date()) continue;
        if (!this.botBridge.isEnabled()) continue;
        const reachable = await this.botBridge.isReachable(true);
        if (!reachable) continue;
        await this.placeEntry(agentId, instance, cycle.id, cycle.intentEnvelope, creds);
        continue;
      }

      if (!participant) continue;

      const meta = await this.loadExecutionMeta(participant.id);

      if (participant.status === SignalCycleStatus.PENDING_ENTRY) {
        await this.monitorEntry(agentId, instance.userId, cycle, participant.id, meta, creds);
        continue;
      }

      if (participant.status === SignalCycleStatus.OPEN) {
        await this.monitorExit(agentId, instance.userId, cycle, participant, meta, creds);
      }
    }

    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: { lastError: null },
    });
  }

  private async placeEntry(
    agentId: string,
    instance: TradingAgentInstance,
    cycleId: string,
    envelopeJson: unknown,
    creds: ExchangeCredentials,
  ) {
    const intent = envelopeJson as SignalIntentEnvelope;
    if (!intent?.direction || intent.action !== 'ENTER') return;

    const mark = await this.bitfinex.getMarkPrice();
    const limitPrice = computeLimitFromMark(mark, intent.entry.offset_pct);
    const leverage = intent.risk.leverage_hint ?? 20;
    const available = await this.bitfinex.getAvailableUsd(creds);
    const marginUsd = Math.min(maxMarginUsd(), available * 0.95);
    if (marginUsd < 10) {
      throw new Error(`Insufficient Bitfinex balance (need ~$10+, have $${available.toFixed(2)})`);
    }
    const qty = computeQty(marginUsd, leverage, limitPrice);

    const orderId = await this.bitfinex.submitLimitOrder(creds, {
      direction: intent.direction,
      qty,
      price: limitPrice,
    });

    const payload: ExecutionPayload = {
      bitfinexOrderId: orderId,
      limitPrice,
      localMark: mark,
      qty,
      direction: intent.direction,
      source: 'hire',
    };

    await this.cycles.recordHireExecutionEvent(instance.userId, agentId, cycleId, 'ORDER_PLACED', {
      venue: 'bitfinex',
      local_mark_at_signal: mark,
      limit_price: limitPrice,
      qty,
      ...payload,
    });

    this.logger.log(
      `Hire entry ${instance.userId} cycle=${cycleId} ${intent.direction} limit=${limitPrice.toFixed(2)} qty=${qty}`,
    );
  }

  private async monitorEntry(
    agentId: string,
    userId: string,
    cycle: { id: string; intentEnvelope: unknown; expiresAt: Date | null; status: SignalCycleStatus },
    participantId: string,
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
  ) {
    const intent = cycle.intentEnvelope as SignalIntentEnvelope;
    const orderId = meta.bitfinexOrderId;
    if (!orderId || !meta.qty || !meta.direction) return;

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
    if (active) return;

    const position = await this.bitfinex.getOpenPosition(creds);
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
    const leverage = intent.risk.leverage_hint ?? 20;
    const stopPrice = computeStopPrice(
      fillPrice,
      meta.direction,
      intent.risk.stop_loss_margin_pct ?? -18,
      leverage,
    );

    const stopOrderId = await this.bitfinex.submitStopOrder(creds, {
      positionDirection: meta.direction,
      qty: meta.qty,
      stopPrice,
    });

    await this.cycles.recordHireExecutionEvent(userId, agentId, cycle.id, 'FILLED', {
      venue: 'bitfinex',
      fill_price: fillPrice,
      qty: meta.qty,
      stop_loss_placed: true,
      stop_loss_margin_pct: intent.risk.stop_loss_margin_pct ?? -18,
      stopOrderId,
      source: 'hire',
    });

    this.logger.log(
      `Hire fill ${userId} cycle=${cycle.id} @ ${fillPrice.toFixed(2)} stop=${stopPrice.toFixed(2)}`,
    );
  }

  private async monitorExit(
    agentId: string,
    userId: string,
    cycle: {
      id: string;
      status: SignalCycleStatus;
      showcasePnlUsd: { toNumber?: () => number } | null;
      closedAt: Date | null;
    },
    participant: { id: string; fillPrice: { toNumber?: () => number } | null },
    meta: ExecutionPayload,
    creds: ExchangeCredentials,
  ) {
    if (cycle.status !== SignalCycleStatus.CLOSED && cycle.status !== SignalCycleStatus.EXPIRED) {
      return;
    }

    if (!meta.qty || !meta.direction) return;

    const fillPrice = participant.fillPrice != null ? Number(participant.fillPrice) : meta.limitPrice;
    let exitPrice = fillPrice ?? 0;
    try {
      exitPrice = await this.bitfinex.getMarkPrice();
      await this.bitfinex.submitMarketClose(creds, {
        positionDirection: meta.direction,
        qty: meta.qty,
      });
    } catch (err) {
      this.logger.warn(`Market close ${userId} cycle=${cycle.id}: ${err instanceof Error ? err.message : err}`);
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
        ? (pnlUsd / (fillPrice * meta.qty)) * 100 * (meta.direction ? 20 : 1)
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
