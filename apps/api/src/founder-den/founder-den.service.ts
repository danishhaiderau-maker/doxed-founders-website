import { Injectable, NotFoundException, ForbiddenException, BadRequestException } from '@nestjs/common';
import { Prisma, SimulatedRaiseStatus } from '@prisma/client';
import {
  computeFounderReputation,
  computePresenceLevel,
  JOURNEY_STAGES,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

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

@Injectable()
export class FounderDenService {
  constructor(private readonly prisma: PrismaService) {}

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

  async getProjectRoom(slug: string) {
    const project = await this.prisma.project.findFirst({
      where: { slug, approved: true },
      include: {
        chain: { select: { slug: true, name: true } },
        category: { select: { slug: true, name: true } },
        founder: {
          include: {
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
      },
    });

    if (!project) throw new NotFoundException('Project not found');

    const activeRaise = project.simulatedRaises.find((r) => r.status === SimulatedRaiseStatus.ACTIVE);
    const totalAllocated = activeRaise
      ? activeRaise.allocations.reduce((s, a) => s + Number(a.amountUsd), 0)
      : 0;

    return {
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
      metrics: project.metrics,
      socials: project.socials,
      founder: project.founder ? this.mapFounderBrief(project.founder) : null,
      videos: project.founder?.videos ?? [],
      buildPosts: project.buildPosts,
      roadmap: project.roadmapItems,
      activeRaise: activeRaise
        ? {
            id: activeRaise.id,
            goalUsd: Number(activeRaise.goalUsd),
            tokenAllocation: activeRaise.tokenAllocation,
            durationDays: activeRaise.durationDays,
            status: activeRaise.status,
            startsAt: activeRaise.startsAt,
            endsAt: activeRaise.endsAt,
            totalAllocated,
            allocatorCount: activeRaise.allocations.length,
          }
        : null,
      demandPolls: project.demandPolls.map((p) => ({
        id: p.id,
        type: p.type,
        question: p.question,
        options: p.options,
        voteCounts: this.tallyPollVotes(p.votes, p.options as string[]),
      })),
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

    const existing = raise.allocations.find((a) => a.userId === userId);
    const existingAmt = existing ? Number(existing.amountUsd) : 0;
    const cash = Number(portfolio.cashBalance);
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
      }
    });

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

    return { success: true };
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
