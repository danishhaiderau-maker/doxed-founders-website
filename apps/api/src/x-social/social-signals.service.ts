import { Injectable, Logger } from '@nestjs/common';
import { PaperTradeSide } from '@prisma/client';
import { extractTwitterHandle, formatPublicAccountLabel } from '@dcf/utils';
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

  private traderLossMinPnl(): number {
    return Number(process.env.TRADER_LOSS_MIN_PNL_PERCENT ?? 25);
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
      select: { projectId: true, userId: true, totalUsd: true },
    });

    const buyersByProject = new Map<string, Set<string>>();
    const investedByProject = new Map<string, number>();
    for (const trade of trades) {
      if (!buyersByProject.has(trade.projectId)) {
        buyersByProject.set(trade.projectId, new Set());
        investedByProject.set(trade.projectId, 0);
      }
      buyersByProject.get(trade.projectId)!.add(trade.userId);
      investedByProject.set(
        trade.projectId,
        (investedByProject.get(trade.projectId) ?? 0) + Number(trade.totalUsd),
      );
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

      const totalInvestedUsd = investedByProject.get(projectId) ?? 0;

      await this.notifications.notifyAllUsers({
        type: 'TRENDING_BUYS',
        title: `🐋 Hot buys: ${project.name}`,
        body: `${buyers.size} traders deployed ~$${Math.round(totalInvestedUsd).toLocaleString()} paper into $${project.ticker} in ${this.trendingWindowHours()}h.`,
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
          totalInvestedUsd,
        });
        if (result.ok) posted += 1;
      }
    }

    return { scanned: buyersByProject.size, posted };
  }

  async scanTraderResults() {
    const minWin = this.traderWinMinPnl();
    const minLoss = this.traderLossMinPnl();
    const autoPostX = process.env.X_AUTO_POST_TRADER_RESULTS === 'true';
    const portfolios = await this.prisma.paperPortfolio.findMany({
      include: {
        user: { select: { id: true, name: true, email: true, twitterHandle: true } },
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
        const avgBuy = Number(position.avgBuyPrice);
        const investedUsd = quantity * avgBuy;
        if (investedUsd <= 0) continue;

        const currentValue = quantity * price;
        const pnlUsd = currentValue - investedUsd;
        const pnlPercent = (pnlUsd / investedUsd) * 100;

        const isWin = pnlPercent >= minWin;
        const isLoss = pnlPercent <= -minLoss;
        if (!isWin && !isLoss) continue;

        const displayName = portfolio.user.twitterHandle
          ? `@${portfolio.user.twitterHandle.replace(/^@/, '')}`
          : formatPublicAccountLabel(portfolio.user.name, portfolio.user.email);

        const feedPost = await this.prisma.feedPost.findFirst({
          where: {
            userId: portfolio.user.id,
            projectId: project.id,
            initialComment: { not: null },
          },
          orderBy: { createdAt: 'desc' },
          select: { initialComment: true },
        });

        const founderUpdate = await this.prisma.founderUpdate.findFirst({
          where: { projectId: project.id, externalId: { not: null } },
          orderBy: { publishedAt: 'desc' },
          select: { externalId: true },
        });

        const founderHandle = project.founder?.twitterUrl
          ? extractTwitterHandle(project.founder.twitterUrl)
          : null;

        const roundedPct = Math.round(Math.abs(pnlPercent));
        const titleEmoji = isWin ? '🚀' : '📉';
        const sign = isWin ? '+' : '−';

        await this.notifications.notifyAllUsers({
          type: isWin ? 'TRADER_WIN' : 'TRADER_LOSS',
          title: `${titleEmoji} ${displayName} ${sign}${roundedPct}% on $${project.ticker}`,
          body: `${fmtUsd(investedUsd)} on this position · ${sign}${fmtUsd(Math.abs(pnlUsd))} P&L. See thesis on their portfolio.`,
          link: `/portfolio/${portfolio.user.id}`,
        });
        notified += 1;

        if (autoPostX && this.xPosting.isConfigured()) {
          const result = await this.xPosting.postTraderConviction({
            userId: portfolio.user.id,
            displayName,
            projectId: project.id,
            projectName: project.name,
            ticker: project.ticker,
            slug: project.slug,
            investedUsd,
            pnlUsd,
            pnlPercent,
            thesis: feedPost?.initialComment ?? null,
            founderName: project.founder?.name ?? null,
            founderHandle: founderHandle ? `@${founderHandle}` : null,
            founderTweetId: founderUpdate?.externalId ?? null,
          });
          if (result.ok) posted += 1;
        }
      }
    }

    return { notified, posted };
  }

  /** @deprecated use scanTraderResults */
  async scanTraderWins() {
    return this.scanTraderResults();
  }

  async runDailySocialJob() {
    const trending = await this.scanTrendingBuys();
    return { trending };
  }
}

function fmtUsd(value: number): string {
  if (value >= 1000) return `$${Math.round(value).toLocaleString('en-US')}`;
  return `$${value.toFixed(0)}`;
}
