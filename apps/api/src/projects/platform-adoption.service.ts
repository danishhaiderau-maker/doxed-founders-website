import { Injectable } from '@nestjs/common';
import { FounderEventType } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

export type AdoptionDayPoint = {
  date: string;
  tokensIn: number;
  tokensOut: number;
  aiCalls: number;
  ddollarVolume: number;
  githubEvents: number;
  buildPosts: number;
};

export type AdoptionProjectRow = {
  slug: string;
  name: string;
  ticker: string;
  tokensIn: number;
  tokensOut: number;
  aiCalls: number;
  ddollarVolume: number;
  githubEvents: number;
  buildPosts: number;
  activityScore: number;
  bubbleScore: number;
  launchReadiness: number;
};

type ProjectAgg = {
  tokensIn: number;
  tokensOut: number;
  aiCalls: number;
  ddollarVolume: number;
  githubEvents: number;
  buildPosts: number;
};

@Injectable()
export class PlatformAdoptionService {
  constructor(private readonly prisma: PrismaService) {}

  async recordAiUsage(input: {
    userId: string;
    provider: string;
    source: string;
    promptTokens: number;
    completionTokens: number;
    projectId?: string | null;
  }) {
    if (input.promptTokens <= 0 && input.completionTokens <= 0) return;
    await this.prisma.aiTokenUsageLog.create({
      data: {
        userId: input.userId,
        projectId: input.projectId ?? null,
        provider: input.provider,
        source: input.source,
        promptTokens: input.promptTokens,
        completionTokens: input.completionTokens,
      },
    });
  }

