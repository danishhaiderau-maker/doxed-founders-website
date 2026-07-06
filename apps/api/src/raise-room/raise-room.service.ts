import { Injectable } from '@nestjs/common';
import {
  FounderEventType,
  LaunchStage,
  ProjectLifecycleStage,
  SimulatedRaiseStatus,
} from '@prisma/client';
import {
  buildProgressiveUnlockProgress,
  computeLaunchQualificationScore,
  formatRaiseMomentum,
  getLaunchQualificationTier,
  LAUNCH_QUALIFICATION_MIN_SCORE,
  type LaunchQualificationComponents,
  type LaunchStageKey,
  tallyWeightedVotes,
  validationCategoryToVote,
  type CommunityValidationCategory,
} from '@dcf/utils';
import { isDemoModeEnabled } from '../demo/demo.constants';
import { PrismaService } from '../prisma/prisma.service';

export type RaiseRoomFilter =
  | 'trending'
  | 'newest'
  | 'almost_qualified'
  | 'ai_picks'
  | 'high_conviction'
  | 'near_graduation'
  | 'needs_review';

const RAISE_ROOM_VALIDATION_UI: {
  category: CommunityValidationCategory;
  label: string;
}[] = [
  { category: 'LOOKS_LEGIT', label: "I'd Use This" },
  { category: 'BUILDING_CONSISTENTLY', label: 'Strong Team' },
  { category: 'COMMUNITY_EXISTS', label: 'Active Community' },
  { category: 'NEEDS_MORE_PROOF', label: 'Reviewed Whitepaper' },
];

const LIFECYCLE_STEPS = [
  { key: 'idea', label: 'Idea' },
  { key: 'community', label: 'Community' },
  { key: 'validation', label: 'Validation' },
  { key: 'proof_raise', label: 'Proof Raise' },
  { key: 'graduation', label: 'Graduation' },
  { key: 'launch', label: 'Launch' },
  { key: 'trading', label: 'Trading' },
] as const;

const REWARD_TIERS = [
  { tier: 'Bronze', communityPercent: 8, feePercent: 1.5, minScore: 0 },
  { tier: 'Silver', communityPercent: 10, feePercent: 1, minScore: 80 },
  { tier: 'Gold', communityPercent: 12, feePercent: 0.5, minScore: 90 },
] as const;

const MARKETPLACE_NEEDS = [
  { slug: 'defi', label: 'DeFi', href: '/projects?category=defi' },
  { slug: 'infrastructure', label: 'Infrastructure', href: '/projects?category=infrastructure' },
  { slug: 'gaming', label: 'Gaming', href: '/projects?category=gaming' },
  { slug: 'payments', label: 'Payments', href: '/projects?category=payments' },
  { slug: 'identity', label: 'Identity', href: '/projects?category=identity' },
  { slug: 'builders', label: 'Need builders', href: '/settings/builder?tab=ai' },
] as const;

type RaiseRow = Awaited<ReturnType<RaiseRoomService['loadActiveRaises']>>[number];

@Injectable()
export class RaiseRoomService {
  constructor(private readonly prisma: PrismaService) {}

  async getDashboard() {
    const [raises, activityFeed, leaderboards, scoutLeaderboardWeek] = await Promise.all([
      this.loadActiveRaises(),
      this.getActivityFeed(24),
      this.getLeaderboards(),
      this.getScoutLeaderboardWeek(),
    ]);

    const cards = raises.map((r) => this.mapProjectCard(r));
    const paperConvictionTotal = cards.reduce((s, c) => s + c.paperConviction, 0);
    const founderIds = new Set(
      raises.map((r) => r.project.founderId).filter(Boolean) as string[],
    );
    const launchesWaiting = raises.filter(
      (r) => Number(r.project.launchQualificationScore) >= LAUNCH_QUALIFICATION_MIN_SCORE - 10,
    ).length;
    const trendingCount = cards.filter((c) => c.momentumScore >= 40).length;
    const communityScore =
      cards.length > 0
        ? Math.round(cards.reduce((s, c) => s + c.launchQualityScore, 0) / cards.length)
        : 0;

    const allocationBreakdown = this.computeAllocationBreakdown(raises);

    return {
      demoMode: isDemoModeEnabled(),
      stats: {
        paperConvictionTotal,
        activeFounders: founderIds.size,
        activeRaises: raises.length,
        launchesWaiting,
        trendingCount,
        communityScore,
      },
      trending: cards.slice(0, 8),
      activityFeed,
      leaderboards,
      scoutLeaderboardWeek,
      rewardTiers: REWARD_TIERS,
      communityAllocation: allocationBreakdown,
      marketplaceNeeds: MARKETPLACE_NEEDS,
      validationActions: RAISE_ROOM_VALIDATION_UI,
      hasData: raises.length > 0,
    };
  }

