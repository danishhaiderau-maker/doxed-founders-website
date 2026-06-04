import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { NotificationType } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { NotificationsService } from '../notifications/notifications.service';
import { buildUserIdentity, findUserByMessagingQuery, labelForUser } from '../account/user-identity.util';

@Injectable()
export class MessagesService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly notifications: NotificationsService,
  ) {}

  async sendMessage(
    fromUserId: string,
    toUserId: string,
    body: string,
    options?: { applicationId?: string },
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

    const message = await this.prisma.platformMessage.create({
      data: {
        fromUserId,
        toUserId,
        applicationId: options?.applicationId,
        body: trimmed,
      },
    });

    const senderLabel = labelForUser({
      id: from.id,
      name: from.name,
      email: from.email,
      platformHandle: from.platformHandle,
      twitterHandle: from.twitterHandle,
    });

    await this.notifications.notifyUser(toUserId, {
      type: NotificationType.PLATFORM_MESSAGE,
      title: `Message from ${senderLabel}`,
      body: trimmed.slice(0, 280),
      link: `/account?tab=messages&with=${fromUserId}`,
      metadata: {
        fromUserId,
        messageId: message.id,
        applicationId: options?.applicationId ?? null,
      },
    });

    return message;
  }

  async listThreads(userId: string) {
    const messages = await this.prisma.platformMessage.findMany({
      where: { OR: [{ fromUserId: userId }, { toUserId: userId }] },
      orderBy: { createdAt: 'desc' },
      take: 200,
      include: {
        from: {
          select: {
            id: true,
            name: true,
            email: true,
            platformHandle: true,
            twitterHandle: true,
            role: true,
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
            oauthAccounts: { select: { provider: true }, take: 3 },
          },
        },
        application: { select: { id: true, projectName: true, ticker: true } },
      },
    });

    const threadMap = new Map<
      string,
      {
        otherUserId: string;
        otherUserLabel: string;
        lastBody: string;
        lastAt: string;
        unreadCount: number;
        applicationId: string | null;
        applicationLabel: string | null;
      }
    >();

    for (const m of messages) {
      const other = m.fromUserId === userId ? m.to : m.from;
      const key = other.id;
      if (threadMap.has(key)) continue;
      const unread = messages.filter(
        (x) => x.toUserId === userId && x.fromUserId === other.id && !x.readAt,
      ).length;
      threadMap.set(key, {
        otherUserId: other.id,
        otherUserLabel: labelForUser(other),
        lastBody: m.body,
        lastAt: m.createdAt.toISOString(),
        unreadCount: unread,
        applicationId: m.applicationId,
        applicationLabel: m.application
          ? `${m.application.projectName} (${m.application.ticker})`
          : null,
      });
    }

    return Array.from(threadMap.values()).sort(
      (a, b) => new Date(b.lastAt).getTime() - new Date(a.lastAt).getTime(),
    );
  }

  async countUnread(userId: string): Promise<number> {
    return this.prisma.platformMessage.count({
      where: { toUserId: userId, readAt: null },
    });
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

    const { identity } = buildUserIdentity(user);

    return {
      userId: user.id,
      label: identity.primaryLabel,
      platformHandle: user.platformHandle,
      messagingAddress: identity.messagingAddress,
      twitterHandle: identity.twitterHandle,
      twitterUrl: identity.twitterUrl,
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
            oauthAccounts: { select: { provider: true }, take: 3 },
          },
        },
      },
    });

    await this.prisma.platformMessage.updateMany({
      where: { fromUserId: otherUserId, toUserId: userId, readAt: null },
      data: { readAt: new Date() },
    });

    return messages.map((m) => ({
      id: m.id,
      fromUserId: m.fromUserId,
      toUserId: m.toUserId,
      body: m.body,
      createdAt: m.createdAt.toISOString(),
      readAt: m.readAt?.toISOString() ?? null,
      mine: m.fromUserId === userId,
      fromLabel: labelForUser(m.from),
      applicationId: m.applicationId,
    }));
  }
}
