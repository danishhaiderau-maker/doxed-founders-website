import { Injectable } from '@nestjs/common';
import { LeaderboardPeriod, NotificationType, PaperTradeSide } from '@prisma/client';
import { formatUsd } from '@dcf/utils';
import { NotificationsService } from '../notifications/notifications.service';
import { PrismaService } from '../prisma/prisma.service';

const HOT_BUY_THRESHOLD = 0.02;
const WINDOW_MS = 24 * 60 * 60 * 1000;
const ACTIVE_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;
const NOTIFY_COOLDOWN_MS = 6 * 60 * 60 * 1000;

export type HotBuySnapshot = {
  projectId: string;
  projectSlug: string;
  projectTicker: string;
  projectName: string;
  buyerCount: number;
  activeTraderCount: number;
  pctOfActive: number;
  topTraderCount: number;
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

    const recent = await this.prisma.notification.findFirst({
      where: {
        type: NotificationType.TRENDING_BUYS,
        body: { contains: snapshot.projectTicker },
        createdAt: { gte: new Date(Date.now() - NOTIFY_COOLDOWN_MS) },
      },
    });
    if (recent) return;

    const pctLabel = `${Math.round(snapshot.pctOfActive * 100)}%`;
    const title = snapshot.topTraderCount >= 3 ? 'Top traders buying' : 'Hot buy';
    const body =
      snapshot.topTraderCount >= 3
        ? `${snapshot.topTraderCount} top traders opened positions in ${snapshot.projectTicker} — ${snapshot.buyerCount} buyers in 24h.`
        : `${snapshot.buyerCount} traders bought ${snapshot.projectTicker} in 24h (${pctLabel} of active traders).`;

    await this.notifications.notifyAllUsers({
      type: NotificationType.TRENDING_BUYS,
      title: `🔥 ${title}: ${snapshot.projectTicker}`,
      body,
      link: `/feed?category=market`,
    });
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

    const topTraderCount = await this.countTopTradersBuying(
      projectId,
      buyers.map((b) => b.userId),
    );

    return {
      projectId: project.id,
      projectSlug: project.slug,
      projectTicker: project.ticker,
      projectName: project.name,
      buyerCount: buyers.length,
      activeTraderCount: activeCount,
      pctOfActive: buyers.length / Math.max(activeCount, 1),
      topTraderCount,
    };
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

  private async countTopTradersBuying(projectId: string, buyerIds: string[]): Promise<number> {
    if (buyerIds.length === 0) return 0;

    const topEntries = await this.prisma.leaderboardEntry.findMany({
      where: { period: LeaderboardPeriod.ALL_TIME, rank: { lte: 100 } },
      select: { userId: true },
      take: 100,
    });
    const topSet = new Set(topEntries.map((e) => e.userId));
    return buyerIds.filter((id) => topSet.has(id)).length;
  }

  formatHotBuyHeadline(snapshot: HotBuySnapshot): string {
    if (snapshot.topTraderCount >= 3) {
      return `${snapshot.topTraderCount} top traders bought ${snapshot.projectTicker}`;
    }
    return `${snapshot.buyerCount} traders bought ${snapshot.projectTicker}`;
  }

  formatHotBuyDetail(snapshot: HotBuySnapshot): string {
    const pct = Math.round(snapshot.pctOfActive * 100);
    const avgPct = 2;
    const above = pct > avgPct ? `+${pct - avgPct}%` : `${pct}%`;
    return `${formatUsd(snapshot.buyerCount * 100, 0)}+ paper activity · ${above} of active traders in 24h`;
  }
}
