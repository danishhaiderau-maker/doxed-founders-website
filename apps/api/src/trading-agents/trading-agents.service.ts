import { Injectable, Logger, NotFoundException, OnModuleInit } from '@nestjs/common';
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
import { PointsService } from '../points/points.service';
import { NotificationsService } from '../notifications/notifications.service';
import { BotBridgeService } from './bot-bridge.service';
import {
  mapBotStateToPublicDashboard,
  sanitizeActivityForPublic,
  filterActivityToExecutedTrades,
  mapBotStateToExecutedTradesActivity,
} from './bot-state.mapper';

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
    hired?: boolean;
    instanceStatus?: string | null;
    instanceMode?: 'copy' | 'live' | null;
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
    hired: extra?.hired ?? false,
    instanceStatus: extra?.instanceStatus ?? null,
    instanceMode: extra?.instanceMode ?? null,
    botConnected: extra?.botConnected ?? false,
    currentPosition: live?.currentPosition,
    currentAction: live?.currentAction,
  };
}

@Injectable()
export class TradingAgentsService implements OnModuleInit {
  private readonly logger = new Logger(TradingAgentsService.name);
  private lastBotPosition: string | null = null;

  constructor(
    private readonly prisma: PrismaService,
    private readonly botBridge: BotBridgeService,
    private readonly points: PointsService,
    private readonly notifications: NotificationsService,
  ) {}

  async onModuleInit() {
    await this.ensureSeed().catch(() => undefined);
    setInterval(() => void this.pollBotPositionAlerts(), 20_000);
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
    const enabled = this.botBridge.isEnabled();
    const state = enabled ? await this.botBridge.fetchState() : null;
    let status: 'online' | 'offline' | 'updating' = 'offline';
    if (enabled && state) {
      status = state.execution_paused ? 'updating' : 'online';
    }
    return {
      status,
      label: status === 'online' ? 'Agent online' : status === 'updating' ? 'Agent updating' : 'Agent offline',
    };
  }

