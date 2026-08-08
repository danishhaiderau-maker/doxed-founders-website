import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { NotificationType, UserRole } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { NotificationsService } from '../notifications/notifications.service';
import { buildUserIdentity, findUserByMessagingQuery, labelForUser } from '../account/user-identity.util';
import { ChatEventsService } from './chat-events.service';

export const CHAT_REACTION_EMOJIS = ['👍', '❤️', '😂', '🔥', '👀', '✅'] as const;
export const MAX_PINNED_CHATS = 20;

type ReactionSummary = { emoji: string; count: number; mine: boolean };

@Injectable()
export class MessagesService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly notifications: NotificationsService,
    private readonly chatEvents: ChatEventsService,
  ) {}

  async sendMessage(
    fromUserId: string,
    toUserId: string,
    body: string,
    options?: { applicationId?: string; replyToId?: string },
  ) {
    const trimmed = body.trim();
    if (trimmed.length < 2) throw new BadRequestException('Message too short');
    if (trimmed.length > 4000) throw new BadRequestException('Message too long');

    if (fromUserId === toUserId) {
      throw new BadRequestException('Cannot message yourself');
    }

    const userSelect = {
      id: true,
      name: true,
      email: true,
      platformHandle: true,
      twitterHandle: true,
      oauthAccounts: { select: { provider: true }, take: 3 },
    } as const;
    const [from, to] = await Promise.all([
      this.prisma.user.findUnique({ where: { id: fromUserId }, select: userSelect }),
      this.prisma.user.findUnique({ where: { id: toUserId }, select: userSelect }),
    ]);
    if (!from || !to) throw new NotFoundException('User not found');

    if (options?.applicationId) {
      const app = await this.prisma.listingApplication.findUnique({
        where: { id: options.applicationId },
      });
      if (!app) throw new NotFoundException('Listing application not found');
      if (app.userId !== toUserId && app.userId !== fromUserId) {
        throw new ForbiddenException('Application not linked to this conversation');
      }
    }

    let replyToId: string | null = null;
    if (options?.replyToId) {
      const parent = await this.prisma.platformMessage.findUnique({
        where: { id: options.replyToId },
        select: { id: true, fromUserId: true, toUserId: true, body: true },
      });
      if (!parent) throw new BadRequestException('Reply target not found');
      const participants = new Set([parent.fromUserId, parent.toUserId]);
      if (!participants.has(fromUserId) || !participants.has(toUserId)) {
        throw new BadRequestException('Reply must stay in this conversation');
      }
      replyToId = parent.id;
    }

    const message = await this.prisma.platformMessage.create({
      data: {
        fromUserId,
        toUserId,
        applicationId: options?.applicationId,
        body: trimmed,
        replyToId,
      },
    });

    const senderLabel = labelForUser({
      id: from.id,
      name: from.name,
      email: from.email,
      platformHandle: from.platformHandle,
      twitterHandle: from.twitterHandle,
    });

    const muted = await this.prisma.chatThreadPreference.findUnique({
      where: {
        userId_scope_targetId: { userId: toUserId, scope: 'dm', targetId: fromUserId },
      },
      select: { muted: true },
    });

    if (!muted?.muted) {
      await this.notifications.notifyUser(toUserId, {
        type: NotificationType.PLATFORM_MESSAGE,
        title: `Message from ${senderLabel}`,
        body: trimmed.slice(0, 280),
        link: `/chat?dm=${fromUserId}`,
        metadata: {
          fromUserId,
          messageId: message.id,
          applicationId: options?.applicationId ?? null,
          replyToId,
        },
      });
    }

    this.chatEvents.emitToUser(toUserId, { type: 'dm', otherUserId: fromUserId });
    this.chatEvents.emitToUser(fromUserId, { type: 'dm', otherUserId: toUserId });

    return message;
  }

  async listThreads(userId: string) {
    const messages = await this.prisma.platformMessage.findMany({
      where: { OR: [{ fromUserId: userId }, { toUserId: userId }] },
      orderBy: { createdAt: 'desc' },
      take: 300,
      include: {
        from: {
          select: {
            id: true,
            name: true,
            email: true,
            platformHandle: true,
            twitterHandle: true,
            role: true,
            lastSeenAt: true,
            founder: { select: { presenceLevel: true, slug: true } },
            oauthAccounts: { select: { provider: true }, take: 3 },
          },
        },
        to: {
          select: {
            id: true,
            name: true,
            email: true,
            platformHandle: true,
            twitterHandle: true,
            role: true,
            lastSeenAt: true,
            founder: { select: { presenceLevel: true, slug: true } },
            oauthAccounts: { select: { provider: true }, take: 3 },
          },
        },
        application: { select: { id: true, projectName: true, ticker: true } },
      },
    });

    const prefs = await this.prisma.chatThreadPreference.findMany({
      where: { userId, scope: 'dm' },
    });
    const prefByTarget = new Map(prefs.map((p) => [p.targetId, p]));

    const threadMap = new Map<
      string,
      {
        otherUserId: string;
        otherUserLabel: string;
        otherUserRole: string;
        isAdmin: boolean;
        isVerifiedFounder: boolean;
        founderSlug: string | null;
        lastSeenAt: string | null;
        online: boolean;
        lastBody: string;
        lastAt: string;
        unreadCount: number;
        applicationId: string | null;
        applicationLabel: string | null;
        pinned: boolean;
        muted: boolean;
        archived: boolean;
        pinnedAt: string | null;
      }
    >();

    const now = Date.now();
    for (const m of messages) {
      const other = m.fromUserId === userId ? m.to : m.from;
      const key = other.id;
      if (threadMap.has(key)) continue;
      const pref = prefByTarget.get(other.id);
      const unread = messages.filter(
        (x) => x.toUserId === userId && x.fromUserId === other.id && !x.readAt,
      ).length;
      const lastSeen = other.lastSeenAt?.getTime() ?? 0;
      threadMap.set(key, {
        otherUserId: other.id,
        otherUserLabel: labelForUser(other),
        otherUserRole: other.role,
        isAdmin: other.role === UserRole.ADMIN,
        isVerifiedFounder: Boolean(
          other.founder && other.founder.presenceLevel !== 'UNVERIFIED',
        ),
        founderSlug: other.founder?.slug ?? null,
        lastSeenAt: other.lastSeenAt?.toISOString() ?? null,
        online: lastSeen > 0 && now - lastSeen < 5 * 60_000,
        lastBody: m.body,
        lastAt: m.createdAt.toISOString(),
        unreadCount: unread,
        applicationId: m.applicationId,
        applicationLabel: m.application
          ? `${m.application.projectName} (${m.application.ticker})`
          : null,
        pinned: Boolean(pref?.pinned),
        muted: Boolean(pref?.muted),
        archived: Boolean(pref?.archived),
        pinnedAt: pref?.pinnedAt?.toISOString() ?? null,
      });
    }

    return Array.from(threadMap.values()).sort((a, b) => {
      if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
      if (a.pinned && b.pinned) {
        return (b.pinnedAt ?? '').localeCompare(a.pinnedAt ?? '');
      }
      return new Date(b.lastAt).getTime() - new Date(a.lastAt).getTime();
    });
  }

  async countUnread(userId: string): Promise<number> {
    return this.prisma.platformMessage.count({
      where: { toUserId: userId, readAt: null },
    });
  }

  async markAllRead(userId: string) {
    const result = await this.prisma.platformMessage.updateMany({
      where: { toUserId: userId, readAt: null },
      data: { readAt: new Date() },
    });
    this.chatEvents.emitToUser(userId, { type: 'prefs' });
    return { success: true as const, updated: result.count };
  }

  async resolveRecipient(
    requesterId: string,
    query: string,
  ): Promise<{
    userId: string;
    label: string;
    platformHandle: string | null;
    messagingAddress: string;
    twitterHandle: string | null;
    twitterUrl: string | null;
    role: string;
    isAdmin: boolean;
    isVerifiedFounder: boolean;
  }> {
    const q = query.trim();
    if (q.length < 2) {
      throw new BadRequestException('Enter @handle, messaging address, platform handle, or user ID');
    }

    const user = await findUserByMessagingQuery(this.prisma, q);
    if (!user) throw new NotFoundException('No user found for that address');

    if (user.id === requesterId) {
      throw new BadRequestException('Cannot message yourself');
    }

    const full = await this.prisma.user.findUnique({
      where: { id: user.id },
      select: {
        id: true,
        name: true,
        email: true,
        platformHandle: true,
        twitterHandle: true,
        role: true,
        founder: { select: { presenceLevel: true } },
        oauthAccounts: { select: { provider: true }, take: 3 },
      },
    });
    if (!full) throw new NotFoundException('No user found for that address');

    const { identity } = buildUserIdentity(full);

    return {
      userId: full.id,
      label: identity.primaryLabel,
      platformHandle: full.platformHandle,
      messagingAddress: identity.messagingAddress,
      twitterHandle: identity.twitterHandle,
      twitterUrl: identity.twitterUrl,
      role: full.role,
      isAdmin: full.role === UserRole.ADMIN,
      isVerifiedFounder: Boolean(
        full.founder && full.founder.presenceLevel !== 'UNVERIFIED',
      ),
    };
  }

  async getConversation(userId: string, otherUserId: string, limit = 80) {
    const messages = await this.prisma.platformMessage.findMany({
      where: {
        OR: [
          { fromUserId: userId, toUserId: otherUserId },
          { fromUserId: otherUserId, toUserId: userId },
        ],
      },
      orderBy: { createdAt: 'asc' },
      take: limit,
      include: {
        from: {
          select: {
            id: true,
            name: true,
            email: true,
            platformHandle: true,
            twitterHandle: true,
            role: true,
            founder: { select: { presenceLevel: true, slug: true } },
            oauthAccounts: { select: { provider: true }, take: 3 },
          },
        },
        replyTo: {
          select: {
            id: true,
            body: true,
            fromUserId: true,
            from: {
              select: {
                id: true,
                name: true,
                platformHandle: true,
                email: true,
                twitterHandle: true,
                oauthAccounts: { select: { provider: true }, take: 1 },
              },
            },
          },
        },
        reactions: { select: { emoji: true, userId: true } },
      },
    });

    await this.prisma.platformMessage.updateMany({
      where: { fromUserId: otherUserId, toUserId: userId, readAt: null },
      data: { readAt: new Date() },
    });

    const other = await this.prisma.user.findUnique({
      where: { id: otherUserId },
      select: {
        id: true,
        lastSeenAt: true,
        role: true,
        founder: { select: { presenceLevel: true, slug: true } },
      },
    });

    const mapped = messages.map((m) => ({
      id: m.id,
      fromUserId: m.fromUserId,
      toUserId: m.toUserId,
      body: m.body,
      createdAt: m.createdAt.toISOString(),
      readAt: m.readAt?.toISOString() ?? null,
      mine: m.fromUserId === userId,
      fromLabel: labelForUser(m.from),
      fromRole: m.from.role,
      isAdmin: m.from.role === UserRole.ADMIN,
      isVerifiedFounder: Boolean(
        m.from.founder && m.from.founder.presenceLevel !== 'UNVERIFIED',
      ),
      founderSlug: m.from.founder?.slug ?? null,
      applicationId: m.applicationId,
      replyTo: m.replyTo
        ? {
            id: m.replyTo.id,
            body: m.replyTo.body.slice(0, 160),
            fromLabel: labelForUser(m.replyTo.from),
          }
        : null,
      reactions: this.summarizeReactions(m.reactions, userId),
    }));

    const lastSeen = other?.lastSeenAt?.getTime() ?? 0;
    return {
      messages: mapped,
      peer: other
        ? {
            userId: other.id,
            lastSeenAt: other.lastSeenAt?.toISOString() ?? null,
            online: lastSeen > 0 && Date.now() - lastSeen < 5 * 60_000,
            isAdmin: other.role === UserRole.ADMIN,
            isVerifiedFounder: Boolean(
              other.founder && other.founder.presenceLevel !== 'UNVERIFIED',
            ),
            founderSlug: other.founder?.slug ?? null,
          }
        : null,
    };
  }

  async toggleReaction(userId: string, messageId: string, emoji: string) {
    if (!CHAT_REACTION_EMOJIS.includes(emoji as (typeof CHAT_REACTION_EMOJIS)[number])) {
      throw new BadRequestException(`Unsupported emoji. Allowed: ${CHAT_REACTION_EMOJIS.join(' ')}`);
    }

    const message = await this.prisma.platformMessage.findUnique({
      where: { id: messageId },
      select: { id: true, fromUserId: true, toUserId: true },
    });
    if (!message) throw new NotFoundException('Message not found');
    if (message.fromUserId !== userId && message.toUserId !== userId) {
      throw new ForbiddenException('Not a participant in this conversation');
    }

    const existing = await this.prisma.platformMessageReaction.findUnique({
      where: {
        messageId_userId_emoji: { messageId, userId, emoji },
      },
    });

    if (existing) {
      await this.prisma.platformMessageReaction.delete({ where: { id: existing.id } });
    } else {
      await this.prisma.platformMessageReaction.create({
        data: { messageId, userId, emoji },
      });
    }

    const otherUserId = message.fromUserId === userId ? message.toUserId : message.fromUserId;
    this.chatEvents.emitToUsers([userId, otherUserId], { type: 'dm', otherUserId });

    const reactions = await this.prisma.platformMessageReaction.findMany({
      where: { messageId },
      select: { emoji: true, userId: true },
    });
    return { messageId, reactions: this.summarizeReactions(reactions, userId) };
  }

  async upsertThreadPref(
    userId: string,
    scope: 'dm' | 'wall',
    targetId: string,
    patch: { pinned?: boolean; muted?: boolean; archived?: boolean },
  ) {
    if (!targetId.trim()) throw new BadRequestException('targetId required');

    const existing = await this.prisma.chatThreadPreference.findUnique({
      where: { userId_scope_targetId: { userId, scope, targetId } },
    });

    const nextPinned = patch.pinned ?? existing?.pinned ?? false;
    if (nextPinned && !existing?.pinned) {
      const pinnedCount = await this.prisma.chatThreadPreference.count({
        where: { userId, pinned: true },
      });
      if (pinnedCount >= MAX_PINNED_CHATS) {
        throw new BadRequestException(
          `You can pin up to ${MAX_PINNED_CHATS} chats for free. Unpin one first.`,
        );
      }
    }

    const row = await this.prisma.chatThreadPreference.upsert({
      where: { userId_scope_targetId: { userId, scope, targetId } },
      create: {
        userId,
        scope,
        targetId,
        pinned: patch.pinned ?? false,
        muted: patch.muted ?? false,
        archived: patch.archived ?? false,
        pinnedAt: patch.pinned ? new Date() : null,
      },
      update: {
        ...(patch.pinned !== undefined
          ? { pinned: patch.pinned, pinnedAt: patch.pinned ? new Date() : null }
          : {}),
        ...(patch.muted !== undefined ? { muted: patch.muted } : {}),
        ...(patch.archived !== undefined ? { archived: patch.archived } : {}),
      },
    });

    this.chatEvents.emitToUser(userId, { type: 'prefs' });

    return {
      scope: row.scope,
      targetId: row.targetId,
      pinned: row.pinned,
      muted: row.muted,
      archived: row.archived,
      pinnedAt: row.pinnedAt?.toISOString() ?? null,
      maxPins: MAX_PINNED_CHATS,
    };
  }

  async listThreadPrefs(userId: string) {
    const rows = await this.prisma.chatThreadPreference.findMany({
      where: { userId },
      orderBy: [{ pinned: 'desc' }, { pinnedAt: 'desc' }],
    });
    const pinnedCount = rows.filter((r) => r.pinned).length;
    return {
      maxPins: MAX_PINNED_CHATS,
      pinnedCount,
      prefs: rows.map((r) => ({
        scope: r.scope,
        targetId: r.targetId,
        pinned: r.pinned,
        muted: r.muted,
        archived: r.archived,
        pinnedAt: r.pinnedAt?.toISOString() ?? null,
      })),
    };
  }

  async heartbeat(userId: string) {
    const now = new Date();
    await this.prisma.user.update({
      where: { id: userId },
      data: { lastSeenAt: now },
    });
    this.chatEvents.emitToUser(userId, { type: 'presence', userId });
    return { lastSeenAt: now.toISOString() };
  }

  async getPresence(userIds: string[]) {
    const ids = [...new Set(userIds.filter(Boolean))].slice(0, 50);
    if (ids.length === 0) return [];
    const users = await this.prisma.user.findMany({
      where: { id: { in: ids } },
      select: { id: true, lastSeenAt: true },
    });
    const now = Date.now();
    return users.map((u) => {
      const ts = u.lastSeenAt?.getTime() ?? 0;
      return {
        userId: u.id,
        lastSeenAt: u.lastSeenAt?.toISOString() ?? null,
        online: ts > 0 && now - ts < 5 * 60_000,
      };
    });
  }

  private summarizeReactions(
    reactions: { emoji: string; userId: string }[],
    viewerId: string,
  ): ReactionSummary[] {
    const map = new Map<string, { count: number; mine: boolean }>();
    for (const r of reactions) {
      const cur = map.get(r.emoji) ?? { count: 0, mine: false };
      cur.count += 1;
      if (r.userId === viewerId) cur.mine = true;
      map.set(r.emoji, cur);
    }
    return [...map.entries()]
      .map(([emoji, v]) => ({ emoji, count: v.count, mine: v.mine }))
      .sort((a, b) => b.count - a.count || a.emoji.localeCompare(b.emoji));
  }
}
