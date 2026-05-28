import { Injectable, Logger } from '@nestjs/common';
import { PaperTradeSide } from '@prisma/client';
import { formatPublicAccountLabel } from '@dcf/utils';
import { NotificationsService } from '../notifications/notifications.service';
import { PrismaService } from '../prisma/prisma.service';
import { XPostingService } from './x-posting.service';

@Injectable()
export class SocialSignalsService {
  private readonly logger = new Logger(SocialSignalsService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly notifications: NotificationsService,
    private readonly xPosting: XPostingService,
  ) {}

  private trendingMinTraders(): number {
    return Number(process.env.TRENDING_BUY_MIN_TRADERS ?? 5);
  }

  private trendingWindowHours(): number {
    return Number(process.env.TRENDING_BUY_WINDOW_HOURS ?? 24);
  }

  private traderWinMinPnl(): number {
    return Number(process.env.TRADER_WIN_MIN_PNL_PERCENT ?? 50);
  }

  /** Called after a paper BUY — checks if project just became trending. */
  async onPaperBuy(projectId: string) {
    try {
      await this.scanTrendingBuys(projectId);
    } catch (err) {
      this.logger.warn(`Trending scan failed: ${err}`);
    }
  }

  async scanTrendingBuys(onlyProjectId?: string) {
    const since = new Date(Date.now() - this.trendingWindowHours() * 60 * 60 * 1000);
    const minTraders = this.trendingMinTraders();

    const trades = await this.prisma.paperTrade.findMany({
      where: {
        side: PaperTradeSide.BUY,
        createdAt: { gte: since },
        ...(onlyProjectId ? { projectId: onlyProjectId } : {}),
      },
      select: { projectId: true, userId: true },
    });

    const buyersByProject = new Map<string, Set<string>>();
    for (const trade of trades) {
      if (!buyersByProject.has(trade.projectId)) {
        buyersByProject.set(trade.projectId, new Set());
      }
      buyersByProject.get(trade.projectId)!.add(trade.userId);
    }

    let posted = 0;
    for (const [projectId, buyers] of buyersByProject) {
      if (buyers.size < minTraders) continue;

      const project = await this.prisma.project.findUnique({
        where: { id: projectId },
        include: {
          founder: {
            select: { name: true, twitterUrl: true, videoUrl: true },
          },
        },
      });
      if (!project?.approved) continue;

      await this.notifications.notifyAllUsers({
        type: 'TRENDING_BUYS',
        title: `🐋 Hot buys: ${project.name}`,
        body: `${buyers.size} traders paper-bought $${project.ticker} in the last ${this.trendingWindowHours()}h. See why on the feed.`,
        link: `/project/${project.slug}`,
      });

      if (this.xPosting.isConfigured()) {
        const result = await this.xPosting.postTrendingBuys({
          projectId,
          projectName: project.name,
          ticker: project.ticker,
          slug: project.slug,
          founderName: project.founder?.name ?? null,
          buyerCount: buyers.size,
          windowHours: this.trendingWindowHours(),
        });
        if (result.ok) posted += 1;
      }
    }

    return { scanned: buyersByProject.size, posted };
  }

  async scanTraderWins() {
    const minPnl = this.traderWinMinPnl();
    const portfolios = await this.prisma.paperPortfolio.findMany({
      include: {
        user: { select: { id: true, name: true, email: true } },
        positions: true,
      },
    });

    let posted = 0;
    let notified = 0;

    for (const portfolio of portfolios) {
      for (const position of portfolio.positions) {
        const project = await this.prisma.project.findUnique({
          where: { id: position.projectId },
          include: {
            metrics: true,
            founder: {
              select: { name: true, twitterUrl: true, videoUrl: true },
            },
          },
        });
        if (!project?.approved) continue;

        const price = Number(project.metrics?.priceUsd ?? position.avgBuyPrice);
        const quantity = Number(position.quantity);
        const costBasis = quantity * Number(position.avgBuyPrice);
        if (costBasis <= 0) continue;
        const pnlPercent = ((quantity * price - costBasis) / costBasis) * 100;
        if (pnlPercent < minPnl) continue;

        const displayName = formatPublicAccountLabel(
          portfolio.user.name,
          portfolio.user.email,
        );

        const feedPost = await this.prisma.feedPost.findFirst({
          where: {
            userId: portfolio.user.id,
            projectId: project.id,
            initialComment: { not: null },
          },
          orderBy: { createdAt: 'desc' },
          select: { initialComment: true },
        });

        await this.notifications.notifyAllUsers({
          type: 'TRADER_WIN',
          title: `🔥 ${displayName} +${Math.round(pnlPercent)}% on $${project.ticker}`,
          body: `Paper trader hit ${Math.round(pnlPercent)}% on a doxxed founder project. View portfolio and thesis.`,
          link: `/portfolio/${portfolio.user.id}`,
        });
        notified += 1;

        if (this.xPosting.isConfigured()) {
          const result = await this.xPosting.postTraderWin({
            userId: portfolio.user.id,
            displayName,
            projectId: project.id,
            projectName: project.name,
            ticker: project.ticker,
            slug: project.slug,
            pnlPercent,
            thesis: feedPost?.initialComment ?? null,
            founderName: project.founder?.name ?? null,
            founderVideoUrl: project.founder?.videoUrl ?? null,
            founderTwitter: project.founder?.twitterUrl ?? null,
          });
          if (result.ok) posted += 1;
        }
      }
    }

    return { notified, posted };
  }

  async runDailySocialJob() {
    const trending = await this.scanTrendingBuys();
    const wins = await this.scanTraderWins();
    return { trending, wins };
  }
}
