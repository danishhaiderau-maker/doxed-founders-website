import { Injectable, NotFoundException, ForbiddenException, BadRequestException } from '@nestjs/common';
import {
  Prisma,
  SimulatedRaiseStatus,
  ProjectLifecycleStage,
  ProjectSource,
  UserProgressTier,
  FounderApplicationStatus,
  BountyStatus,
  FounderEventType,
  UserRole,
} from '@prisma/client';
import {
  computeFounderReputation,
  computePresenceLevel,
  JOURNEY_STAGES,
  computeStartupGenome,
  computeLaunchReadiness,
  LIFECYCLE_STAGES,
  STARTING_CASH_USD,
  RESTRICTED_CASH_THRESHOLD_USD,
  TOP_UP_FEE_USD,
  slugify,
  normalizeProjectName,
  projectTickerFromName,
  inferProjectLifecycleStage,
  resolveProjectListingKind,
  resolveEffectiveLifecycleStage,
  getStageBucket,
  computeJourneyProgress,
  POINTS,
  FOUNDER_LAUNCH_REPUTATION_POINTS,
  computeRaiseAllocationFee,
  buildParticipantExport,
  formatRaiseMomentum,
  formatPublicAccountLabel,
  RAISE_ALLOCATION_FEE_PERCENT,
  TOKEN_LAUNCH_FEE_PERCENT,
  WEEKLY_STIPEND_USD,
  DEFAULT_SCOUT_QUESTIONS,
  computeScoutConviction,
  buildFounderBrainContextBlock,
  FOUNDER_BRAIN_SYSTEM,
  FounderBrainContext,
  computeDiscoverActivityScore,
  computeDiscoverConvictionScore,
  resolveDiscoverUniverseStage,
  computeTrendDirection,
  type DiscoverTimeframe,
  type DiscoverUniverseStage,
  timeframeToMs,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { resolveSolanaTreasuryAddress } from '../payments/platform-treasury';
import { PointsService } from '../points/points.service';
import { NotificationsService } from '../notifications/notifications.service';
import { FounderOsService } from '../founder-os/founder-os.service';
import { EventsService } from '../events/events.service';
import { BuilderService } from '../builder/builder.service';
import { PredictionMarketsService } from '../prediction-markets/prediction-markets.service';
import { MetricsSyncService } from '../projects/metrics-sync.service';
import { NotificationType, ScoutMarketStatus, ListingStatus, PaperTradeSide } from '@prisma/client';

const founderRoomInclude = {
  user: { select: { id: true, name: true, email: true } },
  projects: {
    where: { approved: true },
    include: {
      chain: { select: { slug: true, name: true } },
      metrics: true,
      roadmapItems: { orderBy: { sortOrder: 'asc' as const } },
      simulatedRaises: {
        where: { status: SimulatedRaiseStatus.ACTIVE },
        include: { allocations: true },
        take: 1,
      },
      demandPolls: { where: { active: true }, include: { votes: true } },
    },
  },
  videos: { orderBy: { publishedAt: 'desc' as const } },
  buildPosts: { orderBy: { publishedAt: 'desc' as const }, take: 50 },
} satisfies Prisma.FounderInclude;

const COMMUNITY_CHANNELS = [
  'ANNOUNCEMENTS',
  'GENERAL',
  'FEATURE_REQUESTS',
  'QUESTIONS',
  'LAUNCH',
  'DEVELOPMENT',
] as const;

const DEFAULT_POLL_TEMPLATES = [
  { type: 'WOULD_USE' as const, question: 'Would you use this?', options: ['Yes', 'Maybe', 'No'] },
  { type: 'WOULD_PAY' as const, question: 'Would you pay for this?', options: ['Yes', 'Maybe', 'No'] },
  { type: 'TOKEN_INTEREST' as const, question: 'Do we need a token?', options: ['Yes', 'No', 'Not sure'] },
  { type: 'WOULD_USE' as const, question: 'What should we build next?', options: ['Feature A', 'Feature B', 'Other'] },
];

@Injectable()
export class FounderDenService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly points: PointsService,
    private readonly notifications: NotificationsService,
    private readonly founderOs: FounderOsService,
    private readonly events: EventsService,
    private readonly builder: BuilderService,
    private readonly predictionMarkets: PredictionMarketsService,
    private readonly metricsSync: MetricsSyncService,
  ) {}

  async getLatestVideos(limit = 12) {
    const videos = await this.prisma.founderVideo.findMany({
      orderBy: { publishedAt: 'desc' },
      take: limit,
      include: {
        founder: { select: { slug: true, name: true, photoUrl: true, presenceLevel: true } },
        project: { select: { slug: true, name: true, ticker: true, logoUrl: true } },
      },
    });
    return videos.map((v) => ({
      id: v.id,
      type: v.type,
      title: v.title,
      url: v.url,
      durationMin: v.durationMin,
      publishedAt: v.publishedAt,
      founder: v.founder,
      project: v.project,
    }));
  }

  async getBuildFeed(limit = 40) {
    const posts = await this.prisma.founderBuildPost.findMany({
      orderBy: { publishedAt: 'desc' },
      take: limit,
      include: {
        founder: { select: { slug: true, name: true, photoUrl: true, presenceLevel: true } },
        project: { select: { slug: true, name: true, ticker: true, logoUrl: true } },
      },
    });
    return posts;
  }

  async getFounderRoom(slug: string) {
    const founder = await this.prisma.founder.findUnique({
      where: { slug },
      include: founderRoomInclude,
    });
    if (!founder) throw new NotFoundException('Founder not found');

    const stats = await this.computeFounderStats(founder.id);
    const reputation = computeFounderReputation(stats.reputationInput);
    const presenceLevel = computePresenceLevel(stats.presenceInput);

    return {
      ...this.mapFounder(founder),
      presenceLevel,
      reputation,
      journeyStages: JOURNEY_STAGES,
      stats: stats.counts,
      heatmap: this.buildHeatmap(founder.buildPosts),
    };
  }

  async getProjectRoom(slug: string, viewerUserId?: string) {
    await this.metricsSync.syncBySlug(slug, true);

    const project = await this.prisma.project.findFirst({
      where: { slug, approved: true },
      include: {
        chain: { select: { slug: true, name: true } },
        category: { select: { slug: true, name: true } },
        founder: {
          include: {
            user: { select: { id: true } },
            videos: { orderBy: { publishedAt: 'desc' }, take: 10 },
          },
        },
        metrics: true,
        socials: true,
        roadmapItems: { orderBy: { sortOrder: 'asc' } },
        simulatedRaises: {
          orderBy: { createdAt: 'desc' },
          include: { allocations: true },
          take: 3,
        },
        demandPolls: { where: { active: true }, include: { votes: true } },
        buildPosts: { orderBy: { publishedAt: 'desc' }, take: 30 },
        followers: true,
        communityThreads: {
          orderBy: [{ pinned: 'desc' }, { createdAt: 'desc' }],
          take: 50,
          include: {
            comments: {
              where: { parentId: null },
              orderBy: { createdAt: 'asc' },
              take: 20,
              include: { helpfulMark: true },
            },
          },
        },
        _count: { select: { followers: true, buildPosts: true } },
      },
    });

    if (!project) throw new NotFoundException('Project not found');

    const marketCap = project.metrics?.marketCap ? Number(project.metrics.marketCap) : null;
    const listingKind = resolveProjectListingKind({
      source: project.source,
      founderId: project.founderId,
    });
    const effectiveStage = resolveEffectiveLifecycleStage({
      source: project.source,
      founderId: project.founderId,
      lifecycleStage: project.lifecycleStage,
      isLiveToken: project.isLiveToken,
      dexscreenerUrl: project.dexscreenerUrl,
      contractAddress: project.contractAddress,
      marketCap,
    });

    if (
      effectiveStage !== project.lifecycleStage ||
      ((effectiveStage === 'LIVE_TRADING' || effectiveStage === 'TOKEN_LAUNCH') && !project.isLiveToken)
    ) {
      await this.prisma.project.update({
        where: { id: project.id },
        data: {
          lifecycleStage: effectiveStage as ProjectLifecycleStage,
          isLiveToken:
            effectiveStage === 'LIVE_TRADING' || effectiveStage === 'TOKEN_LAUNCH',
        },
      });
    }

    const activeRaise = project.simulatedRaises.find((r) => r.status === SimulatedRaiseStatus.ACTIVE);
    const totalAllocated = activeRaise
      ? activeRaise.allocations.reduce((s, a) => s + Number(a.amountUsd), 0)
      : 0;

    const amounts = activeRaise?.allocations.map((a) => Number(a.amountUsd)) ?? [];
    const avgCommitment = amounts.length
      ? amounts.reduce((s, v) => s + v, 0) / amounts.length
      : 0;
    const largestCommitment = amounts.length ? Math.max(...amounts) : 0;

    const demandRank = await this.getDemandRank(project.id, totalAllocated);
    const launchReadiness = await this.refreshLaunchReadiness(project.id);

    const githubConnected = Boolean(
      project.socials?.githubUrl || project.founder?.githubUrl || project.founder?.githubUsername,
    );
    const pollVoteCount = project.demandPolls.reduce((s, p) => s + p.votes.length, 0);
    const genome = computeStartupGenome({
      buildPostCount: project._count.buildPosts,
      githubConnected,
      simulatedDemandUsd: totalAllocated,
      followerCount: project._count.followers,
      videoCount: project.founder?.videos.length ?? 0,
      pollVoteCount,
      launchReadiness,
    });

    const launchpadRequirements = this.computeLaunchpadRequirements(project, totalAllocated, launchReadiness);
    const isFollowing = viewerUserId
      ? project.followers.some((f) => f.userId === viewerUserId)
      : false;
    const isProjectFounder = Boolean(
      viewerUserId && project.founder?.user?.id === viewerUserId,
    );

    const bounties = await this.prisma.founderBounty.findMany({
      where: { projectId: project.id, status: BountyStatus.OPEN },
      orderBy: { createdAt: 'desc' },
      take: 10,
    });

    return {
      id: project.id,
      slug: project.slug,
      name: project.name,
      ticker: project.ticker,
      summary: project.summary,
      description: project.description,
      logoUrl: project.logoUrl,
      websiteUrl: project.websiteUrl,
      dexscreenerUrl: project.dexscreenerUrl,
      chain: project.chain,
      category: project.category,
      lifecycleStage: effectiveStage,
      listingKind,
      isVerifiedListing: listingKind === 'verified',
      launchReadiness,
      plannedLaunchDate: project.plannedLaunchDate,
      launchRequestedAt: project.launchRequestedAt,
      isLiveToken:
        project.isLiveToken ||
        effectiveStage === 'LIVE_TRADING' ||
        effectiveStage === 'TOKEN_LAUNCH',
      launchPriceUsd: project.launchPriceUsd ? Number(project.launchPriceUsd) : null,
      followerCount: project._count.followers,
      isFollowing,
      isProjectFounder,
      communityRewardPool: project.communityRewardPool,
      openBounties: bounties,
      genome,
      lifecycleStages: LIFECYCLE_STAGES,
      metrics: project.metrics
        ? {
            priceUsd: project.metrics.priceUsd ? Number(project.metrics.priceUsd) : null,
            marketCap: project.metrics.marketCap ? Number(project.metrics.marketCap) : null,
            fdv: project.metrics.fdv ? Number(project.metrics.fdv) : null,
            volume24h: project.metrics.volume24h ? Number(project.metrics.volume24h) : null,
            liquidity: project.metrics.liquidity ? Number(project.metrics.liquidity) : null,
            holders: project.metrics.holders,
            priceChange24h: project.metrics.priceChange24h
              ? Number(project.metrics.priceChange24h)
              : null,
          }
        : null,
      socials: project.socials,
      founder: project.founder ? this.mapFounderBrief(project.founder) : null,
      founderScore: project.founder?.reputationScore ?? 0,
      buildStreakDays: project.founder?.buildStreakDays ?? 0,
      videos: project.founder?.videos ?? [],
      buildPosts: project.buildPosts,
      roadmap: project.roadmapItems,
      activeRaise: activeRaise
        ? {
            id: activeRaise.id,
            goalUsd: Number(activeRaise.goalUsd),
            tokenAllocation: activeRaise.tokenAllocation,
            communityTokenPercent: activeRaise.communityTokenPercent,
            maxParticipantSlots: activeRaise.maxParticipantSlots,
            totalBurnedUsd: Number(activeRaise.totalBurnedUsd),
            slotsLocked: activeRaise.slotsLocked,
            durationDays: activeRaise.durationDays,
            plannedLaunchDate: activeRaise.plannedLaunchDate,
            status: activeRaise.status,
            startsAt: activeRaise.startsAt,
            endsAt: activeRaise.endsAt,
            totalAllocated,
            allocatorCount: activeRaise.allocations.filter((a) => Number(a.amountUsd) > 0).length,
            convictionScore: Math.min(
              100,
              Math.round((totalAllocated / Number(activeRaise.goalUsd || 1)) * 100),
            ),
            momentumScore: formatRaiseMomentum(
              totalAllocated,
              Number(activeRaise.goalUsd),
              activeRaise.allocations.filter((a) => Number(a.amountUsd) > 0).length,
            ),
            allocationFeePercent: RAISE_ALLOCATION_FEE_PERCENT,
          }
        : null,
      allocationLeaderboard: activeRaise
        ? await this.buildAllocationLeaderboard(activeRaise.id)
        : [],
      demandAnalytics: {
        interestedUsers: project._count.followers + (activeRaise?.allocations.length ?? 0),
        averageCommitment: Math.round(avgCommitment),
        largestCommitment,
        demandRank,
        totalDemand: totalAllocated,
      },
      demandPolls: project.demandPolls.map((p) => ({
        id: p.id,
        type: p.type,
        question: p.question,
        options: p.options,
        voteCounts: this.tallyPollVotes(p.votes, p.options as string[]),
      })),
      communityChannels: COMMUNITY_CHANNELS,
      communityThreads: project.communityThreads.map((t) => ({
        id: t.id,
        channel: t.channel,
        title: t.title,
        body: t.body,
        pinned: t.pinned,
        createdAt: t.createdAt,
        commentCount: t.comments.length,
        comments: t.comments.map((c) => ({
          id: c.id,
          userId: c.userId,
          body: c.body,
          createdAt: c.createdAt,
          isHelpful: Boolean(c.helpfulMark),
        })),
      })),
      launchpadAccess: launchpadRequirements,
    };
  }

  async getDemandHeatmap() {
    const raises = await this.prisma.simulatedRaise.findMany({
      where: { status: SimulatedRaiseStatus.ACTIVE },
      include: {
        project: { select: { slug: true, name: true, ticker: true, logoUrl: true } },
        allocations: true,
      },
    });

    return raises
      .map((r) => ({
        project: r.project,
        goalUsd: Number(r.goalUsd),
        totalDemand: r.allocations.reduce((s, a) => s + Number(a.amountUsd), 0),
        allocatorCount: r.allocations.length,
      }))
      .sort((a, b) => b.totalDemand - a.totalDemand);
  }

  async createBuildPost(
    userId: string,
    dto: { headline: string; body: string; projectId?: string; dayNumber?: number; githubUrl?: string },
  ) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) {
      throw new ForbiddenException('Link your account to a founder profile to post build updates.');
    }

    const post = await this.prisma.founderBuildPost.create({
      data: {
        founderId: founder.id,
        projectId: dto.projectId,
        dayNumber: dto.dayNumber,
        headline: dto.headline.trim(),
        body: dto.body.trim(),
        githubUrl: dto.githubUrl?.trim(),
      },
    });

    const streak = await this.updateBuildStreak(founder.id);
    await this.syncPresenceLevel(founder.id);
    await this.points.award(userId, POINTS.FOUNDER_BUILD_POST, 'FOUNDER_BUILD_POST');

    return { ...post, buildStreakDays: streak };
  }

  async addVideo(
    userId: string,
    dto: {
      title: string;
      url: string;
      type: 'INTRODUCTION' | 'DEEP_DIVE' | 'MONTHLY_UPDATE' | 'QA';
      projectId?: string;
      durationMin?: number;
    },
  ) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) {
      throw new ForbiddenException('Link your account to a founder profile to add videos.');
    }

    const video = await this.prisma.founderVideo.create({
      data: {
        founderId: founder.id,
        projectId: dto.projectId,
        type: dto.type,
        title: dto.title.trim(),
        url: dto.url.trim(),
        durationMin: dto.durationMin,
      },
    });

    await this.syncPresenceLevel(founder.id);
    await this.points.award(userId, POINTS.FOUNDER_VIDEO, 'FOUNDER_VIDEO');
    return video;
  }

  async allocateToRaise(userId: string, raiseId: string, amountUsd: number) {
    if (amountUsd < 1) throw new BadRequestException('Minimum allocation is $1');

    const raise = await this.prisma.simulatedRaise.findUnique({
      where: { id: raiseId },
      include: {
        allocations: true,
        project: { include: { founder: true } },
      },
    });
    if (!raise || raise.status !== SimulatedRaiseStatus.ACTIVE) {
      throw new BadRequestException('Raise is not active');
    }
    if (raise.slotsLocked) {
      throw new BadRequestException('Raise slots are locked — allocation closed');
    }

    const portfolio = await this.prisma.paperPortfolio.findUnique({ where: { userId } });
    if (!portfolio) throw new BadRequestException('Start a paper trading session first');

    const cash = Number(portfolio.cashBalance);
    if (cash < RESTRICTED_CASH_THRESHOLD_USD) {
      throw new BadRequestException(
        `Cash below $${RESTRICTED_CASH_THRESHOLD_USD.toLocaleString()}. Top up for $${TOP_UP_FEE_USD} to allocate.`,
      );
    }

    const existing = raise.allocations.find((a) => a.userId === userId);
    const existingAmt = existing ? Number(existing.amountUsd) : 0;
    const existingBurned = existing ? Number(existing.burnedUsd) : 0;
    const delta = amountUsd - existingAmt;

    if (raise.maxParticipantSlots != null && !existing && amountUsd > 0) {
      const participantCount = raise.allocations.filter((a) => Number(a.amountUsd) > 0).length;
      if (participantCount >= raise.maxParticipantSlots) {
        throw new BadRequestException(`All ${raise.maxParticipantSlots} ICO slots are reserved`);
      }
    }

    const { computeRaiseAllocationFee } = await import('@dcf/utils');
    const fee = computeRaiseAllocationFee(delta);
    const totalDebit = delta > 0 ? delta + fee : delta;

    if (totalDebit > cash) {
      throw new BadRequestException(
        `Insufficient virtual cash (includes ${fee > 0 ? `1% allocation fee $${fee.toFixed(2)}` : 'no fee'})`,
      );
    }

    const wallets = await this.prisma.walletConnection.findMany({ where: { userId } });
    const walletAddress = wallets[0]?.address ?? null;

    await this.prisma.$transaction(async (tx) => {
      await tx.raiseAllocation.upsert({
        where: { raiseId_userId: { raiseId, userId } },
        create: {
          raiseId,
          userId,
          amountUsd: new Prisma.Decimal(amountUsd),
          burnedUsd: new Prisma.Decimal(fee),
          walletAddress,
          slotReserved: amountUsd > 0,
        },
        update: {
          amountUsd: new Prisma.Decimal(amountUsd),
          burnedUsd: new Prisma.Decimal(existingBurned + fee),
          walletAddress: walletAddress ?? undefined,
          slotReserved: amountUsd > 0,
        },
      });

      if (totalDebit !== 0) {
        await tx.paperPortfolio.update({
          where: { userId },
          data: { cashBalance: { increment: -totalDebit } },
        });
        if (delta !== 0) {
          await tx.virtualEconomyEvent.create({
            data: {
              userId,
              type: delta > 0 ? 'RAISE_ALLOCATE' : 'RAISE_DEALLOCATE',
              amountUsd: new Prisma.Decimal(Math.abs(delta)),
              note: `Raise Room ${raiseId}`,
            },
          });
        }
        if (fee > 0) {
          await tx.virtualEconomyEvent.create({
            data: {
              userId,
              type: 'PAPER_BURN',
              amountUsd: new Prisma.Decimal(fee),
              note: 'Raise allocation fee (1%) — removed from circulation',
            },
          });
          await tx.simulatedRaise.update({
            where: { id: raiseId },
            data: { totalBurnedUsd: { increment: fee } },
          });
        }
      }
    });

    await this.syncUserProgressTier(userId);
    await this.refreshLaunchReadiness(raise.projectId);
    await this.points.awardOnce(userId, `RAISE_ALLOCATE:${raiseId}`, POINTS.RAISE_ALLOCATE);

    const followerCount = await this.prisma.projectFollow.count({ where: { projectId: raise.projectId } });
    await this.founderOs.recordEarlyScout(userId, raise.projectId, amountUsd, followerCount);

    if (raise.project.founder && delta > 0) {
      await this.events.emit({
        founderId: raise.project.founder.id,
        projectId: raise.projectId,
        userId,
        type: FounderEventType.RAISE_ALLOCATION,
        source: 'raise-room',
        title: `$${amountUsd.toLocaleString()} allocated to Raise Room`,
        payload: { raiseId, amountUsd, feeBurned: fee },
      });
    }

    return { success: true, amountUsd, feeBurned: fee };
  }

  async votePoll(userId: string, pollId: string, optionKey: string) {
    const poll = await this.prisma.demandPoll.findUnique({ where: { id: pollId } });
    if (!poll?.active) throw new BadRequestException('Poll not active');

    await this.prisma.demandPollVote.upsert({
      where: { pollId_userId: { pollId, userId } },
      create: { pollId, userId, optionKey },
      update: { optionKey },
    });

    await this.syncUserProgressTier(userId);
    await this.points.awardOnce(userId, `DEMAND_POLL_VOTE:${pollId}`, POINTS.DEMAND_POLL_VOTE);
    return { success: true };
  }

  async getDashboard(userId: string) {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      include: {
        founder: {
          include: {
            projects: {
              where: { approved: true },
              include: {
                simulatedRaises: {
                  where: { status: SimulatedRaiseStatus.ACTIVE },
                  include: { allocations: true },
                },
                _count: { select: { followers: true } },
              },
            },
          },
        },
        paperPortfolio: true,
        raiseAllocations: { include: { raise: { include: { project: true } } } },
      },
    });
    if (!user) throw new NotFoundException('User not found');

    const founder = user.founder;
    const primaryProject = founder?.projects[0];
    const activeRaise = primaryProject?.simulatedRaises[0];
    const simulatedDemand = activeRaise
      ? activeRaise.allocations.reduce((s, a) => s + Number(a.amountUsd), 0)
      : 0;

    let launchReadiness = primaryProject?.launchReadiness ?? 0;
    if (primaryProject) {
      launchReadiness = await this.refreshLaunchReadiness(primaryProject.id);
    }

    return {
      progressTier: user.progressTier,
      founderScore: founder?.reputationScore ?? 0,
      currentStage: primaryProject?.lifecycleStage ?? 'IDEA',
      followers: primaryProject?._count.followers ?? 0,
      buildStreakDays: founder?.buildStreakDays ?? 0,
      simulatedDemand,
      launchReadiness,
      cashBalance: user.paperPortfolio ? Number(user.paperPortfolio.cashBalance) : STARTING_CASH_USD,
      hasFounderProfile: Boolean(founder),
      primaryProjectSlug: primaryProject?.slug ?? null,
      founderSlug: founder?.slug ?? null,
      founderCredits: founder?.founderCredits ?? 0,
      communityRewardPool: primaryProject?.communityRewardPool ?? 0,
      applicationPending: await this.prisma.founderApplication.count({
        where: { userId, status: FounderApplicationStatus.SUBMITTED },
      }),
    };
  }

  async submitFounderApplication(
    userId: string,
    dto: {
      projectName: string;
      websiteUrl?: string;
      twitterHandle?: string;
      githubUrl?: string;
      videoUrl?: string;
      ideaDescription: string;
      lifecycleStage: ProjectLifecycleStage;
    },
  ) {
    const existingFounder = await this.prisma.founder.findUnique({ where: { userId } });
    if (existingFounder) {
      throw new BadRequestException('You already have an active founder profile.');
    }

    const chain = await this.prisma.chain.findFirst();
    if (!chain) throw new BadRequestException('Platform not configured — no chains seeded.');

    const projectName = normalizeProjectName(dto.projectName);
    if (projectName.length < 2) {
      throw new BadRequestException('Enter a project name (not a URL alone)');
    }

    const baseSlug = slugify(projectName);
    let slug = baseSlug;
    let n = 1;
    while (await this.prisma.project.findUnique({ where: { slug } })) {
      slug = `${baseSlug}-${n++}`;
    }

    const founderSlug = slugify(projectName + '-founder').slice(0, 48);
    const user = await this.prisma.user.findUnique({ where: { id: userId } });
    const founderName = user?.name ?? projectName;

    const result = await this.prisma.$transaction(async (tx) => {
      const founder = await tx.founder.create({
        data: {
          slug: founderSlug,
          userId,
          name: founderName,
          websiteUrl: dto.websiteUrl?.trim(),
          twitterUrl: dto.twitterHandle
            ? dto.twitterHandle.startsWith('http')
              ? dto.twitterHandle
              : `https://x.com/${dto.twitterHandle.replace(/^@/, '')}`
            : undefined,
          githubUrl: dto.githubUrl?.trim(),
          videoUrl: dto.videoUrl?.trim(),
          publicBuildingSince: new Date(),
        },
      });

      const project = await tx.project.create({
        data: {
          slug,
          name: projectName,
          ticker: projectTickerFromName(projectName),
          summary: dto.ideaDescription.slice(0, 280),
          description: dto.ideaDescription.trim(),
          websiteUrl: dto.websiteUrl?.trim(),
          chainId: chain.id,
          founderId: founder.id,
          approved: true,
          source: 'DYNAMIC',
          lifecycleStage: dto.lifecycleStage,
          socials: {
            create: {
              githubUrl: dto.githubUrl?.trim(),
              twitterUrl: dto.twitterHandle
                ? dto.twitterHandle.startsWith('http')
                  ? dto.twitterHandle
                  : `https://x.com/${dto.twitterHandle.replace(/^@/, '')}`
                : undefined,
            },
          },
        },
      });

      if (dto.videoUrl?.trim()) {
        await tx.founderVideo.create({
          data: {
            founderId: founder.id,
            projectId: project.id,
            type: 'INTRODUCTION',
            title: 'Founder introduction',
            url: dto.videoUrl.trim(),
            durationMin: 5,
          },
        });
      }

      for (const tpl of DEFAULT_POLL_TEMPLATES) {
        await tx.demandPoll.create({
          data: {
            projectId: project.id,
            type: tpl.type,
            question: tpl.question,
            options: tpl.options,
          },
        });
      }

      const application = await tx.founderApplication.create({
        data: {
          userId,
          projectName,
          websiteUrl: dto.websiteUrl?.trim(),
          twitterHandle: dto.twitterHandle?.trim(),
          githubUrl: dto.githubUrl?.trim(),
          videoUrl: dto.videoUrl?.trim(),
          ideaDescription: dto.ideaDescription.trim(),
          lifecycleStage: dto.lifecycleStage,
          status: FounderApplicationStatus.ACTIVE,
          projectId: project.id,
        },
      });

      await tx.user.update({
        where: { id: userId },
        data: {
          progressTier:
            dto.lifecycleStage === 'IDEA' || dto.lifecycleStage === 'BRAINSTORMING'
              ? UserProgressTier.FOUNDER_IDEA
              : UserProgressTier.FOUNDER_BUILDING,
        },
      });

      return { founder, project, application };
    });

    await this.syncPresenceLevel(result.founder.id);
    await this.refreshLaunchReadiness(result.project.id);
    await this.refreshBubbleScore(result.project.id);

    const awarded = await this.points.awardOnce(
      userId,
      `FOUNDER_PROJECT:${result.project.id}`,
      FOUNDER_LAUNCH_REPUTATION_POINTS,
    );
    await this.founderOs.grantLaunchCredits(
      userId,
      result.founder.id,
      result.project.id,
      projectName,
    );
    if (awarded) {
      await this.notifications.notifyUser(userId, {
        type: NotificationType.POINTS_EARNED,
        title: `+${FOUNDER_LAUNCH_REPUTATION_POINTS} reputation points`,
        body: `Your project "${projectName}" is live on Founder OS.`,
        link: '/founder-den',
      });
    }

    return {
      success: true,
      founderSlug: result.founder.slug,
      projectSlug: result.project.slug,
    };
  }

  async createSimulatedRaise(
    userId: string,
    projectId: string,
    dto: {
      goalUsd: number;
      durationDays: number;
      tokenAllocation?: string;
      plannedLaunchDate?: string;
      communityTokenPercent?: number;
      maxParticipantSlots?: number;
    },
  ) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const project = await this.prisma.project.findFirst({
      where: { id: projectId, founderId: founder.id },
    });
    if (!project) throw new NotFoundException('Project not found');

    const startsAt = new Date();
    const endsAt = new Date(startsAt.getTime() + dto.durationDays * 86400000);

    const raise = await this.prisma.$transaction(async (tx) => {
      await tx.simulatedRaise.updateMany({
        where: { projectId, status: SimulatedRaiseStatus.ACTIVE },
        data: { status: SimulatedRaiseStatus.CANCELLED },
      });

      return tx.simulatedRaise.create({
        data: {
          projectId,
          goalUsd: new Prisma.Decimal(dto.goalUsd),
          tokenAllocation: dto.tokenAllocation,
          communityTokenPercent: dto.communityTokenPercent ?? 10,
          maxParticipantSlots: dto.maxParticipantSlots,
          durationDays: dto.durationDays,
          plannedLaunchDate: dto.plannedLaunchDate ? new Date(dto.plannedLaunchDate) : null,
          status: SimulatedRaiseStatus.ACTIVE,
          startsAt,
          endsAt,
        },
      });
    });

    await this.prisma.project.update({
      where: { id: projectId },
      data: {
        lifecycleStage: ProjectLifecycleStage.SIMULATED_RAISE,
        plannedLaunchDate: dto.plannedLaunchDate ? new Date(dto.plannedLaunchDate) : undefined,
      },
    });

    await this.syncUserProgressTier(userId, UserProgressTier.LAUNCH_CANDIDATE);
    await this.refreshLaunchReadiness(projectId);

    return raise;
  }

  async requestLaunchpadAccess(userId: string, projectId: string) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const project = await this.prisma.project.findFirst({
      where: { id: projectId, founderId: founder.id },
      include: {
        simulatedRaises: { where: { status: SimulatedRaiseStatus.ACTIVE }, include: { allocations: true } },
        buildPosts: true,
        founder: { include: { videos: true } },
        _count: { select: { followers: true } },
      },
    });
    if (!project) throw new NotFoundException('Project not found');

    const totalDemand = project.simulatedRaises[0]?.allocations.reduce(
      (s, a) => s + Number(a.amountUsd),
      0,
    ) ?? 0;
    const readiness = await this.refreshLaunchReadiness(projectId);
    const reqs = this.computeLaunchpadRequirements(project, totalDemand, readiness);

    if (!reqs.unlocked) {
      throw new BadRequestException('Launchpad requirements not met yet.');
    }

    await this.prisma.project.update({
      where: { id: projectId },
      data: {
        launchRequestedAt: new Date(),
        lifecycleStage: ProjectLifecycleStage.LAUNCH_READY,
      },
    });

    return { success: true, requestedAt: new Date() };
  }

  async listToken(
    userId: string,
    projectId: string,
    dto: { contractAddress: string; chainSlug: string; websiteUrl?: string; dexscreenerUrl?: string },
  ) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const chain = await this.prisma.chain.findUnique({ where: { slug: dto.chainSlug as never } });
    if (!chain) throw new BadRequestException('Invalid chain');

    const project = await this.prisma.project.findFirst({
      where: { id: projectId, founderId: founder.id },
    });
    if (!project) throw new NotFoundException('Project not found');

    await this.prisma.project.update({
      where: { id: projectId },
      data: {
        contractAddress: dto.contractAddress.trim(),
        chainId: chain.id,
        websiteUrl: dto.websiteUrl?.trim() ?? project.websiteUrl,
        dexscreenerUrl: dto.dexscreenerUrl?.trim(),
        isLiveToken: true,
        lifecycleStage: ProjectLifecycleStage.LIVE_TRADING,
      },
    });

    await this.syncUserProgressTier(userId, UserProgressTier.PROVEN_FOUNDER);
    return { success: true };
  }

  async followProject(userId: string, projectId: string) {
    await this.prisma.projectFollow.upsert({
      where: { userId_projectId: { userId, projectId } },
      create: { userId, projectId },
      update: {},
    });
    await this.syncUserProgressTier(userId);
    await this.refreshBubbleScore(projectId);
    await this.points.awardOnce(userId, `PROJECT_FOLLOW:${projectId}`, POINTS.PROJECT_FOLLOW);
    return { success: true };
  }

  async unfollowProject(userId: string, projectId: string) {
    await this.prisma.projectFollow.deleteMany({ where: { userId, projectId } });
    await this.refreshBubbleScore(projectId);
    return { success: true };
  }

  async createCommunityThread(
    userId: string,
    projectId: string,
    dto: { channel: string; title: string; body: string },
  ) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    const isFounder = founder
      ? await this.prisma.project.count({ where: { id: projectId, founderId: founder.id } })
      : 0;

    if (dto.channel === 'ANNOUNCEMENTS' && !isFounder) {
      throw new ForbiddenException('Only founders can post announcements.');
    }

    const thread = await this.prisma.communityThread.create({
      data: {
        projectId,
        channel: dto.channel,
        title: dto.title.trim(),
        body: dto.body.trim(),
        authorId: userId,
        pinned: dto.channel === 'ANNOUNCEMENTS',
      },
    });

    await this.syncUserProgressTier(userId);
    if (isFounder) {
      await this.points.award(userId, POINTS.FOUNDER_COMMUNITY_POST, 'FOUNDER_COMMUNITY_POST');
    }
    return thread;
  }

  async addCommunityComment(
    userId: string,
    threadId: string,
    dto: { body: string; parentId?: string },
  ) {
    const thread = await this.prisma.communityThread.findUnique({ where: { id: threadId } });
    if (!thread) throw new NotFoundException('Thread not found');

    const comment = await this.prisma.communityComment.create({
      data: {
        threadId,
        userId,
        body: dto.body.trim(),
        parentId: dto.parentId,
      },
    });

    await this.syncUserProgressTier(userId);
    return comment;
  }

  async getCommunityThread(threadId: string) {
    const thread = await this.prisma.communityThread.findUnique({
      where: { id: threadId },
      include: {
        comments: {
          orderBy: { createdAt: 'asc' },
          include: {
            replies: { orderBy: { createdAt: 'asc' } },
          },
        },
      },
    });
    if (!thread) throw new NotFoundException('Thread not found');

    const roots = thread.comments.filter((c) => !c.parentId);
    return { ...thread, comments: roots };
  }

  async getDiscover(filter?: string, stageBucket?: string) {
    const projects = await this.prisma.project.findMany({
      where: {
        approved: true,
        OR: [
          { source: ProjectSource.CURATED, founderId: { not: null } },
          { source: ProjectSource.DYNAMIC, founderId: { not: null } },
        ],
      },
      include: {
        chain: { select: { slug: true, name: true } },
        category: { select: { slug: true, name: true } },
        founder: {
          select: {
            slug: true,
            name: true,
            photoUrl: true,
            reputationScore: true,
            buildStreakDays: true,
            videoUrl: true,
            verifications: { where: { verified: true }, select: { type: true } },
            videos: { orderBy: { publishedAt: 'desc' }, take: 1 },
          },
        },
        metrics: true,
        simulatedRaises: {
          where: { status: SimulatedRaiseStatus.ACTIVE },
          include: { allocations: true },
        },
        buildPosts: { orderBy: { publishedAt: 'desc' }, take: 1 },
        _count: { select: { followers: true } },
      },
      take: 100,
    });

    const mapped = await Promise.all(
      projects.map(async (p) => {
        const marketCap = p.metrics?.marketCap ? Number(p.metrics.marketCap) : null;
        const effectiveStageRaw = inferProjectLifecycleStage({
          lifecycleStage: p.lifecycleStage,
          isLiveToken: p.isLiveToken,
          dexscreenerUrl: p.dexscreenerUrl,
          contractAddress: p.contractAddress,
          marketCap,
        });
        const listingKind = resolveProjectListingKind({
          source: p.source,
          founderId: p.founderId,
        });
        const effectiveStage = resolveEffectiveLifecycleStage({
          source: p.source,
          founderId: p.founderId,
          lifecycleStage: p.lifecycleStage,
          isLiveToken: p.isLiveToken,
          dexscreenerUrl: p.dexscreenerUrl,
          contractAddress: p.contractAddress,
          marketCap,
        });

        if (
          effectiveStage !== p.lifecycleStage &&
          (effectiveStage === 'LIVE_TRADING' || effectiveStage === 'TOKEN_LAUNCH')
        ) {
          await this.prisma.project.update({
            where: { id: p.id },
            data: {
              lifecycleStage: effectiveStage as ProjectLifecycleStage,
              isLiveToken: true,
            },
          });
        }

        const activeRaise = p.simulatedRaises[0];
        const demand = activeRaise?.allocations.reduce((s, a) => s + Number(a.amountUsd), 0) ?? 0;
        const goalUsd = activeRaise ? Number(activeRaise.goalUsd) : 0;
        const demandPct = goalUsd > 0 ? Math.min(100, Math.round((demand / goalUsd) * 100)) : 0;
        const bucket = getStageBucket(effectiveStage, p.isLiveToken || effectiveStage === 'LIVE_TRADING');
        const latestVideo = p.founder?.videos[0];
        const latestPost = p.buildPosts[0];

        return {
          slug: p.slug,
          name: p.name,
          ticker: p.ticker,
          summary: p.summary,
          logoUrl: p.logoUrl,
          lifecycleStage: effectiveStage,
          stageBucket: bucket,
          journeyProgress: computeJourneyProgress(effectiveStage),
          launchReadiness: p.launchReadiness,
          bubbleScore: Math.max(
            p.bubbleScore,
            p.isLiveToken || effectiveStage === 'LIVE_TRADING'
              ? 200 + Math.round((marketCap ?? 0) / 10_000)
              : p.bubbleScore,
          ),
          followerCount: p._count.followers,
          founderScore: p.founder?.reputationScore ?? 0,
          buildStreakDays: p.founder?.buildStreakDays ?? 0,
          simulatedDemand: demand,
          raiseGoalUsd: goalUsd,
          demandPct,
          marketCap,
          priceUsd: p.metrics?.priceUsd ? Number(p.metrics.priceUsd) : null,
          volume24h: p.metrics?.volume24h ? Number(p.metrics.volume24h) : null,
          category: p.category,
          chain: p.chain,
          founder: p.founder
            ? {
                slug: p.founder.slug,
                name: p.founder.name,
                photoUrl: p.founder.photoUrl,
                reputationScore: p.founder.reputationScore,
                buildStreakDays: p.founder.buildStreakDays,
              }
            : null,
          founderVideoUrl: latestVideo?.url ?? p.founder?.videoUrl ?? null,
          founderVideoTitle: latestVideo?.title ?? 'Founder intro',
          lastUpdateAt: latestPost?.publishedAt ?? p.updatedAt,
          lastUpdateHeadline: latestPost?.headline ?? null,
          isLiveToken: p.isLiveToken || effectiveStage === 'LIVE_TRADING',
          source: p.source,
          listingKind,
          communityValidated: listingKind === 'verified',
          createdAt: p.createdAt,
        };
      }),
    );

    let filtered = mapped;
    if (stageBucket) {
      filtered = mapped.filter((p) => p.stageBucket === stageBucket);
      if (stageBucket === 'IDEA_STAGE') {
        filtered = filtered.filter(
          (p) => p.listingKind === 'founder_os' && p.stageBucket === 'IDEA_STAGE',
        );
      }
    }

    const sorted = [...filtered].sort((a, b) => {
      switch (filter) {
        case 'most_followed':
          return b.followerCount - a.followerCount;
        case 'highest_demand':
          return b.simulatedDemand - a.simulatedDemand;
        case 'launch_ready':
          return b.launchReadiness - a.launchReadiness;
        case 'recently_launched':
          return (b.isLiveToken ? 1 : 0) - (a.isLiveToken ? 1 : 0);
        case 'live_tokens':
          return (b.isLiveToken ? 1 : 0) - (a.isLiveToken ? 1 : 0);
        case 'newest':
          return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
        case 'trending':
        default:
          return b.bubbleScore - a.bubbleScore;
      }
    });

    return sorted;
  }

  async getDiscoverUniverse(options?: {
    stageFilter?: DiscoverUniverseStage | 'all';
    chainSlug?: string;
    timeframe?: DiscoverTimeframe;
  }) {
    const timeframe = options?.timeframe ?? '24h';
    const windowMs = timeframeToMs(timeframe);
    const since = new Date(Date.now() - windowMs);
    const priorSince = new Date(Date.now() - windowMs * 2);
    const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);

    const baseProjects = await this.getDiscover('trending');
    let projects = baseProjects;
    if (options?.chainSlug) {
      projects = projects.filter((p) => p.chain.slug === options.chainSlug);
    }
    if (options?.stageFilter && options.stageFilter !== 'all') {
      projects = projects.filter(
        (p) =>
          resolveDiscoverUniverseStage({
            stageBucket: p.stageBucket,
            createdAt: p.createdAt instanceof Date ? p.createdAt : new Date(p.createdAt as string),
            isLiveToken: p.isLiveToken,
          }) === options.stageFilter,
      );
    }

    const projectIds = projects.map((p) => p.slug);
    const idRows = await this.prisma.project.findMany({
      where: { slug: { in: projectIds } },
      select: { id: true, slug: true },
    });
    const slugToId = Object.fromEntries(idRows.map((r) => [r.slug, r.id]));
    const ids = idRows.map((r) => r.id);

    const [
      tradesWindow,
      tradesPrior,
      buildPosts,
      follows,
      githubEvents,
      threads,
      scoutMarkets,
      scoutReviewsAwaiting,
      newBuilders7d,
      chains,
    ] = await Promise.all([
      this.prisma.paperTrade.groupBy({
        by: ['projectId', 'side'],
        where: { projectId: { in: ids }, createdAt: { gte: since } },
        _sum: { totalUsd: true },
      }),
      this.prisma.paperTrade.groupBy({
        by: ['projectId'],
        where: { projectId: { in: ids }, createdAt: { gte: priorSince, lt: since } },
        _sum: { totalUsd: true },
      }),
      this.prisma.founderBuildPost.groupBy({
        by: ['projectId'],
        where: { projectId: { in: ids }, publishedAt: { gte: since } },
        _count: { id: true },
      }),
      this.prisma.projectFollow.groupBy({
        by: ['projectId'],
        where: { projectId: { in: ids }, createdAt: { gte: since } },
        _count: { id: true },
      }),
      this.prisma.founderEvent.groupBy({
        by: ['projectId'],
        where: {
          projectId: { in: ids },
          createdAt: { gte: since },
          type: { in: [FounderEventType.GITHUB_COMMIT, FounderEventType.DEPLOY_SUCCESS] },
        },
        _count: { id: true },
      }),
      this.prisma.communityThread.groupBy({
        by: ['projectId'],
        where: { projectId: { in: ids }, createdAt: { gte: since } },
        _count: { id: true },
      }),
      this.prisma.scoutMarket.findMany({
        where: { projectId: { in: ids }, status: ScoutMarketStatus.OPEN },
        select: {
          projectId: true,
          yesPoolUsd: true,
          noPoolUsd: true,
          positions: {
            where: { createdAt: { gte: since } },
            select: { amountUsd: true },
          },
        },
      }),
      this.prisma.listingApplication.count({
        where: { status: ListingStatus.COMMUNITY_VOTING },
      }),
      this.prisma.founder.count({ where: { createdAt: { gte: weekAgo } } }),
      this.prisma.chain.findMany({ select: { slug: true, name: true }, orderBy: { name: 'asc' } }),
    ]);

    const mapCount = (rows: { projectId: string | null; _count: { id: number } }[]) => {
      const m: Record<string, number> = {};
      for (const r of rows) {
        if (r.projectId) m[r.projectId] = r._count.id;
      }
      return m;
    };

    const buildMap = mapCount(buildPosts);
    const followMap = mapCount(follows);
    const githubMap = mapCount(githubEvents);
    const threadMap = mapCount(threads);

    const inflowMap: Record<string, number> = {};
    const volumeMap: Record<string, number> = {};
    for (const t of tradesWindow) {
      const amt = Number(t._sum.totalUsd ?? 0);
      volumeMap[t.projectId] = (volumeMap[t.projectId] ?? 0) + amt;
      if (t.side === PaperTradeSide.BUY) {
        inflowMap[t.projectId] = (inflowMap[t.projectId] ?? 0) + amt;
      }
    }

    const priorVolumeMap: Record<string, number> = {};
    for (const t of tradesPrior) {
      priorVolumeMap[t.projectId] = Number(t._sum.totalUsd ?? 0);
    }

    const scoutPoolMap: Record<string, number> = {};
    const scoutStakeMap: Record<string, number> = {};
    for (const m of scoutMarkets) {
      scoutPoolMap[m.projectId] =
        Number(m.yesPoolUsd) + Number(m.noPoolUsd);
      scoutStakeMap[m.projectId] = m.positions.reduce(
        (s, p) => s + Number(p.amountUsd),
        0,
      );
    }

    const enriched = projects.map((p) => {
      const pid = slugToId[p.slug];
      const ddInflow = inflowMap[pid] ?? 0;
      const ddVolume = volumeMap[pid] ?? 0;
      const priorVol = priorVolumeMap[pid] ?? 0;
      const trend = computeTrendDirection(ddVolume, priorVol);
      const universeStage = resolveDiscoverUniverseStage({
        stageBucket: p.stageBucket,
        createdAt: p.createdAt instanceof Date ? p.createdAt : new Date(p.createdAt as string),
        isLiveToken: p.isLiveToken,
      });
      const activityScore = computeDiscoverActivityScore({
        buildPosts: buildMap[pid] ?? 0,
        githubEvents: githubMap[pid] ?? 0,
        tradesInflow: ddInflow,
        tradesVolume: ddVolume,
        followers: followMap[pid] ?? 0,
        scoutStake: scoutStakeMap[pid] ?? 0,
        communitySignals: threadMap[pid] ?? 0,
        bubbleScore: p.bubbleScore,
      });
      const convictionScore = computeDiscoverConvictionScore({
        launchReadiness: p.launchReadiness,
        demandPct: p.demandPct,
        founderScore: p.founderScore,
        followerCount: p.followerCount,
        scoutPoolUsd: scoutPoolMap[pid] ?? 0,
      });

      return {
        ...p,
        universeStage,
        activityScore,
        convictionScore,
        ddInflow24h: ddInflow,
        ddVolumeWindow: ddVolume,
        trendDirection: trend.direction,
        trendPct: trend.pct,
        lastActivityPreview: p.lastUpdateHeadline
          ? {
              source: 'founder' as const,
              text: p.lastUpdateHeadline,
              at: p.lastUpdateAt,
            }
          : null,
        scoutPoolUsd: scoutPoolMap[pid] ?? 0,
      };
    });

    enriched.sort((a, b) => b.activityScore - a.activityScore);

    const totalDdInflow = Object.values(inflowMap).reduce((s, v) => s + v, 0);
    const avgConviction =
      enriched.length > 0
        ? Math.round(
            enriched.reduce((s, p) => s + p.convictionScore, 0) / enriched.length,
          )
        : 0;

    const topInflow = [...enriched]
      .sort((a, b) => b.ddInflow24h - a.ddInflow24h)
      .slice(0, 5)
      .filter((p) => p.ddInflow24h > 0);

    const topOutflow = [...enriched]
      .map((p) => {
        const pid = slugToId[p.slug];
        let out = 0;
        for (const t of tradesWindow) {
          if (t.projectId === pid && t.side === PaperTradeSide.SELL) {
            out += Number(t._sum.totalUsd ?? 0);
          }
        }
        return { ...p, ddOutflow: out };
      })
      .sort((a, b) => b.ddOutflow - a.ddOutflow)
      .slice(0, 3)
      .filter((p) => p.ddOutflow > 0);

    const mostFollowed = [...enriched]
      .sort((a, b) => b.followerCount - a.followerCount)
      .slice(0, 5);

    const activeConversations = enriched
      .filter((p) => p.lastActivityPreview)
      .slice(0, 5);

    return {
      timeframe,
      metrics: {
        activeProjects: baseProjects.length,
        ddInflow24h: totalDdInflow,
        newBuilders7d,
        avgConviction,
        scoutReviewsAwaiting,
      },
      chains,
      projects: enriched,
      sidebar: {
        trending: enriched.slice(0, 5),
        topInflow,
        topOutflow,
        mostFollowed,
        activeConversations,
      },
    };
  }

  async getEcosystemPulse() {
    const [activity, projects, raises] = await Promise.all([
      this.getBuildFeed(12),
      this.getDiscover('trending'),
      this.getDemandHeatmap(),
    ]);

    return {
      recentActivity: activity,
      trendingProjects: projects.slice(0, 8),
      topRaises: raises.slice(0, 6),
      liveTokenCount: projects.filter((p) => p.isLiveToken).length,
      buildingCount: projects.filter((p) => p.stageBucket === 'BUILDING').length,
      ideaCount: projects.filter((p) => p.stageBucket === 'IDEA_STAGE').length,
    };
  }

  async getEconomyStats() {
    const [cashAgg, allocAgg, grants, fees, lotteryPaid] = await Promise.all([
      this.prisma.paperPortfolio.aggregate({ _sum: { cashBalance: true } }),
      this.prisma.raiseAllocation.aggregate({ _sum: { amountUsd: true } }),
      this.prisma.virtualEconomyEvent.aggregate({
        where: { type: 'INITIAL_GRANT' },
        _sum: { amountUsd: true },
      }),
      this.prisma.virtualEconomyEvent.aggregate({
        where: { type: 'TOP_UP_FEE' },
        _sum: { amountUsd: true },
      }),
      this.prisma.virtualEconomyEvent.aggregate({
        where: { type: 'ENGAGEMENT_LOTTERY' },
        _sum: { amountUsd: true },
      }),
    ]);

    const cashInCirculation = Number(cashAgg._sum.cashBalance ?? 0);
    const inRaises = Number(allocAgg._sum.amountUsd ?? 0);
    const totalMinted = Number(grants._sum.amountUsd ?? 0);
    const topUpFees = Number(fees._sum.amountUsd ?? 0);
    const engagementLotteryPaid = Number(lotteryPaid._sum.amountUsd ?? 0);

    return {
      cashInCirculation,
      allocatedToRaises: inRaises,
      totalVirtualSupply: cashInCirculation + inRaises,
      totalMinted,
      topUpFeesCollected: topUpFees,
      engagementLotteryPaidUsd: engagementLotteryPaid,
      startingCashUsd: STARTING_CASH_USD,
      restrictedThresholdUsd: RESTRICTED_CASH_THRESHOLD_USD,
      topUpFeeUsd: TOP_UP_FEE_USD,
    };
  }

  /** Migrate legacy videoUrl → FounderVideo rows and compute levels for all founders */
  async syncAllFounders() {
    const founders = await this.prisma.founder.findMany({
      where: { videoUrl: { not: null } },
      select: { id: true, videoUrl: true },
    });

    let migrated = 0;
    for (const f of founders) {
      const exists = await this.prisma.founderVideo.count({
        where: { founderId: f.id, type: 'INTRODUCTION' },
      });
      if (!exists && f.videoUrl) {
        await this.prisma.founderVideo.create({
          data: {
            founderId: f.id,
            type: 'INTRODUCTION',
            title: 'Founder introduction',
            url: f.videoUrl,
            durationMin: 10,
          },
        });
        migrated++;
      }
      await this.syncPresenceLevel(f.id);
    }
    return { migrated, total: founders.length };
  }

  private async getDemandRank(projectId: string, totalDemand: number) {
    const raises = await this.prisma.simulatedRaise.findMany({
      where: { status: SimulatedRaiseStatus.ACTIVE },
      include: { allocations: true },
    });
    const ranked = raises
      .map((r) => ({
        projectId: r.projectId,
        total: r.allocations.reduce((s, a) => s + Number(a.amountUsd), 0),
      }))
      .sort((a, b) => b.total - a.total);
    const idx = ranked.findIndex((r) => r.projectId === projectId);
    return idx >= 0 ? idx + 1 : null;
  }

  private async refreshLaunchReadiness(projectId: string) {
    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      include: {
        founder: { include: { videos: true } },
        simulatedRaises: {
          where: { status: SimulatedRaiseStatus.ACTIVE },
          include: { allocations: true },
        },
        _count: { select: { followers: true, buildPosts: true } },
        socials: true,
      },
    });
    if (!project) return 0;

    const activeRaise = project.simulatedRaises[0];
    const totalDemand = activeRaise?.allocations.reduce((s, a) => s + Number(a.amountUsd), 0) ?? 0;
    const score = computeLaunchReadiness({
      videoCount: project.founder?.videos.length ?? 0,
      buildPostCount: project._count.buildPosts,
      followerCount: project._count.followers,
      simulatedDemandUsd: totalDemand,
      goalUsd: activeRaise ? Number(activeRaise.goalUsd) : 0,
      githubConnected: Boolean(project.socials?.githubUrl || project.founder?.githubUrl),
      hasActiveRaise: Boolean(activeRaise),
    });

    if (score !== project.launchReadiness) {
      await this.prisma.project.update({
        where: { id: projectId },
        data: { launchReadiness: score },
      });
    }
    return score;
  }

  private computeLaunchpadRequirements(
    project: {
      founder?: { videos: unknown[] } | null;
      buildPosts?: unknown[];
      _count?: { followers: number };
    },
    totalDemand: number,
    launchReadiness: number,
  ) {
    const checks = {
      founderVideo: (project.founder?.videos.length ?? 0) >= 1,
      buildLogs: (project.buildPosts?.length ?? 0) >= 2,
      demandValidated: totalDemand >= 10_000,
      simulatedRaiseComplete: totalDemand >= 50_000,
      communityScore: (project._count?.followers ?? 0) >= 5,
    };
    const unlocked =
      checks.founderVideo &&
      checks.buildLogs &&
      checks.demandValidated &&
      launchReadiness >= 60;
    return { unlocked, checks, launchReadiness };
  }

  private async refreshBubbleScore(projectId: string) {
    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      include: {
        simulatedRaises: {
          where: { status: SimulatedRaiseStatus.ACTIVE },
          include: { allocations: true },
        },
        _count: { select: { followers: true, buildPosts: true } },
      },
    });
    if (!project) return;

    const demand = project.simulatedRaises[0]?.allocations.reduce(
      (s, a) => s + Number(a.amountUsd),
      0,
    ) ?? 0;
    const bubbleScore = Math.min(
      1000,
      project._count.followers * 3 +
        project._count.buildPosts * 5 +
        Math.round(demand / 1000) +
        project.launchReadiness,
    );

    await this.prisma.project.update({
      where: { id: projectId },
      data: { bubbleScore },
    });
  }

  private async syncUserProgressTier(userId: string, forceTier?: UserProgressTier) {
    if (forceTier) {
      await this.prisma.user.update({ where: { id: userId }, data: { progressTier: forceTier } });
      return;
    }

    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      include: {
        founder: true,
        paperTrades: { take: 1 },
        demandPollVotes: { take: 1 },
        raiseAllocations: { take: 1 },
      },
    });
    if (!user) return;

    let tier = user.progressTier;
    if (user.founder) {
      tier =
        user.founder.presenceLevel === 'PROVEN_FOUNDER'
          ? UserProgressTier.PROVEN_FOUNDER
          : user.progressTier;
    } else if (user.raiseAllocations.length > 0 || user.demandPollVotes.length > 0) {
      tier = UserProgressTier.COMMUNITY_CONTRIBUTOR;
    } else if (user.paperTrades.length > 0) {
      tier = UserProgressTier.TRADER;
    } else {
      tier = UserProgressTier.EXPLORER;
    }

    if (tier !== user.progressTier) {
      await this.prisma.user.update({ where: { id: userId }, data: { progressTier: tier } });
    }
  }

  private async computeFounderStats(founderId: string) {
    const [videos, buildPosts, roadmapDone, raises, founder] = await Promise.all([
      this.prisma.founderVideo.count({ where: { founderId } }),
      this.prisma.founderBuildPost.count({ where: { founderId } }),
      this.prisma.projectRoadmapItem.count({
        where: { project: { founderId }, status: 'DONE' },
      }),
      this.prisma.raiseAllocation.aggregate({
        where: { raise: { project: { founderId } } },
        _sum: { amountUsd: true },
      }),
      this.prisma.founder.findUnique({ where: { id: founderId } }),
    ]);

    const hasPublicQa = (await this.prisma.founderVideo.count({
      where: { founderId, type: 'QA' },
    })) > 0;

    const githubConnected = Boolean(founder?.githubUrl || founder?.githubUsername);
    const daysBuilding = founder?.publicBuildingSince
      ? Math.floor((Date.now() - founder.publicBuildingSince.getTime()) / 86400000)
      : founder
        ? Math.floor((Date.now() - founder.createdAt.getTime()) / 86400000)
        : 0;

    const shippedProducts = roadmapDone >= 2 ? 1 : 0;

    return {
      counts: { videos, buildPosts, roadmapDone, githubConnected, buildStreakDays: founder?.buildStreakDays ?? 0 },
      reputationInput: {
        videoCount: videos,
        buildPostCount: buildPosts,
        githubConnected,
        hasPublicQa,
        roadmapDoneCount: roadmapDone,
        simulatedDemandUsd: Number(raises._sum.amountUsd ?? 0),
        daysBuilding,
        buildStreakDays: founder?.buildStreakDays ?? 0,
      },
      presenceInput: {
        videoCount: videos,
        buildPostCount: buildPosts,
        githubConnected,
        hasPublicQa,
        roadmapDoneCount: roadmapDone,
        shippedProducts,
      },
    };
  }

  private async syncPresenceLevel(founderId: string) {
    const stats = await this.computeFounderStats(founderId);
    const level = computePresenceLevel(stats.presenceInput);
    const reputation = computeFounderReputation(stats.reputationInput);
    await this.prisma.founder.update({
      where: { id: founderId },
      data: { presenceLevel: level, reputationScore: reputation.total },
    });
  }

  private async updateBuildStreak(founderId: string) {
    const founder = await this.prisma.founder.findUnique({ where: { id: founderId } });
    if (!founder) return 0;

    const now = new Date();
    const last = founder.lastBuildPostAt;
    let streak = founder.buildStreakDays;
    if (!last) {
      streak = 1;
    } else {
      const daysSince = Math.floor((now.getTime() - last.getTime()) / 86400000);
      streak = daysSince <= 7 ? streak + 1 : 1;
    }

    await this.prisma.founder.update({
      where: { id: founderId },
      data: {
        buildStreakDays: streak,
        lastBuildPostAt: now,
        publicBuildingSince: founder.publicBuildingSince ?? now,
      },
    });
    return streak;
  }

  private buildHeatmap(posts: { publishedAt: Date }[]) {
    const map = new Map<string, number>();
    for (const p of posts) {
      const key = p.publishedAt.toISOString().slice(0, 10);
      map.set(key, (map.get(key) ?? 0) + 1);
    }
    return [...map.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-365)
      .map(([date, count]) => ({ date, count }));
  }

  private tallyPollVotes(votes: { optionKey: string }[], options: unknown) {
    const opts = Array.isArray(options) ? options : [];
    const counts: Record<string, number> = {};
    for (const o of opts) {
      const key = typeof o === 'string' ? o : String(o);
      counts[key] = 0;
    }
    for (const v of votes) {
      counts[v.optionKey] = (counts[v.optionKey] ?? 0) + 1;
    }
    return counts;
  }

  private mapFounder(founder: Prisma.FounderGetPayload<{ include: typeof founderRoomInclude }>) {
    return {
      id: founder.id,
      slug: founder.slug,
      name: founder.name,
      bio: founder.bio,
      photoUrl: founder.photoUrl,
      linkedInUrl: founder.linkedInUrl,
      twitterUrl: founder.twitterUrl,
      githubUrl: founder.githubUrl,
      githubUsername: founder.githubUsername,
      websiteUrl: founder.websiteUrl,
      journeyStage: founder.journeyStage,
      buildStreakDays: founder.buildStreakDays,
      reputationScore: founder.reputationScore,
      projects: founder.projects,
      videos: founder.videos,
      buildPosts: founder.buildPosts,
    };
  }

  private mapFounderBrief(
    founder: Prisma.FounderGetPayload<{ include: { videos: true } }>,
  ) {
    return {
      slug: founder.slug,
      name: founder.name,
      photoUrl: founder.photoUrl,
      presenceLevel: founder.presenceLevel,
      reputationScore: founder.reputationScore,
      journeyStage: founder.journeyStage,
      twitterUrl: founder.twitterUrl,
      githubUrl: founder.githubUrl,
    };
  }

  async buildAllocationLeaderboard(raiseId: string) {
    const rows = await this.prisma.raiseAllocation.findMany({
      where: { raiseId, amountUsd: { gt: 0 } },
      include: { user: { select: { id: true, name: true, email: true } } },
      orderBy: { amountUsd: 'desc' },
      take: 50,
    });
    return rows.map((r, i) => ({
      rank: i + 1,
      userId: r.userId,
      displayName: formatPublicAccountLabel(r.user.name, r.user.email),
      amountUsd: Number(r.amountUsd),
      burnedUsd: Number(r.burnedUsd),
      walletAddress: r.walletAddress,
      slotReserved: r.slotReserved,
    }));
  }

  async getRaiseParticipants(raiseId: string) {
    const raise = await this.prisma.simulatedRaise.findUnique({
      where: { id: raiseId },
      include: { project: { select: { slug: true, name: true } } },
    });
    if (!raise) throw new NotFoundException('Raise not found');
    const leaderboard = await this.buildAllocationLeaderboard(raiseId);
    const totalAllocated = leaderboard.reduce((s, r) => s + r.amountUsd, 0);
    return {
      raiseId,
      project: raise.project,
      goalUsd: Number(raise.goalUsd),
      totalAllocated,
      communityTokenPercent: raise.communityTokenPercent,
      slotsLocked: raise.slotsLocked,
      participants: leaderboard,
    };
  }

  async exportRaiseParticipants(userId: string, raiseId: string) {
    const raise = await this.prisma.simulatedRaise.findUnique({
      where: { id: raiseId },
      include: { project: { include: { founder: true } } },
    });
    if (!raise) throw new NotFoundException('Raise not found');
    if (raise.project.founder?.userId !== userId) {
      throw new ForbiddenException('Only the project founder can export participants');
    }

    const rows = await this.prisma.raiseAllocation.findMany({
      where: { raiseId, slotReserved: true, amountUsd: { gt: 0 } },
      include: { user: { select: { id: true, name: true, email: true } } },
      orderBy: { amountUsd: 'desc' },
    });

    const participants = buildParticipantExport(
      rows.map((r) => ({
        userId: r.userId,
        displayName: formatPublicAccountLabel(r.user.name, r.user.email),
        amountUsd: Number(r.amountUsd),
        burnedUsd: Number(r.burnedUsd),
        walletAddress: r.walletAddress,
        slotReserved: r.slotReserved,
      })),
      raise.communityTokenPercent,
    );

    return {
      projectName: raise.project.name,
      raiseId,
      communityTokenPercent: raise.communityTokenPercent,
      totalAllocated: participants.reduce((s, p) => s + p.amountUsd, 0),
      participantCount: participants.length,
      tokenLaunchFeePercent: TOKEN_LAUNCH_FEE_PERCENT,
      participants,
      csv: [
        'displayName,walletAddress,amountUsd,burnedUsd,tokenSharePercent',
        ...participants.map(
          (p) =>
            `"${p.displayName}","${p.walletAddress ?? ''}",${p.amountUsd},${p.burnedUsd},${p.allocationSharePercent}`,
        ),
      ].join('\n'),
    };
  }

  async lockRaiseSlots(userId: string, raiseId: string) {
    const raise = await this.prisma.simulatedRaise.findUnique({
      where: { id: raiseId },
      include: { project: { include: { founder: true } } },
    });
    if (!raise) throw new NotFoundException('Raise not found');
    if (raise.project.founder?.userId !== userId) {
      throw new ForbiddenException('Only the project founder can lock slots');
    }

    await this.prisma.simulatedRaise.update({
      where: { id: raiseId },
      data: { slotsLocked: true, status: SimulatedRaiseStatus.COMPLETED },
    });

    if (raise.project.founder) {
      await this.events.emit({
        founderId: raise.project.founder.id,
        projectId: raise.projectId,
        userId,
        type: FounderEventType.RAISE_ALLOCATION,
        source: 'raise-room',
        title: 'Raise Room slots locked — ready for token distribution',
        payload: { raiseId, action: 'lock_slots' },
      });
    }

    return { success: true, message: 'ICO slots locked. Export participant list for one-click distribution.' };
  }

  async getPlatformEconomy() {
    const solanaTreasury = await resolveSolanaTreasuryAddress(this.prisma);
    const burned = await this.prisma.virtualEconomyEvent.aggregate({
      where: { type: 'PAPER_BURN' },
      _sum: { amountUsd: true },
    });
    return {
      raiseAllocationFeePercent: RAISE_ALLOCATION_FEE_PERCENT,
      tokenLaunchFeePercent: TOKEN_LAUNCH_FEE_PERCENT,
      weeklyStipendUsd: WEEKLY_STIPEND_USD,
      rechargeFeeUsd: TOP_UP_FEE_USD,
      restrictedCashThresholdUsd: RESTRICTED_CASH_THRESHOLD_USD,
      totalPaperBurned: Number(burned._sum.amountUsd ?? 0),
      treasury: {
        solana: solanaTreasury,
        evm: (await this.prisma.platformTreasury.findUnique({ where: { id: 'default' } }))
          ?.evmTreasuryAddress ?? null,
      },
      paperDollarSinks: [
        'Raise Room allocations (1% burned on commit)',
        'Agent marketplace installs',
        'Founder bounties',
        'Scout votes',
        'Project boosts',
        'Token launch fee (0.2%)',
      ],
    };
  }

  async updatePlatformTreasury(
    userId: string,
    input: { solanaTreasuryAddress?: string; evmTreasuryAddress?: string },
  ) {
    const user = await this.prisma.user.findUnique({ where: { id: userId } });
    if (user?.role !== UserRole.ADMIN) {
      throw new ForbiddenException('Admin account required');
    }

    const treasury = await this.prisma.platformTreasury.upsert({
      where: { id: 'default' },
      create: {
        id: 'default',
        solanaTreasuryAddress: input.solanaTreasuryAddress,
        evmTreasuryAddress: input.evmTreasuryAddress,
        updatedByUserId: userId,
      },
      update: {
        ...(input.solanaTreasuryAddress !== undefined
          ? { solanaTreasuryAddress: input.solanaTreasuryAddress }
          : {}),
        ...(input.evmTreasuryAddress !== undefined ? { evmTreasuryAddress: input.evmTreasuryAddress } : {}),
        updatedByUserId: userId,
      },
    });

    return { success: true, treasury };
  }

  async listTopUpPayments(adminUserId: string, limit = 50) {
    const user = await this.prisma.user.findUnique({ where: { id: adminUserId } });
    if (user?.role !== UserRole.ADMIN) {
      throw new ForbiddenException('Admin account required');
    }

    const take = Math.min(Math.max(limit, 1), 100);
    const payments = await this.prisma.topUpPayment.findMany({
      orderBy: { createdAt: 'desc' },
      take,
      include: {
        user: { select: { id: true, email: true, name: true } },
      },
    });

    return payments.map((p) => ({
      id: p.id,
      reference: p.reference,
      userId: p.userId,
      userEmail: p.user.email,
      userName: p.user.name,
      asset: p.asset,
      amountUsd: Number(p.amountUsd),
      treasuryAddress: p.treasuryAddress,
      payerAddress: p.payerAddress,
      txSignature: p.txSignature,
      status: p.status,
      confirmedAt: p.confirmedAt?.toISOString() ?? null,
      createdAt: p.createdAt.toISOString(),
    }));
  }

  async listScoutMarkets(slug: string, viewerUserId?: string) {
    return this.predictionMarkets.listForProject(slug, viewerUserId);
  }

  async stakeScoutMarket(
    userId: string,
    marketId: string,
    side: 'YES' | 'NO',
    amountUsd: number,
  ) {
    return this.predictionMarkets.stake(userId, marketId, side, amountUsd);
  }

  async askFounderBrain(slug: string, question: string) {
    const project = await this.prisma.project.findUnique({
      where: { slug },
      include: {
        founder: {
          include: {
            user: { include: { builderSettings: true } },
            buildQueueItems: {
              where: { status: { notIn: ['DONE', 'DISMISSED'] } },
              orderBy: { updatedAt: 'desc' },
              take: 5,
            },
          },
        },
        buildPosts: { orderBy: { publishedAt: 'desc' }, take: 3 },
        simulatedRaises: {
          where: { status: SimulatedRaiseStatus.ACTIVE },
          include: { allocations: true },
          take: 1,
        },
        _count: { select: { followers: true } },
      },
    });
    if (!project) throw new NotFoundException('Project not found');

    const activeRaise = project.simulatedRaises[0];
    const totalAllocated =
      activeRaise?.allocations.reduce((s, a) => s + Number(a.amountUsd), 0) ?? 0;

    const ctx: FounderBrainContext = {
      projectName: project.name,
      lifecycleStage: project.lifecycleStage,
      launchReadiness: project.launchReadiness,
      followerCount: project._count.followers,
      raiseAllocated: totalAllocated,
      raiseGoal: activeRaise ? Number(activeRaise.goalUsd) : 0,
      lastBuildHeadlines: project.buildPosts.map((p) => p.headline),
      openTasks: project.founder?.buildQueueItems.map((t) => t.title) ?? [],
      currentGoal: project.founder?.user?.builderSettings?.currentGoalFocus ?? undefined,
    };

    const contextBlock = buildFounderBrainContextBlock(ctx);
    const founderUserId = project.founder?.userId;

    let answer: string | null = null;
    if (founderUserId) {
      answer = await this.builder.tryAiCompletion(
        founderUserId,
        FOUNDER_BRAIN_SYSTEM,
        `Project context:\n${contextBlock}\n\nQuestion: ${question.trim()}`,
      );
    }

    if (!answer) {
      answer = this.ruleBasedFounderBrainAnswer(ctx, question);
    }

    return {
      question: question.trim(),
      answer,
      source: answer.includes('has not published') ? 'context' : founderUserId ? 'ai_or_context' : 'context',
      starterQuestions: [
        'What changed this week?',
        'Why does this token exist?',
        'When is launch?',
        'What is the founder building now?',
        'What risks remain?',
      ],
    };
  }

  private ruleBasedFounderBrainAnswer(ctx: FounderBrainContext, question: string): string {
    const q = question.toLowerCase().trim();
    if (q.includes('launch') || q.includes('when')) {
      if (ctx.raiseGoal > 0 && ctx.raiseAllocated >= ctx.raiseGoal * 0.8) {
        return `${ctx.projectName} is at ${ctx.launchReadiness}% launch readiness with Raise Room at $${ctx.raiseAllocated.toLocaleString()} / $${ctx.raiseGoal.toLocaleString()}. The founder has not published a firm launch date yet — follow build logs for updates.`;
      }
      return `${ctx.projectName} is in ${ctx.lifecycleStage.replace(/_/g, ' ')} stage (${ctx.launchReadiness}% launch ready). No confirmed launch date is published yet.`;
    }
    if (q.includes('token') || q.includes('why')) {
      return `${ctx.projectName} is building in public on Founder OS. Token details depend on the founder's Raise Room and roadmap — check the Raise Room tab for allocation momentum and community token %.`;
    }
    if (q.includes('building') || q.includes('now') || q.includes('goal')) {
      if (ctx.currentGoal) {
        return `Current focus: ${ctx.currentGoal}.${ctx.openTasks.length ? ` Open queue: ${ctx.openTasks.slice(0, 3).join('; ')}.` : ''}`;
      }
      if (ctx.openTasks.length) {
        return `Open build queue: ${ctx.openTasks.slice(0, 4).join('; ')}.`;
      }
      return `The founder has not published a current goal in Founder Copilot yet. Recent builds: ${ctx.lastBuildHeadlines.length ? ctx.lastBuildHeadlines.join('; ') : 'none yet'}.`;
    }
    if (q.includes('risk')) {
      return `Public signals: ${ctx.launchReadiness}% launch readiness, ${ctx.followerCount} followers.${ctx.raiseGoal > 0 ? ` Raise at ${Math.round((ctx.raiseAllocated / ctx.raiseGoal) * 100)}% of goal.` : ' No active Raise Room.'} Review build logs and community threads for detailed risk assessment.`;
    }
    if (q.includes('week') || q.includes('changed')) {
      return ctx.lastBuildHeadlines.length
        ? `Recent build headlines: ${ctx.lastBuildHeadlines.join(' · ')}. Stage: ${ctx.lifecycleStage.replace(/_/g, ' ')}.`
        : `No build posts published recently. Project stage: ${ctx.lifecycleStage.replace(/_/g, ' ')}.`;
    }
    return `Based on published data: ${ctx.projectName} is ${ctx.launchReadiness}% launch ready with ${ctx.followerCount} followers.${ctx.raiseGoal > 0 ? ` Raise Room: $${ctx.raiseAllocated.toLocaleString()} / $${ctx.raiseGoal.toLocaleString()}.` : ''} Ask about launch timing, current build focus, or risks for more detail.`;
  }
}
