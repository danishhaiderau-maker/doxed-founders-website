import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  Logger,
  NotFoundException,
  OnModuleInit,
  UnauthorizedException,
} from '@nestjs/common';
import {
  SignalCycleSettlementStatus,
  SignalCycleStatus,
  type Prisma,
} from '@prisma/client';
import { computeSignalSuccessFeeUsd, type SignalCycleEventType } from '@dcf/utils';
import { createHash, randomBytes } from 'crypto';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';
import { BotBridgeService } from './bot-bridge.service';
import {
  buildIntentEnvelope,
  extractBotApproveSnapshot,
} from './signal-envelope.mapper';

const DDOLLAR_PER_USD = 100;

export type SignalApiKeyContext = {
  userId: string;
  agentId: string;
  keyId: string;
};

@Injectable()
export class SignalCyclesService implements OnModuleInit {
  private readonly logger = new Logger(SignalCyclesService.name);
  private lastSeenTradeId: string | null = null;

  constructor(
    private readonly prisma: PrismaService,
    private readonly botBridge: BotBridgeService,
    private readonly points: PointsService,
  ) {}

  onModuleInit() {
    void this.bootstrapLastTradeId();
    setInterval(() => void this.pollBotForIntents(), 25_000);
    setInterval(() => void this.syncShowcaseCycleClosures(), 30_000);
  }

  private async bootstrapLastTradeId() {
    const agent = await this.prisma.tradingAgent.findUnique({
      where: { slug: 'conservative-btc' },
    });
    if (!agent) return;
    const latest = await this.prisma.signalCycle.findFirst({
      where: { agentId: agent.id },
      orderBy: { createdAt: 'desc' },
    });
    if (latest) this.lastSeenTradeId = latest.tradeId;
  }

  private async resolveAgent(slug: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');
    return agent;
  }

  async createApiKey(userId: string, slug: string, label?: string) {
    const agent = await this.resolveAgent(slug);
    const raw = `dcf_sig_${randomBytes(24).toString('hex')}`;
    const keyHash = createHash('sha256').update(raw).digest('hex');
    const keyPrefix = raw.slice(0, 12);
    const row = await this.prisma.signalApiKey.create({
      data: {
        userId,
        agentId: agent.id,
        label: label?.trim() || null,
        keyPrefix,
        keyHash,
      },
    });
    return {
      id: row.id,
      keyPrefix,
      label: row.label,
      apiKey: raw,
      createdAt: row.createdAt.toISOString(),
      message: 'Store this key now — it will not be shown again.',
    };
  }

  async listApiKeys(userId: string, slug: string) {
    const agent = await this.resolveAgent(slug);
    const rows = await this.prisma.signalApiKey.findMany({
      where: { userId, agentId: agent.id, revokedAt: null },
      orderBy: { createdAt: 'desc' },
    });
    return rows.map((r) => ({
      id: r.id,
      keyPrefix: r.keyPrefix,
      label: r.label,
      lastUsedAt: r.lastUsedAt?.toISOString() ?? null,
      createdAt: r.createdAt.toISOString(),
    }));
  }

  async revokeApiKey(userId: string, slug: string, keyId: string) {
    const agent = await this.resolveAgent(slug);
    const row = await this.prisma.signalApiKey.findFirst({
      where: { id: keyId, userId, agentId: agent.id, revokedAt: null },
    });
    if (!row) throw new NotFoundException('API key not found');
    await this.prisma.signalApiKey.update({
      where: { id: keyId },
      data: { revokedAt: new Date() },
    });
    return { ok: true };
  }

  async authenticateApiKey(rawKey: string | undefined): Promise<SignalApiKeyContext | null> {
    if (!rawKey?.trim()) return null;
    const keyHash = createHash('sha256').update(rawKey.trim()).digest('hex');
    const row = await this.prisma.signalApiKey.findFirst({
      where: { keyHash, revokedAt: null },
    });
    if (!row) return null;
    await this.prisma.signalApiKey.update({
      where: { id: row.id },
      data: { lastUsedAt: new Date() },
    });
    return { userId: row.userId, agentId: row.agentId, keyId: row.id };
  }

