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
  NotificationType,
  SignalCycleSettlementStatus,
  SignalCycleStatus,
  UserRole,
  type Prisma,
} from '@prisma/client';
import { PrismaClientKnownRequestError } from '@prisma/client/runtime/library';
import {
  computeSignalSuccessFeeUsd,
  SIGNAL_LEGAL_DISCLAIMER,
  resolveSignalCyclePollMs,
  pickCanonicalTradeId,
  isPaperLaneTradeId,
  type SignalCycleEventType,
} from '@dcf/utils';
import { createHash, randomBytes } from 'crypto';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';
import { NotificationsService } from '../notifications/notifications.service';
import { BotBridgeService } from './bot-bridge.service';
import {
  resolveEvmTreasuryAddress,
  resolveSolanaTreasuryAddress,
  solanaRpcUrl,
} from '../payments/platform-treasury';
import {
  isX402BazaarEnabled,
  resolveX402FacilitatorUrl,
  X402_BAZAAR_CATALOG_URL,
  X402_BAZAAR_SEARCH_URL,
} from '../payments/x402-signal.config';
import { verifySolanaTopUpPayment } from '../payments/solana-tx-verify';
import {
  buildIntentEnvelope,
  extractBotApproveSnapshot,
} from './signal-envelope.mapper';
import { loadSubscriberMaxMarginUsd } from './subscriber-margin.util';
import { normalizeBotSessionTrades } from './bot-state.mapper';
import { resolveShowcaseTradeDetails, tradeIdsMatch } from './relay-fidelity.mapper';

const DDOLLAR_PER_USD = 100;
const SIGNAL_POLL_MS = resolveSignalCyclePollMs();
const BARE_UUID_TRADE_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isBareUuidTradeId(tradeId: string | null | undefined): boolean {
  return Boolean(tradeId && BARE_UUID_TRADE_ID.test(tradeId));
}

/** AI_SCAN used to publish bare UUIDs — resolve the executing lane trade from live bot state. */
function resolveRelayIntentTradeId(
  bot: NonNullable<Awaited<ReturnType<BotBridgeService['fetchStateForExecution']>>>,
  laoTradeId: string,
): string {
  if (!isBareUuidTradeId(laoTradeId)) return laoTradeId;

  for (const o of [...(bot.orders ?? [])].reverse()) {
    const tid = String(o.trade_id ?? '');
    if (tid && !isBareUuidTradeId(tid)) return tid;
  }
  for (const s of [...(bot.signal_info?.signals ?? [])].reverse()) {
    const tid = String(s.trade_id ?? '');
    if (tid && !isBareUuidTradeId(tid)) return tid;
  }
  for (const t of normalizeBotSessionTrades(bot).slice(-12).reverse()) {
    const tid = String(t.trade_id ?? '');
    if (tid && !isBareUuidTradeId(tid)) return tid;
  }
  return laoTradeId;
}

export type SignalApiKeyContext = {
  userId: string;
  agentId: string;
  keyId: string;
};

@Injectable()
export class SignalCyclesService implements OnModuleInit {
  private readonly logger = new Logger(SignalCyclesService.name);
  private lastSeenTradeId: string | null = null;
  private pollingIntents = false;
  private backfilling = false;

  constructor(
    private readonly prisma: PrismaService,
    private readonly botBridge: BotBridgeService,
    private readonly points: PointsService,
    private readonly notifications: NotificationsService,
  ) {}