  async getAdoptionMetrics(days = 14) {
    const windowDays = Math.min(30, Math.max(7, days));
    const since = new Date();
    since.setUTCHours(0, 0, 0, 0);
    since.setUTCDate(since.getUTCDate() - (windowDays - 1));

    const dayKeys: string[] = [];
    for (let i = 0; i < windowDays; i++) {
      const d = new Date(since);
      d.setUTCDate(since.getUTCDate() + i);
      dayKeys.push(d.toISOString().slice(0, 10));
    }

    const emptyDay = (date: string): AdoptionDayPoint => ({
      date,
      tokensIn: 0,
      tokensOut: 0,
      aiCalls: 0,
      ddollarVolume: 0,
      githubEvents: 0,
      buildPosts: 0,
    });

    const seriesMap = new Map<string, AdoptionDayPoint>(
      dayKeys.map((date) => [date, emptyDay(date)]),
    );

    const [tokenLogs, trades, githubEvents, buildPosts, projects] = await Promise.all([
      this.prisma.aiTokenUsageLog.findMany({
        where: { createdAt: { gte: since } },
        select: {
          promptTokens: true,
          completionTokens: true,
          createdAt: true,
          projectId: true,
        },
      }),
      this.prisma.paperTrade.findMany({
        where: { createdAt: { gte: since } },
        select: { totalUsd: true, createdAt: true, projectId: true },
      }),
      this.prisma.founderEvent.findMany({
        where: {
          createdAt: { gte: since },
          type: { in: [FounderEventType.GITHUB_COMMIT, FounderEventType.DEPLOY_SUCCESS] },
        },
        select: { createdAt: true, projectId: true },
      }),
      this.prisma.founderBuildPost.findMany({
        where: { publishedAt: { gte: since } },
        select: { publishedAt: true, projectId: true },
      }),
      this.prisma.project.findMany({
        where: { approved: true },
        select: {
          id: true,
          slug: true,
          name: true,
          ticker: true,
          bubbleScore: true,
          launchReadiness: true,
        },
        take: 200,
      }),
    ]);

    for (const log of tokenLogs) {
      const date = log.createdAt.toISOString().slice(0, 10);
      const row = seriesMap.get(date);
      if (!row) continue;
      row.tokensIn += log.promptTokens;
      row.tokensOut += log.completionTokens;
      row.aiCalls += 1;
    }

    for (const t of trades) {
      const date = t.createdAt.toISOString().slice(0, 10);
      const row = seriesMap.get(date);
      if (!row) continue;
      row.ddollarVolume += Number(t.totalUsd ?? 0);
    }

    for (const e of githubEvents) {
      const date = e.createdAt.toISOString().slice(0, 10);
      const row = seriesMap.get(date);
      if (row) row.githubEvents += 1;
    }

    for (const p of buildPosts) {
      const date = p.publishedAt.toISOString().slice(0, 10);
      const row = seriesMap.get(date);
      if (row) row.buildPosts += 1;
    }

    const series = dayKeys.map((date) => seriesMap.get(date)!);

    const projectAgg = new Map<string, ProjectAgg>();
    for (const p of projects) {
      projectAgg.set(p.id, {
        tokensIn: 0,
        tokensOut: 0,
        aiCalls: 0,
        ddollarVolume: 0,
        githubEvents: 0,
        buildPosts: 0,
      });
    }

    const touch = (projectId: string | null | undefined, update: (row: ProjectAgg) => void) => {
      if (!projectId) return;
      const row = projectAgg.get(projectId);
      if (row) update(row);
    };

    for (const log of tokenLogs) {
      touch(log.projectId, (r) => {
        r.tokensIn += log.promptTokens;
        r.tokensOut += log.completionTokens;
        r.aiCalls += 1;
      });
    }
    for (const t of trades) {
      touch(t.projectId, (r) => {
        r.ddollarVolume += Number(t.totalUsd ?? 0);
      });
    }
    for (const e of githubEvents) {
      touch(e.projectId, (r) => {
        r.githubEvents += 1;
      });
    }
    for (const p of buildPosts) {
      touch(p.projectId, (r) => {
        r.buildPosts += 1;
      });
    }

    const projectRows: AdoptionProjectRow[] = projects
      .map((p) => {
        const agg = projectAgg.get(p.id)!;
        const activityScore = Math.min(
          100,
          Math.round(
            Math.min(agg.buildPosts * 8, 24) +
              Math.min(agg.githubEvents * 6, 18) +
              Math.min(agg.ddollarVolume / 500, 20) +
              Math.min(p.bubbleScore / 50, 10) +
              Math.min(agg.aiCalls * 3, 12),
          ),
        );
        return {
          slug: p.slug,
          name: p.name,
          ticker: p.ticker,
          tokensIn: agg.tokensIn,
          tokensOut: agg.tokensOut,
          aiCalls: agg.aiCalls,
          ddollarVolume: Math.round(agg.ddollarVolume),
          githubEvents: agg.githubEvents,
          buildPosts: agg.buildPosts,
          activityScore,
          bubbleScore: p.bubbleScore,
          launchReadiness: p.launchReadiness,
        };
      })
      .filter(
        (r) =>
          r.tokensIn + r.tokensOut + r.ddollarVolume + r.githubEvents + r.buildPosts > 0 ||
          r.bubbleScore > 0,
      )
      .sort(
        (a, b) =>
          b.activityScore - a.activityScore ||
          b.tokensIn + b.tokensOut - (a.tokensIn + a.tokensOut),
      )
      .slice(0, 12);

    const totals = series.reduce(
      (acc, d) => ({
        tokensIn: acc.tokensIn + d.tokensIn,
        tokensOut: acc.tokensOut + d.tokensOut,
        aiCalls: acc.aiCalls + d.aiCalls,
        ddollarVolume: acc.ddollarVolume + d.ddollarVolume,
        githubEvents: acc.githubEvents + d.githubEvents,
        buildPosts: acc.buildPosts + d.buildPosts,
      }),
      {
        tokensIn: 0,
        tokensOut: 0,
        aiCalls: 0,
        ddollarVolume: 0,
        githubEvents: 0,
        buildPosts: 0,
      },
    );

    return {
      days: windowDays,
      series,
      totals,
      projects: projectRows,
      updatedAt: new Date().toISOString(),
    };
  }
}
