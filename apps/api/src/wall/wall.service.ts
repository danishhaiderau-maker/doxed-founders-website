import { Injectable, NotFoundException, ForbiddenException, BadRequestException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';

/** DDollar cost to upgrade (pin/highlight/promote) a subtopic on a wall. */
export const WALL_PIN_COST_DDOLLAR = 500;
/** Max messages returned per fetch. */
const MESSAGE_PAGE_LIMIT = 100;
/** Small DDollar reward for posting a constructive wall message (anti-spam: only for non-cross-posted). */
const WALL_POST_REWARD = 5;

export interface WallAuthor {
  id: string;
  name: string | null;
  platformHandle: string | null;
  avatarUrl: string | null;
  isVerifiedFounder: boolean;
  founderSlug?: string | null;
}

export interface WallMessageDto {
  id: string;
  projectId: string;
  projectSlug: string;
  projectName: string;
  projectTicker: string;
  projectLogoUrl: string | null;
  authorId: string;
  author: WallAuthor;
  body: string;
  source: string;
  sourceRefId: string | null;
  createdAt: string;
  pin: { kind: string; userId: string; cost: number; createdAt: string } | null;
}

@Injectable()
export class WallService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly points: PointsService,
  ) {}

  // ────────────────────────────────────────────────────────────────────────────
  // Reading
  // ────────────────────────────────────────────────────────────────────────────

  /** List wall messages for a single project (newest last). */
  async listMessages(slug: string, viewerUserId?: string): Promise<WallMessageDto[]> {
    const project = await this.prisma.project.findFirst({
      where: { slug },
      select: { id: true, slug: true, name: true, ticker: true, logoUrl: true, approved: true },
    });
    if (!project) throw new NotFoundException('Project not found');

    const messages = await this.prisma.projectWallMessage.findMany({
      where: { projectId: project.id },
      orderBy: { createdAt: 'asc' },
      take: MESSAGE_PAGE_LIMIT,
      include: {
        author: {
          select: {
            id: true,
            name: true,
            platformHandle: true,
            avatarUrl: true,
            founder: { select: { slug: true, presenceLevel: true } },
          },
        },
        pin: { select: { kind: true, userId: true, cost: true, createdAt: true } },
      },
    });

    return messages.map((m) => this.toDto(m, project));
  }

  /** List the groups (projects) the current user has joined by following. */
  async listMyGroups(userId: string) {
    const follows = await this.prisma.projectFollow.findMany({
      where: { userId },
      orderBy: { createdAt: 'desc' },
      include: {
        project: {
          select: {
            id: true,
            slug: true,
            name: true,
            ticker: true,
            logoUrl: true,
            lifecycleStage: true,
            isLiveToken: true,
            chain: { select: { slug: true, name: true } },
            founder: { select: { name: true, slug: true, presenceLevel: true } },
            _count: { select: { wallMessages: true, followers: true } },
          },
        },
      },
    });

    // Pull last message preview for each project in one query.
    const projectIds = follows.map((f) => f.projectId);
    const lastMessages = projectIds.length
      ? await this.prisma.projectWallMessage.findMany({
          where: { projectId: { in: projectIds } },
          orderBy: { createdAt: 'desc' },
          distinct: ['projectId'],
          take: projectIds.length,
          select: {
            projectId: true,
            body: true,
            createdAt: true,
            author: { select: { name: true, platformHandle: true } },
          },
        })
      : [];

    const lastByProject = new Map(lastMessages.map((m) => [m.projectId, m]));

    return follows.map((f) => ({
      project: {
        id: f.project.id,
        slug: f.project.slug,
        name: f.project.name,
        ticker: f.project.ticker,
        logoUrl: f.project.logoUrl,
        lifecycleStage: f.project.lifecycleStage,
        isLiveToken: f.project.isLiveToken,
        chain: f.project.chain,
        founder: f.project.founder
          ? {
              name: f.project.founder.name,
              slug: f.project.founder.slug,
              presenceLevel: f.project.founder.presenceLevel,
            }
          : null,
        followerCount: f.project._count.followers,
      },
      messageCount: f.project._count.wallMessages,
      lastMessage: lastByProject.get(f.projectId) ?? null,
    }));
  }

  /** Aggregated wall across every project the user has joined (newest first). */
  async listAggregated(userId: string, limit = 60): Promise<WallMessageDto[]> {
    const follows = await this.prisma.projectFollow.findMany({
      where: { userId },
      select: { projectId: true },
    });
    const projectIds = follows.map((f) => f.projectId);
    if (projectIds.length === 0) return [];

    const messages = await this.prisma.projectWallMessage.findMany({
      where: { projectId: { in: projectIds } },
      orderBy: { createdAt: 'desc' },
      take: Math.min(Math.max(limit, 1), MESSAGE_PAGE_LIMIT),
      include: {
        author: {
          select: {
            id: true,
            name: true,
            platformHandle: true,
            avatarUrl: true,
            founder: { select: { slug: true, presenceLevel: true } },
          },
        },
        pin: { select: { kind: true, userId: true, cost: true, createdAt: true } },
        project: { select: { id: true, slug: true, name: true, ticker: true, logoUrl: true } },
      },
    });

    return messages.map((m) => this.toDto(m, m.project));
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Writing
  // ────────────────────────────────────────────────────────────────────────────

  /** Post a chat message to a project wall. User must have joined (followed) or be the founder. */
  async postMessage(userId: string, slug: string, body: string): Promise<WallMessageDto> {
    const text = body.trim();
    if (!text) throw new BadRequestException('Message body is empty.');

    const project = await this.prisma.project.findFirst({
      where: { slug },
      select: {
        id: true,
        slug: true,
        name: true,
        ticker: true,
        logoUrl: true,
        founderId: true,
        founder: { select: { userId: true } },
      },
    });
    if (!project) throw new NotFoundException('Project not found');

    const isFounder = project.founder?.userId === userId;
    const isFollowing = await this.prisma.projectFollow.findUnique({
      where: { userId_projectId: { userId, projectId: project.id } },
      select: { id: true },
    });

    if (!isFounder && !isFollowing) {
      throw new ForbiddenException('Join this project to post on its wall.');
    }

    const message = await this.prisma.projectWallMessage.create({
      data: {
        projectId: project.id,
        authorId: userId,
        body: text,
        source: 'chat',
      },
      include: {
        author: {
          select: {
            id: true,
            name: true,
            platformHandle: true,
            avatarUrl: true,
            founder: { select: { slug: true, presenceLevel: true } },
          },
        },
        pin: { select: { kind: true, userId: true, cost: true, createdAt: true } },
      },
    });

    // Small engagement reward for direct chat posts (anti-spam via follow gate above).
    try {
      await this.points.award(userId, WALL_POST_REWARD, 'WALL_POST');
    } catch {
      /* non-fatal */
    }

    return this.toDto(message, project);
  }

  /** Join a project wall (follows the project). Idempotent. */
  async joinProject(userId: string, slug: string) {
    const project = await this.prisma.project.findFirst({
      where: { slug },
      select: { id: true, slug: true, name: true, ticker: true },
    });
    if (!project) throw new NotFoundException('Project not found');

    await this.prisma.projectFollow.upsert({
      where: { userId_projectId: { userId, projectId: project.id } },
      create: { userId, projectId: project.id },
      update: {},
    });

    return { success: true, project };
  }

  /** Upgrade a subtopic (pin / highlight / promote) by spending DDollar. */
  async pinMessage(
    userId: string,
    messageId: string,
    kind: 'pin' | 'highlight' | 'promote' = 'pin',
    amount = WALL_PIN_COST_DDOLLAR,
  ) {
    const message = await this.prisma.projectWallMessage.findUnique({
      where: { id: messageId },
      select: { id: true, projectId: true, authorId: true },
    });
    if (!message) throw new NotFoundException('Message not found');

    // Spend DDollar first — throws if balance too low.
    await this.points.spend(userId, amount, `WALL_PIN:${kind}`);

    // Replace any existing pin (1:1) so the latest upgrade wins.
    const pin = await this.prisma.projectWallPin.upsert({
      where: { messageId: message.id },
      create: {
        messageId: message.id,
        projectId: message.projectId,
        userId,
        kind,
        cost: amount,
      },
      update: { userId, kind, cost: amount },
    });

    return {
      success: true,
      pin: { id: pin.id, kind: pin.kind, cost: pin.cost, messageId: pin.messageId },
      spentDdollar: amount,
    };
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Cross-post bridge (called by founder-os / founder-den when a social hub post is published)
  // ────────────────────────────────────────────────────────────────────────────

  /** Internal: create a cross-posted wall message from a social hub / build feed publish. */
  async crossPostFromSource(params: {
    projectId: string;
    authorId: string;
    body: string;
    source: string;
    sourceRefId?: string;
  }): Promise<void> {
    try {
      await this.prisma.projectWallMessage.create({
        data: {
          projectId: params.projectId,
          authorId: params.authorId,
          body: params.body.trim().slice(0, 4000),
          source: params.source,
          sourceRefId: params.sourceRefId ?? null,
        },
      });
    } catch {
      /* non-fatal — cross-post is best-effort */
    }
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Helpers
  // ────────────────────────────────────────────────────────────────────────────

  private toDto(
    m: {
      id: string;
      projectId: string;
      authorId: string;
      body: string;
      source: string;
      sourceRefId: string | null;
      createdAt: Date;
      author: {
        id: string;
        name: string | null;
        platformHandle: string | null;
        avatarUrl: string | null;
        founder: { slug: string; presenceLevel: string } | null;
      };
      pin: { kind: string; userId: string; cost: number; createdAt: Date } | null;
    },
    project: { id: string; slug: string; name: string; ticker: string; logoUrl: string | null },
  ): WallMessageDto {
    return {
      id: m.id,
      projectId: m.projectId,
      projectSlug: project.slug,
      projectName: project.name,
      projectTicker: project.ticker,
      projectLogoUrl: project.logoUrl,
      authorId: m.authorId,
      author: {
        id: m.author.id,
        name: m.author.name,
        platformHandle: m.author.platformHandle,
        avatarUrl: m.author.avatarUrl,
        isVerifiedFounder: Boolean(m.author.founder && m.author.founder.presenceLevel !== 'UNVERIFIED'),
        founderSlug: m.author.founder?.slug ?? null,
      },
      body: m.body,
      source: m.source,
      sourceRefId: m.sourceRefId,
      createdAt: m.createdAt.toISOString(),
      pin: m.pin
        ? {
            kind: m.pin.kind,
            userId: m.pin.userId,
            cost: m.pin.cost,
            createdAt: m.pin.createdAt.toISOString(),
          }
        : null,
    };
  }
}
