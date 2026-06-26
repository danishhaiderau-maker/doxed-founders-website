import { BadRequestException, Injectable, Logger, NotFoundException } from '@nestjs/common';
import {
  TRADING_AGENT_AI_PROVIDER_LABELS,
  EXCHANGE_PROVIDER_LABELS,
  type ExchangeProvider,
  type TradingAgentAiProvider,
} from '@dcf/utils';
import { TradingAgentInstanceStatus, SignalCycleStatus, NotificationType } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';
import { NotificationsService } from '../notifications/notifications.service';
import { ExchangesService } from '../exchanges/exchanges.service';
import { BitfinexTradingClient } from '../exchanges/bitfinex-api.client';
import {
  buildFreshInstanceDashboardState,
  readInstanceScope,
  USER_INSTANCE_STARTING_BALANCE,
} from './instance-view.mapper';
import { emptyCopyRelaySimState, readCopyRelaySimState, isCopyRelaySimActive } from '@dcf/utils';
import { CopyRelaySimService } from './copy-relay-sim.service';
import { loadSubscriberMaxMarginUsd } from './subscriber-margin.util';

@Injectable()
export class TradingAgentInstancesService {
  private readonly logger = new Logger(TradingAgentInstancesService.name);
  private readonly bitfinex = new BitfinexTradingClient();

  constructor(
    private readonly prisma: PrismaService,
    private readonly points: PointsService,
    private readonly notifications: NotificationsService,
    private readonly exchanges: ExchangesService,
    private readonly relaySim: CopyRelaySimService,
  ) {}

