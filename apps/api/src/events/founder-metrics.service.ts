import { Injectable } from '@nestjs/common';
import { SimulatedRaiseStatus } from '@prisma/client';
import { computeLaunchReadiness } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class FounderMetricsService {
  constructor(private readonly prisma: PrismaService) {}

  async refreshLaunchReadiness(projectId: string): Promise<{ score: number; previous: number }> {
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
    if (!project) return { score: 0, previous: 0 };

    const previous = project.launchReadiness;
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

    if (score !== previous) {
      await this.prisma.project.update({
        where: { id: projectId },
        data: { launchReadiness: score },
      });
    }
    return { score, previous };
  }

  async refreshBubbleScore(projectId: string) {
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

    const demand =
      project.simulatedRaises[0]?.allocations.reduce((s, a) => s + Number(a.amountUsd), 0) ?? 0;
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
}
