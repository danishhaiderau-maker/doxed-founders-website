import { Injectable, NotFoundException, OnModuleInit } from '@nestjs/common';
import {
  NotificationType,
  Prisma,
  TradingAgentActivityType,
  TradingAgentKind,
  TradingAgentStatus,
} from '@prisma/client';
import {
  TradingAgentDashboardState,
  buildTradingAgentActionShareText,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { BotBridgeService } from './bot-bridge.service';

function daysAgo(n: number) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d;
}

function hoursAgo(n: number) {
  const d = new Date();
  d.setHours(d.getHours() - n);
  return d;
}

function buildDefaultDashboard(support: number, resistance: number, price: number): TradingAgentDashboardState {
  const distRes = resistance > 0 ? ((resistance - price) / price) * 100 : 0;
  const distSup = support > 0 ? ((price - support) / price) * 100 : 0;
  return {
    currentPrice: price,
    regime: 'RANGE',
    support,
    resistance,
    distanceToResistancePct: Math.max(0, Number(distRes.toFixed(2))),
    distanceToSupportPct: Math.max(0, Number(distSup.toFixed(2))),
    currentPosition: 'NONE',
    currentAction: 'WAITING',
    aiDecision: 'NO_TRADE',
    aiWinProbability: 42,
    currentEdge: 0,
    requiredEdge: 4,
    noTradeReason: 'Range Compression',
    currentThinking: {
      market: 'Range Compression',
      support,
      resistance,
      distanceToResistancePct: Math.max(0, Number(distRes.toFixed(2))),
      distanceToSupportPct: Math.max(0, Number(distSup.toFixed(2))),
      conclusion: 'No edge detected. Waiting.',
    },
    transparency: {
      currentEdge: 0,
      requiredEdge: 4,
      currentState: 'No Trade',
      reason: 'Range Compression',
    },
    openTrades: [],
    pendingOrders: [],
    recentTrades: [
      { side: 'LONG', entryPrice: 72850, exitPrice: 74320, profitPct: 2.1, closedAt: hoursAgo(3).toISOString() },
      { side: 'SHORT', entryPrice: 75100, exitPrice: 74680, profitPct: 0.56, closedAt: hoursAgo(8).toISOString() },
      { side: 'LONG', entryPrice: 71900, exitPrice: 71200, profitPct: -0.97, closedAt: hoursAgo(26).toISOString() },
    ],
    marketStructure: 'Compression between support and resistance — no directional bias.',
    aiReasoning:
      'Momentum weak. Volume declining inside range. Edge score 0/6 — insufficient for entry. Waiting for breakout or retest with volume confirmation.',
    riskStatus: 'NORMAL',
    fundingStatus: 'N/A (paper research mode)',
    dataSource: 'CoinGecko + exchange WS',
    wsHealth: 'HEALTHY',
    dataQuality: 'GOOD',
    pnl: { daily: -2.1, total: -4.9 },
  };
}

async function fetchBtcPriceUsd(): Promise<number | null> {
  try {
    const res = await fetch(
      'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd',
      { signal: AbortSignal.timeout(8000) },
    );
    if (!res.ok) return null;
    const data = (await res.json()) as { bitcoin?: { usd?: number } };
    const price = data.bitcoin?.usd;
    return typeof price === 'number' && price > 0 ? price : null;
  } catch {
    return null;
  }
}

function serializeAgent(
  agent: Prisma.TradingAgentGetPayload<object>,
  extra?: {
    following?: boolean;
    liveStats?: {
      balanceUsd?: number;
      equityUsd?: number;
      netReturnPct?: number;
      tradeCount?: number;
      winRatePct?: number;
      liveSinceDays?: number;
      currentPosition?: string;
      currentAction?: string;
    };
    botConnected?: boolean;
  },
) {
  const live = extra?.liveStats;
  return {
    id: agent.id,
    slug: agent.slug,
    name: agent.name,
    description: agent.description,
    kind: agent.kind,
    status: agent.status,
    assetSymbol: agent.assetSymbol,
    startingBalance: Number(agent.startingBalance),
    balanceUsd: live?.balanceUsd ?? Number(agent.balanceUsd),
    equityUsd: live?.equityUsd ?? Number(agent.equityUsd),
    netReturnPct: live?.netReturnPct ?? Number(agent.netReturnPct),
    tradeCount: live?.tradeCount ?? agent.tradeCount,
    winRatePct: live?.winRatePct ?? Number(agent.winRatePct),
    costDdollarDay: agent.costDdollarDay,
    liveSince: agent.liveSince.toISOString(),
    liveSinceDays:
      live?.liveSinceDays ??
      Math.max(1, Math.floor((Date.now() - agent.liveSince.getTime()) / (1000 * 60 * 60 * 24))),
    followerCount: agent.followerCount,
    isExperimental: agent.isExperimental,
    following: extra?.following ?? false,
    botConnected: extra?.botConnected ?? false,
    currentPosition: live?.currentPosition,
    currentAction: live?.currentAction,
  };
}

