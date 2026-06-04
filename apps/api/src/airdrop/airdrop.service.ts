import { Injectable, NotFoundException } from '@nestjs/common';
import {
  AIRDROP_TOKEN_POOL,
  LAUNCH_FDV_USD,
  TOKEN_SUPPLY,
  applyBuilderScoreModifiers,
  computeBuilderRewardStatus,
  computeBuilderSubScores,
  computeRawBuilderScore,
  computeRewardSharePercent,
  computeWeeklyDecayPercent,
  daysSince,
  estimateActiveDaysFromEvents,
  formatBuilderTier,
  tierFromBuilderScore,
  userHasTwitterConnected,
  xTrustForUser,
  BUILDER_REWARDS_INACTIVITY_DAYS,
  BUILDER_REWARDS_SNAPSHOT_WEIGHTS,
  BUILDER_REWARDS_WEEKLY_DECAY_PERCENT,
  BUILDER_REWARDS_X_VERIFIED_BONUS_PERCENT,
  type BuilderRewardStatus,
  type BuilderTier,
  type BuilderXTrust,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { labelForUser } from '../account/user-identity.util';

const ACTIVITY_SINCE_MS = 14 * 24 * 60 * 60 * 1000;

type ActivityAgg = {
  count: number;
  lastAt: Date | null;
  kinds: number;
  trades: number;
  tradesConviction: number;
  scoutStakes: number;
  builderPosts: number;
  ddollarEarned: number;
};

export type BuilderRewardsEntry = {
  rank: number;
  userId: string;
  displayName: string;
  twitterHandle: string | null;
  twitterConnected: boolean;
  tier: BuilderTier;
  tierLabel: string;
  builderScore: number;
  reputationPoints: number;
  activityScore: number;
  builderActivityScore: number;
  scoutScore: number;
  tradingScore: number;
  contributionScore: number;
  humanityScore: number;
  status: BuilderRewardStatus;
  xTrust: BuilderXTrust;
  lastActiveAt: string | null;
  idleDays: number | null;
  weeklyDecayPercent: number;
  ddollarBalanceUsd: number | null;
  rewardSharePercent: number;
  estimatedTokens: number;
  estimatedUsd: number;
};

const RULES = {
  twitterSignInRequired: true,
  inactivityDays: BUILDER_REWARDS_INACTIVITY_DAYS,
  weeklyDecayPercent: BUILDER_REWARDS_WEEKLY_DECAY_PERCENT,
  blueTickBonusPercent: BUILDER_REWARDS_X_VERIFIED_BONUS_PERCENT,
  snapshotWeights: BUILDER_REWARDS_SNAPSHOT_WEIGHTS,
  snapshotNote:
    'Final reward allocation is decided at distribution snapshot. Bots, sybil clusters, and fake engagement may be excluded even if listed today.',
  blueTickNote:
    'X Blue is not required (+5% Builder Score when verified). Historical Blue or org verification may count as a soft trust signal.',
  principle:
    'Builder Rewards measure proof of contribution — not DDollar balance alone.',
};

@Injectable()
export class AirdropService {
  constructor(private readonly prisma: PrismaService) {}

  private async twitterUsers(limit: number) {
    return this.prisma.user.findMany({
      where: {
        banned: false,
        OR: [
          { twitterHandle: { not: null } },
          { oauthAccounts: { some: { provider: { in: ['twitter', 'x'] } } } },
        ],
      },
      select: {
        id: true,
        name: true,
        email: true,
        platformHandle: true,
        twitterHandle: true,
        oauthAccounts: { select: { provider: true }, take: 5 },
        reputationPoints: true,
        contributorLevel: true,
        createdAt: true,
        paperPortfolio: { select: { cashBalance: true } },
        founder: { select: { id: true, buildStreakDays: true } },
      },
      orderBy: [{ reputationPoints: 'desc' }, { createdAt: 'asc' }],
      take: Math.min(500, limit * 3),
    });
  }

  private async activityMaps(userIds: string[], since: Date) {
    const founderIds = (
      await this.prisma.founder.findMany({
        where: { userId: { in: userIds } },
        select: { id: true, userId: true, buildStreakDays: true },
      })
    ).filter((f): f is { id: string; userId: string; buildStreakDays: number } => !!f.userId);

    const founderIdToUser = new Map(founderIds.map((f) => [f.id, f.userId]));
    const userBuildStreak = new Map(
      founderIds.map((f) => [f.userId, f.buildStreakDays]),
    );

    const [
      trades,
      tradesConviction,
      ledger,
      feedComments,
      communityComments,
      pollVotes,
      follows,
      scoutStakes,
      buildPosts,
      pointSums,
    ] = await Promise.all([
      this.prisma.paperTrade.groupBy({
        by: ['userId'],
        where: { userId: { in: userIds }, createdAt: { gte: since } },
        _count: { _all: true },
        _max: { createdAt: true },
      }),
      this.prisma.paperTrade.groupBy({
        by: ['userId'],
        where: {
          userId: { in: userIds },
          createdAt: { gte: since },
          convictionScore: { not: null },
        },
        _count: { _all: true },
      }),
      this.prisma.pointLedger.groupBy({
        by: ['userId'],
        where: { userId: { in: userIds }, createdAt: { gte: since } },
        _count: { _all: true },
        _max: { createdAt: true },
        _sum: { amount: true },
      }),
      this.prisma.feedComment.groupBy({
        by: ['userId'],
        where: { userId: { in: userIds }, createdAt: { gte: since } },
        _count: { _all: true },
        _max: { createdAt: true },
      }),
      this.prisma.communityComment.groupBy({
        by: ['userId'],
        where: { userId: { in: userIds }, createdAt: { gte: since } },
        _count: { _all: true },
        _max: { createdAt: true },
      }),
      this.prisma.demandPollVote.groupBy({
        by: ['userId'],
        where: { userId: { in: userIds }, createdAt: { gte: since } },
        _count: { _all: true },
        _max: { createdAt: true },
      }),
      this.prisma.projectFollow.groupBy({
        by: ['userId'],
        where: { userId: { in: userIds }, createdAt: { gte: since } },
        _count: { _all: true },
        _max: { createdAt: true },
      }),
      this.prisma.scoutMarketPosition.groupBy({
        by: ['userId'],
        where: { userId: { in: userIds }, createdAt: { gte: since } },
        _count: { _all: true },
        _max: { createdAt: true },
      }),
      founderIds.length > 0
        ? this.prisma.founderBuildPost.groupBy({
            by: ['founderId'],
            where: { founderId: { in: founderIds.map((f) => f.id) }, createdAt: { gte: since } },
            _count: { _all: true },
            _max: { createdAt: true },
          })
        : Promise.resolve([]),
      this.prisma.pointLedger.groupBy({
        by: ['userId'],
        where: { userId: { in: userIds }, amount: { gt: 0 } },
        _sum: { amount: true },
      }),
    ]);

    const map = new Map<string, ActivityAgg>();

    const bump = (
      userId: string,
      count: number,
      lastAt: Date | null,
      field?: keyof Pick<
        ActivityAgg,
        'trades' | 'tradesConviction' | 'scoutStakes' | 'builderPosts'
      >,
    ) => {
      const cur = map.get(userId) ?? {
        count: 0,
        lastAt: null,
        kinds: 0,
        trades: 0,
        tradesConviction: 0,
        scoutStakes: 0,
        builderPosts: 0,
        ddollarEarned: 0,
      };
      cur.count += count;
      if (lastAt && (!cur.lastAt || lastAt > cur.lastAt)) cur.lastAt = lastAt;
      if (count > 0) cur.kinds += 1;
      if (field === 'trades') cur.trades += count;
      if (field === 'tradesConviction') cur.tradesConviction += count;
      if (field === 'scoutStakes') cur.scoutStakes += count;
      if (field === 'builderPosts') cur.builderPosts += count;
      map.set(userId, cur);
    };

    for (const r of trades) bump(r.userId, r._count._all, r._max.createdAt, 'trades');
    for (const r of tradesConviction)
      bump(r.userId, r._count._all, null, 'tradesConviction');
    for (const r of ledger) bump(r.userId, r._count._all, r._max.createdAt);
    for (const r of feedComments) bump(r.userId, r._count._all, r._max.createdAt);
    for (const r of communityComments) bump(r.userId, r._count._all, r._max.createdAt);
    for (const r of pollVotes) bump(r.userId, r._count._all, r._max.createdAt);
    for (const r of follows) bump(r.userId, r._count._all, r._max.createdAt);
    for (const r of scoutStakes) bump(r.userId, r._count._all, r._max.createdAt, 'scoutStakes');
    for (const r of buildPosts) {
      const uid = founderIdToUser.get(r.founderId);
      if (uid) bump(uid, r._count._all, r._max.createdAt, 'builderPosts');
    }
    for (const r of pointSums) {
      const cur = map.get(r.userId) ?? {
        count: 0,
        lastAt: null,
        kinds: 0,
        trades: 0,
        tradesConviction: 0,
        scoutStakes: 0,
        builderPosts: 0,
        ddollarEarned: 0,
      };
      cur.ddollarEarned = Number(r._sum.amount ?? 0);
      map.set(r.userId, cur);
    }

    return { map, userBuildStreak };
  }

  private scoreUser(
    user: Awaited<ReturnType<AirdropService['twitterUsers']>>[number],
    agg: ActivityAgg | undefined,
    buildStreakDays: number,
  ) {
    const activityEventCount = Math.min(50, agg?.count ?? 0);
    const actionKinds = agg?.kinds ?? 0;
    const lastActiveAt = agg?.lastAt?.toISOString() ?? null;
    const idleDays = daysSince(lastActiveAt);
    const status = computeBuilderRewardStatus({ lastActiveAt, activityEventCount });
    const twitterConnected = userHasTwitterConnected(user);
    const accountAgeDays = daysSince(user.createdAt.toISOString()) ?? 0;
    const ddollarEarnedApprox =
      agg?.ddollarEarned ?? Number(user.paperPortfolio?.cashBalance ?? 0);

    const subInput = {
      reputationPoints: user.reputationPoints,
      activityEventCount,
      actionKinds,
      builderPosts: agg?.builderPosts ?? 0,
      buildStreakDays,
      scoutStakes: agg?.scoutStakes ?? 0,
      trades: agg?.trades ?? 0,
      tradesWithConviction: agg?.tradesConviction ?? 0,
      communityActions: Math.max(
        0,
        (agg?.count ?? 0) - (agg?.trades ?? 0) - (agg?.scoutStakes ?? 0) - (agg?.builderPosts ?? 0),
      ),
      ddollarEarnedApprox,
      accountAgeDays,
      lastActiveAt,
      twitterConnected,
    };

    const sub = computeBuilderSubScores(subInput);
    const raw = computeRawBuilderScore(sub, ddollarEarnedApprox);
    const humanityScore = sub.humanityScore;
    const builderScore = applyBuilderScoreModifiers({
      rawScore: raw,
      status,
      humanityScore,
      idleDays,
    });
    const tier = tierFromBuilderScore(builderScore);
    const xTrust = xTrustForUser({ twitterConnected, humanityScore, status });

    return {
      user,
      sub,
      builderScore,
      tier,
      xTrust,
      status,
      lastActiveAt,
      idleDays,
      weeklyDecayPercent: computeWeeklyDecayPercent(idleDays),
      twitterConnected,
      activityEventCount,
      humanityScore,
      ddollarBalanceUsd: user.paperPortfolio ? Number(user.paperPortfolio.cashBalance) : null,
    };
  }

  private toEntry(
    row: ReturnType<AirdropService['scoreUser']>,
    rank: number,
    rewardSharePercent: number,
  ): BuilderRewardsEntry {
    const tokenPrice = LAUNCH_FDV_USD / TOKEN_SUPPLY;
    const estimatedTokens = (rewardSharePercent / 100) * AIRDROP_TOKEN_POOL;
    return {
      rank,
      userId: row.user.id,
      displayName: labelForUser(row.user),
      twitterHandle: row.user.twitterHandle,
      twitterConnected: row.twitterConnected,
      tier: row.tier,
      tierLabel: formatBuilderTier(row.tier),
      builderScore: row.builderScore,
      reputationPoints: row.user.reputationPoints,
      activityScore: row.sub.activityScore,
      builderActivityScore: row.sub.builderActivityScore,
      scoutScore: row.sub.scoutScore,
      tradingScore: row.sub.tradingScore,
      contributionScore: row.sub.contributionScore,
      humanityScore: row.humanityScore,
      status: row.status,
      xTrust: row.xTrust,
      lastActiveAt: row.lastActiveAt,
      idleDays: row.idleDays,
      weeklyDecayPercent: row.weeklyDecayPercent,
      ddollarBalanceUsd: row.ddollarBalanceUsd,
      rewardSharePercent,
      estimatedTokens,
      estimatedUsd: estimatedTokens * tokenPrice,
    };
  }

  async getLeaderboard(limit = 100) {
    const users = await this.twitterUsers(limit);
    const userIds = users.map((u) => u.id);
    const since = new Date(Date.now() - ACTIVITY_SINCE_MS);
    const { map, userBuildStreak } =
      userIds.length > 0 ? await this.activityMaps(userIds, since) : { map: new Map(), userBuildStreak: new Map() };

    const scored = users.map((user) =>
      this.scoreUser(user, map.get(user.id), userBuildStreak.get(user.id) ?? user.founder?.buildStreakDays ?? 0),
    );
    scored.sort((a, b) => b.builderScore - a.builderScore);

    const pool = scored.filter((s) => s.twitterConnected && s.builderScore > 0);
    const totalBuilderScore = pool.reduce((s, u) => s + u.builderScore, 0);

    const entries = scored.slice(0, limit).map((row, index) =>
      this.toEntry(
        row,
        index + 1,
        computeRewardSharePercent(row.builderScore, totalBuilderScore || 1),
      ),
    );

    return {
      entries,
      totalListed: users.length,
      twitterConnectedCount: users.filter((u) => userHasTwitterConnected(u)).length,
      totalBuilderScore,
      rules: RULES,
    };
  }

  /** @deprecated Alias */
  getRunwayLeaderboard(limit = 100) {
    return this.getLeaderboard(limit).then((board) => ({
      ...board,
      entries: board.entries.map((e) => ({
        ...e,
        runwayScore: e.builderScore,
        humanScore: e.humanityScore,
        xEligibility: e.xTrust,
        airdropPoolPercent: e.rewardSharePercent,
        projectedDecayDdollar: 0,
      })),
      rules: {
        ...board.rules,
        inactivityWarnDays: board.rules.inactivityDays,
        decayDdollarPerDay: 0,
      },
    }));
  }

  async getMe(userId: string) {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: {
        id: true,
        name: true,
        email: true,
        platformHandle: true,
        twitterHandle: true,
        oauthAccounts: { select: { provider: true }, take: 5 },
        reputationPoints: true,
        createdAt: true,
        banned: true,
        paperPortfolio: { select: { cashBalance: true } },
        founder: { select: { buildStreakDays: true } },
      },
    });
    if (!user || user.banned) throw new NotFoundException('User not found');

    const board = await this.getLeaderboard(200);
    let mine = board.entries.find((e) => e.userId === userId);
    const twitterConnected = userHasTwitterConnected(user);

    if (!mine && twitterConnected) {
      const since = new Date(Date.now() - ACTIVITY_SINCE_MS);
      const { map, userBuildStreak } = await this.activityMaps([userId], since);
      const full = await this.prisma.user.findUnique({
        where: { id: userId },
        select: {
          id: true,
          name: true,
          email: true,
          platformHandle: true,
          twitterHandle: true,
          oauthAccounts: { select: { provider: true }, take: 5 },
          reputationPoints: true,
          contributorLevel: true,
          createdAt: true,
          paperPortfolio: { select: { cashBalance: true } },
          founder: { select: { id: true, buildStreakDays: true } },
        },
      });
      if (!full) throw new NotFoundException('User not found');
      const scored = this.scoreUser(
        full,
        map.get(userId),
        userBuildStreak.get(userId) ?? full.founder?.buildStreakDays ?? 0,
      );
      mine = this.toEntry(
        scored,
        0,
        computeRewardSharePercent(scored.builderScore, board.totalBuilderScore || 1),
      );
    }

    const status = mine?.status ?? 'warming';
    const activeDays = estimateActiveDaysFromEvents(
      mine?.activityScore ?? 0,
      daysSince(user.createdAt.toISOString()) ?? 0,
    );

    return {
      ...(mine ?? {
        userId: user.id,
        displayName: labelForUser(user),
        twitterHandle: user.twitterHandle,
        builderScore: 0,
        tier: 'bronze' as BuilderTier,
        tierLabel: formatBuilderTier('bronze'),
        reputationPoints: user.reputationPoints,
        activityScore: 0,
        builderActivityScore: 0,
        scoutScore: 0,
        tradingScore: 0,
        contributionScore: 0,
        humanityScore: 0,
        status: 'warming' as const,
        xTrust: 'not_connected' as const,
        lastActiveAt: null,
        idleDays: null,
        weeklyDecayPercent: 0,
        ddollarBalanceUsd: user.paperPortfolio ? Number(user.paperPortfolio.cashBalance) : null,
        rewardSharePercent: 0,
        estimatedTokens: 0,
        estimatedUsd: 0,
      }),
      rank: mine && mine.rank > 0 ? mine.rank : null,
      displayName: labelForUser(user),
      twitterConnected,
      needsTwitter: !twitterConnected,
      activeDaysEstimate: activeDays,
      warning:
        status === 'decaying' || status === 'at_risk'
          ? {
              level: status === 'decaying' ? ('critical' as const) : ('warn' as const),
              message:
                status === 'decaying'
                  ? `Inactive ${mine?.idleDays ?? BUILDER_REWARDS_INACTIVITY_DAYS}+ days — Builder Score decays ${BUILDER_REWARDS_WEEKLY_DECAY_PERCENT}% per week. Trade, build, scout, or comment to restore.`
                  : `Activity slowing — ${BUILDER_REWARDS_INACTIVITY_DAYS}-day inactivity window starts soon.`,
            }
          : null,
      rules: board.rules,
    };
  }

  /** @deprecated Alias */
  getRunwayMe(userId: string) {
    return this.getMe(userId).then((me) => ({
      ...me,
      runwayScore: me.builderScore ?? 0,
      humanScore: me.humanityScore ?? 0,
      xEligibility: me.xTrust ?? 'not_connected',
      airdropPoolPercent: me.rewardSharePercent ?? 0,
      projectedDecayDdollar: 0,
      cashBalanceUsd: me.ddollarBalanceUsd,
    }));
  }
}