  async getProjects(filter: RaiseRoomFilter = 'trending', limit = 48) {
    const raises = await this.loadActiveRaises();
    const cards = raises.map((r) => this.mapProjectCard(r));
    const filtered = this.applyFilter(cards, filter);
    return {
      filter,
      projects: filtered.slice(0, limit),
      total: filtered.length,
      demoMode: isDemoModeEnabled(),
      hasData: raises.length > 0,
    };
  }

  private async loadActiveRaises() {
    return this.prisma.simulatedRaise.findMany({
      where: { status: SimulatedRaiseStatus.ACTIVE },
      include: {
        allocations: { include: { user: { select: { id: true, name: true, platformHandle: true } } } },
        project: {
          include: {
            category: { select: { slug: true, name: true } },
            chain: { select: { slug: true, name: true } },
            founder: {
              select: {
                id: true,
                slug: true,
                name: true,
                photoUrl: true,
                reputationScore: true,
                buildStreakDays: true,
                githubUsername: true,
                verifications: { where: { verified: true }, select: { type: true } },
              },
            },
            trustReports: { select: { category: true, voteWeight: true } },
            buildPosts: { orderBy: { publishedAt: 'desc' }, take: 1, select: { headline: true, publishedAt: true } },
            _count: { select: { followers: true, trustReports: true, buildPosts: true } },
          },
        },
      },
      orderBy: { createdAt: 'desc' },
      take: 120,
    });
  }

