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
  cancelOrderWithRetry,
  confirmOrderGone,
  type CancelCapableClient,
} from '../exchanges/bitfinex-cancel.util';
import type { ExchangeCredentials } from '../exchanges/exchange-adapter.interface';
import {
  buildFreshInstanceDashboardState,
  readInstanceScope,
  USER_INSTANCE_STARTING_BALANCE,
} from './instance-view.mapper';
import { emptyCopyRelaySimState, readCopyRelaySimState, isCopyRelaySimActive } from '@dcf/utils';
import { CopyRelaySimService } from './copy-relay-sim.service';
import { SignalSubscriberExecutionService } from './signal-subscriber-execution.service';
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
    private readonly execution: SignalSubscriberExecutionService,
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

    // F8 (2026-07-07 follow-up) — Validate Start pre-conditions BEFORE flipping
    // the status to ACTIVE. Previously this endpoint returned 200 without
    // checking Bitfinex credentials, derivatives funding, or showcase
    // reachability, then the next tick would silently write `lastError` and
    // skip — the user saw a green "Relay on" pill with no activity for up to
    // 20s and concluded the relay was broken.
    let startValidation: { derivativesUsd?: number; credentialsOk: boolean } | undefined;
    if (!paused && instance.exchangeProvider !== 'paper') {
      const creds = await this.exchanges.getUserCredentials(userId, instance.exchangeProvider);
      if (!creds) {
        throw new BadRequestException(
          'Bitfinex API keys missing — re-hire this agent to reconnect your exchange credentials.',
        );
      }
      // Probe the wallet snapshot. This doubles as a credentials-auth check —
      // a 401 / invalid-key returns null here and we throw a clear error
      // instead of letting the executor silently skip on the next tick.
      const snapshot = await this.exchanges.getUserBitfinexWalletSnapshot(userId);
      if (!snapshot) {
        throw new BadRequestException(
          'Bitfinex rejected your API key — re-hire with fresh credentials (Read + Write on Orders + Wallets, no Withdraw).',
        );
      }
      const derivativesUsd = Number(snapshot.derivativesUsd ?? 0);
      const MIN_DERIVATIVES_USD = 5;
      if (derivativesUsd < MIN_DERIVATIVES_USD) {
        throw new BadRequestException(
          `Move at least $${MIN_DERIVATIVES_USD} USDT to your Bitfinex Derivatives wallet before starting. Current: $${derivativesUsd.toFixed(2)}. (Wallet → Transfer → Exchange/Funding → Derivatives.)`,
        );
      }
      startValidation = { derivativesUsd, credentialsOk: true };
    }

    const status = paused ? TradingAgentInstanceStatus.PAUSED : TradingAgentInstanceStatus.ACTIVE;
    let relayAction: { cancelledOrders?: number } | undefined;

    if (paused && instance.exchangeProvider !== 'paper') {
      const stopCreds = await this.exchanges.getUserCredentials(
        userId,
        instance.exchangeProvider,
      );
      relayAction = await this.severShowcaseRelay(
        userId,
        instance.exchangeProvider,
        stopCreds ?? undefined,
      );
      await this.expirePendingCopyParticipants(userId, agent.id, stopCreds ?? undefined);
    }

    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: { status, lastError: null },
    });

    // F8 — Wake the executor immediately so the first tick fires within
    // milliseconds of the user clicking Start, instead of waiting up to 2s
    // for the next setInterval cadence. Without this, the user perceives
    // "clicked Start, nothing happened" when in reality the next tick was
    // just slow to land.
    if (!paused) {
      try {
        await this.execution.wakeNow('USER_RESUME');
      } catch (err) {
        this.logger.warn(
          `wakeNow on resume failed (non-fatal): ${err instanceof Error ? err.message : err}`,
        );
      }
    }

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
      validated: startValidation,
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
      const simStartedAt =
        dash.copyRelaySim && typeof dash.copyRelaySim === 'object'
          ? (dash.copyRelaySim as { startedAt?: string }).startedAt
          : undefined;

      // Phase 6 fix 2 — the session-reset path was a guaranteed orphan generator
      // (it marked the ledger EXPIRED with NO preceding cancel). Fetch creds and
      // hand them to expirePendingCopyParticipants so it cancels each pending
      // participant's resting order first, only marking EXPIRED on confirmed-gone.
      const resetCreds =
        instance.exchangeProvider === 'bitfinex'
          ? await this.exchanges.getUserCredentials(instance.userId, instance.exchangeProvider)
          : undefined;
      await this.expirePendingCopyParticipants(
        instance.userId,
        input.agentId,
        resetCreds ?? undefined,
      );
      this.relaySim.dropSimClient(instance.userId);

      // A showcase session reset refreshes the paper ledger to a $500 baseline, but it must NOT
      // disarm a relay sim the user explicitly armed — the sim should only stop on explicit user
      // action (POST /relay-sim/stop). Preserve active + startedAt when the sim was running.
      const nextSim = simWasActive
        ? {
            ...emptyCopyRelaySimState(startingUsd),
            active: true,
            startedAt: simStartedAt ?? new Date().toISOString(),
          }
        : emptyCopyRelaySimState(startingUsd);

      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: {
          dashboardState: {
            ...fresh,
            copyRelaySim: nextSim,
            copyRelayReconcile: null,
            copyRelayCapacity: null,
            relaySimChannel: simWasActive,
            copyRelayLimitChain: null,
            tradeLifecycleIntegrity: null,
          },
          lastError: simWasActive
            ? 'Showcase session reset — paper ledger refreshed to $500; relay sim stays armed.'
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
    creds?: ExchangeCredentials,
  ): Promise<{ cancelledOrders: number }> {
    if (provider !== 'bitfinex') {
      return { cancelledOrders: 0 };
    }
    const resolvedCreds = creds ?? (await this.exchanges.getUserCredentials(userId, provider));
    if (!resolvedCreds) return { cancelledOrders: 0 };

    let cancelled = 0;
    try {
      const orders = await this.bitfinex.listActiveOrders(resolvedCreds);
      const client = this.bitfinex as CancelCapableClient;
      for (const order of orders) {
        // Phase 6 fix 2 — retry + loud-fail semantics (was warn-only swallow).
        // The kill switch is user-initiated, so cancelling every active order
        // on the symbol is intentional; the upgrade is just to not silently
        // drop cancel failures (which would leave orphans behind).
        const result = await cancelOrderWithRetry(client, resolvedCreds, order.id, {
          logger: this.logger,
          label: `Kill switch cancel ${order.id} for ${userId}`,
        });
        if (result.ok) {
          cancelled += 1;
        } else {
          this.logger.error(
            `Kill switch: failed to cancel order ${order.id} for ${userId} after ${result.attempts} attempts: ${result.reason}`,
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

  /**
   * Resolve the most recent Bitfinex order id recorded for a participant by
   * scanning its ORDER_PLACED / UPDATE_STOPS event payloads (newest first).
   * Used by {@link expirePendingCopyParticipants} to know which exchange order
   * to cancel before marking the ledger row EXPIRED.
   */
  private async loadParticipantBitfinexOrderId(participantId: string): Promise<number | null> {
    const events = await this.prisma.signalCycleEvent.findMany({
      where: {
        participantId,
        eventType: { in: ['ORDER_PLACED', 'UPDATE_STOPS'] },
      },
      orderBy: { createdAt: 'desc' },
      take: 8,
    });
    for (const e of events) {
      const p = (e.payload ?? {}) as Record<string, unknown>;
      const oid = p.bitfinexOrderId ?? p.bitfinex_order_id;
      if (typeof oid === 'number' && oid > 0) return oid;
    }
    return null;
  }

  /**
   * Stop relay: clear pending ledger rows so resume does not resurrect stale limits.
   *
   * Phase 6 fix 2 — when `creds` are supplied (live Bitfinex instance), this
   * now cancels each participant's resting exchange order BEFORE marking it
   * EXPIRED, using the same retry + confirm-gone semantics as the monitorEntry
   * path. A participant is only flipped to EXPIRED when the cancel succeeded
   * OR a follow-up `findOrder` confirms the order is gone; otherwise it is left
   * PENDING_ENTRY with `instance.lastError = 'CANCEL_FAILED_ORDER_STILL_LIVE'`
   * and an audit `RECONCILE_CANCEL_FAILED` event so the next tick retries.
   * Without this, the session-reset path was a guaranteed orphan generator
   * (it marked the ledger EXPIRED while the Bitfinex order stayed live).
   */
  private async expirePendingCopyParticipants(
    userId: string,
    agentId: string,
    creds?: ExchangeCredentials,
  ): Promise<void> {
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
    let expired = 0;
    let leftPending = 0;
    for (const row of pending) {
      const oid = creds ? await this.loadParticipantBitfinexOrderId(row.id) : null;

      if (creds && oid != null) {
        const client = this.bitfinex as CancelCapableClient;
        const cancel = await cancelOrderWithRetry(client, creds, oid, {
          logger: this.logger,
          label: `Relay stop expire ${userId} cancel pending order ${oid}`,
        });
        let gone = cancel.ok;
        if (!gone) {
          // Cancel API failed — verify the order is actually still on the book
          // before refusing to mark EXPIRED (a transient API error may have
          // left the order cancelled server-side with a lost response).
          gone = await confirmOrderGone(client, creds, oid);
        }
        if (!gone) {
          this.logger.error(
            `Relay stop ${userId}: cancel of pending order ${oid} (participant ${row.id}) failed and order still live — leaving PENDING_ENTRY for next tick`,
          );
          await this.prisma.tradingAgentInstance
            .updateMany({
              where: { userId, agentId },
              data: { lastError: 'CANCEL_FAILED_ORDER_STILL_LIVE' },
            })
            .catch(() => {
              /* best-effort */
            });
          await this.prisma.signalCycleEvent
            .create({
              data: {
                cycleId: row.cycleId,
                participantId: row.id,
                eventType: 'RECONCILE_CANCEL_FAILED',
                payload: {
                  venue: 'bitfinex',
                  source: 'hire',
                  event: 'RELAY_STOP_CANCEL_FAILED',
                  bitfinex_order_id: oid,
                  cancel_attempts: cancel.attempts,
                  cancel_reason: cancel.reason ?? 'unknown',
                },
              },
            })
            .catch(() => {
              /* audit-best-effort */
            });
          leftPending += 1;
          continue;
        }
      }

      // No creds / no order id (paper or never placed) OR cancel succeeded /
      // order confirmed gone — safe to mark EXPIRED.
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
            ...(oid != null ? { bitfinex_order_id: oid } : {}),
          },
        },
      });
      expired += 1;
    }
    this.logger.log(
      `Relay stop ${userId}: expired ${expired} pending copy participant(s)${
        leftPending > 0 ? `, ${leftPending} left PENDING_ENTRY (cancel failed — order still live)` : ''
      }`,
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