  onModuleInit() {
    void this.bootstrapLastTradeId();
    this.logger.log(`Signal cycle bridge polling every ${SIGNAL_POLL_MS / 1000}s`);
    setInterval(() => void this.pollBotForIntents(), SIGNAL_POLL_MS);
    setInterval(() => void this.syncShowcaseCycleClosures(), SIGNAL_POLL_MS);
    // C4 fix: periodic reconnect-backfill — catches signals missed while the bot was
    // briefly unreachable (a poll failed during a trade's lao window and the bot
    // advanced past it). Scans session trades for still-OPEN showcase positions with
    // no cycle row and creates INTENT cycles so the relay can still copy them.
    setInterval(() => void this.backfillMissedIntents(), 30_000);
    setTimeout(() => void this.pollBotForIntents(), 1_000);
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

  private requireAdmin(role: string) {
    if (role !== UserRole.ADMIN) {
      throw new ForbiddenException('Signal API keys are admin-only');
    }
  }

  async createApiKey(userId: string, slug: string, label: string | undefined, role: string) {
    this.requireAdmin(role);
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

  async listApiKeys(userId: string, slug: string, role: string) {
    this.requireAdmin(role);
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

  async revokeApiKey(userId: string, slug: string, keyId: string, role: string) {
    this.requireAdmin(role);
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

  async pollBotForIntents(): Promise<boolean> {
    if (this.pollingIntents) return false; // prevent setInterval + setTimeout + wake race
    this.pollingIntents = true;
    try {
      return await this._pollBotForIntentsInner();
    } finally {
      this.pollingIntents = false;
    }
  }

  private async _pollBotForIntentsInner(): Promise<boolean> {
    if (!this.botBridge.isEnabled()) return false;
    const agent = await this.prisma.tradingAgent.findUnique({
      where: { slug: 'conservative-btc' },
    });
    if (!agent) return false;

    const bot = await this.botBridge.fetchStateForExecution(true);
    if (!bot) return false;

    const lao = extractBotApproveSnapshot(bot);
    if (!lao?.trade_id) return false;
    if (lao.trade_id === this.lastSeenTradeId) return false;
    if (lao.status !== 'EXECUTED' && lao.status !== 'PENDING') return false;

    const intentTradeId = resolveRelayIntentTradeId(bot, lao.trade_id);
    // F6 — refuse to create a copy intent for paper-lane trades (a160v2-*, etc).
    // These are the showcase bot's shadow-sim research lanes — paper P&L only,
    // never real Bitfinex fills. Mirroring them would put real money on a
    // trade that has no real showcase counterpart (the exact bug pattern from
    // the 2026-07-07 incident). Skip silently — research data still flows to
    // the analyzer.
    if (isPaperLaneTradeId(intentTradeId)) {
      this.logger.warn(
        `Skipping paper-lane trade_id=${intentTradeId} (F6: paper lane never mirrored to live copy)`,
      );
      this.lastSeenTradeId = intentTradeId;
      return false;
    }
    const showcaseMatch = resolveShowcaseTradeDetails(bot, intentTradeId);
    const canonicalTradeId = pickCanonicalTradeId(
      intentTradeId,
      showcaseMatch?.matchedTradeId ?? intentTradeId,
    );

    const existing = await this.prisma.signalCycle.findUnique({
      where: { agentId_tradeId: { agentId: agent.id, tradeId: canonicalTradeId } },
    });
    if (existing) {
      this.lastSeenTradeId = intentTradeId;
      return false;
    }

    const recentCycles = await this.prisma.signalCycle.findMany({
      where: { agentId: agent.id },
      orderBy: { createdAt: 'desc' },
      take: 30,
      select: { tradeId: true },
    });
    if (recentCycles.some((c) => tradeIdsMatch(c.tradeId, canonicalTradeId))) {
      this.lastSeenTradeId = intentTradeId;
      return false;
    }

    const cycleId = `cyc_${randomBytes(8).toString('hex')}`;
    const maxMarginUsd = await loadSubscriberMaxMarginUsd(this.prisma);
    const envelope = buildIntentEnvelope(cycleId, canonicalTradeId, bot, { maxMarginUsd });
    if (!envelope) return false;

    const ttlSec = envelope.entry.ttl_sec;
    try {
      await this.prisma.signalCycle.create({
        data: {
          id: cycleId,
          agentId: agent.id,
          tradeId: canonicalTradeId,
          status: SignalCycleStatus.INTENT,
          botVersion: bot.bot_version ?? null,
          intentEnvelope: envelope as unknown as Prisma.InputJsonValue,
          researchVenue: 'bitfinex',
          expiresAt: new Date(Date.now() + ttlSec * 1000),
        },
      });
    } catch (err) {
      // Concurrent poll/wake can race past the findUnique pre-check and hit the
      // (agentId, tradeId) unique constraint. Treat as "already created" — never crash the API.
      if (err instanceof PrismaClientKnownRequestError && err.code === 'P2002') {
        this.lastSeenTradeId = intentTradeId;
        this.logger.warn(`Signal cycle already exists for trade=${canonicalTradeId} (P2002) — skipping`);
        return false;
      }
      throw err;
    }
    this.lastSeenTradeId = intentTradeId;
    this.logger.log(`Signal cycle INTENT ${cycleId} trade=${canonicalTradeId} (bot lao=${lao.trade_id})`);
    return true;
  }

  /**
   * C4 reconnect-backfill: scan the showcase bot's session trades for still-OPEN
   * positions that have no signalCycle row (missed because a poll failed during the
   * trade's last_approve_outcome window and the bot advanced past it). Creates INTENT
   * cycles so the relay can still copy them. Already-closed showcase trades are skipped
   * (handled by syncShowcaseCycleClosures + the cycle-close-cancel path).
   */
  async backfillMissedIntents(): Promise<boolean> {
    if (this.backfilling) return false;
    this.backfilling = true;
    try {
      if (!this.botBridge.isEnabled()) return false;
      const agent = await this.prisma.tradingAgent.findUnique({
        where: { slug: 'conservative-btc' },
      });
      if (!agent) return false;
      const bot = await this.botBridge.fetchStateForExecution(true);
      if (!bot) return false;

      const sessionTrades = normalizeBotSessionTrades(bot).filter(
        (t) => t.trade_id && t.exit == null,
      );
      if (!sessionTrades.length) return false;

      const existing = await this.prisma.signalCycle.findMany({
        where: { agentId: agent.id },
        orderBy: { createdAt: 'desc' },
        take: 300,
        select: { tradeId: true },
      });
      const existingIds = existing.map((c) => c.tradeId);

      const maxMarginUsd = await loadSubscriberMaxMarginUsd(this.prisma);
      let created = 0;
      for (const t of sessionTrades) {
        const tid = String(t.trade_id);
        // F6 — never backfill paper-lane trades. Same rationale as pollBotForIntents.
        if (isPaperLaneTradeId(tid)) {
          this.logger.warn(
            `Skipping backfill of paper-lane trade_id=${tid} (F6: paper lane never mirrored)`,
          );
          continue;
        }
        const showcaseMatch = resolveShowcaseTradeDetails(bot, tid);
        const canonical = pickCanonicalTradeId(tid, showcaseMatch?.matchedTradeId ?? tid);
        if (existingIds.some((e) => tradeIdsMatch(e, canonical))) continue;

        const cycleId = `cyc_${randomBytes(8).toString('hex')}`;
        const envelope = buildIntentEnvelope(cycleId, canonical, bot, { maxMarginUsd });
        if (!envelope) continue;
        const ttlSec = envelope.entry.ttl_sec;
        try {
          await this.prisma.signalCycle.create({
            data: {
              id: cycleId,
              agentId: agent.id,
              tradeId: canonical,
              status: SignalCycleStatus.INTENT,
              botVersion: bot.bot_version ?? null,
              intentEnvelope: envelope as unknown as Prisma.InputJsonValue,
              researchVenue: 'bitfinex',
              expiresAt: new Date(Date.now() + ttlSec * 1000),
            },
          });
          existingIds.push(canonical);
          created++;
          this.logger.log(
            `Signal cycle BACKFILL ${cycleId} trade=${canonical} (missed-during-outage)`,
          );
        } catch (err) {
          if (err instanceof PrismaClientKnownRequestError && err.code === 'P2002') continue;
          throw err;
        }
      }
      return created > 0;
    } finally {
      this.backfilling = false;
    }
  }

  /** Push wake from showcase bot — returns true when a new INTENT was created. */
  async wakeFromShowcase(opts?: { intents?: boolean; closures?: boolean }) {
    const intents = opts?.intents !== false;
    const closures = opts?.closures !== false;
    let created = false;
    if (intents) {
      created = await this.pollBotForIntents();
      // Also run the missed-signal backfill on wake so signals dropped during a brief
      // bot outage are recovered when the showcase pushes its next event.
      if (await this.backfillMissedIntents()) created = true;
    }
    if (closures) {
      await this.syncShowcaseCycleClosures(true);
    }
    return created;
  }

  async syncShowcaseCycleClosures(force = false) {
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
      take: 200,
    });
    if (!openCycles.length) return;

    const bot = await this.botBridge.fetchStateForExecution(force);
    if (!bot) return;

    const trades = normalizeBotSessionTrades(bot);
    const tradesMap = bot.trades_map ?? {};
    for (const cycle of openCycles) {
      let trade = trades.find((t) => t.trade_id && tradeIdsMatch(t.trade_id, cycle.tradeId));
      let mapEntry = tradesMap[cycle.tradeId] as
        | { signal_ref?: Record<string, unknown> }
        | undefined;
      if (!mapEntry) {
        for (const [mapKey, entry] of Object.entries(tradesMap)) {
          const sig = (entry as { signal_ref?: Record<string, unknown> })?.signal_ref;
          const refId = String(sig?.trade_id ?? mapKey);
          if (tradeIdsMatch(refId, cycle.tradeId) || tradeIdsMatch(mapKey, cycle.tradeId)) {
            mapEntry = entry as { signal_ref?: Record<string, unknown> };
            if (!trade) {
              trade = trades.find((t) => t.trade_id && tradeIdsMatch(t.trade_id, refId));
            }
            break;
          }
        }
      }
      const showcaseDetails = resolveShowcaseTradeDetails(bot, cycle.tradeId);
      const sigRef = mapEntry?.signal_ref;
      const mapClosed =
        sigRef &&
        String(sigRef.status ?? '') === 'CLOSED' &&
        (sigRef.exit_price != null || sigRef.closed_ts != null);
      const listedClosed = trade?.exit != null && trade.pnl != null;

      if (listedClosed || mapClosed) {
        const netPnl =
          trade?.net_pnl_usd ??
          (typeof sigRef?.net_pnl_usd === 'number' ? Number(sigRef.net_pnl_usd) : trade?.pnl);
        const canonicalTradeId = showcaseDetails?.matchedTradeId ?? cycle.tradeId;
        await this.prisma.signalCycle.update({
          where: { id: cycle.id },
          data: {
            status: SignalCycleStatus.CLOSED,
            closedAt: new Date(),
            tradeId: canonicalTradeId !== cycle.tradeId ? canonicalTradeId : undefined,
            showcasePnlUsd: netPnl ?? undefined,
            showcaseExitReason:
              trade?.exit_reason ??
              (typeof sigRef?.exit_reason === 'string' ? sigRef.exit_reason : 'SHOWCASE_CLOSED'),
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
    if (!cycle) return { cycle: null, mandate: await this.getSubscriberMandate() };

    const envelope = cycle.intentEnvelope as Record<string, unknown>;
    if (!apiCtx) {
      return {
        cycle: {
          cycleId: cycle.id,
          status: cycle.status,
          direction: envelope?.direction ?? null,
          createdAt: cycle.createdAt.toISOString(),
          preview: true,
          message: 'Create a signal API key or pay via x402 on GET /signals/intent for full ENSE payload.',
        },
        mandate: await this.getSubscriberMandate(),
      };
    }

    return this.buildLatestFullResponse(cycle);
  }

  /** Full ENSE intent — caller must pass API key or have paid via x402 middleware on /signals/intent. */
  async getLatestFull(
    slug: string,
    apiCtx: SignalApiKeyContext | null,
    opts?: { x402Paid?: boolean },
  ) {
    if (!apiCtx && !opts?.x402Paid) {
      throw new UnauthorizedException(
        'Full signal intent requires X-Signal-Api-Key or x402 payment (GET /signals/intent).',
      );
    }
    const agent = await this.resolveAgent(slug);
    const cycle = await this.prisma.signalCycle.findFirst({
      where: {
        agentId: agent.id,
        status: { in: [SignalCycleStatus.INTENT, SignalCycleStatus.PENDING_ENTRY, SignalCycleStatus.OPEN] },
      },
      orderBy: { createdAt: 'desc' },
    });
    if (!cycle) return { cycle: null, mandate: await this.getSubscriberMandate() };
    return this.buildLatestFullResponse(cycle);
  }

  private async buildLatestFullResponse(cycle: {
    id: string;
    tradeId: string;
    status: SignalCycleStatus;
    intentEnvelope: unknown;
    expiresAt: Date | null;
    botVersion: string | null;
    createdAt: Date;
  }) {
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
      mandate: await this.getSubscriberMandate(),
    };
  }

  async getSubscriberMandate() {
    const treasury = await resolveSolanaTreasuryAddress(this.prisma);
    const evmTreasury = await resolveEvmTreasuryAddress(this.prisma);
    const maxMarginUsd = await loadSubscriberMaxMarginUsd(this.prisma);
    const x402Enabled = Boolean(evmTreasury || process.env.X402_EVM_PAY_TO?.trim());
    const facilitator = resolveX402FacilitatorUrl();
    const bazaarEnabled = isX402BazaarEnabled();
    return {
      stop_loss_at_fill: true,
      use_subscriber_mark_at_receipt: true,
      no_research_venue_absolute_prices: true,
      max_margin_usd_per_trade: maxMarginUsd,
      platform_enforced_margin: true,
      x402: x402Enabled
        ? {
            support: true,
            intent_endpoint: '/trading-agents/conservative-btc/signals/intent',
            preview_endpoint: '/trading-agents/conservative-btc/signals/latest',
            price_usd: 0.1,
            price_label: '$0.10',
            network: process.env.X402_SIGNAL_NETWORK ?? 'eip155:8453',
            scheme: 'exact',
            pay_to_evm: evmTreasury ?? process.env.X402_EVM_PAY_TO?.trim() ?? null,
            facilitator,
            bazaar: bazaarEnabled
              ? {
                  discoverable: true,
                  catalog_url: X402_BAZAAR_CATALOG_URL,
                  search_url: X402_BAZAAR_SEARCH_URL,
                  indexed_after: 'first_cdp_settlement',
                }
              : {
                  discoverable: false,
                  note: 'Set CDP_API_KEY_ID + CDP_API_KEY_SECRET for x402 Bazaar listing',
                },
          }
        : { support: false },
      success_fee: {
        pct: 0.1,
        min_profit_fee_usd: 0.2,
        min_charge_usd: 0.1,
        charge_on_loss: false,
        settlement_order: ['ddollar_balance', 'solana_usdc'],
        treasury_solana: treasury,
        settle_endpoint: '/trading-agents/conservative-btc/signals/cycles/{cycleId}/settle',
      },
      docs: '/docs/signal-api',
      disclaimer: SIGNAL_LEGAL_DISCLAIMER,
      admin_owned: true,
      hire_fee_ddollar: 2000,
      hire_duration_days: 7,
    };
  }

  /** @deprecated use getSubscriberMandate */
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
      let feeSettlementMethod: string | null = null;
      let feeTreasuryAddress: string | null = null;
      let feePaymentReference: string | null = null;
      let solanaPayment: {
        treasuryAddress: string;
        amountUsd: number;
        reference: string;
        asset: 'USDC';
        memo: string;
        instructions: string;
      } | null = null;

      if (feeUsd > 0) {
        feePaymentReference = `SIG-${cycleId.replace(/^cyc_/, '').slice(0, 8).toUpperCase()}`;
        feeTreasuryAddress = await resolveSolanaTreasuryAddress(this.prisma);

        try {
          const ddollar = Math.ceil(feeUsd * DDOLLAR_PER_USD);
          await this.points.spend(ctx.userId, ddollar, `SIGNAL_CYCLE_FEE:${cycleId}`);
          await this.points.creditAdminFee(ddollar, `signal-cycle:${cycleId}`);
          settlementStatus = SignalCycleSettlementStatus.PAID;
          feeSettlementMethod = 'DDOLLAR';
          settledAt = new Date();
        } catch {
          if (feeTreasuryAddress) {
            settlementStatus = SignalCycleSettlementStatus.PENDING;
            feeSettlementMethod = 'SOLANA_USDC';
            solanaPayment = {
              treasuryAddress: feeTreasuryAddress,
              amountUsd: feeUsd,
              reference: feePaymentReference,
              asset: 'USDC',
              memo: feePaymentReference,
              instructions: `Send $${feeUsd.toFixed(2)} USDC on Solana from your linked wallet to ${feeTreasuryAddress}. Memo: ${feePaymentReference}. Then POST tx_signature to .../cycles/${cycleId}/settle`,
            };
          } else {
            settlementStatus = SignalCycleSettlementStatus.FAILED;
            feeSettlementMethod = null;
          }
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
          feeTreasuryAddress,
          feePaymentReference,
          feeSettlementMethod,
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
          settlement_method: feeSettlementMethod,
          solana_payment: solanaPayment,
          message:
            feeUsd > 0
              ? settlementStatus === SignalCycleSettlementStatus.PAID
                ? `Success fee $${feeUsd.toFixed(2)} settled via DDollar.`
                : settlementStatus === SignalCycleSettlementStatus.PENDING
                  ? `Success fee $${feeUsd.toFixed(2)} due — pay USDC to admin treasury (Solana).`
                  : `Success fee $${feeUsd.toFixed(2)} due — configure admin Solana treasury or add DDollar balance.`
              : pnlUsd <= 0
                ? 'No fee — losing or flat trade.'
                : 'No fee — profit share below $0.20 threshold.',
        },
      };
    }

    return { ok: true };
  }

  /** Platform hire runner — records lifecycle without Signal API key; no success fee on hire tier. */
  async recordHireExecutionEvent(
    userId: string,
    agentId: string,
    cycleId: string,
    event: SignalCycleEventType,
    body: Record<string, unknown>,
  ) {
    const cycle = await this.prisma.signalCycle.findFirst({
      where: { id: cycleId, agentId },
    });
    if (!cycle) throw new NotFoundException('Cycle not found');

    let participant = await this.prisma.signalCycleParticipant.findUnique({
      where: { cycleId_userId: { cycleId, userId } },
    });

    if (!participant) {
      participant = await this.prisma.signalCycleParticipant.create({
        data: {
          cycleId,
          userId,
          venue: typeof body.venue === 'string' ? body.venue : 'bitfinex',
          status: SignalCycleStatus.PENDING_ENTRY,
        },
      });
    }

    await this.prisma.signalCycleEvent.create({
      data: {
        cycleId,
        participantId: participant.id,
        eventType: event,
        payload: body as Prisma.InputJsonValue,
      },
    });

    if (event === 'ORDER_PLACED') {
      await this.prisma.signalCycleParticipant.update({
        where: { id: participant.id },
        data: {
          venue: typeof body.venue === 'string' ? body.venue : participant.venue,
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

    if (event === 'FILLED') {
      await this.prisma.signalCycleParticipant.update({
        where: { id: participant.id },
        data: {
          status: SignalCycleStatus.OPEN,
          stopLossConfirmedAt: body.stop_loss_placed === true ? new Date() : participant.stopLossConfirmedAt,
          fillPrice: typeof body.fill_price === 'number' ? body.fill_price : null,
          venue: typeof body.venue === 'string' ? body.venue : participant.venue,
        },
      });
      await this.prisma.signalCycle.update({
        where: { id: cycleId },
        data: { status: SignalCycleStatus.OPEN },
      });
    }

    if (event === 'STOP_LOSS_ARMED') {
      await this.prisma.signalCycleParticipant.update({
        where: { id: participant.id },
        data: {
          status: SignalCycleStatus.OPEN,
          stopLossConfirmedAt: new Date(),
        },
      });
      if (cycle.status !== SignalCycleStatus.OPEN) {
        await this.prisma.signalCycle.update({
          where: { id: cycleId },
          data: { status: SignalCycleStatus.OPEN },
        });
      }
    }

    if (event === 'EXIT' || event === 'EXPIRED') {
      const pnlUsd = typeof body.pnl_usd === 'number' ? body.pnl_usd : 0;
      const pnlMarginPct = typeof body.pnl_margin_pct === 'number' ? body.pnl_margin_pct : null;
      const exitPrice = typeof body.exit_price === 'number' ? body.exit_price : null;
      await this.prisma.signalCycleParticipant.update({
        where: { id: participant.id },
        data: {
          status: event === 'EXPIRED' ? SignalCycleStatus.EXPIRED : SignalCycleStatus.CLOSED,
          exitPrice,
          pnlUsd,
          pnlMarginPct,
          settlementStatus: SignalCycleSettlementStatus.WAIVED,
          feeUsd: 0,
          settledAt: new Date(),
        },
      });
      if (cycle.status !== SignalCycleStatus.CLOSED && cycle.status !== SignalCycleStatus.EXPIRED) {
        await this.prisma.signalCycle.update({
          where: { id: cycleId },
          data: {
            status: event === 'EXPIRED' ? SignalCycleStatus.EXPIRED : SignalCycleStatus.CLOSED,
            closedAt: new Date(),
          },
        });
      }

      // Significant-trade alert: only emit on real closures (not EXPIRED/no-fill) where the
      // realized margin PnL magnitude is >= 20% (gain OR loss). Position opens, small moves,
      // and expiries never create Alerts. Full trade detail is attached in metadata for the
      // Alerts card to render.
      if (event === 'EXIT' && pnlMarginPct != null && Math.abs(pnlMarginPct) >= 20) {
        await this.maybeNotifySignificantTradeClose({
          userId,
          agentId,
          cycleId,
          participantId: participant.id,
          pnlMarginPct,
          pnlUsd,
          exitPrice,
          exitReason: typeof body.exit_reason === 'string' ? body.exit_reason : null,
          qtyClosed: typeof body.qty_closed === 'number' ? body.qty_closed : null,
        });
      }
    }

    return { ok: true, participantId: participant.id };
  }

  /**
   * Build + send the significant-trade-close Alerts notification. Pulls entry price, side,
   * symbol, and leverage from the cycle's intent envelope + agent row; close price, PnL %,
   * PnL $, trigger, and size come from the EXIT event payload. Trigger is mapped from the
   * bot relay-state exit_reason (PROFIT_LOCK / THESIS_FAST_CUT → Take Profit; HARD_STOP /
   * EXCHANGE_STOP → Stop Loss; MANUAL_* / IMMEDIATE_EXCHANGE_FLAT → Manual; everything else
   * → Signal).
   */
  private async maybeNotifySignificantTradeClose(input: {
    userId: string;
    agentId: string;
    cycleId: string;
    participantId: string;
    pnlMarginPct: number;
    pnlUsd: number;
    exitPrice: number | null;
    exitReason: string | null;
    qtyClosed: number | null;
  }) {
    try {
      const agent = await this.prisma.tradingAgent.findUnique({
        where: { id: input.agentId },
        select: { name: true, assetSymbol: true, slug: true },
      });
      if (!agent) return;

      const participant = await this.prisma.signalCycleParticipant.findUnique({
        where: { id: input.participantId },
        select: { fillPrice: true },
      });
      const cycle = await this.prisma.signalCycle.findUnique({
        where: { id: input.cycleId },
        select: { intentEnvelope: true, tradeId: true },
      });
      if (!cycle) return;

      const intent = (cycle.intentEnvelope ?? {}) as {
        direction?: 'LONG' | 'SHORT';
        risk?: { leverage_hint?: number };
      };
      const side = intent.direction ?? null;
      const leverage = intent.risk?.leverage_hint ?? null;
      const entryPrice = participant?.fillPrice != null ? Number(participant.fillPrice) : null;
      const symbol = agent.assetSymbol ?? 'BTC';

      const trigger = mapExitReasonToTrigger(input.exitReason);

      const isGain = input.pnlMarginPct >= 0;
      const sign = isGain ? '+' : '−';
      const roundedPct = Math.round(Math.abs(input.pnlMarginPct));
      const title = `${isGain ? '🚀' : '📉'} ${agent.name} closed ${side ?? ''} ${sign}${roundedPct}% on ${symbol}`;
      const pnlUsdDisplay = input.pnlUsd ? `${isGain ? '+' : '−'}$${Math.abs(input.pnlUsd).toFixed(2)}` : null;
      const bodyParts = [
        `${side ?? '—'} ${symbol}`,
        entryPrice != null ? `Entry $${entryPrice.toFixed(2)}` : null,
        input.exitPrice != null ? `Close $${input.exitPrice.toFixed(2)}` : null,
        `PnL ${sign}${roundedPct}%${pnlUsdDisplay ? ` (${pnlUsdDisplay})` : ''}`,
        `Trigger: ${trigger}`,
        input.qtyClosed != null ? `Size ${input.qtyClosed.toFixed(5)} ${symbol}` : null,
      ].filter(Boolean);
      const body = bodyParts.join(' · ');

      await this.notifications.notifyUser(input.userId, {
        type: NotificationType.TRADING_AGENT_UPDATE,
        title,
        body,
        link: `/agent-hub/${agent.slug}`,
        metadata: {
          kind: 'SIGNIFICANT_TRADE_CLOSE',
          symbol,
          side,
          entryPrice,
          closePrice: input.exitPrice,
          pnlPct: Math.round(input.pnlMarginPct * 100) / 100,
          pnlUsd: input.pnlUsd || null,
          trigger,
          exitReason: input.exitReason,
          size: input.qtyClosed,
          leverage,
          tradeId: cycle.tradeId,
          cycleId: input.cycleId,
          agentSlug: agent.slug,
          agentName: agent.name,
          timestamp: new Date().toISOString(),
        },
      });
    } catch (err) {
      this.logger.warn(
        `Significant-trade alert failed cycle=${input.cycleId}: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  requireApiKey(ctx: SignalApiKeyContext | null): SignalApiKeyContext {
    if (!ctx) throw new UnauthorizedException('Missing or invalid X-Signal-Api-Key');
    return ctx;
  }

  async confirmSolanaFeePayment(
    slug: string,
    cycleId: string,
    ctx: SignalApiKeyContext,
    txSignature: string,
  ) {
    const agent = await this.resolveAgent(slug);
    if (ctx.agentId !== agent.id) {
      throw new ForbiddenException('API key is not valid for this agent');
    }

    const participant = await this.prisma.signalCycleParticipant.findUnique({
      where: { cycleId_userId: { cycleId, userId: ctx.userId } },
    });
    if (!participant) throw new NotFoundException('Cycle participant not found');
    if (participant.settlementStatus === SignalCycleSettlementStatus.PAID) {
      return { ok: true, alreadyPaid: true };
    }
    if (participant.settlementStatus !== SignalCycleSettlementStatus.PENDING) {
      throw new BadRequestException('No pending Solana fee for this cycle');
    }

    const feeUsd = participant.feeUsd != null ? Number(participant.feeUsd) : 0;
    const treasury = participant.feeTreasuryAddress ?? (await resolveSolanaTreasuryAddress(this.prisma));
    if (!treasury || feeUsd <= 0) {
      throw new BadRequestException('Fee settlement not configured');
    }

    const wallet = await this.prisma.walletConnection.findFirst({
      where: { userId: ctx.userId, chain: 'SOLANA' },
    });
    if (!wallet) {
      throw new BadRequestException('Link Solana wallet in Account → Security before paying on-chain.');
    }

    const existingTx = await this.prisma.signalCycleParticipant.findFirst({
      where: { feeTxSignature: txSignature.trim() },
    });
    if (existingTx && existingTx.id !== participant.id) {
      throw new BadRequestException('Transaction signature already used');
    }

    const verification = await verifySolanaTopUpPayment({
      rpcUrl: solanaRpcUrl(),
      txSignature: txSignature.trim(),
      treasuryAddress: treasury,
      expectedPayerAddress: wallet.address,
      minAmountUsd: feeUsd,
      asset: 'USDC',
    });
    if (!verification.ok) {
      throw new BadRequestException(verification.reason ?? 'Payment verification failed');
    }

    await this.prisma.signalCycleParticipant.update({
      where: { id: participant.id },
      data: {
        settlementStatus: SignalCycleSettlementStatus.PAID,
        feeSettlementMethod: 'SOLANA_USDC',
        feeTxSignature: txSignature.trim(),
        settledAt: new Date(),
      },
    });

    return {
      ok: true,
      settlement: {
        fee_usd: feeUsd,
        settlement_status: SignalCycleSettlementStatus.PAID,
        settlement_method: 'SOLANA_USDC',
        treasury_address: treasury,
        tx_signature: txSignature.trim(),
        message: `Success fee $${feeUsd.toFixed(2)} received on Solana.`,
      },
    };
  }
}

/**
 * Map the bot relay-state exit_reason to a user-facing trigger label for the trade alert.
 * Relay-state reasons come from signal-subscriber-execution.service.ts EXIT recordings:
 *  - PROFIT_LOCK / THESIS_FAST_CUT → Take Profit (Scenario C profit-lock or thesis cut)
 *  - HARD_STOP / EXCHANGE_STOP → Stop Loss (protective stop fired)
 *  - MANUAL_PARTIAL_CLOSE / MANUAL_OR_EXCHANGE_CLOSE / IMMEDIATE_EXCHANGE_FLAT → Manual
 *  - SHOWCASE_MIRROR / SHOWCASE_MIRROR_ALREADY_FLAT / anything else → Signal (showcase-driven)
 */
function mapExitReasonToTrigger(exitReason: string | null): 'Take Profit' | 'Stop Loss' | 'Manual' | 'Signal' {
  const r = (exitReason ?? '').toUpperCase();
  if (r === 'PROFIT_LOCK' || r === 'THESIS_FAST_CUT') return 'Take Profit';
  if (r === 'HARD_STOP' || r === 'EXCHANGE_STOP') return 'Stop Loss';
  if (r.startsWith('MANUAL') || r === 'IMMEDIATE_EXCHANGE_FLAT' || r === 'ORPHAN_LEDGER_RECONCILE') {
    return 'Manual';
  }
  return 'Signal';
}
