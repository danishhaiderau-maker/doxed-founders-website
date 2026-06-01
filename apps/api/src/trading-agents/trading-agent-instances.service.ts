import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import {
  TRADING_AGENT_AI_PROVIDER_LABELS,
  EXCHANGE_PROVIDER_LABELS,
  exchangeCredentialProvider,
  type ExchangeProvider,
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
    input: { exchangeProvider: string; apiKey: string; apiSecret: string; testnet?: boolean },
  ) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { id: agentId } });
    if (!agent) throw new NotFoundException('Agent not found');

    const existing = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId, userId } },
    });
    if (existing?.status === TradingAgentInstanceStatus.ACTIVE) {
      return this.formatInstance(existing, agent);
    }

    const connected = await this.exchanges.connectUserExchange(userId, input.exchangeProvider, {
      apiKey: input.apiKey,
      apiSecret: input.apiSecret,
      testnet: input.testnet,
    });

    const cost = agent.costDdollarDay;
    if (cost > 0 && !existing) {
      await this.points.spend(userId, cost, `AGENT_HIRE:${agent.slug}`);
    }

    const showcase = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const aiProvider = showcase?.showcaseAiProvider ?? 'deepseek';

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
      },
      update: {
        exchangeProvider: input.exchangeProvider,
        credentialId: connected.credentialId,
        status: TradingAgentInstanceStatus.ACTIVE,
        aiProvidedByPlatform: true,
        aiProvider,
        activatedAt: new Date(),
        lastError: null,
      },
    });

    await this.prisma.tradingAgentFollow.upsert({
      where: { agentId_userId: { agentId, userId } },
      create: { agentId, userId },
      update: {},
    });

    await this.notifications.notifyUser(userId, {
      type: NotificationType.TRADING_AGENT_UPDATE,
      title: `${agent.name} hired`,
      body: `Private dashboard active on ${EXCHANGE_PROVIDER_LABELS[input.exchangeProvider as ExchangeProvider]}. AI included — no API key needed.`,
      link: `/agent-hub/${agent.slug}/my-dashboard`,
    });

    return this.formatInstance(instance, agent);
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

    const exchangeStatus = await this.exchanges.getUserExchangeStatus(
      userId,
      instance.exchangeProvider,
    );

    return {
      kind: 'private' as const,
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
      },
      exchange: exchangeStatus,
      /** Isolated runtime — execution worker attaches here later */
      runtime: {
        connected: instance.status === TradingAgentInstanceStatus.ACTIVE,
        message:
          'Your keys power your private instance only. Admin showcase keys are never used.',
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
      dashboardUrl: `/agent-hub/${agent.slug}/my-dashboard`,
    };
  }
}