  private mapProjectCard(raise: RaiseRow) {
    const p = raise.project;
    const paperConviction = raise.allocations.reduce((s, a) => s + Number(a.amountUsd), 0);
    const goalUsd = Number(raise.goalUsd);
    const demandPct = goalUsd > 0 ? Math.min(100, Math.round((paperConviction / goalUsd) * 100)) : 0;
    const allocatorCount = raise.allocations.filter((a) => Number(a.amountUsd) > 0).length;
    const momentumScore = formatRaiseMomentum(paperConviction, goalUsd, allocatorCount);

    const meta = (p.launchQualificationMeta ?? null) as LaunchQualificationComponents | null;
    const components = meta ?? this.deriveComponents(p, raise, paperConviction, goalUsd);
    const launchQualityScore =
      p.launchQualificationScore > 0
        ? p.launchQualificationScore
        : computeLaunchQualificationScore(components);
    const lqTier = getLaunchQualificationTier(launchQualityScore);
    const rewardTier =
      lqTier === 'ELITE' ? 'Gold' : lqTier === 'STRONG' ? 'Silver' : 'Bronze';

    const tally = tallyWeightedVotes(
      p.trustReports.map((r) => ({
        vote: validationCategoryToVote(r.category),
        weight: r.voteWeight,
      })),
      3,
    );

    const founderVerified = Boolean(
      p.founder?.verifications?.length || p.founder?.githubUsername,
    );

    const lifecycleStep = this.computeLifecycleStep(p, raise);
    const launchStageProgress = buildProgressiveUnlockProgress(
      (p.launchStage ?? LaunchStage.BUILDER) as LaunchStageKey,
    );

    const scoutInterest = Math.round(tally.yesPercent * (p._count.trustReports || 1));

    const validationCounts = RAISE_ROOM_VALIDATION_UI.map((action) => ({
      ...action,
      count: p.trustReports.filter((r) => r.category === action.category).length,
    }));

    const launchQualityBreakdown = {
      founder: Math.round((components.founderLaunchScore + components.founderIntegrity) / 2),
      community: components.communityTrust,
      aiReview: Math.round((components.buildDelivery + components.founderLaunchScore) / 2),
      transparency: components.regulatoryClearance,
      code: components.buildDelivery,
    };

    const categoryName = p.category?.name ?? 'crypto';
    const aiSummary = this.buildAiSummary({
      name: p.name,
      category: categoryName,
      lifecycleStage: p.lifecycleStage,
      followerCount: p._count.followers,
      paperConviction,
      launchQualityScore,
      founderVerified,
      demandPct,
    });

    const daysLeft = raise.endsAt
      ? Math.max(0, Math.ceil((new Date(raise.endsAt).getTime() - Date.now()) / 86400000))
      : raise.durationDays;

    return {
      raiseId: raise.id,
      slug: p.slug,
      name: p.name,
      ticker: p.ticker,
      summary: p.summary,
      logoUrl: p.logoUrl,
      category: p.category,
      chain: p.chain,
      goalUsd,
      paperConviction,
      demandPct,
      momentumScore,
      allocatorCount,
      followerCount: p._count.followers,
      scoutInterest,
      launchEtaDays: daysLeft,
      endsAt: raise.endsAt?.toISOString() ?? null,
      founderVerified,
      founder: p.founder
        ? {
            slug: p.founder.slug,
            name: p.founder.name,
            photoUrl: p.founder.photoUrl,
            reputationScore: p.founder.reputationScore,
            buildStreakDays: p.founder.buildStreakDays,
          }
        : null,
      rewardTier,
      communityAllocationPct: raise.communityTokenPercent ?? 10,
      allocationFeePercent: 1,
      launchQualityScore,
      launchQualityTier: lqTier,
      launchQualityBreakdown,
      launchStage: p.launchStage,
      launchStageProgress,
      lifecycleSteps: LIFECYCLE_STEPS.map((step, idx) => ({
        ...step,
        complete: idx < lifecycleStep,
        current: idx === lifecycleStep - 1,
      })),
      lifecycleStage: p.lifecycleStage,
      validationCounts,
      validationTally: {
        yesPercent: tally.yesPercent,
        totalReports: p._count.trustReports,
      },
      aiSummary,
      lastUpdateHeadline: p.buildPosts[0]?.headline ?? null,
      createdAt: p.createdAt.toISOString(),
      needsReview: tally.yesPercent < LAUNCH_QUALIFICATION_MIN_SCORE && p._count.trustReports < 3,
      nearGraduation:
        p.launchStage === LaunchStage.GRADUATION ||
        p.launchStage === LaunchStage.FOUNDER_EXCHANGE ||
        launchQualityScore >= LAUNCH_QUALIFICATION_MIN_SCORE,
    };
  }

  private deriveComponents(
    project: RaiseRow['project'],
    _raise: RaiseRow,
    paperConviction: number,
    goalUsd: number,
  ): LaunchQualificationComponents {
    const fillRatio = goalUsd > 0 ? Math.min(100, (paperConviction / goalUsd) * 100) : 0;
    const tally = tallyWeightedVotes(
      project.trustReports.map((r) => ({
        vote: validationCategoryToVote(r.category),
        weight: r.voteWeight,
      })),
      3,
    );
    const founderVerified = Boolean(
      project.founder?.verifications?.length || project.founder?.githubUsername,
    );
    return {
      communityTrust: Math.min(100, tally.yesPercent),
      paperConviction: Math.round(fillRatio),
      founderLaunchScore: Math.min(100, project.launchReadiness),
      founderIntegrity: founderVerified ? 75 : 50,
      buildDelivery: Math.min(100, project._count.buildPosts * 15),
      regulatoryClearance: project.regulatoryClass === 'PENDING' ? 40 : 80,
    };
  }

