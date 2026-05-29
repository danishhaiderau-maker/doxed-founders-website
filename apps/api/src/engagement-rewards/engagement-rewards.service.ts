import { Injectable, Logger } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import { NotificationType } from '@prisma/client';
import {
  ACTIVITY_WEIGHTS,
  ENGAGEMENT_ACTIVITY_WINDOW_HOURS,
  STARTING_CASH_USD,
  engagementLotteryWinnerCount,
  pickWeightedWinners,
  randomEngagementPrizeUsd,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { NotificationsService } from '../notifications/notifications.service';

type ActivityEntry = { userId: string; score: number };

@Injectable()
export class EngagementRewardsService {
  private readonly logger = new Logger(EngagementRewardsService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly notifications: NotificationsService,
  ) {}

  async computeActivityScores(since: Date): Promise<ActivityEntry[]> {
    const scores = new Map<string, number>();

    const add = (userId: string, weight: number, count = 1) => {
      if (!userId) return;
      scores.set(userId, (scores.get(userId) ?? 0) + weight * count);
    };

    const [
      trades,
      feedComments,
      communityComments,
      communityThreads,
      buildPosts,
      pollVotes,
      listingVotes,
      raiseAllocs,
      follows,
    ] = await Promise.all([
      this.prisma.paperTrade.groupBy({
        by: ['userId'],
        where: { createdAt: { gte: since }, user: { banned: false } },
        _count: { id: true },
      }),
      this.prisma.feedComment.groupBy({
        by: ['userId'],
        where: { createdAt: { gte: since }, user: { banned: false } },
        _count: { id: true },
      }),
      this.prisma.communityComment.groupBy({
        by: ['userId'],
        where: { createdAt: { gte: since } },
        _count: { id: true },
      }),
      this.prisma.communityThread.findMany({
        where: { createdAt: { gte: since }, authorId: { not: null } },
        select: { authorId: true },
      }),
      this.prisma.founderBuildPost.findMany({
        where: { createdAt: { gte: since } },
        select: { founder: { select: { userId: true } } },
      }),
      this.prisma.demandPollVote.groupBy({
        by: ['userId'],
        where: { createdAt: { gte: since }, user: { banned: false } },
        _count: { id: true },
      }),
      this.prisma.listingVote.groupBy({
        by: ['userId'],
        where: { createdAt: { gte: since }, user: { banned: false } },
        _count: { id: true },
      }),
      this.prisma.raiseAllocation.groupBy({
        by: ['userId'],
        where: { createdAt: { gte: since }, user: { banned: false } },
        _count: { id: true },
      }),
      this.prisma.projectFollow.groupBy({
        by: ['userId'],
        where: { createdAt: { gte: since }, user: { banned: false } },
        _count: { id: true },
      }),
    ]);

    for (const row of trades) add(row.userId, ACTIVITY_WEIGHTS.PAPER_TRADE, row._count.id);
    for (const row of feedComments) add(row.userId, ACTIVITY_WEIGHTS.FEED_COMMENT, row._count.id);
    for (const row of communityComments) {
      add(row.userId, ACTIVITY_WEIGHTS.COMMUNITY_COMMENT, row._count.id);
    }
    for (const row of communityThreads) {
      if (row.authorId) add(row.authorId, ACTIVITY_WEIGHTS.COMMUNITY_THREAD);
    }
    for (const row of buildPosts) {
      const uid = row.founder.userId;
      if (uid) add(uid, ACTIVITY_WEIGHTS.BUILD_POST);
    }
    for (const row of pollVotes) add(row.userId, ACTIVITY_WEIGHTS.DEMAND_POLL_VOTE, row._count.id);
    for (const row of listingVotes) add(row.userId, ACTIVITY_WEIGHTS.LISTING_VOTE, row._count.id);
    for (const row of raiseAllocs) add(row.userId, ACTIVITY_WEIGHTS.RAISE_ALLOCATE, row._count.id);
    for (const row of follows) add(row.userId, ACTIVITY_WEIGHTS.PROJECT_FOLLOW, row._count.id);

    return [...scores.entries()]
      .map(([userId, score]) => ({ userId, score }))
      .sort((a, b) => b.score - a.score);
  }

  private async ensurePortfolio(userId: string) {
    const existing = await this.prisma.paperPortfolio.findUnique({ where: { userId } });
    if (existing) return existing;

    const portfolio = await this.prisma.paperPortfolio.create({
      data: {
        userId,
        cashBalance: STARTING_CASH_USD,
        totalValue: STARTING_CASH_USD,
      },
    });

    await this.prisma.virtualEconomyEvent.create({
      data: {
        userId,
        type: 'INITIAL_GRANT',
        amountUsd: new Prisma.Decimal(STARTING_CASH_USD),
        note: 'Signup paper trading grant',
      },
    });

    return portfolio;
  }

  async creditPaperCash(userId: string, amountUsd: number, note: string) {
    await this.ensurePortfolio(userId);

    await this.prisma.$transaction(async (tx) => {
      await tx.paperPortfolio.update({
        where: { userId },
        data: {
          cashBalance: { increment: amountUsd },
          totalValue: { increment: amountUsd },
        },
      });
      await tx.virtualEconomyEvent.create({
        data: {
          userId,
          type: 'ENGAGEMENT_LOTTERY',
          amountUsd: new Prisma.Decimal(amountUsd),
          note,
        },
      });
    });
  }

  async runDailyLottery(force = false) {
    const today = new Date();
    today.setUTCHours(0, 0, 0, 0);

    if (!force) {
      const existing = await this.prisma.engagementLotteryDraw.findUnique({
        where: { drawDate: today },
      });
      if (existing) {
        return { skipped: true, reason: 'Already drawn today', drawId: existing.id };
      }
    }

    const since = new Date(Date.now() - ENGAGEMENT_ACTIVITY_WINDOW_HOURS * 60 * 60 * 1000);
    const activity = await this.computeActivityScores(since);
    const activeUsers = activity.length;
    const winnerCount = engagementLotteryWinnerCount(activeUsers);

    if (winnerCount === 0) {
      const draw = await this.prisma.engagementLotteryDraw.create({
        data: {
          drawDate: today,
          activeUsers,
          winnerCount: 0,
          totalPaidUsd: 0,
        },
      });
      return { skipped: false, activeUsers, winnerCount: 0, drawId: draw.id, winners: [] };
    }

    const winnerIds = pickWeightedWinners(activity, winnerCount);
    const winnerDetails: {
      userId: string;
      amountUsd: number;
      activityScore: number;
      displayName: string;
    }[] = [];

    let totalPaid = 0;
    for (const userId of winnerIds) {
      const amountUsd = randomEngagementPrizeUsd();
      const activityScore = activity.find((a) => a.userId === userId)?.score ?? 0;
      totalPaid += amountUsd;
      winnerDetails.push({ userId, amountUsd, activityScore, displayName: '' });
    }

    const draw = await this.prisma.$transaction(async (tx) => {
      const created = await tx.engagementLotteryDraw.create({
        data: {
          drawDate: today,
          activeUsers,
          winnerCount: winnerIds.length,
          totalPaidUsd: new Prisma.Decimal(totalPaid),
        },
      });

      for (const w of winnerDetails) {
        await tx.engagementLotteryWinner.create({
          data: {
            drawId: created.id,
            userId: w.userId,
            amountUsd: new Prisma.Decimal(w.amountUsd),
            activityScore: w.activityScore,
          },
        });
      }

      return created;
    });

    for (const w of winnerDetails) {
      await this.creditPaperCash(
        w.userId,
        w.amountUsd,
        `Daily engagement lottery ${today.toISOString().slice(0, 10)}`,
      );

      await this.notifications.notifyUser(w.userId, {
        type: NotificationType.SYSTEM,
        title: '🎉 Engagement lottery winner',
        body: `You won $${w.amountUsd.toLocaleString()} paper cash for being active in the community. It's in your trading account now.`,
        link: '/paper-trading',
      });
    }

    this.logger.log(
      `Engagement lottery: ${winnerIds.length} winners from ${activeUsers} active users ($${totalPaid} paid)`,
    );

    return {
      skipped: false,
      drawId: draw.id,
      activeUsers,
      winnerCount: winnerIds.length,
      totalPaidUsd: totalPaid,
      winners: winnerDetails.map((w) => ({
        userId: w.userId,
        amountUsd: w.amountUsd,
        activityScore: w.activityScore,
      })),
    };
  }

  async getLatestLottery() {
    const draw = await this.prisma.engagementLotteryDraw.findFirst({
      orderBy: { drawDate: 'desc' },
      include: {
        winners: {
          include: {
            user: { select: { id: true, name: true, twitterHandle: true } },
          },
          orderBy: { activityScore: 'desc' },
        },
      },
    });

    if (!draw) {
      return {
        drawDate: null,
        activeUsers: 0,
        winnerCount: 0,
        totalPaidUsd: 0,
        winners: [],
      };
    }

    return {
      drawDate: draw.drawDate.toISOString().slice(0, 10),
      activeUsers: draw.activeUsers,
      winnerCount: draw.winnerCount,
      totalPaidUsd: Number(draw.totalPaidUsd),
      winners: draw.winners.map((w) => ({
        displayName: w.user.name ?? w.user.twitterHandle ?? 'Trader',
        amountUsd: Number(w.amountUsd),
        activityScore: w.activityScore,
      })),
    };
  }

  async getEngagementStats() {
    const since = new Date(Date.now() - ENGAGEMENT_ACTIVITY_WINDOW_HOURS * 60 * 60 * 1000);
    const activity = await this.computeActivityScores(since);
    const latest = await this.getLatestLottery();

    return {
      activeUsers24h: activity.length,
      expectedWinnersToday: engagementLotteryWinnerCount(activity.length),
      prizeRangeUsd: { min: 500, max: 2000 },
      winnerRatePercent: 0.2,
      latestDraw: latest,
    };
  }
}