@Injectable()
export class TradingAgentsService implements OnModuleInit {
  constructor(
    private readonly prisma: PrismaService,
    private readonly botBridge: BotBridgeService,
  ) {}

  async onModuleInit() {
    await this.ensureSeed().catch(() => undefined);
  }

  async ensureSeed() {
    const existing = await this.prisma.tradingAgent.findUnique({
      where: { slug: 'conservative-btc' },
    });
    if (existing) return;

    const livePrice = (await fetchBtcPriceUsd()) ?? 73500;
    const support = Math.round(livePrice * 0.995);
    const resistance = Math.round(livePrice * 1.008);
    const dashboard = buildDefaultDashboard(support, resistance, livePrice);

    const agent = await this.prisma.tradingAgent.create({
      data: {
        slug: 'conservative-btc',
        name: 'Conservative BTC Agent',
        description:
          'Transparent BTC range trader in research mode. Watch every decision, rejection, and wait state live.',
        kind: TradingAgentKind.TRADING,
        status: TradingAgentStatus.TESTING,
        assetSymbol: 'BTC',
        startingBalance: 500,
        balanceUsd: 500,
        equityUsd: 475.5,
        netReturnPct: -4.9,
        tradeCount: 43,
        winRatePct: 58,
        costDdollarDay: 1000,
        liveSince: daysAgo(31),
        dashboardState: dashboard as unknown as Prisma.InputJsonValue,
        isExperimental: true,
        sortOrder: 0,
      },
    });

    const activities: Array<{
      type: TradingAgentActivityType;
      title: string;
      reason?: string;
      outcome?: string;
      profitPct?: number;
      edgeScore?: number;
      edgeRequired?: number;
      marketRegime?: string;
      createdAt: Date;
    }> = [
      {
        type: 'NO_TRADE',
        title: 'No Trade',
        reason: 'Insufficient Edge',
        edgeScore: 0,
        edgeRequired: 4,
        marketRegime: 'Range Compression',
        createdAt: hoursAgo(1),
      },
      {
        type: 'REJECTED',
        title: 'Analyzed Market',
        outcome: 'Rejected',
        reason: 'Weak Momentum',
        edgeScore: 2,
        edgeRequired: 4,
        marketRegime: 'Range Compression',
        createdAt: hoursAgo(2),
      },
      {
        type: 'POSITION_CLOSED',
        title: 'Position Closed',
        outcome: 'Profit',
        profitPct: 2.1,
        reason: 'Take profit at resistance test',
        createdAt: hoursAgo(3),
      },
      {
        type: 'AI_REJECTED',
        title: 'Rejected LONG',
        reason: 'Resistance Too Close',
        edgeScore: 2,
        edgeRequired: 6,
        marketRegime: 'Range Compression',
        createdAt: hoursAgo(5),
      },
      {
        type: 'ANALYZED',
        title: 'Analyzed Market',
        outcome: 'Rejected',
        reason: 'Funding skew unfavorable',
        createdAt: hoursAgo(7),
      },
      {
        type: 'POSITION_OPENED',
        title: 'Position Opened',
        outcome: 'LONG',
        reason: 'Edge score 5/6 — breakout retest',
        edgeScore: 5,
        edgeRequired: 4,
        createdAt: hoursAgo(12),
      },
    ];

    for (const item of activities) {
      const shareText = buildTradingAgentActionShareText({
        agentName: agent.name,
        action: item.title,
        reason: item.reason,
        edgeScore: item.edgeScore,
        edgeRequired: item.edgeRequired,
        marketRegime: item.marketRegime,
      });
      await this.prisma.tradingAgentActivity.create({
        data: {
          agentId: agent.id,
          type: item.type,
          title: item.title,
          reason: item.reason,
          outcome: item.outcome,
          profitPct: item.profitPct,
          edgeScore: item.edgeScore,
          edgeRequired: item.edgeRequired,
          marketRegime: item.marketRegime,
          shareText,
          createdAt: item.createdAt,
        },
      });
    }

    const placeholders = [
      { slug: 'eth-momentum', name: 'ETH Momentum', kind: TradingAgentKind.TRADING, sortOrder: 1 },
      { slug: 'alpha-hunter', name: 'Alpha Hunter', kind: TradingAgentKind.TRADING, sortOrder: 2 },
      { slug: 'scalp-engine', name: 'Scalp Engine', kind: TradingAgentKind.TRADING, sortOrder: 3 },
      { slug: 'community-sentiment', name: 'Community Sentiment', kind: TradingAgentKind.SCOUT, sortOrder: 4 },
    ];

    for (const p of placeholders) {
      await this.prisma.tradingAgent.create({
        data: {
          slug: p.slug,
          name: p.name,
          description: 'Coming soon — agent leaderboard slot reserved.',
          kind: p.kind,
          status: TradingAgentStatus.PAUSED,
          assetSymbol: p.slug.includes('eth') ? 'ETH' : 'MULTI',
          startingBalance: 500,
          balanceUsd: 500,
          equityUsd: 500,
          netReturnPct: 0,
          tradeCount: 0,
          winRatePct: 0,
          costDdollarDay: 1000,
          liveSince: new Date(),
          dashboardState: buildDefaultDashboard(0, 0, 0) as unknown as Prisma.InputJsonValue,
          isExperimental: true,
          sortOrder: p.sortOrder,
        },
      });
    }
  }