  private computeLifecycleStep(project: RaiseRow['project'], raise: RaiseRow): number {
    if (
      project.lifecycleStage === ProjectLifecycleStage.LIVE_TRADING ||
      project.isLiveToken
    ) {
      return 7;
    }
    if (project.lifecycleStage === ProjectLifecycleStage.TOKEN_LAUNCH) return 6;
    if (
      project.launchStage === LaunchStage.GRADUATION ||
      project.launchStage === LaunchStage.FOUNDER_EXCHANGE
    ) {
      return 5;
    }
    if (raise) return 4;
    if (project._count.trustReports > 0) return 3;
    if (project._count.followers >= 3) return 2;
    return 1;
  }

  private buildAiSummary(input: {
    name: string;
    category: string;
    lifecycleStage: ProjectLifecycleStage;
    followerCount: number;
    paperConviction: number;
    launchQualityScore: number;
    founderVerified: boolean;
    demandPct: number;
  }): string {
    const verified = input.founderVerified ? 'Verified founder with' : 'Founder building';
    return `${input.name} is a ${input.category} project at ${input.lifecycleStage.replace(/_/g, ' ').toLowerCase()}. ${verified} ${input.followerCount} followers, $${Math.round(input.paperConviction).toLocaleString()} paper conviction (${input.demandPct}% of goal), and launch quality ${input.launchQualityScore}/100.`;
  }

