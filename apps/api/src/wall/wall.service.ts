import { BadRequestException, ForbiddenException, Injectable, Logger, NotFoundException, ServiceUnavailableException } from '@nestjs/common';
import { NotificationType, UserRole } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';
import { AiInvokerService } from '../ai-routing/ai-invoker.service';
import { ContextBuilderService } from '../founder-ai-runtime/context-builder.service';
import { FounderAiRuntimeService } from '../founder-ai-runtime/founder-ai-runtime.service';
import type { AiRuntimeRequest } from '../founder-ai-runtime/founder-ai-runtime.types';
import { NotificationsService } from '../notifications/notifications.service';
import { ChatEventsService } from '../messages/chat-events.service';
import { CHAT_REACTION_EMOJIS } from '../messages/messages.service';

/** DDollar cost to upgrade (pin/highlight/promote) a subtopic on a wall. Flat for all three kinds. */
export const WALL_PIN_COST_DDOLLAR = 10;
/** DDollar cost per month to keep the Chat Summarizer agent active on a project wall. */
export const WALL_SUMMARIZER_COST_DDOLLAR = 1000;
/** Subscription window length for the summarizer. */
const WALL_SUMMARIZER_WINDOW_DAYS = 30;
/** Number of recent messages the summarizer inspects (analysis window only — NOT a display cap). */
const SUMMARIZER_WINDOW = 500;
/** Default page size for message pagination. */
const MESSAGE_PAGE_LIMIT = 100;
/** Small DDollar reward for posting a constructive wall message (anti-spam: only for non-cross-posted). */
const WALL_POST_REWARD = 5;

/** Email of the platform admin account that receives wall upgrade revenue. Overridable via env. */
const PLATFORM_ADMIN_EMAIL = process.env.PLATFORM_ADMIN_EMAIL ?? 'admin@doxedcryptofounder.local';

/** Module-level memo for the resolved admin userId so we don't query every pin call. */
let cachedAdminUserId: string | null | undefined = undefined;

export interface WallAuthor {
  id: string;
  name: string | null;
  platformHandle: string | null;
  avatarUrl: string | null;
  isVerifiedFounder: boolean;
  isAdmin?: boolean;
  founderSlug?: string | null;
}

export interface WallReplyPreview {
  id: string;
  body: string;
  authorLabel: string;
}

export interface WallReactionSummary {
  emoji: string;
  count: number;
  mine: boolean;
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
  replyTo: WallReplyPreview | null;
  reactions: WallReactionSummary[];
  hidden: boolean;
  /** Deep-link targets for jump-from-wall UI. */
  links: {
    project: string;
    raiseRoom: string;
    founderSpotlight: string | null;
  };
}

export interface WallMembershipDto {
  joined: boolean;
  isFounder: boolean;
  founderVerified: boolean;
  liveTrading: boolean;
  summarizerEligible: boolean;
  postingMode: 'OPEN' | 'ANNOUNCEMENTS';
  slowModeSeconds: number;
  canPost: boolean;
  mutedUntil: string | null;
}

export interface WallSummaryDto {
  active: boolean;
  summaryBody: string | null;
  sentimentLabel: 'positive' | 'neutral' | 'negative' | null;
  sentimentReasoning: string | null;
  activatedAt: string | null;
  expiresAt: string | null;
  renewedAt: string | null;
  cost: number;
  activatedBy: string | null;
}

export interface WallUnreadEntryDto {
  projectId: string;
  slug: string;
  name: string;
  ticker: string;
  logoUrl: string | null;
  unreadCount: number;
}

export interface WallUnreadDto {
  total: number;
  projects: WallUnreadEntryDto[];
}

@Injectable()
export class WallService {
  private readonly logger = new Logger(WallService.name);
  constructor(
    private readonly prisma: PrismaService,
    private readonly points: PointsService,
    private readonly aiInvoker: AiInvokerService,
    private readonly founderAiRuntime: FounderAiRuntimeService,
    private readonly contextBuilder: ContextBuilderService,
    private readonly notifications: NotificationsService,
    private readonly chatEvents: ChatEventsService,
  ) {}

  // ────────────────────────────────────────────────────────────────────────────
  // Reading
  // ────────────────────────────────────────────────────────────────────────────

  /**
   * List wall messages for a single project. Cursor-paginated backwards:
   * pass `before` (an ISO createdAt) to fetch the page older than that cursor.
   * Without `before`, returns the most recent window. Results are oldest-first
   * so the UI can prepend them when scrolling up.
   */
  async listMessages(slug: string, before?: Date, viewerId?: string): Promise<WallMessageDto[]> {
    const project = await this.prisma.project.findFirst({
      where: { slug },
      select: {
        id: true,
        slug: true,
        name: true,
        ticker: true,
        logoUrl: true,
        approved: true,
        founder: { select: { slug: true } },
      },
    });
    if (!project) throw new NotFoundException('Project not found');

    const reportedIds = viewerId
      ? (
          await this.prisma.projectWallReport.findMany({
            where: { reporterId: viewerId, status: 'OPEN' },
            select: { messageId: true },
          })
        ).map((r) => r.messageId)
      : [];

    const messages = await this.prisma.projectWallMessage.findMany({
      where: {
        projectId: project.id,
        ...(before ? { createdAt: { lt: before } } : {}),
        AND: [
          {
            OR: viewerId
              ? [{ hiddenAt: null }, { authorId: viewerId }, { hiddenById: viewerId }]
              : [{ hiddenAt: null }],
          },
          ...(reportedIds.length ? [{ id: { notIn: reportedIds } }] : []),
        ],
      },
      orderBy: { createdAt: 'desc' },
      take: MESSAGE_PAGE_LIMIT,
      include: {
        author: {
          select: {
            id: true,
            name: true,
            platformHandle: true,
            avatarUrl: true,
            role: true,
            founder: { select: { slug: true, presenceLevel: true } },
          },
        },
        pin: { select: { kind: true, userId: true, cost: true, createdAt: true } },
        replyTo: {
          select: {
            id: true,
            body: true,
            author: { select: { name: true, platformHandle: true } },
          },
        },
        reactions: { select: { emoji: true, userId: true } },
      },
    });

    // Reverse to oldest-first for consistent prepend behaviour.
    return messages.reverse().map((m) => this.toDto(m, project, viewerId));
  }

