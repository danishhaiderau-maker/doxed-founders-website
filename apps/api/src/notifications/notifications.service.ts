import { Injectable } from '@nestjs/common';
import { NotificationType, Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

function inboxCategoryFilter(category: string): Prisma.NotificationWhereInput | undefined {
  switch (category) {
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
      })),
    });

    return users.length;
  }

  async notifyUser(
    userId: string,
    input: {
      type: NotificationType;
      title: string;
      body: string;
      link?: string;
    },
  ) {
    return this.prisma.notification.create({
      data: { userId, ...input },
    });
  }
}
