import { Injectable, Logger, NotFoundException, ForbiddenException, OnModuleInit } from '@nestjs/common';
import {
  NotificationType,
  Prisma,
  SignalCycleStatus,
  TradingAgentActivityType,
  TradingAgentKind,
  TradingAgentStatus,
} from '@prisma/client';
import {
  TradingAgentDashboardState,
  buildTradingAgentActionShareText,
  EXCHANGE_PROVIDER_LABELS,
  readCopyRelaySimState,
  type CopyRelayReconcileSnapshot,
  type CopyRelaySimState,
  type CopyRelayCapacitySnapshot,
  type ExchangeProvider,
  buildCopyRelayLimitChain,
  buildTradeLifecycleIntegrity,
  type CopyRelayLimitChainSnapshot,
  type TradeLifecycleIntegritySnapshot,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';
import { NotificationsService } from '../notifications/notifications.service';
import { ExchangesService } from '../exchanges/exchanges.service';
import { BotBridgeService } from './bot-bridge.service';
import { CopyRelaySimService } from './copy-relay-sim.service';
import {
  mapBotStateToPublicDashboard,
  sanitizeActivityForPublic,
  filterActivityToExecutedTrades,
  mapBotStateToExecutedTradesActivity,
  mapBotStateToActivity,
  type BotActivityEntry,
} from './bot-state.mapper';
import {
  buildShowcaseFlashFromBot,
} from './showcase-flash.util';
import {
  readInstanceScope,
  scopeActivityToUserSession,
  statsFromScopedActivity,
  type UserInstanceScope,
  type UserInstanceStats,
} from './instance-view.mapper';
import {
  mapSubscriberExchangeLiveBook,
  type SubscriberCycleRow,
} from './subscriber-exchange-live.mapper';
import {
  mapLiveBookToActivity,
  mergeActivityFeeds,
} from './livebook-activity.mapper';
import {
  liveTradesToCsv,
  mapParticipantToExportRow,
  type LiveTradeExportPayload,
} from './live-trade-export.mapper';

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
    leverage: 100,
    liveBook: {
      activeSignals: [],
      positions: [],
      pendingOrders: [],
      expiredOrders: [],
      trades: [],
    },
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
    exchangeProvider?: string | null;
    exchangeLabel?: string | null;
    exchangeConnected?: boolean;
    viewScope?: 'showcase' | 'user';
    userSessionStartedAt?: string | null;
    rentalExpiresAt?: string | null;
    exchangeBalanceUsd?: number | null;
    hireFeeDdollar?: number | null;
    paperDdRefunded?: number | null;
    exchangeUsd?: number | null;
    fundingUsd?: number | null;
    tradingFeesUsd?: number | null;
    fundingFeesUsd?: number | null;
    fundsInWrongWallet?: boolean;
    openPositionSide?: string | null;
    walletStatusHint?: string | null;
    liveStats?: {
      balanceUsd?: number;
      equityUsd?: number;
      netReturnPct?: number;
      tradeCount?: number;
      winRatePct?: number;
      dailyPnlUsd?: number;
      sessionPnlUsd?: number;
      unrealizedPnlUsd?: number;
      liveSinceDays?: number;
      currentPosition?: string;
      currentAction?: string;
      leverage?: number;
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
    dailyPnlUsd: live?.dailyPnlUsd ?? 0,
    sessionPnlUsd: live?.sessionPnlUsd ?? 0,
    unrealizedPnlUsd: live?.unrealizedPnlUsd ?? 0,
    costDdollarDay: agent.costDdollarDay,
    costDdollarWeek: agent.costDdollarWeek,
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
    exchangeProvider: extra?.exchangeProvider ?? null,
    exchangeLabel: extra?.exchangeLabel ?? null,
    exchangeConnected: extra?.exchangeConnected ?? false,
    viewScope: extra?.viewScope ?? 'showcase',
    userSessionStartedAt: extra?.userSessionStartedAt ?? null,
    rentalExpiresAt: extra?.rentalExpiresAt ?? null,
    exchangeBalanceUsd: extra?.exchangeBalanceUsd ?? null,
    hireFeeDdollar: extra?.hireFeeDdollar ?? null,
    paperDdRefunded: extra?.paperDdRefunded ?? null,
    exchangeUsd: extra?.exchangeUsd ?? null,
    fundingUsd: extra?.fundingUsd ?? null,
    tradingFeesUsd: extra?.tradingFeesUsd ?? null,
    fundingFeesUsd: extra?.fundingFeesUsd ?? null,
    fundsInWrongWallet: extra?.fundsInWrongWallet ?? false,
    openPositionSide: extra?.openPositionSide ?? null,
    walletStatusHint: extra?.walletStatusHint ?? null,
    botConnected: extra?.botConnected ?? false,
    currentPosition: live?.currentPosition,
    currentAction: live?.currentAction,
    leverage: live?.leverage ?? 100,
  };
}

