import { Injectable } from '@nestjs/common';
import { mergeNotificationPreferences } from '@dcf/utils';
import { NotificationType, Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

function inboxCategoryFilter(category: string): Prisma.NotificationWhereInput | undefined {
  switch (category) {
    case 'following':
      return {
        type: { in: [NotificationType.FOUNDER_UPDATES, NotificationType.TRADER_WIN, NotificationType.TRADER_LOSS] },
      };
    case 'projects':
      return { type: NotificationType.FOUNDER_UPDATES };
    case 'market':
      return {
        type: { in: [NotificationType.TRENDING_BUYS, NotificationType.TRADER_WIN, NotificationType.TRADER_LOSS] },
      };
    case 'platform':
      return {
        type: {
          in: [
            NotificationType.LISTING_VOTING,
            NotificationType.LISTING_APPROVED,
            NotificationType.LISTING_PROOF_REQUEST,
            NotificationType.PLATFORM_MESSAGE,
            NotificationType.SYSTEM,
            NotificationType.FOUNDER_EVENT,
          ],
        },
      };
    case 'build':
      return { type: NotificationType.BUILD_QUEUE };
    case 'agents':
      return { type: NotificationType.AGENT_RESULT };
    case 'community':
      return { type: { in: [NotificationType.FOUNDER_UPDATES, NotificationType.POINTS_EARNED] } };
    case 'funding':
      return { type: NotificationType.POINTS_EARNED };
    default:
      return undefined;
  }
}

@Injectable()
export class NotificationsService {
  constructor(private readonly prisma: PrismaService) {}

  async listForUser(userId: string, limit = 30, category?: string) {
    const categoryFilter = category ? inboxCategoryFilter(category) : undefined;
    return this.prisma.notification.findMany({
      where: { userId, ...categoryFilter },
      orderBy: { createdAt: 'desc' },
      take: limit,
    });
  }

  async unreadCount(userId: string) {
    return this.prisma.notification.count({
      where: { userId, readAt: null },
    });
  }

  async markRead(userId: string, notificationId: string) {
    return this.prisma.notification.updateMany({
      where: { id: notificationId, userId },
      data: { readAt: new Date() },
    });
  }

  async markAllRead(userId: string) {
    return this.prisma.notification.updateMany({
      where: { userId, readAt: null },
      data: { readAt: new Date() },
    });
  }

  async notifyAllUsers(input: {
    type: NotificationType;
    title: string;
    body: string;
    link?: string;
    metadata?: Record<string, unknown>;
  }) {
    const users = await this.prisma.user.findMany({
      where: { banned: false },
      select: { id: true },
    });

    if (users.length === 0) return 0;

    await this.prisma.notification.createMany({
      data: users.map((user) => ({
        userId: user.id,
        type: input.type,
        title: input.title,
        body: input.body,
        link: input.link,
        metadata: input.metadata as Prisma.InputJsonValue | undefined,
      })),
    });

    return users.length;
  }

  /** Market alerts respect user notification prefs (hot buys, etc.). */
  async notifyMarketAlert(input: {
    type: NotificationType;
    title: string;
    body: string;
    link?: string;
    metadata?: Record<string, unknown>;
    /** When set, only these users receive the alert (avoids platform-wide spam). */
    recipientIds?: string[];
  }) {
    if (input.recipientIds && input.recipientIds.length === 0) return 0;

    const users = input.recipientIds?.length
      ? await this.prisma.user.findMany({
          where: { id: { in: input.recipientIds }, banned: false },
          select: { id: true, notificationPrefs: true },
        })
      : await this.prisma.user.findMany({
          where: { banned: false },
          select: { id: true, notificationPrefs: true },
        });

    let sent = 0;
    for (const user of users) {
      const prefs = mergeNotificationPreferences(
        user.notificationPrefs as Parameters<typeof mergeNotificationPreferences>[0],
      );
      if (input.type === NotificationType.TRENDING_BUYS && !prefs.market.hotBuys) continue;

      await this.notifyUser(user.id, input);
      sent += 1;
    }
    return sent;
  }

  async notifyUser(
    userId: string,
    input: {
      type: NotificationType;
      title: string;
      body: string;
      link?: string;
      metadata?: Record<string, unknown>;
    },
  ) {
    return this.prisma.notification.create({
      data: {
        userId,
        type: input.type,
        title: input.title,
        body: input.body,
        link: input.link,
        metadata: input.metadata as Prisma.InputJsonValue | undefined,
      },
    });
  }

  /**
   * @deprecated Use HighValueInsightsService.notifyVerifiedTraderBuy — kept for callers migrating.
   */
  async notifyFollowersOfTraderBuy(
    traderUserId: string,
    input: { ticker: string; amountUsd: number; projectSlug?: string },
  ) {
    void traderUserId;
    void input;
    return 0;
  }
}