  /** Membership + summarizer-eligibility probe for the current viewer. */
  async getMembership(slug: string, userId?: string): Promise<WallMembershipDto> {
    const project = await this.prisma.project.findFirst({
      where: { slug },
      select: {
        id: true,
        isLiveToken: true,
        lifecycleStage: true,
        founderId: true,
        founder: { select: { userId: true, presenceLevel: true } },
        wallSettings: { select: { postingMode: true, slowModeSeconds: true } },
      },
    });
    if (!project) throw new NotFoundException('Project not found');

    const postingMode =
      project.wallSettings?.postingMode === 'ANNOUNCEMENTS' ? 'ANNOUNCEMENTS' : 'OPEN';
    const slowModeSeconds = project.wallSettings?.slowModeSeconds ?? 0;

    if (!userId) {
      return {
        joined: false,
        isFounder: false,
        founderVerified: this.founderVerified(project.founder),
        liveTrading: this.isLiveTrading(project),
        summarizerEligible: this.summarizerEligible(project),
        postingMode,
        slowModeSeconds,
        canPost: false,
        mutedUntil: null,
      };
    }

    const isFounder = project.founder?.userId === userId;
    const [follow, mute, admin] = await Promise.all([
      this.prisma.projectFollow.findUnique({
        where: { userId_projectId: { userId, projectId: project.id } },
        select: { id: true },
      }),
      this.prisma.projectWallMute.findUnique({
        where: { projectId_userId: { projectId: project.id, userId } },
        select: { mutedUntil: true },
      }),
      this.prisma.user.findUnique({ where: { id: userId }, select: { role: true } }),
    ]);

    const joined = Boolean(follow) || isFounder;
    const isAdmin = admin?.role === UserRole.ADMIN;
    const mutedUntil =
      mute && mute.mutedUntil > new Date() ? mute.mutedUntil.toISOString() : null;
    const announcementsOnly = postingMode === 'ANNOUNCEMENTS';
    const canPost =
      joined &&
      !mutedUntil &&
      (!announcementsOnly || isFounder || isAdmin);

    return {
      joined,
      isFounder,
      founderVerified: this.founderVerified(project.founder),
      liveTrading: this.isLiveTrading(project),
      summarizerEligible: this.summarizerEligible(project),
      postingMode,
      slowModeSeconds,
      canPost,
      mutedUntil,
    };
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

    // Unread counts per project: messages newer than the user's last-read cursor.
    const unreadMap = await this.unreadCountsForUser(userId, projectIds);

    const prefs = await this.prisma.chatThreadPreference.findMany({
      where: { userId, scope: 'wall', targetId: { in: projectIds } },
    });
    const prefByProject = new Map(prefs.map((p) => [p.targetId, p]));

    const mapped = follows.map((f) => {
      const pref = prefByProject.get(f.projectId);
      return {
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
        unreadCount: unreadMap.get(f.projectId) ?? 0,
        pinned: Boolean(pref?.pinned),
        muted: Boolean(pref?.muted),
        archived: Boolean(pref?.archived),
        pinnedAt: pref?.pinnedAt?.toISOString() ?? null,
      };
    });

    return mapped.sort((a, b) => {
      if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
      if (a.archived !== b.archived) return a.archived ? 1 : -1;
      const aAt = a.lastMessage?.createdAt?.getTime?.() ?? 0;
      const bAt = b.lastMessage?.createdAt?.getTime?.() ?? 0;
      return bAt - aAt;
    });
  }

  /**
   * Compute unread counts for the given user across the given projects.
   * Returns a Map<projectId, count> where count = ProjectWallMessage rows newer
   * than the user's last-read cursor for that project (defaults to epoch → all
   * messages count as unread if no cursor exists yet, EXCEPT messages authored
   * by the user themselves).
   */
  private async unreadCountsForUser(userId: string, projectIds: string[]): Promise<Map<string, number>> {
    const out = new Map<string, number>();
    if (projectIds.length === 0) return out;

    const cursors = await this.prisma.projectWallReadCursor.findMany({
      where: { userId, projectId: { in: projectIds } },
      select: { projectId: true, lastReadAt: true },
    });
    const cursorByProject = new Map(cursors.map((c) => [c.projectId, c.lastReadAt]));

    // Group counts via groupBy for one round-trip.
    const grouped = await this.prisma.projectWallMessage.groupBy({
      by: ['projectId'],
      where: {
        projectId: { in: projectIds },
        authorId: { not: userId }, // don't count your own messages as unread
      },
      _count: { _all: true },
    });

    for (const g of grouped) {
      const since = cursorByProject.get(g.projectId);
      if (!since) {
        // No cursor yet → everything from others is unread.
        out.set(g.projectId, g._count._all);
        continue;
      }
      // Need a filtered count for messages newer than the cursor.
      const n = await this.prisma.projectWallMessage.count({
        where: {
          projectId: g.projectId,
          authorId: { not: userId },
          createdAt: { gt: since },
        },
      });
      out.set(g.projectId, n);
    }
    return out;
  }