function latestParticipantExecutionMeta(
  events: Array<{ eventType: string; payload: unknown }>,
): { limitPrice: number | null; qty: number | null } {
  for (const e of events) {
    const p = (e.payload ?? {}) as Record<string, unknown>;
    const limit = Number(p.limitPrice ?? p.limit_price ?? 0);
    const qty = Number(p.qty ?? 0);
    if (limit > 0 || qty > 0) {
      return {
        limitPrice: limit > 0 ? limit : null,
        qty: qty > 0 ? qty : null,
      };
    }
  }
  return { limitPrice: null, qty: null };
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
    private readonly exchanges: ExchangesService,
    private readonly relaySim: CopyRelaySimService,
  ) {}

  async onModuleInit() {
    await this.ensureSeed().catch(() => undefined);
    await this.syncConservativeBtcAgentRecord().catch(() => undefined);
    setInterval(() => void this.pollBotPositionAlerts(), 20_000);
  }

  /** Keep showcase pricing in sync; live PnL comes from bot bridge (never zero here). */
  private async syncConservativeBtcAgentRecord() {
    await this.prisma.tradingAgent.updateMany({
      where: { slug: 'conservative-btc' },
      data: {
        costDdollarWeek: 2000,
        startingBalance: 500,
      },
    });
    await this.syncShowcaseMetricsFromBot().catch(() => undefined);
  }

  /** Mirror live bot session stats into Neon so public profile never shows stale seed data. */
  async syncShowcaseMetricsFromBot() {
    if (!this.botBridge.isEnabled()) return;
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
    if (!agent) return;

    const live = await this.botBridge.getLiveDashboard(agent.name, true);
    if (!live?.stats) return;

    await this.prisma.tradingAgent.update({
      where: { id: agent.id },
      data: {
        balanceUsd: live.stats.balanceUsd,
        equityUsd: live.stats.equityUsd,
        netReturnPct: live.stats.netReturnPct,
        tradeCount: live.stats.tradeCount,
        winRatePct: live.stats.winRatePct,
      },
    });
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
        costDdollarWeek: 2000,
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
    const live = await this.botBridge.getLiveDashboard(agent.name, false);
    if (!live) {
      return serializeAgent(agent, { ...extra, botConnected: false });
    }
    return serializeAgent(agent, {
      ...extra,
      liveStats: extra?.viewScope === 'user' && extra?.liveStats ? extra.liveStats : live.stats,
      botConnected: true,
    });
  }

  async getBotBridgeStatus() {
    const enabled = this.botBridge.isEnabled();
    if (!enabled) {
      return { status: 'offline' as const, label: 'Agent offline' };
    }
    const reachable = await this.botBridge.isReachable(true);
    if (!reachable) {
      return { status: 'offline' as const, label: 'Showcase bot offline (stopped on Railway)' };
    }
    const state = await this.botBridge.fetchState(true);
    let status: 'online' | 'offline' | 'updating' = 'offline';
    if (state) {
      if (state.execution_paused) {
        const reason = state.execution_reason ?? '';
        status = reason === 'ADMIN_MANUAL' ? 'offline' : 'updating';
      } else {
        status = 'online';
      }
    }
    return {
      status,
      label:
        status === 'online'
          ? 'Agent online'
          : status === 'updating'
            ? 'Agent updating'
            : 'Showcase bot offline (stopped on Railway)',
    };
  }

  async getBotBridgeStatusAdmin() {
    const enabled = this.botBridge.isEnabled();
    const url = this.botBridge.getBotUrl();
    const [state, health] = await Promise.all([
      enabled ? this.botBridge.fetchState(true) : Promise.resolve(null),
      enabled ? this.botBridge.fetchHealth() : Promise.resolve(null),
    ]);

    const reachable = Boolean(state) || Boolean(health);
    let publicStatus: 'online' | 'offline' | 'updating' = 'offline';
    if (enabled && reachable && state) {
      if (state.execution_paused) {
        const reason = state.execution_reason ?? '';
        publicStatus = reason === 'ADMIN_MANUAL' ? 'offline' : 'updating';
      } else {
        publicStatus = 'online';
      }
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
      connected: reachable && Boolean(state),
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
    let exchangeProvider: string | null = null;
    let exchangeLabel: string | null = null;
    let exchangeConnected = false;
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
        if (instanceRow.exchangeProvider !== 'paper') {
          exchangeProvider = instanceRow.exchangeProvider;
          exchangeLabel =
            EXCHANGE_PROVIDER_LABELS[instanceRow.exchangeProvider as ExchangeProvider] ??
            instanceRow.exchangeProvider;
          const exStatus = await this.exchanges.getUserExchangeStatus(
            userId,
            instanceRow.exchangeProvider,
          );
          exchangeConnected = Boolean(exStatus.connected);
        }
      }
      hired =
        instanceRow?.status === 'ACTIVE' || instanceRow?.status === 'PAUSED';
    }

    const userOverlay = userId
      ? await this.resolveUserInstanceOverlay(agent.id, userId)
      : null;

    return this.enrichWithBotLive(agent, {
      following,
      hired,
      exchangeProvider,
      exchangeLabel,
      exchangeConnected,
      ...(userOverlay ?? { instanceStatus, instanceMode }),
    });
  }

  private async resolveUserInstanceOverlay(agentId: string, userId: string) {
    const instance = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId, userId } },
    });
    if (!instance || (instance.status !== 'ACTIVE' && instance.status !== 'PAUSED')) {
      return null;
    }
    const agent = await this.prisma.tradingAgent.findUnique({ where: { id: agentId } });
    if (!agent) return null;

    const dash = (instance.dashboardState ?? {}) as Record<string, unknown>;
    let paperDdRefunded: number | null = null;

    if (
      instance.exchangeProvider !== 'paper' &&
      typeof dash.paperDdSpent === 'number' &&
      dash.paperDdSpent > 0 &&
      !dash.paperDdRefunded
    ) {
      const refund = dash.paperDdSpent as number;
      await this.points.award(userId, refund, `AGENT_PAPER_REFUND:${agent.slug}`);
      await this.prisma.tradingAgentInstance.update({
        where: { id: instance.id },
        data: {
          dashboardState: {
            ...dash,
            paperDdRefunded: true,
            paperDdRefundedAmount: refund,
          },
        },
      });
      paperDdRefunded = refund;
    } else if (typeof dash.paperDdRefundedAmount === 'number') {
      paperDdRefunded = dash.paperDdRefundedAmount;
    }

    const scope = readInstanceScope(instance);
    const rentalExpiresAt = instance.expiresAt?.toISOString() ?? null;
    const hireFeeDdollar =
      typeof dash.hireFeeDdollarPaid === 'number' ? (dash.hireFeeDdollarPaid as number) : null;

    if (scope.instanceMode === 'live' && instance.exchangeProvider !== 'paper') {
      const sessionStart = scope.sessionStartedAt;
      const closedParticipants = await this.prisma.signalCycleParticipant.findMany({
        where: {
          userId,
          status: SignalCycleStatus.CLOSED,
          updatedAt: { gte: sessionStart },
          cycle: { agentId },
        },
        select: { pnlUsd: true },
      });
      const realizedPnlUsd = closedParticipants.reduce(
        (sum, row) => sum + Number(row.pnlUsd ?? 0),
        0,
      );

      const metrics =
        instance.exchangeProvider === 'bitfinex'
          ? await this.exchanges.getUserBitfinexLiveMetrics(userId, {
              sessionStartedAt: sessionStart,
              realizedPnlUsd,
            })
          : null;

      const exchangeBalanceUsd = metrics?.derivativesAvailableUsd ?? 0;
      const walletHint = metrics?.fundsInWrongWallet
        ? `USDT detected in Exchange/Funding — move to Derivatives to trade (Exchange $${metrics.exchangeUsd.toFixed(0)} · Funding $${metrics.fundingUsd.toFixed(0)}).`
        : metrics?.openPosition
          ? `${metrics.openPosition.direction} open · unrealized ${metrics.unrealizedPnlUsd >= 0 ? '+' : ''}$${metrics.unrealizedPnlUsd.toFixed(2)}`
          : exchangeBalanceUsd < 5
            ? 'Transfer USDT to Bitfinex Derivatives wallet to arm the next copy trade.'
            : 'Derivatives funded — relay will copy next showcase signal.';

      return {
        instanceStatus: instance.status,
        instanceMode: 'live' as const,
        viewScope: 'user' as const,
        userSessionStartedAt: sessionStart.toISOString(),
        rentalExpiresAt,
        exchangeBalanceUsd,
        exchangeUsd: metrics?.exchangeUsd ?? null,
        fundingUsd: metrics?.fundingUsd ?? null,
        tradingFeesUsd: metrics?.tradingFeesUsd ?? null,
        fundingFeesUsd: metrics?.fundingFeesUsd ?? null,
        fundsInWrongWallet: metrics?.fundsInWrongWallet ?? false,
        openPositionSide: metrics?.openPosition?.direction ?? null,
        walletStatusHint: walletHint,
        hireFeeDdollar,
        paperDdRefunded,
        liveStats: {
          balanceUsd: metrics?.derivativesTotalUsd ?? exchangeBalanceUsd,
          equityUsd: metrics?.equityUsd ?? exchangeBalanceUsd,
          netReturnPct:
            metrics && metrics.derivativesTotalUsd > 0
              ? Number(((metrics.sessionPnlUsd / metrics.derivativesTotalUsd) * 100).toFixed(2))
              : 0,
          tradeCount: closedParticipants.length + (metrics?.openPosition ? 1 : 0),
          winRatePct: 0,
          sessionPnlUsd: metrics?.sessionPnlUsd ?? realizedPnlUsd,
          unrealizedPnlUsd: metrics?.unrealizedPnlUsd ?? 0,
          dailyPnlUsd: metrics?.sessionPnlUsd ?? 0,
        },
      };
    }

    const rawActivity = await this.fetchShowcaseExecutedActivity(agent.slug);
    const scoped = scopeActivityToUserSession(rawActivity, scope);
    const stats = statsFromScopedActivity(scoped, scope);
    return {
      instanceStatus: instance.status,
      instanceMode: scope.instanceMode,
      viewScope: 'user' as const,
      userSessionStartedAt: scope.sessionStartedAt.toISOString(),
      rentalExpiresAt,
      exchangeBalanceUsd: null,
      hireFeeDdollar,
      paperDdRefunded,
      liveStats: {
        balanceUsd: stats.balanceUsd,
        equityUsd: stats.equityUsd,
        netReturnPct: stats.netReturnPct,
        tradeCount: stats.tradeCount,
        winRatePct: stats.winRatePct,
        sessionPnlUsd: stats.sessionPnlUsd,
      },
    };
  }

  private async buildSubscriberExchangeLiveBook(
    userId: string,
    agentId: string,
    sessionStartedAt: Date,
    markPrice?: number,
  ): Promise<TradingAgentDashboardState['liveBook'] | null> {
    const instance = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId, userId } },
    });
    if (!instance) return null;

    const snapshot =
      instance.exchangeProvider === 'bitfinex'
        ? await this.exchanges.getUserBitfinexExchangeSnapshot(userId)
        : null;
    const metrics =
      instance.exchangeProvider === 'bitfinex'
        ? await this.exchanges.getUserBitfinexLiveMetrics(userId, {
            sessionStartedAt,
          })
        : null;

    const rows = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        createdAt: { gte: sessionStartedAt },
        cycle: { agentId },
      },
      include: {
        cycle: {
          select: {
            tradeId: true,
            status: true,
            intentEnvelope: true,
            showcaseExitReason: true,
            createdAt: true,
          },
        },
        events: {
          orderBy: { createdAt: 'desc' },
          take: 5,
          select: { eventType: true, payload: true },
        },
      },
      orderBy: { updatedAt: 'desc' },
      take: 100,
    });

    const participants: SubscriberCycleRow[] = rows.map((r) => {
      const meta = latestParticipantExecutionMeta(r.events);
      return {
        status: r.status,
        fillPrice: r.fillPrice != null ? Number(r.fillPrice) : null,
        exitPrice: r.exitPrice != null ? Number(r.exitPrice) : null,
        pnlUsd: r.pnlUsd != null ? Number(r.pnlUsd) : null,
        pnlMarginPct: r.pnlMarginPct != null ? Number(r.pnlMarginPct) : null,
        limitPrice: meta.limitPrice,
        qty: meta.qty,
        updatedAt: r.updatedAt,
        createdAt: r.createdAt,
        cycle: {
          tradeId: r.cycle.tradeId,
          status: r.cycle.status,
          intentEnvelope: r.cycle.intentEnvelope,
          showcaseExitReason: r.cycle.showcaseExitReason,
          createdAt: r.cycle.createdAt,
        },
      };
    });

    const exchangePosition = snapshot?.position ?? metrics?.openPosition ?? null;

    return mapSubscriberExchangeLiveBook({
      orders: snapshot?.orders ?? [],
      position: exchangePosition,
      markPrice,
      participants,
    });
  }

  private async fetchShowcaseExecutedActivity(slug: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) return [];
    if (slug === 'conservative-btc' && this.botBridge.isEnabled()) {
      const bot = await this.botBridge.fetchState(true);
      if (bot) {
        return filterActivityToExecutedTrades(
          mapBotStateToExecutedTradesActivity(bot, agent.name),
        );
      }
    }
    return [];
  }

  /** Public showcase dashboard — user instance overlays their own $500 session when signed in. */
  async getPublicDashboard(slug: string, userId?: string, _role?: string) {
    const payload = await this.getDashboard(slug, { publicSafe: true });
    const { rawBotState: _raw, ...rest } = payload as typeof payload & {
      rawBotState?: unknown;
    };

    let userInstance: UserInstanceStats | null = null;
    let viewScope: 'showcase' | 'user' = 'showcase';
    let agent = rest.agent;
    const showcaseAgent = { ...rest.agent };
    const showcaseLiveBook = rest.dashboard.liveBook;
    let exchangeLiveBook: TradingAgentDashboardState['liveBook'] | null = null;
    let overlayMeta: Awaited<ReturnType<typeof this.resolveUserInstanceOverlay>> | null = null;
    let agentRowId: string | null = null;

    if (userId) {
      const agentRow = await this.prisma.tradingAgent.findUnique({ where: { slug } });
      agentRowId = agentRow?.id ?? null;
      if (agentRow) {
        const overlay = await this.resolveUserInstanceOverlay(agentRow.id, userId);
        overlayMeta = overlay;
        if (overlay?.liveStats) {
          viewScope = 'user';
          userInstance = {
            ...overlay.liveStats,
            sessionStartedAt: overlay.userSessionStartedAt ?? new Date().toISOString(),
            startingBalanceUsd:
              overlay.instanceMode === 'live'
                ? (overlay.exchangeBalanceUsd ?? overlay.liveStats.balanceUsd)
                : overlay.liveStats.balanceUsd,
            instanceMode: overlay.instanceMode ?? 'copy',
          };
          agent = {
            ...agent,
            balanceUsd: overlay.liveStats.balanceUsd,
            equityUsd: overlay.liveStats.equityUsd,
            netReturnPct: overlay.liveStats.netReturnPct,
            tradeCount: overlay.liveStats.tradeCount,
            winRatePct: overlay.liveStats.winRatePct,
            sessionPnlUsd: overlay.liveStats.sessionPnlUsd ?? 0,
            unrealizedPnlUsd: overlay.liveStats.unrealizedPnlUsd ?? 0,
            dailyPnlUsd: overlay.liveStats.dailyPnlUsd ?? overlay.liveStats.sessionPnlUsd ?? 0,
            startingBalance:
              overlay.instanceMode === 'live'
                ? (overlay.liveStats.balanceUsd ?? overlay.exchangeBalanceUsd ?? 0)
                : overlay.liveStats.balanceUsd,
            viewScope: 'user',
            userSessionStartedAt: overlay.userSessionStartedAt,
            instanceMode: overlay.instanceMode,
            rentalExpiresAt: overlay.rentalExpiresAt ?? null,
            exchangeBalanceUsd: overlay.exchangeBalanceUsd ?? null,
            exchangeUsd: overlay.exchangeUsd ?? null,
            fundingUsd: overlay.fundingUsd ?? null,
            tradingFeesUsd: overlay.tradingFeesUsd ?? null,
            fundingFeesUsd: overlay.fundingFeesUsd ?? null,
            fundsInWrongWallet: overlay.fundsInWrongWallet ?? false,
            openPositionSide: overlay.openPositionSide ?? null,
            walletStatusHint: overlay.walletStatusHint ?? null,
            hireFeeDdollar: overlay.hireFeeDdollar ?? null,
            paperDdRefunded: overlay.paperDdRefunded ?? null,
          };
        }
      }
    }

    let showcaseFlash = null;
    if (slug === 'conservative-btc') {
      const bot = this.botBridge.isEnabled() ? await this.botBridge.fetchState() : null;
      showcaseFlash = buildShowcaseFlashFromBot(bot, {
        botConnected: Boolean(rest.botConnected),
        executionPaused: Boolean(rest.executionPaused),
      });
    }

    const showcaseActivityFromBook = mapLiveBookToActivity(showcaseLiveBook, 'showcase');
    const listActivityRows = await this.listActivity(slug, 50, true, undefined);
    let showcaseActivity = mergeActivityFeeds(listActivityRows, showcaseActivityFromBook);

    if (slug === 'conservative-btc' && this.botBridge.isEnabled()) {
      const botForActivity = await this.botBridge.fetchState();
      if (botForActivity) {
        const richFeed = mapBotStateToActivity(botForActivity, agent.name);
        showcaseActivity = mergeActivityFeeds(richFeed, showcaseActivity);
      }
    }

    let userActivity = showcaseActivity;
    if (userId && viewScope === 'user') {
      if (agentRowId && overlayMeta?.userSessionStartedAt) {
        exchangeLiveBook = await this.buildSubscriberExchangeLiveBook(
          userId,
          agentRowId,
          new Date(overlayMeta.userSessionStartedAt),
          rest.dashboard.currentPrice,
        );
      }
      if (overlayMeta?.instanceMode === 'live') {
        userActivity = mapLiveBookToActivity(
          exchangeLiveBook,
          `user-${userId}`,
          'positions-only',
        );
      } else {
        const userBookActivity = mapLiveBookToActivity(
          exchangeLiveBook,
          `user-${userId}`,
        );
        const scopedList = await this.listActivity(slug, 50, true, userId);
        userActivity = mergeActivityFeeds(userBookActivity, scopedList);
      }
    }

    let copyRelaySim: CopyRelaySimState | null = null;
    let relaySimLiveBook: TradingAgentDashboardState['liveBook'] | null = null;
    let copyRelayReconcile: CopyRelayReconcileSnapshot | null = null;
    let copyRelayCapacity: CopyRelayCapacitySnapshot | null = null;
    let copyRelayLimitChain: CopyRelayLimitChainSnapshot | null = null;
    let tradeLifecycleIntegrity: TradeLifecycleIntegritySnapshot | null = null;

    if (userId && agentRowId) {
      const inst = await this.prisma.tradingAgentInstance.findUnique({
        where: { agentId_userId: { agentId: agentRowId, userId } },
      });
      if (inst && inst.exchangeProvider === 'bitfinex') {
        const instDash = (inst.dashboardState ?? {}) as Record<string, unknown>;
        copyRelaySim = readCopyRelaySimState(instDash);
        copyRelayReconcile =
          (instDash.copyRelayReconcile as CopyRelayReconcileSnapshot | undefined) ??
          copyRelaySim.reconcile ??
          null;
        copyRelayCapacity =
          (instDash.copyRelayCapacity as CopyRelayCapacitySnapshot | undefined) ?? null;
        copyRelayLimitChain = buildCopyRelayLimitChain({
          showcaseMaxActiveSignals: copyRelayCapacity?.showcaseMaxActiveSignals ?? null,
          capacityLimit: copyRelayCapacity?.capacityLimit,
          activeOpen: copyRelayCapacity?.activeOpen,
          activePending: copyRelayCapacity?.activePending,
          source: copyRelayCapacity?.source ?? null,
        });

        const recentParticipants = await this.prisma.signalCycleParticipant.findMany({
          where: { userId, cycle: { agentId: agentRowId } },
          include: {
            cycle: { select: { tradeId: true } },
            events: { select: { eventType: true } },
          },
          orderBy: { updatedAt: 'desc' },
          take: 100,
        });
        tradeLifecycleIntegrity = buildTradeLifecycleIntegrity(recentParticipants);

        if (copyRelaySim.active) {
          const mark =
            typeof rest.dashboard.currentPrice === 'number' ? rest.dashboard.currentPrice : null;
          relaySimLiveBook = await this.relaySim.buildSimLiveBook(
            userId,
            agentRowId,
            copyRelaySim,
            mark,
          );
        }
      }
    }

    return {
      ...rest,
      agent,
      kind: 'public' as const,
      viewScope,
      userInstance,
      showcaseFlash,
      showcaseAgent: viewScope === 'user' ? showcaseAgent : undefined,
      showcaseLiveBook,
      exchangeLiveBook,
      showcaseActivity,
      userActivity,
      copyRelaySim,
      relaySimLiveBook,
      copyRelayReconcile,
      copyRelayCapacity,
      copyRelayLimitChain,
      tradeLifecycleIntegrity,
      showcaseNote:
        viewScope === 'user' && userInstance?.instanceMode === 'live'
          ? 'Your live copy session — balance from your connected exchange. Relay mirrors admin showcase signals.'
          : viewScope === 'user'
            ? 'Your isolated copy session — stats and trades only from when you started testing.'
            : 'Admin showcase for observation only. Paper-track or hire for your own session.',
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
      const live = await this.botBridge.getLiveDashboard(agent.name, false);
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

  async listActivity(slug: string, limit = 30, publicSafe = false, userId?: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');

    let userScope: UserInstanceScope | null = null;
    if (userId) {
      const instance = await this.prisma.tradingAgentInstance.findUnique({
        where: { agentId_userId: { agentId: agent.id, userId } },
      });
      if (instance && (instance.status === 'ACTIVE' || instance.status === 'PAUSED')) {
        userScope = readInstanceScope(instance);
      }
    }

    if (slug === 'conservative-btc' && this.botBridge.isEnabled()) {
      const bot = await this.botBridge.fetchState();
      if (bot) {
        const take = Math.min(50, Math.max(1, limit));
        const source = publicSafe
          ? mapBotStateToExecutedTradesActivity(bot, agent.name)
          : ((await this.botBridge.getLiveActivity(agent.name)) ?? []);
        let mapped: BotActivityEntry[] = source.slice(0, take).map((row) => ({
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
        if (publicSafe) {
          mapped = filterActivityToExecutedTrades(sanitizeActivityForPublic(mapped));
        }
        if (userScope) {
          mapped = scopeActivityToUserSession(mapped, userScope).slice(0, take);
        }
        return mapped;
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
        'Copy-trades admin DeepSeek AI on the showcase bot. DDollar paper track needs no API keys. Live Bitfinex hire: platform enforces $20 max margin per trade.',
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

    await this.syncShowcaseMetricsFromBot();

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

  /** Full Bitfinex live-copy history for the signed-in user's hired instance (no row cap). */
  async exportUserBitfinexLiveTrades(
    userId: string,
    slug: string,
    format: 'csv' | 'json' = 'csv',
  ): Promise<{ filename: string; csv?: string; payload: LiveTradeExportPayload }> {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');

    const instance = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId: agent.id, userId } },
    });
    if (!instance) {
      throw new ForbiddenException('Hire this agent to export your live Bitfinex trades');
    }
    if (instance.exchangeProvider === 'paper') {
      throw new ForbiddenException(
        'This export is for Bitfinex live copy only — switch from paper track or hire with Bitfinex API keys',
      );
    }
    if (instance.exchangeProvider !== 'bitfinex') {
      throw new ForbiddenException(
        `Live trade export is only available for Bitfinex (your venue: ${instance.exchangeProvider})`,
      );
    }

    const scope = readInstanceScope(instance);

    const participants = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        cycle: { agentId: agent.id },
      },
      include: {
        cycle: {
          select: {
            id: true,
            tradeId: true,
            status: true,
            intentEnvelope: true,
            showcaseExitReason: true,
            createdAt: true,
            closedAt: true,
          },
        },
        events: {
          orderBy: { createdAt: 'asc' },
          select: { eventType: true, payload: true, createdAt: true },
        },
      },
      orderBy: { createdAt: 'desc' },
    });

    const rows = participants.map((p) =>
      mapParticipantToExportRow({
        participant: {
          id: p.id,
          status: p.status,
          venue: p.venue,
          fillPrice: p.fillPrice,
          exitPrice: p.exitPrice,
          pnlUsd: p.pnlUsd,
          pnlMarginPct: p.pnlMarginPct,
          feeUsd: p.feeUsd,
          createdAt: p.createdAt,
          updatedAt: p.updatedAt,
          events: p.events,
        },
        cycle: {
          id: p.cycle.id,
          tradeId: p.cycle.tradeId,
          intentEnvelope: p.cycle.intentEnvelope,
          showcaseExitReason: p.cycle.showcaseExitReason,
          createdAt: p.cycle.createdAt,
          closedAt: p.cycle.closedAt,
        },
      }),
    );

    const closedRows = rows.filter((r) => r.status === 'CLOSED' || r.status === 'EXPIRED');
    const totalPnlUsd = closedRows.reduce((sum, r) => sum + (r.pnlUsd ?? 0), 0);

    const payload: LiveTradeExportPayload = {
      exportedAt: new Date().toISOString(),
      agentSlug: slug,
      agentName: agent.name,
      exchange: 'bitfinex',
      sessionStartedAt: scope.sessionStartedAt.toISOString(),
      userId,
      tradeCount: rows.length,
      closedCount: closedRows.length,
      totalPnlUsd: Number(totalPnlUsd.toFixed(4)),
      rows,
    };

    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    const filename = `bitfinex-live-trades-${slug}-${stamp}.${format === 'json' ? 'json' : 'csv'}`;

    if (format === 'json') {
      return { filename, payload };
    }
    return { filename, csv: liveTradesToCsv(payload), payload };
  }
}