  private async enrichWithBotLive(
    agent: Prisma.TradingAgentGetPayload<object>,
    extra?: Parameters<typeof serializeAgent>[1],
  ) {
    if (agent.slug !== 'conservative-btc' || !this.botBridge.isEnabled()) {
      return serializeAgent(agent, extra);
    }
    const live = await this.botBridge.getLiveDashboard(agent.name);
    if (!live) {
      return serializeAgent(agent, extra);
    }
    return serializeAgent(agent, {
      ...extra,
      liveStats: live.stats,
      botConnected: true,
    });
  }

  async getBotBridgeStatus() {
    const url = this.botBridge.getBotUrl();
    const enabled = this.botBridge.isEnabled();
    const state = enabled ? await this.botBridge.fetchState() : null;
    return {
      enabled,
      url: url ? `${url}/api/state` : null,
      connected: Boolean(state),
      strategyMode: state?.strategy_mode ?? null,
      executionPaused: state?.execution_paused ?? false,
      executionReason: state?.execution_reason ?? null,
      price: state?.price ?? null,
      lastFetchAt: state ? new Date().toISOString() : null,
    };
  }

  async list(kind?: TradingAgentKind) {
    const agents = await this.prisma.tradingAgent.findMany({
      where: kind ? { kind } : undefined,
      orderBy: [{ sortOrder: 'asc' }, { createdAt: 'asc' }],
    });
    const serialized = await Promise.all(agents.map((a) => this.enrichWithBotLive(a)));
    return {
      agents: serialized,
      kinds: Object.values(TradingAgentKind),
    };
  }

  async getBySlug(slug: string, userId?: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');

    let following = false;
    if (userId) {
      const row = await this.prisma.tradingAgentFollow.findUnique({
        where: { agentId_userId: { agentId: agent.id, userId } },
      });
      following = Boolean(row);
    }

    return this.enrichWithBotLive(agent, { following });
  }