  /** Per-project unread counts + total for the current user (for the header badge + drawer rail). */
  async getUnread(userId: string): Promise<WallUnreadDto> {
    const follows = await this.prisma.projectFollow.findMany({
      where: { userId },
      select: {
        projectId: true,
        project: {
          select: { id: true, slug: true, name: true, ticker: true, logoUrl: true },
        },
      },
    });
    const projectIds = follows.map((f) => f.projectId);
    const unreadMap = await this.unreadCountsForUser(userId, projectIds);

    const projects = follows
      .map((f) => ({
        projectId: f.project.id,
        slug: f.project.slug,
        name: f.project.name,
        ticker: f.project.ticker,
        logoUrl: f.project.logoUrl,
        unreadCount: unreadMap.get(f.projectId) ?? 0,
      }))
      .sort((a, b) => b.unreadCount - a.unreadCount);

    return {
      total: projects.reduce((sum, p) => sum + p.unreadCount, 0),
      projects,
    };
  }

  /** Mark a project as read up to now for the current user (clears its unread badge). */
  async markRead(userId: string, slug: string): Promise<{ success: true; projectId: string; unreadCount: 0 }> {
    const project = await this.prisma.project.findFirst({
      where: { slug },
      select: { id: true },
    });
    if (!project) throw new NotFoundException('Project not found');

    await this.prisma.projectWallReadCursor.upsert({
      where: { userId_projectId: { userId, projectId: project.id } },
      create: { userId, projectId: project.id, lastReadAt: new Date() },
      update: { lastReadAt: new Date() },
    });

    return { success: true, projectId: project.id, unreadCount: 0 };
  }

