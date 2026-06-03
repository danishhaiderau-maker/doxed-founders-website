import { Injectable } from '@nestjs/common';
import { formatPublicAccountLabel, mergeNotificationPreferences } from '@dcf/utils';
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
  }) {
    const users = await this.prisma.user.findMany({
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

  /** Notify users following a trader when they open a conviction buy (amount threshold avoids spam). */
  async notifyFollowersOfTraderBuy(
    traderUserId: string,
    input: { ticker: string; amountUsd: number; projectSlug?: string },
  ) {
    if (input.amountUsd < 100) return 0;

    const [trader, followers] = await Promise.all([
      this.prisma.user.findUnique({
        where: { id: traderUserId },
        select: { name: true, email: true },
      }),
      this.prisma.userFollow.findMany({
        where: { followingId: traderUserId },
        select: {
          follower: { select: { id: true, notificationPrefs: true } },
        },
      }),
    ]);

    if (!trader || followers.length === 0) return 0;

    const label = formatPublicAccountLabel(trader.name, trader.email);
    const link = input.projectSlug ? `/project/${input.projectSlug}` : '/paper-trading';
    let sent = 0;

    for (const row of followers) {
      const prefs = mergeNotificationPreferences(
        row.follower.notificationPrefs as Parameters<typeof mergeNotificationPreferences>[0],
      );
      if (!prefs.following.followedTraderBought) continue;

      await this.notifyUser(row.follower.id, {
        type: NotificationType.TRADER_WIN,
        title: `${label} opened conviction trade`,
        body: `Bought $${Math.round(input.amountUsd).toLocaleString()} of ${input.ticker}`,
        link,
        metadata: {
          traderUserId,
          displayName: label,
          ticker: input.ticker,
          amountUsd: input.amountUsd,
        },
      });
      sent += 1;
    }

    return sent;
  }
}
