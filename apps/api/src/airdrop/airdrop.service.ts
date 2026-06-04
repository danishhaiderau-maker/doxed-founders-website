import { Injectable, NotFoundException } from '@nestjs/common';
import {
  computeAirdropAllocation,
  computeHumanLikelihoodScore,
  computeInactivityDecayDdollar,
  computeAirdropRunwayStatus,
  computeRunwayRankScore,
  daysSince,
  userHasTwitterConnected,
  xEligibilityForUser,
  AIRDROP_INACTIVITY_DECAY_DDOLLAR_PER_DAY,
  AIRDROP_INACTIVITY_WARN_DAYS,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { labelForUser } from '../account/user-identity.util';

const ACTIVITY_SINCE_MS = 14 * 24 * 60 * 60 * 1000;

export type AirdropRunwayEntry = {
  rank: number;
  userId: string;
  displayName: string;
  twitterHandle: string | null;
  twitterConnected: boolean;
  reputationPoints: number;
  activityScore: number;
  humanScore: number;
  runwayScore: number;
  status: ReturnType<typeof computeAirdropRunwayStatus>;
  xEligibility: ReturnType<typeof xEligibilityForUser>;
  lastActiveAt: string | null;
  idleDays: number | null;
  projectedDecayDdollar: number;
  airdropPoolPercent: number;
  estimatedTokens: number;
  estimatedUsd: number;
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
      },
      orderBy: [{ reputationPoints: 'desc' }, { createdAt: 'asc' }],
      take: Math.min(500, limit * 3),
    });
  }

  private async activityMaps(userIds: string[], since: Date) {
    const [trades, ledger, feedComments, communityComments, pollVotes, follows] =
      await Promise.all([
        this.prisma.paperTrade.groupBy({
          by: ['userId'],
          where: { userId: { in: userIds }, createdAt: { gte: since } },
          _count: { _all: true },
          _max: { createdAt: true },
        }),
        this.prisma.pointLedger.groupBy({
          by: ['userId'],
          where: { userId: { in: userIds }, createdAt: { gte: since } },
          _count: { _all: true },
          _max: { createdAt: true },
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
      ]);

    type Agg = { count: number; lastAt: Date | null; kinds: number };
    const map = new Map<string, Agg>();

    const bump = (userId: string, count: number, lastAt: Date | null) => {
      const cur = map.get(userId) ?? { count: 0, lastAt: null, kinds: 0 };
      cur.count += count;
      if (lastAt && (!cur.lastAt || lastAt > cur.lastAt)) cur.lastAt = lastAt;
      if (count > 0) cur.kinds += 1;
      map.set(userId, cur);
    };

    for (const r of trades) bump(r.userId, r._count._all, r._max.createdAt);
    for (const r of ledger) bump(r.userId, r._count._all, r._max.createdAt);
    for (const r of feedComments) bump(r.userId, r._count._all, r._max.createdAt);
    for (const r of communityComments) bump(r.userId, r._count._all, r._max.createdAt);
    for (const r of pollVotes) bump(r.userId, r._count._all, r._max.createdAt);
    for (const r of follows) bump(r.userId, r._count._all, r._max.createdAt);

    return map;
  }

  async getRunwayLeaderboard(limit = 100) {
    const users = await this.twitterUsers(limit);
    const userIds = users.map((u) => u.id);
    const since = new Date(Date.now() - ACTIVITY_SINCE_MS);
    const activityMap = userIds.length > 0 ? await this.activityMaps(userIds, since) : new Map();

    const scored = users.map((user) => {
      const agg = activityMap.get(user.id);
      const activityScore = Math.min(50, agg?.count ?? 0);
      const actionKinds = agg?.kinds ?? 0;
      const lastActiveAt = agg?.lastAt?.toISOString() ?? null;
      const idleDays = daysSince(lastActiveAt);
      const status = computeAirdropRunwayStatus({ lastActiveAt, activityScore });
      const twitterConnected = userHasTwitterConnected(user);
      const accountAgeDays = daysSince(user.createdAt.toISOString()) ?? 0;
      const humanScore = computeHumanLikelihoodScore({
        activityScore,
        reputationPoints: user.reputationPoints,
        actionKinds,
        accountAgeDays,
      });
      const runwayScore = computeRunwayRankScore({
        reputationPoints: user.reputationPoints,
        activityScore,
        humanScore,
        status,
      });
      const xEligibility = xEligibilityForUser({ twitterConnected, humanScore, status });

      return {
        user,
        activityScore,
        humanScore,
        runwayScore,
        status,
        xEligibility,
        lastActiveAt,
        idleDays,
        projectedDecayDdollar: computeInactivityDecayDdollar(idleDays),
        twitterConnected,
      };
    });

    scored.sort((a, b) => b.runwayScore - a.runwayScore);

    const poolUsers = scored.filter((s) => s.twitterConnected && s.user.reputationPoints > 0);
    const totalPoints = poolUsers.reduce((s, u) => s + u.user.reputationPoints, 0);

    const entries: AirdropRunwayEntry[] = scored.slice(0, limit).map((row, index) => {
      const allocation = computeAirdropAllocation(row.user.reputationPoints, totalPoints || 1);
      return {
        rank: index + 1,
        userId: row.user.id,
        displayName: labelForUser(row.user),
        twitterHandle: row.user.twitterHandle,
        twitterConnected: row.twitterConnected,
        reputationPoints: row.user.reputationPoints,
        activityScore: row.activityScore,
        humanScore: row.humanScore,
        runwayScore: Math.round(row.runwayScore),
        status: row.status,
        xEligibility: row.xEligibility,
        lastActiveAt: row.lastActiveAt,
        idleDays: row.idleDays,
        projectedDecayDdollar: row.projectedDecayDdollar,
        airdropPoolPercent: allocation.airdropPoolPercent,
        estimatedTokens: allocation.estimatedTokens,
        estimatedUsd: allocation.estimatedUsd,
      };
    });

    return {
      entries,
      totalListed: users.length,
      twitterConnectedCount: users.filter((u) => userHasTwitterConnected(u)).length,
      totalPoints,
      rules: {
        twitterSignInRequired: true,
        inactivityWarnDays: AIRDROP_INACTIVITY_WARN_DAYS,
        decayDdollarPerDay: AIRDROP_INACTIVITY_DECAY_DDOLLAR_PER_DAY,
        snapshotNote:
          'Final airdrop list is decided at distribution time. Unverified or bot-like accounts may be removed even if listed today.',
        blueTickNote:
          'X Blue is not required. Accounts that ever had Blue may receive a trust boost when we can verify history. Affordability matters — daily activity is the main human signal.',
      },
    };
  }

  async getRunwayMe(userId: string) {
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
      },
    });
    if (!user || user.banned) throw new NotFoundException('User not found');

    const board = await this.getRunwayLeaderboard(200);
    let mine = board.entries.find((e) => e.userId === userId);
    const twitterConnected = userHasTwitterConnected(user);

    if (!mine && twitterConnected) {
      const since = new Date(Date.now() - ACTIVITY_SINCE_MS);
      const activityMap = await this.activityMaps([userId], since);
      const agg = activityMap.get(userId);
      const activityScore = Math.min(50, agg?.count ?? 0);
      const lastActiveAt = agg?.lastAt?.toISOString() ?? null;
      const idleDays = daysSince(lastActiveAt);
      const status = computeAirdropRunwayStatus({ lastActiveAt, activityScore });
      const humanScore = computeHumanLikelihoodScore({
        activityScore,
        reputationPoints: user.reputationPoints,
        actionKinds: agg?.kinds ?? 0,
        accountAgeDays: daysSince(user.createdAt.toISOString()) ?? 0,
      });
      const allocation = computeAirdropAllocation(
        user.reputationPoints,
        board.totalPoints || 1,
      );
      mine = {
        rank: 0,
        userId: user.id,
        displayName: labelForUser(user),
        twitterHandle: user.twitterHandle,
        twitterConnected: true,
        reputationPoints: user.reputationPoints,
        activityScore,
        humanScore,
        runwayScore: Math.round(
          computeRunwayRankScore({
            reputationPoints: user.reputationPoints,
            activityScore,
            humanScore,
            status,
          }),
        ),
        status,
        xEligibility: xEligibilityForUser({ twitterConnected, humanScore, status }),
        lastActiveAt,
        idleDays,
        projectedDecayDdollar: computeInactivityDecayDdollar(idleDays),
        airdropPoolPercent: allocation.airdropPoolPercent,
        estimatedTokens: allocation.estimatedTokens,
        estimatedUsd: allocation.estimatedUsd,
      };
    }

    const status = mine?.status ?? 'warming';

    return {
      ...(mine ?? {
        userId: user.id,
        displayName: labelForUser(user),
        twitterHandle: user.twitterHandle,
        reputationPoints: user.reputationPoints,
        activityScore: 0,
        humanScore: 0,
        runwayScore: 0,
        status: 'warming' as const,
        xEligibility: 'not_connected' as const,
        lastActiveAt: null,
        idleDays: null,
        projectedDecayDdollar: 0,
        airdropPoolPercent: 0,
        estimatedTokens: 0,
        estimatedUsd: 0,
      }),
      rank: mine && mine.rank > 0 ? mine.rank : null,
      displayName: labelForUser(user),
      twitterConnected,
      needsTwitter: !twitterConnected,
      cashBalanceUsd: user.paperPortfolio ? Number(user.paperPortfolio.cashBalance) : null,
      warning:
        status === 'decaying' || status === 'at_risk'
          ? {
              level: status === 'decaying' ? ('critical' as const) : ('warn' as const),
              message:
                status === 'decaying'
                  ? `Inactive ${mine?.idleDays ?? '21+'} days — ${AIRDROP_INACTIVITY_DECAY_DDOLLAR_PER_DAY} DDollar/day may be redirected to active traders at airdrop snapshot. Trade, comment, or vote to recover.`
                  : `No activity in ${mine?.idleDays ?? '14+'} days — you are entering the ${AIRDROP_INACTIVITY_WARN_DAYS}-day inactivity window.`,
            }
          : null,
      rules: board.rules,
    };
  }
}
