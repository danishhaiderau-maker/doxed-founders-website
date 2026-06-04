import { Injectable } from '@nestjs/common';
import {
  formatPublicAccountLabel,
  mergeNotificationPreferences,
  userHasTwitterConnected,
} from '@dcf/utils';
import { NotificationType, UserRole } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { MessagesService } from '../messages/messages.service';
import { NotificationsService } from './notifications.service';

const TRADER_BUY_MIN_USD = 150;
const TRADER_BUY_COOLDOWN_MS = 4 * 60 * 60 * 1000;
const COPY_WATCH_DAYS = 21;
const PLATFORM_INSIGHT_LABEL = 'Doxxed Insights';

@Injectable()
export class HighValueInsightsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly notifications: NotificationsService,
    private readonly messages: MessagesService,
  ) {}

  private async platformInsightSenderId(): Promise<string | null> {
    const admin = await this.prisma.user.findFirst({
      where: { role: UserRole.ADMIN, banned: false },
      orderBy: { createdAt: 'asc' },
      select: { id: true },
    });
    return admin?.id ?? null;
  }

  private traderSelect = {
    id: true,
    name: true,
    email: true,
    platformHandle: true,
    twitterHandle: true,
    oauthAccounts: { select: { provider: true }, take: 3 },
  } as const;

  async isVerifiedSocialTrader(userId: string): Promise<boolean> {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: this.traderSelect,
    });
    return user ? userHasTwitterConnected(user) : false;
  }

  private async recentInsightSent(
    recipientId: string,
    traderUserId: string,
  ): Promise<boolean> {
    const since = new Date(Date.now() - TRADER_BUY_COOLDOWN_MS);
    const hit = await this.prisma.notification.findFirst({
      where: {
        userId: recipientId,
        type: { in: [NotificationType.TRADER_WIN, NotificationType.TRENDING_BUYS] },
        createdAt: { gte: since },
        metadata: {
          path: ['traderUserId'],
          equals: traderUserId,
        },
      },
    });
    return Boolean(hit);
  }

  /** Followers + recent copy-traders when a verified-X trader opens a conviction buy. */
  async notifyVerifiedTraderBuy(
    traderUserId: string,
    input: { ticker: string; amountUsd: number; projectSlug?: string },
  ) {
    if (input.amountUsd < TRADER_BUY_MIN_USD) return 0;

    const trader = await this.prisma.user.findUnique({
      where: { id: traderUserId },
      select: this.traderSelect,
    });
    if (!trader || !userHasTwitterConnected(trader)) return 0;

    const label = formatPublicAccountLabel(
      trader.name,
      trader.email,
      trader.platformHandle,
      trader.twitterHandle,
      { hasTwitterConnected: true },
    );
    const handle = trader.twitterHandle ? `@${trader.twitterHandle.replace(/^@/, '')}` : label;
    const link = input.projectSlug ? `/project/${input.projectSlug}` : '/feed';
    const title = `${handle} opened conviction trade`;
    const body = `Bought ${formatUsdCompact(input.amountUsd)} of ${input.ticker} — verified X profile on Doxxed Crypto`;
    const messageBody = `${title}\n\n${body}\n\nView feed: ${link}`;

    const recipientIds = new Set<string>();

    const [followers, copiers] = await Promise.all([
      this.prisma.userFollow.findMany({
        where: { followingId: traderUserId },
        select: { followerId: true },
      }),
      this.prisma.paperTrade.findMany({
        where: {
          inspiredByUserId: traderUserId,
          createdAt: { gte: new Date(Date.now() - COPY_WATCH_DAYS * 86400000) },
        },
        select: { userId: true },
        distinct: ['userId'],
      }),
    ]);

    for (const f of followers) recipientIds.add(f.followerId);
    for (const c of copiers) {
      if (c.userId !== traderUserId) recipientIds.add(c.userId);
    }

    const platformSenderId = await this.platformInsightSenderId();
    let sent = 0;

    for (const recipientId of recipientIds) {
      if (recipientId === traderUserId) continue;
      if (await this.recentInsightSent(recipientId, traderUserId)) continue;

      const prefsRow = await this.prisma.user.findUnique({
        where: { id: recipientId },
        select: { notificationPrefs: true },
      });
      const prefs = mergeNotificationPreferences(
        prefsRow?.notificationPrefs as Parameters<typeof mergeNotificationPreferences>[0],
      );
      if (!prefs.following.followedTraderBought) continue;

      await this.notifications.notifyUser(recipientId, {
        type: NotificationType.TRADER_WIN,
        title,
        body,
        link,
        metadata: {
          traderUserId,
          displayName: label,
          twitterHandle: trader.twitterHandle,
          ticker: input.ticker,
          amountUsd: input.amountUsd,
          insight: 'verified_trader_buy',
        },
      });

      if (platformSenderId) {
        try {
          await this.messages.sendMessage(platformSenderId, recipientId, messageBody);
        } catch {
          /* recipient may block or invalid — notification still delivered */
        }
      }

      sent += 1;
    }

    return sent;
  }

  /** Human-created prediction market by a verified profile — no blast for AI seeds. */
  async notifyHumanPredictionMarket(
    creatorUserId: string,
    input: { question: string; ticker: string; projectSlug: string },
  ) {
    const creator = await this.prisma.user.findUnique({
      where: { id: creatorUserId },
      select: this.traderSelect,
    });
    if (!creator) return 0;

    const verified = userHasTwitterConnected(creator);
    const founder = await this.prisma.founder.findUnique({
      where: { userId: creatorUserId },
      select: { id: true },
    });
    if (!verified && !founder) return 0;

    const label = formatPublicAccountLabel(
      creator.name,
      creator.email,
      creator.platformHandle,
      creator.twitterHandle,
      { hasTwitterConnected: verified },
    );

    const since = new Date(Date.now() - 30 * 86400000);
    const interested = await this.prisma.paperTrade.findMany({
      where: {
        project: { slug: input.projectSlug },
        createdAt: { gte: since },
      },
      select: { userId: true },
      distinct: ['userId'],
      take: 40,
    });

    const q = input.question;
    const snippet = q.length > 120 ? `${q.slice(0, 120)}…` : q;
    let sent = 0;

    for (const row of interested) {
      if (row.userId === creatorUserId) continue;
      const prefsRow = await this.prisma.user.findUnique({
        where: { id: row.userId },
        select: { notificationPrefs: true },
      });
      const prefs = mergeNotificationPreferences(
        prefsRow?.notificationPrefs as Parameters<typeof mergeNotificationPreferences>[0],
      );
      if (!prefs.platform.systemMessages) continue;

      await this.notifications.notifyUser(row.userId, {
        type: NotificationType.SYSTEM,
        title: `New conviction market: ${input.ticker}`,
        body: `${label} asked: “${snippet}”`,
        link: '/predict?tab=markets',
        metadata: {
          creatorUserId,
          projectSlug: input.projectSlug,
          insight: 'human_prediction_market',
        },
      });
      sent += 1;
    }

    return sent;
  }
}

function formatUsdCompact(amount: number): string {
  return `$${Math.round(amount).toLocaleString()}`;
}