  async getBotBridgeStatusAdmin() {
    const enabled = this.botBridge.isEnabled();
    const url = this.botBridge.getBotUrl();
    const [state, health] = await Promise.all([
      enabled ? this.botBridge.fetchState(true) : Promise.resolve(null),
      enabled ? this.botBridge.fetchHealth() : Promise.resolve(null),
    ]);

    let publicStatus: 'online' | 'offline' | 'updating' = 'offline';
    if (enabled && state) {
      publicStatus = state.execution_paused ? 'updating' : 'online';
    }

    let host: string | null = null;
    if (url) {
      try {
        host = new URL(url).hostname;
      } catch {
        host = 'configured';
      }
    }

    return {
      enabled,
      connected: Boolean(state),
      publicStatus,
      strategyMode: state?.strategy_mode ?? null,
      executionPaused: state?.execution_paused ?? false,
      executionReason: state?.execution_reason ?? null,
      price: state?.price ?? null,
      wsHealth: state?.diag?.ws_status ?? state?.ws_ready ?? null,
      deepSeekConnected: Boolean(state?.last_ai?.source && state.last_ai.source !== 'NONE'),
      appVersion:
        (state as Record<string, unknown> | null)?.bot_version ??
        (state as Record<string, unknown> | null)?.app_version ??
        null,
      lastFetchAt: state ? new Date().toISOString() : null,
      stateEndpoint: host,
      health,
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
    let hired = false;
    let instanceStatus: string | null = null;
    let instanceMode: 'copy' | 'live' | null = null;
    if (userId) {
      const [followRow, instanceRow] = await Promise.all([
        this.prisma.tradingAgentFollow.findUnique({
          where: { agentId_userId: { agentId: agent.id, userId } },
        }),
        this.prisma.tradingAgentInstance.findUnique({
          where: { agentId_userId: { agentId: agent.id, userId } },
        }),
      ]);
      following = Boolean(followRow);
      instanceStatus = instanceRow?.status ?? null;
      if (instanceRow) {
        const dash = (instanceRow.dashboardState ?? {}) as Record<string, unknown>;
        instanceMode =
          dash.instanceMode === 'copy' || instanceRow.exchangeProvider === 'paper'
            ? 'copy'
            : dash.instanceMode === 'live'
              ? 'live'
              : instanceRow.exchangeProvider === 'paper'
                ? 'copy'
                : 'live';
      }
      hired =
        instanceRow?.status === 'ACTIVE' || instanceRow?.status === 'PAUSED';
    }

    return this.enrichWithBotLive(agent, { following, hired, instanceStatus, instanceMode });
  }

  /** Public showcase dashboard — never exposes raw bot state or AI input payloads. */
  async getPublicDashboard(slug: string, userId?: string, _role?: string) {
    const payload = await this.getDashboard(slug, { publicSafe: true });
    const { rawBotState: _raw, ...rest } = payload as typeof payload & {
      rawBotState?: unknown;
    };
    return {
      ...rest,
      kind: 'public' as const,
      showcaseNote:
        'Platform-owned showcase. Hire this agent for an isolated private instance with your exchange API.',
    };
  }

  /** Admin-only full research bot snapshot (sensitive AI input / pipeline data). */
  async getAdminResearchDashboard(slug: string) {
    if (slug !== 'conservative-btc' || !this.botBridge.isEnabled()) {
      throw new NotFoundException('Research dashboard unavailable');
    }
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');
    const live = await this.botBridge.getLiveDashboard(agent.name);
    if (!live?.rawState) throw new NotFoundException('Bot not connected');
    const raw = live.rawState as Record<string, unknown>;
    return {
      agent: { slug: agent.slug, name: agent.name },
      rawBotState: live.rawState,
      botVersion: raw.bot_version ?? raw.app_version ?? null,
      executionPaused: live.executionPaused,
      executionReason: live.executionReason,
      strategyMode: live.strategyMode,
      updatedAt: new Date().toISOString(),
    };
  }

  async getDashboard(slug: string, opts?: { publicSafe?: boolean }) {
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
          dashboard: opts?.publicSafe
            ? mapBotStateToPublicDashboard(live.rawState)
            : live.dashboard,
          rawBotState: opts?.publicSafe ? undefined : live.rawState,
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

  async listActivity(slug: string, limit = 30, publicSafe = false) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');

    if (slug === 'conservative-btc' && this.botBridge.isEnabled()) {
      const bot = await this.botBridge.fetchState();
      if (bot) {
        const take = Math.min(50, Math.max(1, limit));
        const source = publicSafe
          ? mapBotStateToExecutedTradesActivity(bot, agent.name)
          : ((await this.botBridge.getLiveActivity(agent.name)) ?? []);
        const mapped = source.slice(0, take).map((row) => ({
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
        return publicSafe
          ? filterActivityToExecutedTrades(sanitizeActivityForPublic(mapped))
          : mapped;
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

  async getShowcaseDefaultSettings() {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    return {
      message:
        row?.agentShowcaseDefaultSettings?.trim() ??
        'Copy-trades admin DeepSeek AI on the showcase bot. DDollar demo ($500 max) needs no API keys. Live tier connects your exchange only — same AI signals as admin.',
    };
  }

  async follow(userId: string, agentId: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { id: agentId } });
    if (!agent) throw new NotFoundException('Agent not found');

    const existing = await this.prisma.tradingAgentFollow.findUnique({
      where: { agentId_userId: { agentId, userId } },
    });
    if (existing) return { following: true, costDdollarDay: agent.costDdollarDay };

    const cost = agent.costDdollarDay;
    if (cost > 0) {
      await this.points.spend(userId, cost, `AGENT_RENTAL:${agent.slug}`);
    }

    await this.prisma.tradingAgentFollow.create({ data: { agentId, userId } });
    await this.prisma.tradingAgent.update({
      where: { id: agentId },
      data: { followerCount: { increment: 1 } },
    });

    await this.notifications.notifyUser(userId, {
      type: NotificationType.TRADING_AGENT_UPDATE,
      title: `Following ${agent.name}`,
      body:
        cost > 0
          ? `Charged ${cost.toLocaleString()} DDollar/day rental. Alerts on trade open, close, and bias shifts.`
          : 'You will receive alerts when this agent opens trades, closes positions, or changes bias.',
      link: `/agent-hub/${agent.slug}`,
    });

    return { following: true, costDdollarDay: cost };
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

  private async pollBotPositionAlerts() {
    if (!this.botBridge.isEnabled()) return;

    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
    if (!agent || agent.status === TradingAgentStatus.PAUSED) return;

    const bot = await this.botBridge.fetchState();
    if (!bot) return;

    const positions = bot.positions ?? [];
    const openPos = positions[0];
    const position =
      positions.length === 0 ? 'NONE' : (openPos?.dir ?? openPos?.side ?? 'OPEN').toUpperCase();

    if (this.lastBotPosition === null) {
      this.lastBotPosition = position;
      return;
    }

    if (position === this.lastBotPosition) return;

    const prev = this.lastBotPosition;
    this.lastBotPosition = position;

    if (prev === 'NONE' && position !== 'NONE') {
      await this.notifyAgentFollowers(agent, {
        title: `${agent.name} opened ${position}`,
        body: `Live position detected at $${(bot.price ?? 0).toLocaleString()} — edge ${bot.debug_state?.last_edge_score ?? bot.last_edge ?? 0}/${bot.edge_threshold ?? 3}.`,
      });
      return;
    }

    if (prev !== 'NONE' && position === 'NONE') {
      const lastTrade = bot.trades?.[0];
      const pnl = lastTrade?.pnl ?? lastTrade?.net_pnl_usd;
      await this.notifyAgentFollowers(agent, {
        title: `${agent.name} closed ${prev}`,
        body:
          pnl != null
            ? `Position closed · PnL ${Number(pnl) >= 0 ? '+' : ''}${Number(pnl).toFixed(2)}%`
            : 'Position closed — check mission control for details.',
      });
    }
  }

  private async notifyAgentFollowers(
    agent: { id: string; slug: string; name: string },
    alert: { title: string; body: string },
  ) {
    const followers = await this.prisma.tradingAgentFollow.findMany({
      where: { agentId: agent.id },
      select: { userId: true },
    });
    if (followers.length === 0) return;

    await Promise.all(
      followers.map((row) =>
        this.notifications.notifyUser(row.userId, {
          type: NotificationType.TRADING_AGENT_UPDATE,
          title: alert.title,
          body: alert.body,
          link: `/agent-hub/${agent.slug}`,
        }),
      ),
    );
    this.logger.log(`Notified ${followers.length} follower(s): ${alert.title}`);
  }
}