  async hireAgent(
    userId: string,
    agentId: string,
    input: {
      exchangeProvider: string;
      apiKey: string;
      apiSecret: string;
      passphrase?: string;
      testnet?: boolean;
      aiMode?: 'platform' | 'own';
      aiProvider?: string;
      aiApiKey?: string;
    },
  ) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { id: agentId } });
    if (!agent) throw new NotFoundException('Agent not found');

    const existing = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId, userId } },
    });
    if (
      existing?.status === TradingAgentInstanceStatus.ACTIVE &&
      existing.exchangeProvider !== 'paper'
    ) {
      return this.formatInstance(existing, agent);
    }

    const connected = await this.exchanges.connectUserExchange(userId, input.exchangeProvider, {
      apiKey: input.apiKey,
      apiSecret: input.apiSecret,
      passphrase: input.passphrase,
      testnet: input.testnet,
    });

    const cost = agent.costDdollarWeek > 0 ? agent.costDdollarWeek : agent.costDdollarDay;
    const needsHireFee =
      !existing ||
      existing.exchangeProvider === 'paper' ||
      (existing.expiresAt != null && existing.expiresAt < new Date());

    const existingDash = (existing?.dashboardState ?? {}) as Record<string, unknown>;
    const paperDdSpent =
      existing?.exchangeProvider === 'paper' && typeof existingDash.paperDdSpent === 'number'
        ? existingDash.paperDdSpent
        : 0;
    if (paperDdSpent > 0 && !existingDash.paperDdRefunded) {
      await this.points.award(userId, paperDdSpent, `AGENT_PAPER_REFUND:${agent.slug}`);
    }

    if (cost > 0 && needsHireFee) {
      await this.points.spend(userId, cost, `AGENT_HIRE:${agent.slug}`);
      await this.points.creditAdminFee(cost, agent.slug);
    }

    let exchangeBalanceUsd = 0;
    let walletNote: string | undefined;
    if (input.exchangeProvider === 'bitfinex') {
      const marginCap = await loadSubscriberMaxMarginUsd(this.prisma);
      const funding = await this.exchanges.ensureUserBitfinexDerivativesMargin(userId, marginCap);
      const snapshot = await this.exchanges.getUserBitfinexWalletSnapshot(userId);
      exchangeBalanceUsd = funding?.derivativesUsd ?? snapshot?.derivativesUsd ?? 0;
      walletNote = funding?.message;
    }

    const hireExpiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
    const sessionState = buildFreshInstanceDashboardState('live', exchangeBalanceUsd, {
      liveSessionStartingBalanceUsd: exchangeBalanceUsd,
      paperDdRefunded: paperDdSpent > 0,
      paperDdRefundedAmount: paperDdSpent > 0 ? paperDdSpent : undefined,
      hireFeeDdollarPaid: needsHireFee ? cost : 0,
    });

    const showcase = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    /** Live tier mirrors admin DeepSeek decisions — users only connect exchange keys. */
    const aiProvider = (showcase?.showcaseAiProvider ?? 'deepseek') as string;

    const instance = await this.prisma.tradingAgentInstance.upsert({
      where: { agentId_userId: { agentId, userId } },
      create: {
        agentId,
        userId,
        exchangeProvider: input.exchangeProvider,
        credentialId: connected.credentialId,
        status: TradingAgentInstanceStatus.ACTIVE,
        aiProvidedByPlatform: true,
        aiProvider,
        activatedAt: new Date(),
        lastBilledAt: new Date(),
        expiresAt: hireExpiresAt,
        dashboardState: sessionState,
      },
      update: {
        exchangeProvider: input.exchangeProvider,
        credentialId: connected.credentialId,
        status: TradingAgentInstanceStatus.ACTIVE,
        aiProvidedByPlatform: true,
        aiProvider,
        activatedAt: new Date(),
        expiresAt: hireExpiresAt,
        lastError: null,
        dashboardState: sessionState,
      },
    });

    await this.prisma.tradingAgentFollow.upsert({
      where: { agentId_userId: { agentId, userId } },
      create: { agentId, userId },
      update: {},
    });

    await this.notifications.notifyUser(userId, {
      type: NotificationType.TRADING_AGENT_UPDATE,
      title: `${agent.name} live copy trading active`,
      body: `Charged ${cost.toLocaleString()} DDollar for 1 week. Platform auto-executes admin signals on your ${EXCHANGE_PROVIDER_LABELS[input.exchangeProvider as ExchangeProvider]} account (Bitfinex: max $${await loadSubscriberMaxMarginUsd(this.prisma)} margin/trade).`,
      link: `/agent-hub/${agent.slug}`,
    });

    return {
      ...this.formatInstance(instance, agent),
      hireFeeDdollar: needsHireFee ? cost : 0,
      paperDdRefunded: paperDdSpent > 0 ? paperDdSpent : 0,
      rentalExpiresAt: hireExpiresAt.toISOString(),
      exchangeBalanceUsd,
      walletNote,
    };
  }

  async getMyDashboard(userId: string, agentSlug: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: agentSlug } });
    if (!agent) throw new NotFoundException('Agent not found');

    const instance = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId: agent.id, userId } },
    });
    if (!instance) {
      throw new NotFoundException('No private instance — hire this agent first');
    }

    const isCopy = instance.exchangeProvider === 'paper';
    const openHirePositions = isCopy
      ? 0
      : await this.prisma.signalCycleParticipant.count({
          where: {
            userId,
            status: SignalCycleStatus.OPEN,
            cycle: { agentId: agent.id },
          },
        });

    const executionLive = process.env.SUBSCRIBER_EXECUTION_ENABLED !== 'false';
    const marginCap = await loadSubscriberMaxMarginUsd(this.prisma);
    const dashState = (instance.dashboardState ?? {}) as Record<string, unknown>;
    const relaySimActive = isCopyRelaySimActive(dashState);
    const relayPaused = instance.status === TradingAgentInstanceStatus.PAUSED && !relaySimActive;
    const scope = readInstanceScope(instance);
    const exchangeStatus = isCopy
      ? { connected: false, provider: 'copy', accountLabel: 'DDollar copy track' }
      : await this.exchanges.getUserExchangeStatus(userId, instance.exchangeProvider);
    const bitfinexWallets =
      !isCopy && instance.exchangeProvider === 'bitfinex'
        ? await this.exchanges.getUserBitfinexWalletSnapshot(userId)
        : null;

    return {
      kind: isCopy ? ('copy' as const) : ('live' as const),
      agent: {
        id: agent.id,
        slug: agent.slug,
        name: agent.name,
        assetSymbol: agent.assetSymbol,
      },
      instance: {
        id: instance.id,
        status: instance.status,
        exchangeProvider: instance.exchangeProvider,
        exchangeLabel:
          EXCHANGE_PROVIDER_LABELS[instance.exchangeProvider as ExchangeProvider] ??
          instance.exchangeProvider,
        aiProvidedByPlatform: instance.aiProvidedByPlatform,
        aiProvider: instance.aiProvider,
        aiLabel: instance.aiProvider
          ? TRADING_AGENT_AI_PROVIDER_LABELS[
              instance.aiProvider as keyof typeof TRADING_AGENT_AI_PROVIDER_LABELS
            ] ?? instance.aiProvider
          : 'Platform AI',
        hiredAt: instance.hiredAt.toISOString(),
        activatedAt: instance.activatedAt?.toISOString() ?? null,
        expiresAt: instance.expiresAt?.toISOString() ?? null,
        lastError: instance.lastError,
        instanceMode: (dashState.instanceMode as string) ?? (isCopy ? 'copy' : 'live'),
        paperAllocationUsd: dashState.paperAllocationUsd as number | undefined,
        startingBalanceUsd: scope.startingBalanceUsd,
        sessionStartedAt: scope.sessionStartedAt.toISOString(),
      },
      exchange: {
        ...exchangeStatus,
        derivativesUsd: bitfinexWallets?.derivativesUsd ?? null,
        exchangeUsd: bitfinexWallets?.exchangeUsd ?? null,
        fundingUsd: bitfinexWallets?.fundingUsd ?? null,
        fundingWalletLabel: 'Derivatives (USDT)',
        fundingHint:
          bitfinexWallets && bitfinexWallets.derivativesUsd < marginCap
            ? `Move USDT to Bitfinex Derivatives wallet (need ~$${marginCap} per copy trade). Exchange/Funding balances can be auto-moved when your API key allows wallet transfers.`
            : 'USDT in Derivatives wallet — ready for live copy signals.',
      },
      runtime: {
        connected: instance.status === TradingAgentInstanceStatus.ACTIVE || relaySimActive,
        message: isCopy
          ? 'Copy-trading admin DeepSeek decisions with DDollar — no API keys required.'
          : relaySimActive
            ? 'Relay simulation active — paper book mirrors showcase signals; live Bitfinex orders blocked.'
            : relayPaused
              ? 'Relay stopped — showcase signals will not execute on your exchange until you press Start.'
              : executionLive
                ? `Live copy execution active — platform places Bitfinex limit orders from admin signals (max $${marginCap} margin/trade).`
                : 'Live tier mirrors admin AI trades on your exchange when execution is enabled.',
        openPositions: openHirePositions,
        pnlPct: 0,
      },
      copyRelaySim: readCopyRelaySimState(dashState),
    };
  }

  async getInstanceForUser(userId: string, agentId: string) {
    return this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId, userId } },
    });
  }

  async paperTrackAgent(userId: string, agentSlug: string, amountUsd = 500) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: agentSlug } });
    if (!agent) throw new NotFoundException('Agent not found');

    const existing = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId: agent.id, userId } },
    });
    if (existing?.exchangeProvider !== 'paper' && existing?.status === TradingAgentInstanceStatus.ACTIVE) {
      throw new BadRequestException(
        'You already have a live instance. Pause it first or use your live dashboard.',
      );
    }

    const ddCost = Math.min(500, Math.max(100, amountUsd));
    await this.points.spend(userId, ddCost, `AGENT_PAPER_TRACK:${agent.slug}`);

    const showcase = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const aiProvider = showcase?.showcaseAiProvider ?? 'deepseek';
    const sessionState = buildFreshInstanceDashboardState('copy', amountUsd, {
      paperDdSpent: ddCost,
    });
    const now = new Date();

    await this.prisma.tradingAgentInstance.upsert({
      where: { agentId_userId: { agentId: agent.id, userId } },
      create: {
        agentId: agent.id,
        userId,
        exchangeProvider: 'paper',
        status: TradingAgentInstanceStatus.ACTIVE,
        aiProvidedByPlatform: true,
        aiProvider,
        activatedAt: now,
        hiredAt: now,
        dashboardState: sessionState,
      },
      update: {
        status: TradingAgentInstanceStatus.ACTIVE,
        aiProvidedByPlatform: true,
        aiProvider,
        activatedAt: now,
        dashboardState: sessionState,
      },
    });

    await this.prisma.tradingAgentFollow.upsert({
      where: { agentId_userId: { agentId: agent.id, userId } },
      create: { agentId: agent.id, userId },
      update: {},
    });

    await this.notifications.notifyUser(userId, {
      type: NotificationType.TRADING_AGENT_UPDATE,
      title: `Copy trading ${agent.name}`,
      body: `$${amountUsd} DDollar allocated — mirrors admin DeepSeek trades with no exchange or AI keys.`,
      link: `/agent-hub/${agent.slug}`,
    });

    return {
      ok: true,
      instanceMode: 'copy' as const,
      paperAllocationUsd: amountUsd,
      ddSpent: ddCost,
      dashboardUrl: `/agent-hub/${agent.slug}`,
    };
  }

  async setInstancePaused(userId: string, agentSlug: string, paused: boolean) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: agentSlug } });
    if (!agent) throw new NotFoundException('Agent not found');

    const instance = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId: agent.id, userId } },
    });
    if (!instance) {
      throw new NotFoundException('No private instance — hire or paper-track this agent first');
    }

    const dash = (instance.dashboardState ?? {}) as Record<string, unknown>;
    if (!paused && isCopyRelaySimActive(dash)) {
      throw new BadRequestException(
        'Stop relay simulation first — live copy cannot resume while the paper sim book is active.',
      );
    }

    if (!paused && instance.expiresAt && instance.expiresAt < new Date()) {
      throw new BadRequestException(
        'Live copy rental expired — renew your weekly subscription before starting real trading.',
      );
    }

    const status = paused ? TradingAgentInstanceStatus.PAUSED : TradingAgentInstanceStatus.ACTIVE;
    let relayAction: { cancelledOrders?: number } | undefined;

    if (paused && instance.exchangeProvider !== 'paper') {
      relayAction = await this.severShowcaseRelay(userId, instance.exchangeProvider);
      await this.expirePendingCopyParticipants(userId, agent.id);
    }

    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: { status, lastError: null },
    });

    if (paused) {
      await this.notifications.notifyUser(userId, {
        type: NotificationType.TRADING_AGENT_UPDATE,
        title: `${agent.name} relay stopped`,
        body:
          relayAction?.cancelledOrders != null && relayAction.cancelledOrders > 0
            ? `Showcase copy severed — ${relayAction.cancelledOrders} pending order(s) cancelled on your exchange.`
            : 'Showcase copy severed — no new trades until you press Start.',
        link: `/agent-hub/${agent.slug}`,
      });
    } else {
      await this.notifications.notifyUser(userId, {
        type: NotificationType.TRADING_AGENT_UPDATE,
        title: `${agent.name} relay resumed`,
        body: 'Live copy trading active again — mirroring admin showcase signals on your exchange.',
        link: `/agent-hub/${agent.slug}`,
      });
    }

    return {
      ok: true,
      status,
      relay: relayAction,
      message: paused
        ? relayAction?.cancelledOrders
          ? `Relay severed — ${relayAction.cancelledOrders} pending order(s) cancelled. No new showcase trades until you Start.`
          : 'Relay severed — no new showcase trades until you Start.'
        : 'Relay resumed — copying admin showcase signals on your exchange again.',
    };
  }

  async renewLiveCopyRental(userId: string, agentSlug: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: agentSlug } });
    if (!agent) throw new NotFoundException('Agent not found');

    const instance = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId: agent.id, userId } },
    });
    if (!instance || instance.exchangeProvider === 'paper') {
      throw new BadRequestException('Connect a live exchange before renewing rental.');
    }

    const cost = agent.costDdollarWeek > 0 ? agent.costDdollarWeek : agent.costDdollarDay;
    if (cost <= 0) {
      throw new BadRequestException('This agent has no weekly rental fee configured.');
    }

    await this.points.spend(userId, cost, `AGENT_HIRE_RENEW:${agent.slug}`);
    await this.points.creditAdminFee(cost, agent.slug);

    const baseMs =
      instance.expiresAt && instance.expiresAt > new Date()
        ? instance.expiresAt.getTime()
        : Date.now();
    const hireExpiresAt = new Date(baseMs + 7 * 24 * 60 * 60 * 1000);

    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        expiresAt: hireExpiresAt,
        lastBilledAt: new Date(),
        lastError: null,
      },
    });

    await this.notifications.notifyUser(userId, {
      type: NotificationType.TRADING_AGENT_UPDATE,
      title: `${agent.name} rental renewed`,
      body: `Live copy extended through ${hireExpiresAt.toLocaleString()} — you can start real trading again.`,
      link: `/agent-hub/${agent.slug}`,
    });

    return {
      ok: true,
      rentalExpiresAt: hireExpiresAt.toISOString(),
      ddSpent: cost,
    };
  }

  /**
   * Wipe user copy/relay/paper session counters when showcase bot fresh-collection or version changes.
   * Resets display P&L to $500 baseline; live exchange positions are not force-closed.
   */
  async resetAllUserCopySessions(input: {
    agentId: string;
    reason: string;
    botStartTime?: number;
  }) {
    const sessionIso =
      input.botStartTime != null && input.botStartTime > 0
        ? new Date(input.botStartTime * 1000).toISOString()
        : new Date().toISOString();

    const instances = await this.prisma.tradingAgentInstance.findMany({
      where: { agentId: input.agentId },
    });

    for (const instance of instances) {
      const dash = (instance.dashboardState ?? {}) as Record<string, unknown>;
      const isPaper = instance.exchangeProvider === 'paper';
      const isLive = !isPaper && instance.exchangeProvider !== 'paper';
      const mode: 'copy' | 'live' = isPaper ? 'copy' : isLive ? 'live' : 'copy';
      const startingUsd = USER_INSTANCE_STARTING_BALANCE;

      const fresh = buildFreshInstanceDashboardState(mode, startingUsd, {
        showcaseSessionResetAt: sessionIso,
        showcaseSessionResetReason: input.reason,
        paperDdSpent: typeof dash.paperDdSpent === 'number' ? dash.paperDdSpent : undefined,
        paperDdRefunded: dash.paperDdRefunded,
        paperDdRefundedAmount: dash.paperDdRefundedAmount,
        hireFeeDdollarPaid: dash.hireFeeDdollarPaid,
      });

      if (mode === 'live') {
        fresh.liveSessionStartingBalanceUsd = startingUsd;
      }

      const simWasActive = Boolean(
        dash.copyRelaySim && typeof dash.copyRelaySim === 'object' && (dash.copyRelaySim as { active?: boolean }).active,
      );

      await this.expirePendingCopyParticipants(instance.userId, input.agentId);
      this.relaySim.dropSimClient(instance.userId);

      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: {
          dashboardState: {
            ...fresh,
            copyRelaySim: emptyCopyRelaySimState(startingUsd),
            copyRelayReconcile: null,
            copyRelayCapacity: null,
            relaySimChannel: false,
            copyRelayLimitChain: null,
            tradeLifecycleIntegrity: null,
          },
          lastError: simWasActive
            ? 'Showcase session reset — relay sim cleared. Start relay sim again for a fresh $500 paper book.'
            : null,
        },
      });
    }

    this.logger.log(
      `Reset ${instances.length} user copy session(s) for agent ${input.agentId} (${input.reason})`,
    );
    return { resetCount: instances.length };
  }

  /** Kill switch: cancel pending exchange orders so showcase relay cannot fill new trades. */
  private async severShowcaseRelay(
    userId: string,
    provider: string,
  ): Promise<{ cancelledOrders: number }> {
    if (provider !== 'bitfinex') {
      return { cancelledOrders: 0 };
    }
    const creds = await this.exchanges.getUserCredentials(userId, provider);
    if (!creds) return { cancelledOrders: 0 };

    let cancelled = 0;
    try {
      const orders = await this.bitfinex.listActiveOrders(creds);
      for (const order of orders) {
        try {
          await this.bitfinex.cancelOrder(creds, order.id);
          cancelled += 1;
        } catch (err) {
          this.logger.warn(
            `Kill switch: failed to cancel order ${order.id} for ${userId}: ${
              err instanceof Error ? err.message : err
            }`,
          );
        }
      }
    } catch (err) {
      this.logger.warn(
        `Kill switch: could not list Bitfinex orders for ${userId}: ${
          err instanceof Error ? err.message : err
        }`,
      );
    }
    return { cancelledOrders: cancelled };
  }

  /** Stop relay: clear pending ledger rows so resume does not resurrect stale limits. */
  private async expirePendingCopyParticipants(userId: string, agentId: string) {
    const pending = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId },
      },
      select: { id: true, cycleId: true },
    });
    if (!pending.length) return;

    const now = new Date();
    for (const row of pending) {
      await this.prisma.signalCycleParticipant.update({
        where: { id: row.id },
        data: { status: SignalCycleStatus.EXPIRED, updatedAt: now },
      });
      await this.prisma.signalCycleEvent.create({
        data: {
          cycleId: row.cycleId,
          participantId: row.id,
          eventType: 'EXPIRED',
          payload: {
            venue: 'bitfinex',
            exit_reason: 'USER_RELAY_STOP',
            pnl_usd: 0,
            source: 'hire',
          },
        },
      });
    }
    this.logger.log(
      `Relay stop ${userId}: expired ${pending.length} pending copy participant(s)`,
    );
  }

  private formatInstance(
    instance: {
      id: string;
      status: TradingAgentInstanceStatus;
      exchangeProvider: string;
      aiProvidedByPlatform: boolean;
      aiProvider: string | null;
      hiredAt: Date;
      activatedAt: Date | null;
      expiresAt?: Date | null;
    },
    agent: { slug: string; name: string },
  ) {
    return {
      instanceId: instance.id,
      agentSlug: agent.slug,
      agentName: agent.name,
      status: instance.status,
      exchangeProvider: instance.exchangeProvider,
      exchangeLabel:
        EXCHANGE_PROVIDER_LABELS[instance.exchangeProvider as ExchangeProvider] ??
        instance.exchangeProvider,
      aiProvidedByPlatform: instance.aiProvidedByPlatform,
      aiProvider: instance.aiProvider,
      hiredAt: instance.hiredAt.toISOString(),
      activatedAt: instance.activatedAt?.toISOString() ?? null,
      rentalExpiresAt: instance.expiresAt?.toISOString() ?? null,
      dashboardUrl: `/agent-hub/${agent.slug}`,
    };
  }
}