  /** Mark every joined project wall as read. */
  async markAllRead(userId: string) {
    const follows = await this.prisma.projectFollow.findMany({
      where: { userId },
      select: { projectId: true },
    });
    const now = new Date();
    await Promise.all(
      follows.map((f) =>
        this.prisma.projectWallReadCursor.upsert({
          where: { userId_projectId: { userId, projectId: f.projectId } },
          create: { userId, projectId: f.projectId, lastReadAt: now },
          update: { lastReadAt: now },
        }),
      ),
    );
    this.chatEvents.emitToUser(userId, { type: 'prefs' });
    return { success: true as const, updated: follows.length };
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
      where: {
        projectId: { in: projectIds },
        OR: [{ hiddenAt: null }, { authorId: userId }],
      },
      orderBy: { createdAt: 'desc' },
      take: Math.min(Math.max(limit, 1), MESSAGE_PAGE_LIMIT),
      include: {
        author: {
          select: {
            id: true,
            name: true,
            platformHandle: true,
            avatarUrl: true,
            role: true,
            founder: { select: { slug: true, presenceLevel: true } },
          },
        },
        pin: { select: { kind: true, userId: true, cost: true, createdAt: true } },
        replyTo: {
          select: {
            id: true,
            body: true,
            author: { select: { name: true, platformHandle: true } },
          },
        },
        reactions: { select: { emoji: true, userId: true } },
        project: {
          select: {
            id: true,
            slug: true,
            name: true,
            ticker: true,
            logoUrl: true,
            founder: { select: { slug: true } },
          },
        },
      },
    });

    return messages.map((m) => this.toDto(m, m.project, userId));
  }

  /** Public: latest cached Chat Summarizer output + subscription state. */
  async getSummary(slug: string): Promise<WallSummaryDto> {
    const project = await this.prisma.project.findFirst({
      where: { slug },
      select: { id: true },
    });
    if (!project) throw new NotFoundException('Project not found');

    const row = await this.prisma.projectWallSummary.findUnique({
      where: { projectId: project.id },
    });
    if (!row) {
      return {
        active: false,
        summaryBody: null,
        sentimentLabel: null,
        sentimentReasoning: null,
        activatedAt: null,
        expiresAt: null,
        renewedAt: null,
        cost: WALL_SUMMARIZER_COST_DDOLLAR,
        activatedBy: null,
      };
    }
    return this.toSummaryDto(row);
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Writing
  // ────────────────────────────────────────────────────────────────────────────

  /** Post a chat text message to a project wall. User must have joined (followed) or be the founder. */
  async postMessage(
    userId: string,
    slug: string,
    body: string,
    options?: { replyToId?: string },
  ): Promise<WallMessageDto> {
    const text = body.trim();
    if (!text) throw new BadRequestException('Message body is empty.');
    if (text.length > 4000) throw new BadRequestException('Message too long.');

    const project = await this.prisma.project.findFirst({
      where: { slug },
      select: {
        id: true,
        slug: true,
        name: true,
        ticker: true,
        logoUrl: true,
        founderId: true,
        founder: { select: { userId: true, slug: true } },
        wallSettings: { select: { postingMode: true, slowModeSeconds: true } },
      },
    });
    if (!project) throw new NotFoundException('Project not found');

    const isFounder = project.founder?.userId === userId;
    const [isFollowing, mute, poster] = await Promise.all([
      this.prisma.projectFollow.findUnique({
        where: { userId_projectId: { userId, projectId: project.id } },
        select: { id: true },
      }),
      this.prisma.projectWallMute.findUnique({
        where: { projectId_userId: { projectId: project.id, userId } },
        select: { mutedUntil: true },
      }),
      this.prisma.user.findUnique({
        where: { id: userId },
        select: { role: true, name: true, platformHandle: true },
      }),
    ]);

    if (!isFounder && !isFollowing) {
      throw new ForbiddenException('Join this project to post on its wall.');
    }

    if (mute && mute.mutedUntil > new Date()) {
      throw new ForbiddenException(
        `You are muted on this wall until ${mute.mutedUntil.toISOString()}.`,
      );
    }

    const isAdmin = poster?.role === UserRole.ADMIN;
    const postingMode = project.wallSettings?.postingMode ?? 'OPEN';
    if (postingMode === 'ANNOUNCEMENTS' && !isFounder && !isAdmin) {
      throw new ForbiddenException(
        'This wall is in announcements mode — only the founder (or platform admin) can post.',
      );
    }

    const slowModeSeconds = project.wallSettings?.slowModeSeconds ?? 0;
    if (slowModeSeconds > 0 && !isFounder && !isAdmin) {
      const last = await this.prisma.projectWallMessage.findFirst({
        where: { projectId: project.id, authorId: userId, source: 'chat' },
        orderBy: { createdAt: 'desc' },
        select: { createdAt: true },
      });
      if (last) {
        const elapsed = Date.now() - last.createdAt.getTime();
        const waitMs = slowModeSeconds * 1000 - elapsed;
        if (waitMs > 0) {
          throw new BadRequestException(
            `Slow mode: wait ${Math.ceil(waitMs / 1000)}s before posting again.`,
          );
        }
      }
    }

    let replyToId: string | null = null;
    if (options?.replyToId) {
      const parent = await this.prisma.projectWallMessage.findFirst({
        where: { id: options.replyToId, projectId: project.id },
        select: { id: true },
      });
      if (!parent) throw new BadRequestException('Reply target not found on this wall.');
      replyToId = parent.id;
    }

    const message = await this.prisma.projectWallMessage.create({
      data: {
        projectId: project.id,
        authorId: userId,
        body: text,
        source: 'chat',
        replyToId,
      },
      include: {
        author: {
          select: {
            id: true,
            name: true,
            platformHandle: true,
            avatarUrl: true,
            role: true,
            founder: { select: { slug: true, presenceLevel: true } },
          },
        },
        pin: { select: { kind: true, userId: true, cost: true, createdAt: true } },
        replyTo: {
          select: {
            id: true,
            body: true,
            author: { select: { name: true, platformHandle: true } },
          },
        },
        reactions: { select: { emoji: true, userId: true } },
      },
    });

    try {
      await this.points.award(userId, WALL_POST_REWARD, 'WALL_POST');
    } catch {
      /* non-fatal */
    }

    await this.notifyMentions(userId, project, text, message.id, poster);

    // Fan-out refresh hint to followers (best-effort, capped).
    const followers = await this.prisma.projectFollow.findMany({
      where: { projectId: project.id },
      select: { userId: true },
      take: 200,
    });
    this.chatEvents.emitToUsers(
      [...followers.map((f) => f.userId), project.founder?.userId].filter(Boolean) as string[],
      { type: 'wall', projectId: project.id, slug: project.slug },
    );

    return this.toDto(message, project, userId);
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

    // Best-effort revenue routing: credit the same amount to the platform admin.
    // If the credit fails, log but don't refund — the pin still succeeds.
    try {
      const adminId = await this.resolveAdminUserId();
      if (adminId) {
        await this.points.award(adminId, amount, `WALL_PIN_REVENUE:${kind}`);
      }
    } catch (err) {
      this.logger.warn(
        `WALL_PIN revenue credit to admin failed (kind=${kind}, amount=${amount}): ${String(err)}`,
      );
    }

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
  // Chat Summarizer agent — recurring monthly DDollar subscription
  // ────────────────────────────────────────────────────────────────────────────

  /**
   * Activate (or renew) the Chat Summarizer for a project wall.
   * - Charges WALL_SUMMARIZER_COST_DDOLLAR (1,000) DDollar for a 30-day window.
   * - Re-runs the LLM over the last SUMMARIZER_WINDOW messages and caches the result.
   * - Any joined follower (or the founder) may pay to activate. The button is only
   *   surfaced by the UI when the project's founder is verified AND live-trading.
   */
  async activateSummarizer(userId: string, slug: string): Promise<WallSummaryDto> {
    const project = await this.prisma.project.findFirst({
      where: { slug },
      select: {
        id: true,
        slug: true,
        name: true,
        ticker: true,
        isLiveToken: true,
        lifecycleStage: true,
        founderId: true,
        founder: { select: { userId: true, presenceLevel: true } },
      },
    });
    if (!project) throw new NotFoundException('Project not found');

    // Qualification gate — the project's founder must be verified + live-trading.
    if (!this.summarizerEligible(project)) {
      throw new BadRequestException(
        'Chat Summarizer is only available on projects with a verified founder that is already live trading.',
      );
    }

    // Caller must be the founder or a follower (joined).
    const isFounder = project.founder?.userId === userId;
    if (!isFounder) {
      const follow = await this.prisma.projectFollow.findUnique({
        where: { userId_projectId: { userId, projectId: project.id } },
        select: { id: true },
      });
      if (!follow) {
        throw new ForbiddenException('Join this project to activate the Chat Summarizer.');
      }
    }

    // Charge 1,000 DDollar for the month. Throws if balance too low. The
    // aiSpend flag runs the tier-cap pre-check so parasite-tier accounts are
    // rejected before the summarizer LLM call fires (DeepSeek; GLM reserved for Second Brain).
    await this.points.spend(userId, WALL_SUMMARIZER_COST_DDOLLAR, 'WALL_SUMMARIZER_MONTHLY', true);

    // Subscription date math: extend an active sub by 30d; otherwise start fresh.
    const now = new Date();
    const existing = await this.prisma.projectWallSummary.findUnique({
      where: { projectId: project.id },
      select: { expiresAt: true },
    });
    const stillActive = existing && existing.expiresAt > now;
    const base = stillActive ? existing.expiresAt : now;
    const expiresAt = new Date(base.getTime() + WALL_SUMMARIZER_WINDOW_DAYS * 24 * 60 * 60 * 1000);
    const renewedAt = stillActive ? now : null;
    const activatedAt = stillActive ? existing.expiresAt : now;

    // Fetch the last SUMMARIZER_WINDOW messages (analysis window only).
    const recent = await this.prisma.projectWallMessage.findMany({
      where: { projectId: project.id },
      orderBy: { createdAt: 'desc' },
      take: SUMMARIZER_WINDOW,
      select: { body: true, createdAt: true, author: { select: { name: true, platformHandle: true } } },
    });

    const analysis = await this.runSummarizerLlm(project.name, project.ticker, recent, {
      userId,
      projectId: project.id,
    });
    const sentiment = this.normalizeSentiment(analysis.sentimentLabel);

    const row = await this.prisma.projectWallSummary.upsert({
      where: { projectId: project.id },
      create: {
        projectId: project.id,
        summaryBody: analysis.summary,
        sentimentLabel: sentiment,
        sentimentReasoning: analysis.reasoning,
        activatedBy: userId,
        cost: WALL_SUMMARIZER_COST_DDOLLAR,
        activatedAt,
        expiresAt,
        renewedAt,
      },
      update: {
        summaryBody: analysis.summary,
        sentimentLabel: sentiment,
        sentimentReasoning: analysis.reasoning,
        activatedBy: userId,
        cost: WALL_SUMMARIZER_COST_DDOLLAR,
        activatedAt,
        expiresAt,
        renewedAt,
      },
    });

    return this.toSummaryDto(row);
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Cross-post bridge (called by founder-os / founder-den when a social hub post is published)
  // ────────────────────────────────────────────────────────────────────────────

  /**
   * Internal: create a cross-posted wall message from a social hub / build feed publish.
   * GATED: only the project's own verified founder's content cross-posts to the wall.
   * Random users' build posts / community threads / social-hub publishes are NEVER echoed.
   */
  async crossPostFromSource(params: {
    projectId: string;
    authorId: string;
    body: string;
    source: string;
    sourceRefId?: string;
  }): Promise<void> {
    try {
      const project = await this.prisma.project.findUnique({
        where: { id: params.projectId },
        select: { id: true, founder: { select: { userId: true, presenceLevel: true } } },
      });
      if (!project?.founder) return;
      const isVerifiedFounder =
        project.founder.userId === params.authorId &&
        project.founder.presenceLevel !== 'UNVERIFIED';
      if (!isVerifiedFounder) return; // silent drop — non-founder content does not spam the wall

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

  /**
   * Resolve the platform admin userId (cached at module scope).
   * Returns null if the admin account doesn't exist (e.g. seed not run yet).
   */
  private async resolveAdminUserId(): Promise<string | null> {
    if (cachedAdminUserId !== undefined) return cachedAdminUserId;
    try {
      const admin = await this.prisma.user.findUnique({
        where: { email: PLATFORM_ADMIN_EMAIL },
        select: { id: true },
      });
      cachedAdminUserId = admin?.id ?? null;
    } catch (err) {
      this.logger.warn(`Failed to resolve platform admin by email ${PLATFORM_ADMIN_EMAIL}: ${String(err)}`);
      cachedAdminUserId = null;
    }
    return cachedAdminUserId;
  }

  private founderVerified(founder: { presenceLevel: string } | null | undefined): boolean {
    return Boolean(founder && founder.presenceLevel !== 'UNVERIFIED');
  }

  private isLiveTrading(project: { isLiveToken: boolean; lifecycleStage: string }): boolean {
    return Boolean(project.isLiveToken) || project.lifecycleStage === 'LIVE_TRADING';
  }

  private summarizerEligible(project: {
    isLiveToken: boolean;
    lifecycleStage: string;
    founder: { presenceLevel: string } | null;
  }): boolean {
    return this.founderVerified(project.founder) && this.isLiveTrading(project);
  }

  private normalizeSentiment(label: string): 'positive' | 'neutral' | 'negative' {
    const l = (label ?? '').trim().toLowerCase();
    if (l.startsWith('pos')) return 'positive';
    if (l.startsWith('neg')) return 'negative';
    return 'neutral';
  }

  private toSummaryDto(row: {
    summaryBody: string;
    sentimentLabel: string;
    sentimentReasoning: string;
    activatedBy: string;
    cost: number;
    activatedAt: Date;
    expiresAt: Date;
    renewedAt: Date | null;
  }): WallSummaryDto {
    const now = new Date();
    return {
      active: row.expiresAt > now,
      summaryBody: row.summaryBody,
      sentimentLabel: this.normalizeSentiment(row.sentimentLabel) as WallSummaryDto['sentimentLabel'],
      sentimentReasoning: row.sentimentReasoning,
      activatedAt: row.activatedAt.toISOString(),
      expiresAt: row.expiresAt.toISOString(),
      renewedAt: row.renewedAt ? row.renewedAt.toISOString() : null,
      cost: row.cost,
      activatedBy: row.activatedBy,
    };
  }

  /**
   * Call the routed AI provider for section `wall_summarizer` to summarize +
   * sentiment-analyze a message window. The provider/key is admin-configurable
   * in /admin/control → AI Routing (default: DeepSeek — GLM is reserved for Second Brain). Token usage is logged
   * centrally by the AiInvokerService (billingSource = 'platform_promo' to
   * preserve the existing adoption-chart bucketing for the summarizer).
   * Returns a parsed {summary, sentimentLabel, reasoning}.
   */
  private async runSummarizerLlm(
    projectName: string,
    ticker: string,
    messages: { body: string; createdAt: Date; author: { name: string | null; platformHandle: string | null } }[],
    ctx: { userId: string; projectId: string },
  ): Promise<{ summary: string; sentimentLabel: string; reasoning: string }> {
    if (messages.length === 0) {
      return {
        summary: 'No messages on this wall yet — be the first to start the conversation.',
        sentimentLabel: 'neutral',
        reasoning: 'There is no conversation to analyze yet.',
      };
    }

    const maxLines = this.contextBuilder.maxWallTranscriptMessages();
    const windowMessages = messages.slice(-maxLines);

    const transcript = windowMessages
      .slice()
      .reverse() // chronological order for the model
      .map((m, i) => {
        const who = m.author.name ?? m.author.platformHandle ?? 'Anon';
        return `[${i + 1}] ${who}: ${m.body}`;
      })
      .join('\n');

    const system =
      'You are the Chat Summarizer agent for a crypto founder community wall. ' +
      'You read a Telegram-style chat transcript and produce (a) a concise plain-text summary ' +
      'of what is being discussed (3-6 sentences, no bullet markers) and (b) a sentiment analysis ' +
      'with reasoning explaining WHY the sentiment reads that way. ' +
      'Respond ONLY with a compact JSON object, no markdown fences, with keys: ' +
      '"summary" (string), "sentiment" (one of "positive" | "neutral" | "negative"), "reasoning" (string).';

    const userPrompt =
      `Project: ${projectName} ($${ticker}).\n` +
      `Transcript of the last ${windowMessages.length} messages (oldest first):\n\n${transcript}\n\n` +
      `Return the JSON object now.`;

    const runtimeRequest: AiRuntimeRequest = {
      userId: ctx.userId,
      system,
      userPrompt,
      section: 'wall_summarizer',
      projectId: ctx.projectId,
    };

    let text: string;
    try {
      const runtimeResult = await this.founderAiRuntime.complete(
        runtimeRequest,
        async (route, invokeCtx) => {
          const result = await this.aiInvoker.invoke({
            section: 'wall_summarizer',
            providerKey: route.providerKey,
            model: route.model,
            messages: [
              { role: 'system', content: invokeCtx.request.system },
              { role: 'user', content: invokeCtx.request.userPrompt },
            ],
            temperature: 0.3,
            maxTokens: invokeCtx.maxOutputTokens,
            userId: ctx.userId,
            projectId: ctx.projectId,
            billingSource: 'platform_promo',
          });
          return {
            ok: true,
            text: result.content,
            provider: result.provider,
            model: result.model,
            promptTokens: result.usage.promptTokens,
            completionTokens: result.usage.completionTokens,
          };
        },
      );
      if (!runtimeResult.ok || !runtimeResult.text?.trim()) {
        throw new ServiceUnavailableException('Chat Summarizer LLM call failed. Try again in a moment.');
      }
      text = runtimeResult.text;
    } catch (err) {
      if (err instanceof ServiceUnavailableException) throw err;
      this.logger.warn(
        `Summarizer call failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      throw new ServiceUnavailableException('Chat Summarizer LLM call failed. Try again in a moment.');
    }

    return this.parseSummarizerResponse(text);
  }

  private parseSummarizerResponse(raw: string): {
    summary: string;
    sentimentLabel: string;
    reasoning: string;
  } {
    const stripped = raw.replace(/^```(?:json)?/i, '').replace(/```$/i, '').trim();
    const start = stripped.indexOf('{');
    const end = stripped.lastIndexOf('}');
    if (start >= 0 && end > start) {
      try {
        const parsed = JSON.parse(stripped.slice(start, end + 1)) as {
          summary?: string;
          sentiment?: string;
          reasoning?: string;
        };
        return {
          summary: (parsed.summary ?? '').trim() || raw.trim(),
          sentimentLabel: (parsed.sentiment ?? 'neutral').trim(),
          reasoning: (parsed.reasoning ?? '').trim(),
        };
      } catch {
        /* fall through to plain-text fallback */
      }
    }
    return { summary: raw.trim(), sentimentLabel: 'neutral', reasoning: '' };
  }

  private toDto(
    m: {
      id: string;
      projectId: string;
      authorId: string;
      body: string;
      source: string;
      sourceRefId: string | null;
      createdAt: Date;
      hiddenAt?: Date | null;
      author: {
        id: string;
        name: string | null;
        platformHandle: string | null;
        avatarUrl: string | null;
        role?: string;
        founder: { slug: string; presenceLevel: string } | null;
      };
      pin: { kind: string; userId: string; cost: number; createdAt: Date } | null;
      replyTo?: {
        id: string;
        body: string;
        author: { name: string | null; platformHandle: string | null };
      } | null;
      reactions?: { emoji: string; userId: string }[];
    },
    project: {
      id: string;
      slug: string;
      name: string;
      ticker: string;
      logoUrl: string | null;
      founder?: { slug: string } | null;
    },
    viewerId?: string,
  ): WallMessageDto {
    const founderSlug = project.founder?.slug ?? m.author.founder?.slug ?? null;
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
        isAdmin: m.author.role === UserRole.ADMIN,
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
      replyTo: m.replyTo
        ? {
            id: m.replyTo.id,
            body: m.replyTo.body.slice(0, 160),
            authorLabel: m.replyTo.author.name ?? m.replyTo.author.platformHandle ?? 'Anon',
          }
        : null,
      reactions: this.summarizeReactions(m.reactions ?? [], viewerId),
      hidden: Boolean(m.hiddenAt),
      links: {
        project: `/project/${project.slug}`,
        raiseRoom: `/raise-room?project=${encodeURIComponent(project.slug)}`,
        founderSpotlight: founderSlug ? `/founder/${founderSlug}` : null,
      },
    };
  }

  private summarizeReactions(
    reactions: { emoji: string; userId: string }[],
    viewerId?: string,
  ): WallReactionSummary[] {
    const map = new Map<string, { count: number; mine: boolean }>();
    for (const r of reactions) {
      const cur = map.get(r.emoji) ?? { count: 0, mine: false };
      cur.count += 1;
      if (viewerId && r.userId === viewerId) cur.mine = true;
      map.set(r.emoji, cur);
    }
    return [...map.entries()]
      .map(([emoji, v]) => ({ emoji, count: v.count, mine: v.mine }))
      .sort((a, b) => b.count - a.count || a.emoji.localeCompare(b.emoji));
  }

  private async notifyMentions(
    authorId: string,
    project: { id: string; slug: string; name: string },
    body: string,
    messageId: string,
    author: { name: string | null; platformHandle: string | null } | null,
  ) {
    const handles = [...body.matchAll(/@([a-zA-Z0-9_]{2,32})/g)].map((m) => m[1]!.toLowerCase());
    if (handles.length === 0) return;
    const unique = [...new Set(handles)].slice(0, 10);
    const users = await this.prisma.user.findMany({
      where: {
        OR: unique.map((h) => ({ platformHandle: { equals: h, mode: 'insensitive' } })),
      },
      select: { id: true, platformHandle: true },
    });
    const authorLabel = author?.name ?? (author?.platformHandle ? `@${author.platformHandle}` : 'Someone');
    for (const u of users) {
      if (u.id === authorId) continue;
      try {
        await this.notifications.notifyUser(u.id, {
          type: NotificationType.WALL_MENTION,
          title: `${authorLabel} mentioned you in ${project.name}`,
          body: body.slice(0, 280),
          link: `/chat?group=${encodeURIComponent(project.slug)}`,
          metadata: { projectId: project.id, messageId, fromUserId: authorId },
        });
        this.chatEvents.emitToUser(u.id, {
          type: 'wall',
          projectId: project.id,
          slug: project.slug,
        });
      } catch {
        /* non-fatal */
      }
    }
  }

  async toggleReaction(userId: string, messageId: string, emoji: string) {
    if (!CHAT_REACTION_EMOJIS.includes(emoji as (typeof CHAT_REACTION_EMOJIS)[number])) {
      throw new BadRequestException(`Unsupported emoji. Allowed: ${CHAT_REACTION_EMOJIS.join(' ')}`);
    }
    const message = await this.prisma.projectWallMessage.findUnique({
      where: { id: messageId },
      select: { id: true, projectId: true, project: { select: { slug: true } } },
    });
    if (!message) throw new NotFoundException('Message not found');

    const existing = await this.prisma.projectWallReaction.findUnique({
      where: { messageId_userId_emoji: { messageId, userId, emoji } },
    });
    if (existing) {
      await this.prisma.projectWallReaction.delete({ where: { id: existing.id } });
    } else {
      await this.prisma.projectWallReaction.create({ data: { messageId, userId, emoji } });
    }

    const reactions = await this.prisma.projectWallReaction.findMany({
      where: { messageId },
      select: { emoji: true, userId: true },
    });
    this.chatEvents.emitToUser(userId, {
      type: 'wall',
      projectId: message.projectId,
      slug: message.project.slug,
    });
    return { messageId, reactions: this.summarizeReactions(reactions, userId) };
  }

  async reportMessage(userId: string, messageId: string, reason: string) {
    const trimmed = reason.trim();
    if (trimmed.length < 3) throw new BadRequestException('Please provide a short reason.');
    const message = await this.prisma.projectWallMessage.findUnique({
      where: { id: messageId },
      select: {
        id: true,
        authorId: true,
        projectId: true,
        project: { select: { slug: true, founder: { select: { userId: true } } } },
      },
    });
    if (!message) throw new NotFoundException('Message not found');
    if (message.authorId === userId) {
      throw new BadRequestException('You cannot report your own message.');
    }

    await this.prisma.projectWallReport.upsert({
      where: { messageId_reporterId: { messageId, reporterId: userId } },
      create: { messageId, reporterId: userId, reason: trimmed.slice(0, 500), status: 'OPEN' },
      update: { reason: trimmed.slice(0, 500), status: 'OPEN' },
    });

    // Notify founder (best-effort) so they can mute / hide.
    const founderId = message.project.founder?.userId;
    if (founderId && founderId !== userId) {
      try {
        await this.notifications.notifyUser(founderId, {
          type: NotificationType.SYSTEM,
          title: `Wall report on ${message.project.slug}`,
          body: trimmed.slice(0, 280),
          link: `/chat?group=${encodeURIComponent(message.project.slug)}`,
          metadata: { messageId, reporterId: userId, kind: 'WALL_REPORT' },
        });
      } catch {
        /* non-fatal */
      }
    }

    return { success: true as const, hiddenForYou: true };
  }

  async hideMessage(actorId: string, messageId: string) {
    const message = await this.prisma.projectWallMessage.findUnique({
      where: { id: messageId },
      select: {
        id: true,
        projectId: true,
        project: {
          select: {
            slug: true,
            founder: { select: { userId: true } },
          },
        },
      },
    });
    if (!message) throw new NotFoundException('Message not found');

    const actor = await this.prisma.user.findUnique({
      where: { id: actorId },
      select: { role: true },
    });
    const isFounder = message.project.founder?.userId === actorId;
    const isAdmin = actor?.role === UserRole.ADMIN;
    if (!isFounder && !isAdmin) {
      throw new ForbiddenException('Only the project founder or an admin can hide messages.');
    }

    await this.prisma.projectWallMessage.update({
      where: { id: messageId },
      data: { hiddenAt: new Date(), hiddenById: actorId },
    });
    await this.prisma.projectWallReport.updateMany({
      where: { messageId, status: 'OPEN' },
      data: { status: 'RESOLVED' },
    });

    this.chatEvents.emitToUser(actorId, {
      type: 'wall',
      projectId: message.projectId,
      slug: message.project.slug,
    });
    return { success: true as const };
  }

  async muteUser(
    actorId: string,
    slug: string,
    targetUserId: string,
    hours = 24,
    reason?: string,
  ) {
    const project = await this.prisma.project.findFirst({
      where: { slug },
      select: { id: true, slug: true, founder: { select: { userId: true } } },
    });
    if (!project) throw new NotFoundException('Project not found');

    const actor = await this.prisma.user.findUnique({
      where: { id: actorId },
      select: { role: true },
    });
    const isFounder = project.founder?.userId === actorId;
    const isAdmin = actor?.role === UserRole.ADMIN;
    if (!isFounder && !isAdmin) {
      throw new ForbiddenException('Only the project founder or an admin can mute users.');
    }
    if (targetUserId === project.founder?.userId) {
      throw new BadRequestException('Cannot mute the project founder.');
    }

    const mutedUntil = new Date(Date.now() + Math.min(Math.max(hours, 1), 168) * 3600_000);
    const row = await this.prisma.projectWallMute.upsert({
      where: { projectId_userId: { projectId: project.id, userId: targetUserId } },
      create: {
        projectId: project.id,
        userId: targetUserId,
        mutedById: actorId,
        reason: reason?.slice(0, 280) ?? null,
        mutedUntil,
      },
      update: {
        mutedById: actorId,
        reason: reason?.slice(0, 280) ?? null,
        mutedUntil,
      },
    });

    return {
      success: true as const,
      userId: targetUserId,
      mutedUntil: row.mutedUntil.toISOString(),
    };
  }

  async getSettings(slug: string) {
    const project = await this.prisma.project.findFirst({
      where: { slug },
      select: {
        id: true,
        wallSettings: true,
        founder: { select: { slug: true } },
      },
    });
    if (!project) throw new NotFoundException('Project not found');
    return {
      postingMode: (project.wallSettings?.postingMode === 'ANNOUNCEMENTS'
        ? 'ANNOUNCEMENTS'
        : 'OPEN') as 'OPEN' | 'ANNOUNCEMENTS',
      slowModeSeconds: project.wallSettings?.slowModeSeconds ?? 0,
      links: {
        project: `/project/${slug}`,
        raiseRoom: `/raise-room?project=${encodeURIComponent(slug)}`,
        founderSpotlight: project.founder?.slug ? `/founder/${project.founder.slug}` : null,
      },
    };
  }

  async updateSettings(
    actorId: string,
    slug: string,
    patch: { postingMode?: 'OPEN' | 'ANNOUNCEMENTS'; slowModeSeconds?: number },
  ) {
    const project = await this.prisma.project.findFirst({
      where: { slug },
      select: { id: true, founder: { select: { userId: true } } },
    });
    if (!project) throw new NotFoundException('Project not found');

    const actor = await this.prisma.user.findUnique({
      where: { id: actorId },
      select: { role: true },
    });
    const isFounder = project.founder?.userId === actorId;
    const isAdmin = actor?.role === UserRole.ADMIN;
    if (!isFounder && !isAdmin) {
      throw new ForbiddenException('Only the project founder or an admin can change wall settings.');
    }

    const postingMode =
      patch.postingMode === 'ANNOUNCEMENTS' ? 'ANNOUNCEMENTS' : patch.postingMode === 'OPEN' ? 'OPEN' : undefined;
    const slowModeSeconds =
      patch.slowModeSeconds !== undefined
        ? Math.min(Math.max(Math.floor(patch.slowModeSeconds), 0), 3600)
        : undefined;

    const row = await this.prisma.projectWallSettings.upsert({
      where: { projectId: project.id },
      create: {
        projectId: project.id,
        postingMode: postingMode ?? 'OPEN',
        slowModeSeconds: slowModeSeconds ?? 0,
      },
      update: {
        ...(postingMode ? { postingMode } : {}),
        ...(slowModeSeconds !== undefined ? { slowModeSeconds } : {}),
      },
    });

    return {
      postingMode: (row.postingMode === 'ANNOUNCEMENTS' ? 'ANNOUNCEMENTS' : 'OPEN') as
        | 'OPEN'
        | 'ANNOUNCEMENTS',
      slowModeSeconds: row.slowModeSeconds,
    };
  }
}
