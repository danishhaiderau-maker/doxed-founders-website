import { Injectable } from '@nestjs/common';
import { LeaderboardPeriod, NotificationType, PaperTradeSide } from '@prisma/client';
import { formatPublicAccountLabel, formatUsd, type NotificationBuyerMeta } from '@dcf/utils';
import { NotificationsService } from '../notifications/notifications.service';
import { PrismaService } from '../prisma/prisma.service';

const HOT_BUY_THRESHOLD = 0.02;
const WINDOW_MS = 24 * 60 * 60 * 1000;
const ACTIVE_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;
const NOTIFY_COOLDOWN_MS = 6 * 60 * 60 * 1000;
const TOP_TRADER_RANK_LIMIT = 20;

export type HotBuySnapshot = {
  projectId: string;
  projectSlug: string;
  projectTicker: string;
  projectName: string;
  buyerCount: number;
  activeTraderCount: number;
  pctOfActive: number;
  topTraderCount: number;
  recentBuyers: NotificationBuyerMeta['buyers'];
};

@Injectable()
export class HotBuyService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly notifications: NotificationsService,
  ) {}

  async checkAfterBuy(projectId: string) {
    const snapshot = await this.computeHotBuy(projectId);
    if (!snapshot || snapshot.pctOfActive < HOT_BUY_THRESHOLD) return;

    const xBuyers = (snapshot.recentBuyers ?? []).filter((b) => b.twitterHandle?.trim());
    const minXBuyers = 2;
    if (xBuyers.length < minXBuyers && snapshot.topTraderCount < 3) return;

    const recent = await this.prisma.notification.findFirst({
      where: {
        type: NotificationType.TRENDING_BUYS,
        body: { contains: snapshot.projectTicker },
        createdAt: { gte: new Date(Date.now() - NOTIFY_COOLDOWN_MS) },
      },
    });
    if (recent) return;

    const buyerNames = (snapshot.recentBuyers ?? []).map((b) => b.displayName);
    const xHandles = xBuyers
      .map((b) => (b.twitterHandle ? `@${b.twitterHandle.replace(/^@/, '')}` : null))
      .filter((h): h is string => Boolean(h));
    const pctLabel = `${Math.round(snapshot.pctOfActive * 100)}%`;
    const title =
      xBuyers.length >= minXBuyers
        ? 'Verified X traders converging'
        : snapshot.topTraderCount >= 3
          ? 'Top traders buying'
          : 'Hot buy';
    const headline = this.formatHotBuyHeadline(snapshot);
    const detail = this.formatHotBuyDetail(snapshot);
    const xLine =
      xHandles.length >= 2
        ? ` X profiles: ${xHandles.slice(0, 4).join(', ')}${xHandles.length > 4 ? ' + more' : ''}.`
        : '';
    const body =
      snapshot.topTraderCount >= 3
        ? `${headline} — ${detail}.${xLine}`
        : buyerNames.length > 0
          ? `${headline} (${pctLabel} of active) — ${buyerNames.slice(0, 5).join(', ')}${buyerNames.length > 5 ? ' + more' : ''}.${xLine}`
          : `${headline} (${pctLabel} of active).${xLine}`;

    const metadata: NotificationBuyerMeta = {
      projectSlug: snapshot.projectSlug,
      projectTicker: snapshot.projectTicker,
      buyers: snapshot.recentBuyers,
    };

    const xBuyerIds = xBuyers
      .map((b) => b.userId)
      .filter((id): id is string => Boolean(id));
    const recipientIds = await this.hotBuyRecipientIds(snapshot.projectId, xBuyerIds);

    await this.notifications.notifyMarketAlert({
      type: NotificationType.TRENDING_BUYS,
      title: `🔥 ${title}: ${snapshot.projectTicker}`,
      body,
      link: `/project/${snapshot.projectSlug}`,
      metadata,
      recipientIds,
    });
  }

  /** Users who follow the project, watchlist it, trade it, or follow converging X buyers. */
  private async hotBuyRecipientIds(projectId: string, xBuyerUserIds: string[]): Promise<string[]> {
    const since = new Date(Date.now() - 30 * 86400000);
    const [projectFollowers, watchlisted, recentTraders, xTraderFollowers] = await Promise.all([
      this.prisma.projectFollow.findMany({
        where: { projectId },
        select: { userId: true },
        take: 50,
      }),
      this.prisma.watchlist.findMany({
        where: { projectId },
        select: { userId: true },
        take: 50,
      }),
      this.prisma.paperTrade.findMany({
        where: { projectId, createdAt: { gte: since } },
        select: { userId: true },
        distinct: ['userId'],
        take: 40,
      }),
      xBuyerUserIds.length > 0
        ? this.prisma.userFollow.findMany({
            where: { followingId: { in: xBuyerUserIds } },
            select: { followerId: true },
            take: 60,
          })
        : Promise.resolve([]),
    ]);

    const ids = new Set<string>();
    for (const row of projectFollowers) ids.add(row.userId);
    for (const row of watchlisted) ids.add(row.userId);
    for (const row of recentTraders) ids.add(row.userId);
    for (const row of xTraderFollowers) ids.add(row.followerId);
    for (const buyerId of xBuyerUserIds) ids.delete(buyerId);

    return [...ids].slice(0, 80);
  }

  async listHotBuys(limit = 5): Promise<HotBuySnapshot[]> {
    const windowStart = new Date(Date.now() - WINDOW_MS);

    const projectRows = await this.prisma.paperTrade.findMany({
      where: { side: PaperTradeSide.BUY, createdAt: { gte: windowStart } },
      select: { projectId: true },
      distinct: ['projectId'],
    });

    const hot: HotBuySnapshot[] = [];
    for (const row of projectRows) {
      const snapshot = await this.computeHotBuy(row.projectId);
      if (snapshot && snapshot.pctOfActive >= HOT_BUY_THRESHOLD) {
        hot.push(snapshot);
      }
    }

    return hot
      .sort((a, b) => b.pctOfActive - a.pctOfActive || b.buyerCount - a.buyerCount)
      .slice(0, limit);
  }

  private async computeHotBuy(projectId: string): Promise<HotBuySnapshot | null> {
    const windowStart = new Date(Date.now() - WINDOW_MS);
    const activeCount = await this.countActiveTraders();

    const buyers = await this.prisma.paperTrade.findMany({
      where: { projectId, side: PaperTradeSide.BUY, createdAt: { gte: windowStart } },
      select: { userId: true },
      distinct: ['userId'],
    });

    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      select: { id: true, slug: true, ticker: true, name: true },
    });
    if (!project) return null;

    const topTraderIds = await this.getTopTraderUserIds();
    const topTraderCount = buyers.filter((b) => topTraderIds.has(b.userId)).length;
    const recentBuyers = await this.loadRecentBuyers(projectId, windowStart);

    return {
      projectId: project.id,
      projectSlug: project.slug,
      projectTicker: project.ticker,
      projectName: project.name,
      buyerCount: buyers.length,
      activeTraderCount: activeCount,
      pctOfActive: buyers.length / Math.max(activeCount, 1),
      topTraderCount,
      recentBuyers,
    };
  }

  private async loadRecentBuyers(
    projectId: string,
    since: Date,
  ): Promise<NotificationBuyerMeta['buyers']> {
    const trades = await this.prisma.paperTrade.findMany({
      where: { projectId, side: PaperTradeSide.BUY, createdAt: { gte: since } },
      orderBy: { createdAt: 'desc' },
      take: 40,
      include: {
        user: { select: { id: true, name: true, email: true, twitterHandle: true } },
      },
    });

    const seen = new Set<string>();
    const buyers: NonNullable<NotificationBuyerMeta['buyers']> = [];

    for (const trade of trades) {
      if (seen.has(trade.userId)) continue;
      seen.add(trade.userId);
      buyers.push({
        userId: trade.userId,
        displayName: formatPublicAccountLabel(trade.user.name, trade.user.email),
        amountUsd: Number(trade.totalUsd),
        twitterHandle: trade.user.twitterHandle,
      });
      if (buyers.length >= 8) break;
    }

    return buyers;
  }

  private async getTopTraderUserIds(): Promise<Set<string>> {
    const entries = await this.prisma.leaderboardEntry.findMany({
      where: { period: LeaderboardPeriod.ALL_TIME, rank: { lte: TOP_TRADER_RANK_LIMIT } },
      select: { userId: true },
      take: TOP_TRADER_RANK_LIMIT,
    });
    if (entries.length > 0) {
      return new Set(entries.map((e) => e.userId));
    }

    const portfolios = await this.prisma.paperPortfolio.findMany({
      select: { id: true, userId: true, cashBalance: true },
    });
    const ranked = await Promise.all(
      portfolios.map(async (p) => {
        const positions = await this.prisma.paperPosition.findMany({
          where: { portfolioId: p.id },
          include: { project: { select: { id: true } } },
        });
        let positionsValue = 0;
        for (const pos of positions) {
          positionsValue += Number(pos.quantity) * Number(pos.avgBuyPrice);
        }
        return {
          userId: p.userId,
          totalValue: Number(p.cashBalance) + positionsValue,
        };
      }),
    );
    ranked.sort((a, b) => b.totalValue - a.totalValue);
    return new Set(ranked.slice(0, TOP_TRADER_RANK_LIMIT).map((r) => r.userId));
  }

  private async countActiveTraders(): Promise<number> {
    const activeSince = new Date(Date.now() - ACTIVE_WINDOW_MS);
    const rows = await this.prisma.paperTrade.findMany({
      where: { createdAt: { gte: activeSince } },
      select: { userId: true },
      distinct: ['userId'],
    });
    return Math.max(rows.length, 1);
  }

  async getRecentBuyersForProject(projectId: string, days = 30) {
    const since = new Date(Date.now() - days * 86400000);
    return this.loadRecentBuyers(projectId, since);
  }

  formatHotBuyHeadline(snapshot: HotBuySnapshot): string {
    const names = (snapshot.recentBuyers ?? []).map((b) => b.displayName);
    if (snapshot.topTraderCount >= 3) {
      const named = names.slice(0, 3).join(', ');
      return named
        ? `${named} + top traders bought ${snapshot.projectTicker}`
        : `${snapshot.topTraderCount} top traders bought ${snapshot.projectTicker}`;
    }
    if (names.length === 1) {
      return `${names[0]} bought ${snapshot.projectTicker}`;
    }
    if (names.length > 1) {
      return `${names.slice(0, 3).join(', ')} bought ${snapshot.projectTicker}`;
    }
    if (snapshot.buyerCount === 1) {
      return `1 trader bought ${snapshot.projectTicker}`;
    }
    return `${snapshot.buyerCount} traders bought ${snapshot.projectTicker}`;
  }

  formatHotBuyDetail(snapshot: HotBuySnapshot): string {
    const pct = Math.round(snapshot.pctOfActive * 100);
    const avgPct = 2;
    const above = pct > avgPct ? `+${pct - avgPct}%` : `${pct}%`;
    const names = (snapshot.recentBuyers ?? []).map((b) => b.displayName);
    const who =
      names.length > 0
        ? names.slice(0, 4).join(', ')
        : `${snapshot.buyerCount} trader${snapshot.buyerCount === 1 ? '' : 's'}`;
    return `${who} · ${formatUsd(snapshot.buyerCount * 100, 0)}+ paper · ${above} of active in 24h`;
  }
}