  async pollBotForIntents() {
    if (!this.botBridge.isEnabled()) return;
    const agent = await this.prisma.tradingAgent.findUnique({
      where: { slug: 'conservative-btc' },
    });
    if (!agent) return;

    const bot = await this.botBridge.fetchState(true);
    if (!bot) return;

    const lao = extractBotApproveSnapshot(bot);
    if (!lao?.trade_id) return;
    if (lao.trade_id === this.lastSeenTradeId) return;
    if (lao.status !== 'EXECUTED' && lao.status !== 'PENDING') return;

    const existing = await this.prisma.signalCycle.findUnique({
      where: { agentId_tradeId: { agentId: agent.id, tradeId: lao.trade_id } },
    });
    if (existing) {
      this.lastSeenTradeId = lao.trade_id;
      return;
    }

    const cycleId = `cyc_${randomBytes(8).toString('hex')}`;
    const envelope = buildIntentEnvelope(cycleId, lao.trade_id, bot);
    if (!envelope) return;

    const ttlSec = envelope.entry.ttl_sec;
    await this.prisma.signalCycle.create({
      data: {
        id: cycleId,
        agentId: agent.id,
        tradeId: lao.trade_id,
        status: SignalCycleStatus.INTENT,
        botVersion: bot.bot_version ?? null,
        intentEnvelope: envelope as unknown as Prisma.InputJsonValue,
        researchVenue: 'bitfinex',
        expiresAt: new Date(Date.now() + ttlSec * 1000),
      },
    });
    this.lastSeenTradeId = lao.trade_id;
    this.logger.log(`Signal cycle INTENT ${cycleId} trade=${lao.trade_id}`);
  }

  async syncShowcaseCycleClosures() {
    if (!this.botBridge.isEnabled()) return;
    const agent = await this.prisma.tradingAgent.findUnique({
      where: { slug: 'conservative-btc' },
    });
    if (!agent) return;

    const openCycles = await this.prisma.signalCycle.findMany({
      where: {
        agentId: agent.id,
        status: { in: [SignalCycleStatus.INTENT, SignalCycleStatus.PENDING_ENTRY, SignalCycleStatus.OPEN] },
      },
      take: 20,
    });
    if (!openCycles.length) return;

    const bot = await this.botBridge.fetchState();
    if (!bot) return;

    const trades = bot.trades ?? [];
    for (const cycle of openCycles) {
      const trade = trades.find((t) => t.trade_id === cycle.tradeId);
      if (trade?.exit != null && trade.pnl != null) {
        await this.prisma.signalCycle.update({
          where: { id: cycle.id },
          data: {
            status: SignalCycleStatus.CLOSED,
            closedAt: new Date(),
            showcasePnlUsd: trade.net_pnl_usd ?? trade.pnl,
            showcaseExitReason: trade.exit_reason ?? 'SHOWCASE_CLOSED',
          },
        });
      } else if (cycle.expiresAt && cycle.expiresAt < new Date() && cycle.status === SignalCycleStatus.INTENT) {
        await this.prisma.signalCycle.update({
          where: { id: cycle.id },
          data: { status: SignalCycleStatus.EXPIRED, closedAt: new Date() },
        });
      }
    }
  }

  async getLatest(slug: string, apiCtx: SignalApiKeyContext | null) {
    const agent = await this.resolveAgent(slug);
    const cycle = await this.prisma.signalCycle.findFirst({
      where: {
        agentId: agent.id,
        status: { in: [SignalCycleStatus.INTENT, SignalCycleStatus.PENDING_ENTRY, SignalCycleStatus.OPEN] },
      },
      orderBy: { createdAt: 'desc' },
    });
    if (!cycle) return { cycle: null, mandate: this.subscriberMandate() };

    const envelope = cycle.intentEnvelope as Record<string, unknown>;
    if (!apiCtx) {
      return {
        cycle: {
          cycleId: cycle.id,
          status: cycle.status,
          direction: envelope?.direction ?? null,
          createdAt: cycle.createdAt.toISOString(),
          preview: true,
          message: 'Create a signal API key for full ENSE payload and lifecycle webhooks.',
        },
        mandate: this.subscriberMandate(),
      };
    }

    return {
      cycle: {
        cycleId: cycle.id,
        tradeId: cycle.tradeId,
        status: cycle.status,
        intent: cycle.intentEnvelope,
        expiresAt: cycle.expiresAt?.toISOString() ?? null,
        botVersion: cycle.botVersion,
        createdAt: cycle.createdAt.toISOString(),
      },
      mandate: this.subscriberMandate(),
    };
  }