  async getDashboard(slug: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');

    if (slug === 'conservative-btc' && this.botBridge.isEnabled()) {
      const live = await this.botBridge.getLiveDashboard(agent.name);
      if (live) {
        return {
          agent: serializeAgent(agent, {
            liveStats: live.stats,
            botConnected: true,
          }),
          dashboard: live.dashboard,
          updatedAt: new Date().toISOString(),
          botConnected: true,
          botSource: 'LIVE' as const,
          strategyMode: live.strategyMode,
          executionPaused: live.executionPaused,
          executionReason: live.executionReason,
        };
      }
    }

    const stored = agent.dashboardState as TradingAgentDashboardState;
    const livePrice = await fetchBtcPriceUsd();

    if (livePrice && agent.assetSymbol === 'BTC' && agent.status !== TradingAgentStatus.PAUSED) {
      const support = stored.support || Math.round(livePrice * 0.995);
      const resistance = stored.resistance || Math.round(livePrice * 1.008);
      const refreshed = buildDefaultDashboard(support, resistance, livePrice);
      refreshed.recentTrades = stored.recentTrades ?? refreshed.recentTrades;
      refreshed.pnl = stored.pnl ?? refreshed.pnl;
      refreshed.openTrades = stored.openTrades ?? [];
      refreshed.pendingOrders = stored.pendingOrders ?? [];

      return {
        agent: serializeAgent(agent),
        dashboard: refreshed,
        updatedAt: new Date().toISOString(),
        botConnected: false,
        botSource: 'FALLBACK' as const,
      };
    }

    return {
      agent: serializeAgent(agent),
      dashboard: stored,
      updatedAt: agent.updatedAt.toISOString(),
      botConnected: false,
      botSource: 'FALLBACK' as const,
    };
  }

  async listActivity(slug: string, limit = 30) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');

    if (slug === 'conservative-btc' && this.botBridge.isEnabled()) {
      const liveActivity = await this.botBridge.getLiveActivity(agent.name);
      if (liveActivity?.length) {
        const take = Math.min(50, Math.max(1, limit));
        return liveActivity.slice(0, take).map((row) => ({
          ...row,
          shareText:
            row.shareText ??
            buildTradingAgentActionShareText({
              agentName: agent.name,
              action: row.title,
              reason: row.reason,
              edgeScore: row.edgeScore,
              edgeRequired: row.edgeRequired,
              marketRegime: row.marketRegime,
            }),
        }));
      }
    }

    const take = Math.min(50, Math.max(1, limit));
    const rows = await this.prisma.tradingAgentActivity.findMany({
      where: { agentId: agent.id },
      orderBy: { createdAt: 'desc' },
      take,
    });

    return rows.map((row) => ({
      id: row.id,
      type: row.type,
      title: row.title,
      reason: row.reason,
      outcome: row.outcome,
      profitPct: row.profitPct != null ? Number(row.profitPct) : null,
      edgeScore: row.edgeScore,
      edgeRequired: row.edgeRequired,
      marketRegime: row.marketRegime,
      shareText: row.shareText,
      createdAt: row.createdAt.toISOString(),
    }));
  }

  async follow(userId: string, agentId: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { id: agentId } });
    if (!agent) throw new NotFoundException('Agent not found');

    const existing = await this.prisma.tradingAgentFollow.findUnique({
      where: { agentId_userId: { agentId, userId } },
    });
    if (existing) return { following: true };

    await this.prisma.tradingAgentFollow.create({ data: { agentId, userId } });
    await this.prisma.tradingAgent.update({
      where: { id: agentId },
      data: { followerCount: { increment: 1 } },
    });

    await this.prisma.notification.create({
      data: {
        userId,
        type: NotificationType.TRADING_AGENT_UPDATE,
        title: `Following ${agent.name}`,
        body: 'You will receive alerts when this agent opens trades, closes positions, or changes bias.',
        link: `/agent-hub/${agent.slug}`,
      },
    });

    return { following: true };
  }

  async unfollow(userId: string, agentId: string) {
    const deleted = await this.prisma.tradingAgentFollow.deleteMany({
      where: { agentId, userId },
    });
    if (deleted.count > 0) {
      await this.prisma.tradingAgent.update({
        where: { id: agentId },
        data: { followerCount: { decrement: 1 } },
      });
    }
    return { following: false };
  }

  async leaderboard() {
    const agents = await this.prisma.tradingAgent.findMany({
      where: { kind: TradingAgentKind.TRADING },
      orderBy: [{ status: 'asc' }, { netReturnPct: 'desc' }],
    });
    return Promise.all(agents.map((a) => this.enrichWithBotLive(a)));
  }
}
