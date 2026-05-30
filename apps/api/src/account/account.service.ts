import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import {
  formatPublicAccountLabel,
  mergeNotificationPreferences,
  pointActionLabel,
  resolveGamifiedRole,
  type NotificationPreferenceGroups,
} from '@dcf/utils';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { ReputationService } from '../reputation/reputation.service';

export type AccountOverview = {
  userId: string;
  username: string;
  email: string;
  avatarUrl: string | null;
  joinedAt: string;
  platformRole: string;
  gamifiedRole: ReturnType<typeof resolveGamifiedRole>;
  isAdmin: boolean;
  adminBanner: string | null;
  authMethods: { provider: string; label: string; connected: boolean }[];
  reputation: Awaited<ReturnType<ReputationService['getMe']>>;
  builderStatus: {
    isFounder: boolean;
    badge: string | null;
    presenceLevel: string | null;
    founderSlug: string | null;
  };
  followingCount: number;
  followersCount: number;
};

@Injectable()
export class AccountService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly reputation: ReputationService,
  ) {}

  async getOverview(userId: string): Promise<AccountOverview> {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: {
        id: true,
        name: true,
        email: true,
        avatarUrl: true,
        role: true,
        progressTier: true,
        reputationPoints: true,
        passwordHash: true,
        createdAt: true,
        oauthAccounts: { select: { provider: true } },
        webAuthnCredentials: { select: { id: true } },
        totp: { select: { enabled: true } },
        walletConnections: { select: { chain: true } },
        founder: {
          select: {
            slug: true,
            videoUrl: true,
            presenceLevel: true,
            reputationScore: true,
            _count: { select: { buildPosts: true } },
          },
        },
        _count: {
          select: {
            following: true,
            followers: true,
            paperTrades: true,
            listingVotes: true,
          },
        },
      },
    });

    if (!user) throw new NotFoundException('User not found');

    const authMethods: AccountOverview['authMethods'] = [];

    if (user.passwordHash) {
      authMethods.push({ provider: 'email', label: 'Email', connected: true });
    }

    for (const oauth of user.oauthAccounts) {
      const provider = oauth.provider.toLowerCase();
      const label =
        provider === 'twitter' || provider === 'x'
          ? 'X (Twitter)'
          : provider.charAt(0).toUpperCase() + provider.slice(1);
      authMethods.push({ provider, label, connected: true });
    }

    if (user.webAuthnCredentials.length > 0) {
      authMethods.push({ provider: 'passkey', label: 'Passkey', connected: true });
    }
    if (user.totp?.enabled) {
      authMethods.push({ provider: 'totp', label: 'Google Authenticator (TOTP)', connected: true });
    }
    for (const wallet of user.walletConnections) {
      authMethods.push({
        provider: `wallet-${wallet.chain.toLowerCase()}`,
        label: `${wallet.chain} Wallet`,
        connected: true,
      });
    }

    const gamifiedRole = resolveGamifiedRole({
      platformRole: user.role,
      progressTier: user.progressTier,
      reputationPoints: user.reputationPoints,
      paperTradeCount: user._count.paperTrades,
      listingVoteCount: user._count.listingVotes,
      founder: user.founder
        ? {
            presenceLevel: user.founder.presenceLevel,
            videoUrl: user.founder.videoUrl,
            reputationScore: user.founder.reputationScore,
            buildPostCount: user.founder._count.buildPosts,
          }
        : null,
    });

    const reputation = await this.reputation.getMe(userId);

    let builderBadge: string | null = null;
    if (user.founder) {
      if (gamifiedRole.badge) {
        builderBadge = gamifiedRole.badge;
      } else if (user.founder.videoUrl) {
        builderBadge = 'Public Founder';
      }
    }

    return {
      userId: user.id,
      username: formatPublicAccountLabel(user.name, user.email),
      email: user.email,
      avatarUrl: user.avatarUrl,
      joinedAt: user.createdAt.toISOString(),
      platformRole: user.role,
      gamifiedRole,
      isAdmin: user.role === 'ADMIN',
      adminBanner:
        user.role === 'ADMIN'
          ? 'You are currently signed in as Platform Admin'
          : null,
      authMethods,
      reputation,
      builderStatus: {
        isFounder: Boolean(user.founder),
        badge: builderBadge,
        presenceLevel: user.founder?.presenceLevel ?? null,
        founderSlug: user.founder?.slug ?? null,
      },
      followingCount: user._count.following,
      followersCount: user._count.followers,
    };
  }

  async getPointLedger(userId: string, limit = 50) {
    const [ledger, awards] = await Promise.all([
      this.prisma.pointLedger.findMany({
        where: { userId },
        orderBy: { createdAt: 'desc' },
        take: limit,
      }),
      this.prisma.reputationAward.findMany({
        where: { userId },
        orderBy: { createdAt: 'desc' },
        take: limit,
      }),
    ]);

    const seen = new Set<string>();
    const items: {
      id: string;
      amount: number;
      actionKey: string;
      label: string;
      createdAt: string;
    }[] = [];

    for (const row of ledger) {
      const key = `${row.actionKey}:${row.createdAt.toISOString()}:${row.amount}`;
      seen.add(key);
      items.push({
        id: row.id,
        amount: row.amount,
        actionKey: row.actionKey,
        label: row.label,
        createdAt: row.createdAt.toISOString(),
      });
    }

    for (const award of awards) {
      const actionKey = award.actionKey.split(':')[0] ?? award.actionKey;
      const key = `${actionKey}:${award.createdAt.toISOString()}:${award.amount}`;
      if (seen.has(key)) continue;
      items.push({
        id: award.id,
        amount: award.amount,
        actionKey,
        label: pointActionLabel(award.actionKey),
        createdAt: award.createdAt.toISOString(),
      });
    }

    return items
      .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
      .slice(0, limit);
  }

  async getNotificationPreferences(userId: string): Promise<NotificationPreferenceGroups> {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { notificationPrefs: true },
    });
    if (!user) throw new NotFoundException('User not found');
    return mergeNotificationPreferences(
      user.notificationPrefs as Partial<NotificationPreferenceGroups> | null,
    );
  }

  async updateNotificationPreferences(
    userId: string,
    prefs: Partial<NotificationPreferenceGroups>,
  ): Promise<NotificationPreferenceGroups> {
    const current = await this.getNotificationPreferences(userId);
    const merged = mergeNotificationPreferences({ ...current, ...prefs });
    await this.prisma.user.update({
      where: { id: userId },
      data: { notificationPrefs: merged as unknown as Prisma.InputJsonValue },
    });
    return merged;
  }

  async followUser(followerId: string, followingId: string) {
    if (followerId === followingId) {
      throw new BadRequestException('You cannot follow yourself');
    }
    const target = await this.prisma.user.findUnique({ where: { id: followingId } });
    if (!target || target.banned) {
      throw new NotFoundException('User not found');
    }
    await this.prisma.userFollow.upsert({
      where: { followerId_followingId: { followerId, followingId } },
      create: { followerId, followingId },
      update: {},
    });
    return { following: true };
  }

  async unfollowUser(followerId: string, followingId: string) {
    await this.prisma.userFollow.deleteMany({
      where: { followerId, followingId },
    });
    return { following: false };
  }

  async listFollowing(userId: string) {
    const rows = await this.prisma.userFollow.findMany({
      where: { followerId: userId },
      include: {
        following: {
          select: {
            id: true,
            name: true,
            email: true,
            twitterHandle: true,
            reputationPoints: true,
            contributorLevel: true,
            founder: { select: { slug: true, name: true } },
          },
        },
      },
      orderBy: { createdAt: 'desc' },
    });

    return rows.map((row) => ({
      userId: row.following.id,
      displayName: formatPublicAccountLabel(row.following.name, row.following.email),
      twitterHandle: row.following.twitterHandle,
      reputationPoints: row.following.reputationPoints,
      contributorLevel: row.following.contributorLevel,
      founderSlug: row.following.founder?.slug ?? null,
      followedAt: row.createdAt.toISOString(),
    }));
  }

  async getActivityHistory(userId: string, limit = 40) {
    const notifications = await this.prisma.notification.findMany({
      where: { userId },
      orderBy: { createdAt: 'desc' },
      take: limit,
      select: {
        id: true,
        type: true,
        title: true,
        body: true,
        link: true,
        readAt: true,
        createdAt: true,
      },
    });

    return notifications.map((n) => ({
      id: n.id,
      type: n.type,
      title: n.title,
      body: n.body,
      link: n.link,
      readAt: n.readAt?.toISOString() ?? null,
      createdAt: n.createdAt.toISOString(),
    }));
  }
}