  subscriberMandate() {
    return {
      stop_loss_at_fill: true,
      use_subscriber_mark_at_receipt: true,
      no_research_venue_absolute_prices: true,
      success_fee: {
        pct: 0.1,
        min_profit_fee_usd: 0.2,
        min_charge_usd: 0.1,
        charge_on_loss: false,
      },
      docs: '/docs/signal-api',
    };
  }

  async listCycles(slug: string, userId: string, limit = 20) {
    const agent = await this.resolveAgent(slug);
    const rows = await this.prisma.signalCycle.findMany({
      where: { agentId: agent.id },
      orderBy: { createdAt: 'desc' },
      take: Math.min(limit, 50),
      include: {
        participants: { where: { userId } },
      },
    });
    return rows.map((c) => this.serializeCycle(c, c.participants[0] ?? null));
  }

  async getCycle(slug: string, cycleId: string, userId: string) {
    const agent = await this.resolveAgent(slug);
    const cycle = await this.prisma.signalCycle.findFirst({
      where: { id: cycleId, agentId: agent.id },
      include: {
        participants: { where: { userId } },
        events: { orderBy: { createdAt: 'desc' }, take: 30 },
      },
    });
    if (!cycle) throw new NotFoundException('Cycle not found');
    return {
      ...this.serializeCycle(cycle, cycle.participants[0] ?? null),
      events: cycle.events.map((e) => ({
        id: e.id,
        eventType: e.eventType,
        payload: e.payload,
        createdAt: e.createdAt.toISOString(),
      })),
    };
  }

  private serializeCycle(
    cycle: {
      id: string;
      tradeId: string;
      status: SignalCycleStatus;
      intentEnvelope: unknown;
      botVersion: string | null;
      expiresAt: Date | null;
      showcasePnlUsd: { toNumber?: () => number } | null;
      closedAt: Date | null;
      createdAt: Date;
    },
    participant: {
      status: SignalCycleStatus;
      venue: string | null;
      stopLossConfirmedAt: Date | null;
      pnlUsd: { toNumber?: () => number } | null;
      feeUsd: { toNumber?: () => number } | null;
      settlementStatus: SignalCycleSettlementStatus;
    } | null,
  ) {
    return {
      cycleId: cycle.id,
      tradeId: cycle.tradeId,
      status: cycle.status,
      intent: cycle.intentEnvelope,
      botVersion: cycle.botVersion,
      expiresAt: cycle.expiresAt?.toISOString() ?? null,
      showcasePnlUsd: cycle.showcasePnlUsd != null ? Number(cycle.showcasePnlUsd) : null,
      closedAt: cycle.closedAt?.toISOString() ?? null,
      createdAt: cycle.createdAt.toISOString(),
      participant: participant
        ? {
            status: participant.status,
            venue: participant.venue,
            stopLossConfirmed: Boolean(participant.stopLossConfirmedAt),
            pnlUsd: participant.pnlUsd != null ? Number(participant.pnlUsd) : null,
            feeUsd: participant.feeUsd != null ? Number(participant.feeUsd) : null,
            settlementStatus: participant.settlementStatus,
          }
        : null,
    };
  }

