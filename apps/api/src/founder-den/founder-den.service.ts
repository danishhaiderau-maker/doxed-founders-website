import { Injectable, NotFoundException, ForbiddenException, BadRequestException } from '@nestjs/common';
import {
  Prisma,
  SimulatedRaiseStatus,
  ProjectLifecycleStage,
  UserProgressTier,
  FounderApplicationStatus,
  BountyStatus,
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
  inferProjectLifecycleStage,
  getStageBucket,
  computeJourneyProgress,
  POINTS,
  FOUNDER_LAUNCH_REPUTATION_POINTS,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';
import { NotificationsService } from '../notifications/notifications.service';
import { FounderOsService } from '../founder-os/founder-os.service';
import { NotificationType } from '@prisma/client';

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
      lifecycleStage: project.lifecycleStage,
      launchReadiness,
      plannedLaunchDate: project.plannedLaunchDate,
      launchRequestedAt: project.launchRequestedAt,
      isLiveToken: project.isLiveToken,
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
            durationDays: activeRaise.durationDays,
            plannedLaunchDate: activeRaise.plannedLaunchDate,
            status: activeRaise.status,
            startsAt: activeRaise.startsAt,
            endsAt: activeRaise.endsAt,
            totalAllocated,
            allocatorCount: activeRaise.allocations.length,
            convictionScore: Math.min(
              100,
              Math.round((totalAllocated / Number(activeRaise.goalUsd || 1)) * 100),
            ),
          }
        : null,
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
    await this.points.award(userId, POINTS.FOUNDER_BUILD_POST);

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
    await this.points.award(userId, POINTS.FOUNDER_VIDEO);
    return video;
  }

  async allocateToRaise(userId: string, raiseId: string, amountUsd: number) {
    if (amountUsd < 1) throw new BadRequestException('Minimum allocation is $1');

    const raise = await this.prisma.simulatedRaise.findUnique({
      where: { id: raiseId },
      include: { allocations: true },
    });
    if (!raise || raise.status !== SimulatedRaiseStatus.ACTIVE) {
      throw new BadRequestException('Raise is not active');
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
    if (amountUsd - existingAmt > cash) {
      throw new BadRequestException('Insufficient virtual cash for this allocation');
    }

    await this.prisma.$transaction(async (tx) => {
      await tx.raiseAllocation.upsert({
        where: { raiseId_userId: { raiseId, userId } },
        create: { raiseId, userId, amountUsd: new Prisma.Decimal(amountUsd) },
        update: { amountUsd: new Prisma.Decimal(amountUsd) },
      });

      const delta = amountUsd - existingAmt;
      if (delta !== 0) {
        await tx.paperPortfolio.update({
          where: { userId },
          data: { cashBalance: { increment: -delta } },
        });
        await tx.virtualEconomyEvent.create({
          data: {
            userId,
            type: delta > 0 ? 'RAISE_ALLOCATE' : 'RAISE_DEALLOCATE',
            amountUsd: new Prisma.Decimal(Math.abs(delta)),
            note: `Simulated raise ${raiseId}`,
          },
        });
      }
    });

    await this.syncUserProgressTier(userId);
    await this.refreshLaunchReadiness(raise.projectId);
    await this.points.awardOnce(userId, `RAISE_ALLOCATE:${raiseId}`, POINTS.RAISE_ALLOCATE);

    const followerCount = await this.prisma.projectFollow.count({ where: { projectId: raise.projectId } });
    await this.founderOs.recordEarlyScout(userId, raise.projectId, amountUsd, followerCount);

    return { success: true, amountUsd };
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

    const baseSlug = slugify(dto.projectName);
    let slug = baseSlug;
    let n = 1;
    while (await this.prisma.project.findUnique({ where: { slug } })) {
      slug = `${baseSlug}-${n++}`;
    }

    const founderSlug = slugify(dto.projectName + '-founder').slice(0, 48);
    const user = await this.prisma.user.findUnique({ where: { id: userId } });
    const founderName = user?.name ?? dto.projectName;

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
          name: dto.projectName.trim(),
          ticker: dto.projectName.slice(0, 6).toUpperCase().replace(/[^A-Z0-9]/g, '') || 'IDEA',
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
          projectName: dto.projectName.trim(),
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
      dto.projectName,
    );
    if (awarded) {
      await this.notifications.notifyUser(userId, {
        type: NotificationType.POINTS_EARNED,
        title: `+${FOUNDER_LAUNCH_REPUTATION_POINTS} reputation points`,
        body: `Your project "${dto.projectName}" is live on Founder OS.`,
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
      await this.points.award(userId, POINTS.FOUNDER_COMMUNITY_POST);
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
      where: { approved: true, founderId: { not: null } },
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
        const effectiveStage = inferProjectLifecycleStage({
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
          createdAt: p.createdAt,
        };
      }),
    );

    let filtered = mapped;
    if (stageBucket) {
      filtered = mapped.filter((p) => p.stageBucket === stageBucket);
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
}
