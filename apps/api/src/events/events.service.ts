import { ForbiddenException, Injectable } from '@nestjs/common';
import { FounderEventType, Prisma } from '@prisma/client';
import { PrismaClientKnownRequestError } from '@prisma/client/runtime/library';
import { PrismaService } from '../prisma/prisma.service';
import { EventOrchestratorService } from './event-orchestrator.service';

export type EmitEventInput = {
  founderId: string;
  projectId?: string;
  userId?: string;
  type: FounderEventType;
  source: string;
  title: string;
  payload?: Record<string, unknown>;
  dedupeKey?: string;
};

@Injectable()
export class EventsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly orchestrator: EventOrchestratorService,
  ) {}

  async emit(input: EmitEventInput) {
    if (input.dedupeKey) {
      const existing = await this.prisma.founderEvent.findUnique({
        where: { dedupeKey: input.dedupeKey },
      });
      if (existing) return { eventId: existing.id, duplicate: true };
    }

    try {
      const event = await this.prisma.founderEvent.create({
        data: {
          founderId: input.founderId,
          projectId: input.projectId,
          userId: input.userId,
          type: input.type,
          source: input.source,
          title: input.title,
          payload: (input.payload ?? {}) as Prisma.InputJsonValue,
          dedupeKey: input.dedupeKey,
        },
      });

      await this.orchestrator.process(event);
      return { eventId: event.id, duplicate: false };
    } catch (err) {
      if (
        err instanceof PrismaClientKnownRequestError &&
        err.code === 'P2002' &&
        input.dedupeKey
      ) {
        const existing = await this.prisma.founderEvent.findUnique({
          where: { dedupeKey: input.dedupeKey },
        });
        if (existing) return { eventId: existing.id, duplicate: true };
      }
      throw err;
    }
  }

  async listForUser(userId: string, limit = 30) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) return { events: [] };

    const events = await this.prisma.founderEvent.findMany({
      where: { founderId: founder.id },
      orderBy: { createdAt: 'desc' },
      take: limit,
    });

    return {
      events: events.map((e) => ({
        id: e.id,
        type: e.type,
        source: e.source,
        title: e.title,
        payload: e.payload,
        status: e.status,
        createdAt: e.createdAt.toISOString(),
      })),
    };
  }

  async getActivityFeed(userId: string) {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { where: { approved: true }, take: 1 } },
    });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const project = founder.projects[0];
    const weekAgo = new Date(Date.now() - 7 * 86400000);

    const [events, commits, deploys, followers, featureThreads] = await Promise.all([
      this.prisma.founderEvent.findMany({
        where: { founderId: founder.id, createdAt: { gte: weekAgo } },
        orderBy: { createdAt: 'desc' },
        take: 20,
      }),
      this.prisma.founderEvent.count({
        where: { founderId: founder.id, type: FounderEventType.GITHUB_COMMIT, createdAt: { gte: weekAgo } },
      }),
      this.prisma.founderEvent.count({
        where: { founderId: founder.id, type: FounderEventType.DEPLOY_SUCCESS, createdAt: { gte: weekAgo } },
      }),
      project
        ? this.prisma.projectFollow.count({ where: { projectId: project.id } })
        : Promise.resolve(0),
      project
        ? this.prisma.communityThread.count({
            where: { projectId: project.id, channel: 'FEATURE_REQUESTS' },
          })
        : Promise.resolve(0),
    ]);

    return {
      projectName: project?.name ?? founder.name,
      launchReadiness: project?.launchReadiness ?? 0,
      buildStreakDays: founder.buildStreakDays,
      weekStats: {
        commits,
        deploys,
        followers,
        featureRequests: featureThreads,
        events: events.length,
      },
      recentEvents: events.map((e) => ({
        id: e.id,
        type: e.type,
        source: e.source,
        title: e.title,
        createdAt: e.createdAt.toISOString(),
      })),
    };
  }
}