  async postEvent(
    slug: string,
    cycleId: string,
    ctx: SignalApiKeyContext,
    body: {
      event: SignalCycleEventType;
      venue?: string;
      local_mark_at_signal?: number;
      limit_price?: number;
      fill_price?: number;
      exit_price?: number;
      qty?: number;
      pnl_usd?: number;
      pnl_margin_pct?: number;
      stop_loss_placed?: boolean;
      stop_loss_margin_pct?: number;
      exit_reason?: string;
    },
  ) {
    const agent = await this.resolveAgent(slug);
    if (ctx.agentId !== agent.id) {
      throw new ForbiddenException('API key is not valid for this agent');
    }

    const cycle = await this.prisma.signalCycle.findFirst({
      where: { id: cycleId, agentId: agent.id },
    });
    if (!cycle) throw new NotFoundException('Cycle not found');

    const event = body.event;
    let participant = await this.prisma.signalCycleParticipant.findUnique({
      where: { cycleId_userId: { cycleId, userId: ctx.userId } },
    });

    if (!participant) {
      participant = await this.prisma.signalCycleParticipant.create({
        data: {
          cycleId,
          userId: ctx.userId,
          venue: body.venue ?? null,
          status: SignalCycleStatus.PENDING_ENTRY,
        },
      });
    }

    await this.prisma.signalCycleEvent.create({
      data: {
        cycleId,
        participantId: participant.id,
        eventType: event,
        payload: body as unknown as Prisma.InputJsonValue,
      },
    });

    if (event === 'ORDER_PLACED') {
      await this.prisma.signalCycleParticipant.update({
        where: { id: participant.id },
        data: {
          venue: body.venue ?? participant.venue,
          status: SignalCycleStatus.PENDING_ENTRY,
        },
      });
      if (cycle.status === SignalCycleStatus.INTENT) {
        await this.prisma.signalCycle.update({
          where: { id: cycleId },
          data: { status: SignalCycleStatus.PENDING_ENTRY },
        });
      }
    }

    if (event === 'FILLED' || event === 'STOP_LOSS_ARMED') {
      if (!body.stop_loss_placed) {
        throw new BadRequestException(
          'stop_loss_placed=true is mandatory at fill. Place exchange-native stop before acknowledging FILLED.',
        );
      }
      const intent = cycle.intentEnvelope as { risk?: { stop_loss_margin_pct?: number } };
      const expectedSl = intent?.risk?.stop_loss_margin_pct ?? -18;
      if (
        body.stop_loss_margin_pct != null &&
        Math.abs(body.stop_loss_margin_pct - expectedSl) > 0.5
      ) {
        throw new BadRequestException(
          `stop_loss_margin_pct must match intent (${expectedSl}). Adjust order or reject signal.`,
        );
      }

      await this.prisma.signalCycleParticipant.update({
        where: { id: participant.id },
        data: {
          status: SignalCycleStatus.OPEN,
          stopLossConfirmedAt: new Date(),
          fillPrice: body.fill_price ?? null,
          venue: body.venue ?? participant.venue,
        },
      });
      await this.prisma.signalCycle.update({
        where: { id: cycleId },
        data: { status: SignalCycleStatus.OPEN },
      });
    }

    if (event === 'EXIT' || event === 'EXPIRED') {
      const pnlUsd = body.pnl_usd ?? 0;
      const feeUsd = computeSignalSuccessFeeUsd(pnlUsd);
      let settlementStatus: SignalCycleSettlementStatus = SignalCycleSettlementStatus.WAIVED;
      let settledAt: Date | null = null;

      if (feeUsd > 0) {
        settlementStatus = SignalCycleSettlementStatus.PENDING;
        try {
          const ddollar = Math.ceil(feeUsd * DDOLLAR_PER_USD);
          await this.points.spend(ctx.userId, ddollar, `SIGNAL_CYCLE_FEE:${cycleId}`);
          await this.points.creditAdminFee(ddollar, `signal-cycle:${cycleId}`);
          settlementStatus = SignalCycleSettlementStatus.PAID;
          settledAt = new Date();
        } catch {
          settlementStatus = SignalCycleSettlementStatus.FAILED;
        }
      }

      await this.prisma.signalCycleParticipant.update({
        where: { id: participant.id },
        data: {
          status: SignalCycleStatus.CLOSED,
          exitPrice: body.exit_price ?? null,
          pnlUsd: pnlUsd,
          pnlMarginPct: body.pnl_margin_pct ?? null,
          feeUsd,
          settlementStatus,
          settledAt,
        },
      });

      await this.prisma.signalCycle.update({
        where: { id: cycleId },
        data: {
          status: event === 'EXPIRED' ? SignalCycleStatus.EXPIRED : SignalCycleStatus.CLOSED,
          closedAt: new Date(),
        },
      });

      return {
        ok: true,
        settlement: {
          pnl_usd: pnlUsd,
          fee_usd: feeUsd,
          settlement_status: settlementStatus,
          message:
            feeUsd > 0
              ? `Success fee $${feeUsd.toFixed(2)} (10% of profit, min thresholds applied).`
              : pnlUsd <= 0
                ? 'No fee — losing or flat trade.'
                : 'No fee — profit share below $0.20 threshold.',
        },
      };
    }

    return { ok: true };
  }

  requireApiKey(ctx: SignalApiKeyContext | null): SignalApiKeyContext {
    if (!ctx) throw new UnauthorizedException('Missing or invalid X-Signal-Api-Key');
    return ctx;
  }
}
