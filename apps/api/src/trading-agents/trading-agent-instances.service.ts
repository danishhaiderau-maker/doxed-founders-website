import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import {
  TRADING_AGENT_AI_PROVIDER_LABELS,
  EXCHANGE_PROVIDER_LABELS,
  type ExchangeProvider,
  type TradingAgentAiProvider,
} from '@dcf/utils';
import { TradingAgentInstanceStatus } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';
import { NotificationsService } from '../notifications/notifications.service';
import { NotificationType } from '@prisma/client';
import { ExchangesService } from '../exchanges/exchanges.service';

@Injectable()
export class TradingAgentInstancesService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly points: PointsService,
    private readonly notifications: NotificationsService,
    private readonly exchanges: ExchangesService,
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
    if (cost > 0 && needsHireFee) {
      await this.points.spend(userId, cost, `AGENT_HIRE:${agent.slug}`);
      await this.points.creditAdminFee(cost, agent.slug);
    }

    const hireExpiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);

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
        dashboardState: {
          instanceMode: 'live',
          copySource: 'admin-showcase',
        },
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
        dashboardState: {
          instanceMode: 'live',
          copySource: 'admin-showcase',
        },
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
      body: `Charged ${cost.toLocaleString()} DDollar for 1 week. Your ${EXCHANGE_PROVIDER_LABELS[input.exchangeProvider as ExchangeProvider]} account will mirror admin ${TRADING_AGENT_AI_PROVIDER_LABELS[aiProvider as TradingAgentAiProvider] ?? aiProvider} trades when the live tier executes.`,
      link: `/agent-hub/${agent.slug}`,
    });

    return {
      ...this.formatInstance(instance, agent),
      hireFeeDdollar: needsHireFee ? cost : 0,
      rentalExpiresAt: hireExpiresAt.toISOString(),
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
    const dashState = (instance.dashboardState ?? {}) as Record<string, unknown>;
    const exchangeStatus = isCopy
      ? { connected: false, provider: 'copy', accountLabel: 'DDollar copy track' }
      : await this.exchanges.getUserExchangeStatus(userId, instance.exchangeProvider);

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
        lastError: instance.lastError,
        instanceMode: (dashState.instanceMode as string) ?? (isCopy ? 'copy' : 'live'),
        paperAllocationUsd: dashState.paperAllocationUsd as number | undefined,
      },
      exchange: exchangeStatus,
      runtime: {
        connected: instance.status === TradingAgentInstanceStatus.ACTIVE,
        message: isCopy
          ? 'Copy-trading admin DeepSeek decisions with DDollar — no API keys required.'
          : 'Live tier mirrors admin AI trades on your exchange when execution is enabled.',
        openPositions: 0,
        pnlPct: 0,
      },
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

    await this.prisma.tradingAgentInstance.upsert({
      where: { agentId_userId: { agentId: agent.id, userId } },
      create: {
        agentId: agent.id,
        userId,
        exchangeProvider: 'paper',
        status: TradingAgentInstanceStatus.ACTIVE,
        aiProvidedByPlatform: true,
        aiProvider,
        activatedAt: new Date(),
        dashboardState: {
          instanceMode: 'copy',
          copySource: 'admin-showcase',
          paperAllocationUsd: amountUsd,
          paperDdSpent: ddCost,
        },
      },
      update: {
        status: TradingAgentInstanceStatus.ACTIVE,
        aiProvidedByPlatform: true,
        aiProvider,
        dashboardState: {
          instanceMode: 'copy',
          copySource: 'admin-showcase',
          paperAllocationUsd: amountUsd,
          paperDdSpent: ddCost,
        },
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

    const status = paused ? TradingAgentInstanceStatus.PAUSED : TradingAgentInstanceStatus.ACTIVE;
    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: { status, lastError: null },
    });

    return {
      ok: true,
      status,
      message: paused
        ? 'Your agent instance is paused — no new trades until you resume.'
        : 'Your agent instance is active again.',
    };
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
      dashboardUrl: `/agent-hub/${agent.slug}`,
    };
  }
}