  private applyFilter(
    cards: ReturnType<RaiseRoomService['mapProjectCard']>[],
    filter: RaiseRoomFilter,
  ) {
    const sorted = [...cards];
    switch (filter) {
      case 'newest':
        return sorted.sort(
          (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
        );
      case 'almost_qualified':
        return sorted
          .filter(
            (c) =>
              c.launchQualityScore >= LAUNCH_QUALIFICATION_MIN_SCORE - 15 &&
              c.launchQualityScore < LAUNCH_QUALIFICATION_MIN_SCORE,
          )
          .sort((a, b) => b.launchQualityScore - a.launchQualityScore);
      case 'ai_picks':
        return sorted
          .filter((c) => c.launchQualityBreakdown.aiReview >= 65 && c.founderVerified)
          .sort((a, b) => b.launchQualityBreakdown.aiReview - a.launchQualityBreakdown.aiReview);
      case 'high_conviction':
        return sorted.sort((a, b) => b.paperConviction - a.paperConviction);
      case 'near_graduation':
        return sorted.filter((c) => c.nearGraduation).sort((a, b) => b.launchQualityScore - a.launchQualityScore);
      case 'needs_review':
        return sorted.filter((c) => c.needsReview).sort((a, b) => a.validationTally.totalReports - b.validationTally.totalReports);
      case 'trending':
      default:
        return sorted.sort((a, b) => b.momentumScore - a.momentumScore || b.paperConviction - a.paperConviction);
    }
  }

  private computeAllocationBreakdown(raises: RaiseRow[]) {
    const allocCount = raises.reduce((s, r) => s + r.allocations.length, 0);
    const reviewCount = raises.reduce((s, r) => s + r.project._count.trustReports, 0);
    const buildCount = raises.reduce((s, r) => s + r.project._count.buildPosts, 0);
    const scoutCount = Math.max(1, Math.round(reviewCount * 0.4));
    const total = allocCount + reviewCount + scoutCount + buildCount || 1;
    return {
      paperContributors: Math.round((allocCount / total) * 100),
      reviewers: Math.round((reviewCount / total) * 100),
      scouts: Math.round((scoutCount / total) * 100),
      builders: Math.max(0, 100 - Math.round(((allocCount + reviewCount + scoutCount) / total) * 100)),
    };
  }

  private async getActivityFeed(limit: number) {
    const weekAgo = new Date(Date.now() - 7 * 86400000);
    const events = await this.prisma.founderEvent.findMany({
      where: {
        createdAt: { gte: weekAgo },
        type: {
          in: [
            FounderEventType.RAISE_ALLOCATION,
            FounderEventType.BUILD_PUBLISHED,
            FounderEventType.COMMUNITY_ACTIVITY,
          ],
        },
      },
      include: {
        project: { select: { slug: true, name: true, ticker: true } },
        user: { select: { name: true, platformHandle: true } },
      },
      orderBy: { createdAt: 'desc' },
      take: limit,
    });

    return events.map((e) => ({
      id: e.id,
      type: e.type,
      title: e.title,
      source: e.source,
      at: e.createdAt.toISOString(),
      project: e.project
        ? { slug: e.project.slug, name: e.project.name, ticker: e.project.ticker }
        : null,
      user: e.user
        ? { name: e.user.name, handle: e.user.platformHandle }
        : null,
      payload: e.payload,
    }));
  }

  private async getLeaderboards() {
    const [founders, scouts, builders, earners] = await Promise.all([
      this.prisma.founder.findMany({
        orderBy: { reputationScore: 'desc' },
        take: 8,
        select: {
          slug: true,
          name: true,
          photoUrl: true,
          reputationScore: true,
          buildStreakDays: true,
        },
      }),
      this.prisma.user.findMany({
        where: { reputationPoints: { gt: 0 } },
        orderBy: { reputationPoints: 'desc' },
        take: 8,
        select: {
          id: true,
          name: true,
          platformHandle: true,
          reputationPoints: true,
          progressTier: true,
        },
      }),
      this.prisma.founder.findMany({
        orderBy: { buildStreakDays: 'desc' },
        take: 8,
        select: {
          slug: true,
          name: true,
          photoUrl: true,
          buildStreakDays: true,
          reputationScore: true,
        },
      }),
      this.prisma.user.findMany({
        where: { lifetimeContributionEarned: { gt: 0 } },
        orderBy: { lifetimeContributionEarned: 'desc' },
        take: 8,
        select: {
          id: true,
          name: true,
          platformHandle: true,
          lifetimeContributionEarned: true,
        },
      }),
    ]);

    return {
      topFounders: founders.map((f, i) => ({
        rank: i + 1,
        slug: f.slug,
        name: f.name,
        photoUrl: f.photoUrl,
        score: f.reputationScore,
        buildStreakDays: f.buildStreakDays,
      })),
      topScouts: scouts.map((u, i) => ({
        rank: i + 1,
        name: u.name,
        handle: u.platformHandle,
        points: u.reputationPoints,
        tier: u.progressTier,
      })),
      topBuilders: builders.map((f, i) => ({
        rank: i + 1,
        slug: f.slug,
        name: f.name,
        photoUrl: f.photoUrl,
        buildStreakDays: f.buildStreakDays,
        score: f.reputationScore,
      })),
      topDdollarEarners: earners.map((u, i) => ({
        rank: i + 1,
        name: u.name,
        handle: u.platformHandle,
        earned: u.lifetimeContributionEarned,
      })),
    };
  }

  private async getScoutLeaderboardWeek() {
    const weekAgo = new Date(Date.now() - 7 * 86400000);
    const ledger = await this.prisma.pointLedger.groupBy({
      by: ['userId'],
      where: {
        createdAt: { gte: weekAgo },
        amount: { gt: 0 },
        actionKey: { in: ['SCOUT_EARLY', 'COMMUNITY_HELPFUL', 'TRUST_VALIDATION'] },
      },
      _sum: { amount: true },
      orderBy: { _sum: { amount: 'desc' } },
      take: 10,
    });

    if (ledger.length === 0) {
      const fallback = await this.prisma.user.findMany({
        where: { reputationPoints: { gt: 0 } },
        orderBy: { reputationPoints: 'desc' },
        take: 10,
        select: { name: true, platformHandle: true, reputationPoints: true },
      });
      return fallback.map((u, i) => ({
        rank: i + 1,
        name: u.name,
        handle: u.platformHandle,
        weeklyPoints: u.reputationPoints,
      }));
    }

    const users = await this.prisma.user.findMany({
      where: { id: { in: ledger.map((l) => l.userId) } },
      select: { id: true, name: true, platformHandle: true },
    });
    const byId = Object.fromEntries(users.map((u) => [u.id, u]));

    return ledger.map((row, i) => ({
      rank: i + 1,
      name: byId[row.userId]?.name ?? 'Scout',
      handle: byId[row.userId]?.platformHandle ?? null,
      weeklyPoints: row._sum.amount ?? 0,
    }));
  }
}
