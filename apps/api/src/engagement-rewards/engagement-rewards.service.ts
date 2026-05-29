import { Injectable, Logger } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import { NotificationType } from '@prisma/client';
import {
  ENGAGEMENT_ACTIVITY_WINDOW_HOURS,
  STARTING_CASH_USD,
  QUALITY_WEIGHTS,
  engagementLotteryWinnerCount,
  isLikelySpamComment,
  pickWeightedWinners,
  qualityTierPoolSize,
  randomEngagementPrizeUsd,
  takeTopQualityTier,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { NotificationsService } from '../notifications/notifications.service';

type QualityEntry = { userId: string; score: number };

@Injectable()
export class EngagementRewardsService {
  private readonly logger = new Logger(EngagementRewardsService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly notifications: NotificationsService,
  ) {}

  /** Quality score — rewards validated usefulness, not spam volume */
  async computeQualityScores(since: Date): Promise<QualityEntry[]> {
    const scores = new Map<string, number>();

    const add = (userId: string, weight: number, count = 1) => {
      if (!userId) return;
      scores.set(userId, (scores.get(userId) ?? 0) + weight * count);
    };

    const [
      helpfulMarks,
      bountyAwards,
      earlyScouts,
      buildPosts,
      videos,
      pollVotes,
      listingVotes,
      raiseAllocs,
      convictions,
      rawComments,
    ] = await Promise.all([
      this.prisma.helpfulMark.findMany({
        where: { createdAt: { gte: since } },
        select: { recipientUserId: true },
      }),
      this.prisma.founderBounty.findMany({
        where: { status: 'AWARDED', updatedAt: { gte: since }, awardeeUserId: { not: null } },
        select: { awardeeUserId: true },
      }),
      this.prisma.earlyScoutRecord.findMany({
        where: { createdAt: { gte: since } },
        select: { userId: true },
      }),
      this.prisma.founderBuildPost.findMany({
        where: { createdAt: { gte: since } },
        select: { founder: { select: { userId: true } } },
      }),
      this.prisma.founderVideo.findMany({
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
      this.prisma.paperPosition.findMany({
        where: {
          convictionRecordedAt: { gte: since },
          convictionThesis: { not: null },
        },
        select: { portfolio: { select: { userId: true } } },
      }),
      this.prisma.communityComment.findMany({
        where: { createdAt: { gte: since } },
        select: { userId: true, body: true, helpfulMark: true },
      }),
    ]);

    for (const h of helpfulMarks) add(h.recipientUserId, QUALITY_WEIGHTS.HELPFUL_MARK);
    for (const b of bountyAwards) {
      if (b.awardeeUserId) add(b.awardeeUserId, QUALITY_WEIGHTS.BOUNTY_AWARDED);
    }
    for (const s of earlyScouts) add(s.userId, QUALITY_WEIGHTS.EARLY_SCOUT);
    for (const p of buildPosts) {
      const uid = p.founder.userId;
      if (uid) add(uid, QUALITY_WEIGHTS.BUILD_POST);
    }
    for (const v of videos) {
      const uid = v.founder.userId;
      if (uid) add(uid, QUALITY_WEIGHTS.FOUNDER_VIDEO);
    }
    for (const row of pollVotes) add(row.userId, QUALITY_WEIGHTS.DEMAND_POLL_VOTE, row._count.id);
    for (const row of listingVotes) add(row.userId, QUALITY_WEIGHTS.LISTING_VOTE, row._count.id);
    for (const row of raiseAllocs) add(row.userId, QUALITY_WEIGHTS.RAISE_ALLOCATE, row._count.id);
    for (const c of convictions) add(c.portfolio.userId, QUALITY_WEIGHTS.CONVICTION_WITH_THESIS);
    for (const c of rawComments) {
      if (c.helpfulMark) continue;
      if (isLikelySpamComment(c.body)) continue;
      add(c.userId, QUALITY_WEIGHTS.RAW_COMMENT);
    }

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
    const allScored = await this.computeQualityScores(since);
    const activeUsers = allScored.length;
    const tierSize = qualityTierPoolSize(activeUsers);
    const tier = takeTopQualityTier(allScored, tierSize);
    const winnerCount = engagementLotteryWinnerCount(tier.length);

    if (winnerCount === 0 || tier.length === 0) {
      const draw = await this.prisma.engagementLotteryDraw.create({
        data: {
          drawDate: today,
          activeUsers,
          winnerCount: 0,
          totalPaidUsd: 0,
        },
      });
      return { skipped: false, activeUsers, tierSize, winnerCount: 0, drawId: draw.id, winners: [] };
    }

    const winnerIds = pickWeightedWinners(tier, winnerCount);
    const winnerDetails: {
      userId: string;
      amountUsd: number;
      activityScore: number;
    }[] = [];

    let totalPaid = 0;
    for (const userId of winnerIds) {
      const amountUsd = randomEngagementPrizeUsd();
      const activityScore = tier.find((a) => a.userId === userId)?.score ?? 0;
      totalPaid += amountUsd;
      winnerDetails.push({ userId, amountUsd, activityScore });
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
        `Daily quality lottery ${today.toISOString().slice(0, 10)}`,
      );

      await this.notifications.notifyUser(w.userId, {
        type: NotificationType.SYSTEM,
        title: 'Daily discovery reward',
        body: `Top contributor tier — you won $${w.amountUsd.toLocaleString()} paper cash for valuable participation.`,
        link: '/paper-trading',
      });
    }

    this.logger.log(
      `Quality lottery: ${winnerIds.length} winners from top ${tier.length}/${activeUsers} contributors ($${totalPaid})`,
    );

    return {
      skipped: false,
      drawId: draw.id,
      activeUsers,
      tierSize,
      winnerCount: winnerIds.length,
      totalPaidUsd: totalPaid,
      winners: winnerDetails,
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
        displayName: w.user.name ?? w.user.twitterHandle ?? 'Contributor',
        amountUsd: Number(w.amountUsd),
        activityScore: w.activityScore,
      })),
    };
  }

  async getEngagementStats() {
    const since = new Date(Date.now() - ENGAGEMENT_ACTIVITY_WINDOW_HOURS * 60 * 60 * 1000);
    const scored = await this.computeQualityScores(since);
    const latest = await this.getLatestLottery();
    const tierSize = qualityTierPoolSize(scored.length);

    return {
      activeContributors24h: scored.length,
      topTierSize: tierSize,
      expectedWinnersToday: engagementLotteryWinnerCount(tierSize),
      prizeRangeUsd: { min: 500, max: 2000 },
      winnerRatePercent: 0.2,
      model: 'quality',
      latestDraw: latest,
    };
  }
}
