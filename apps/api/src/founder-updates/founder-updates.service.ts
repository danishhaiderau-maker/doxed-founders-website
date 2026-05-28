import { Injectable, Logger } from '@nestjs/common';
import { extractTwitterHandle } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { NotificationsService } from '../notifications/notifications.service';
import { XPostingService } from '../x-social/x-posting.service';

const PIN_HOURS = 6;

@Injectable()
export class FounderUpdatesService {
  private readonly logger = new Logger(FounderUpdatesService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly notifications: NotificationsService,
    private readonly xPosting: XPostingService,
  ) {}

  async findPinned(limit = 20) {
    const now = new Date();
    return this.prisma.founderUpdate.findMany({
      where: {
        pinned: true,
        OR: [{ pinnedUntil: null }, { pinnedUntil: { gt: now } }],
      },
      orderBy: { publishedAt: 'desc' },
      take: limit,
      include: {
        project: {
          select: {
            slug: true,
            name: true,
            ticker: true,
            logoUrl: true,
          },
        },
        founder: {
          select: {
            slug: true,
            name: true,
            photoUrl: true,
            twitterUrl: true,
          },
        },
      },
    });
  }

  async findSpotlightProjects(limit = 12) {
    return this.prisma.project.findMany({
      where: { approved: true },
      orderBy: [{ featured: 'desc' }, { updatedAt: 'desc' }],
      take: limit,
      include: {
        chain: { select: { slug: true, name: true } },
        founder: {
          select: {
            slug: true,
            name: true,
            twitterUrl: true,
            videoUrl: true,
          },
        },
        metrics: true,
        socials: true,
      },
    });
  }

  async syncTwitterUpdates(): Promise<{ created: number; skipped: string }> {
    const token = process.env.TWITTER_BEARER_TOKEN?.trim();
    if (!token) {
      return {
        created: 0,
        skipped: 'Set TWITTER_BEARER_TOKEN for automated X sync (paid X API required).',
      };
    }

    const founders = await this.prisma.founder.findMany({
      where: { twitterUrl: { not: null } },
      include: {
        projects: {
          where: { approved: true },
          take: 1,
          select: { id: true, slug: true, name: true },
        },
      },
    });

    let created = 0;

    for (const founder of founders) {
      const handle = extractTwitterHandle(founder.twitterUrl);
      if (!handle) continue;

      try {
        const userRes = await fetch(
          `https://api.twitter.com/2/users/by/username/${handle}?user.fields=public_metrics`,
          { headers: { Authorization: `Bearer ${token}` } },
        );
        if (!userRes.ok) continue;
        const userData = (await userRes.json()) as { data?: { id: string } };
        const twitterUserId = userData.data?.id;
        if (!twitterUserId) continue;

        const tweetsRes = await fetch(
          `https://api.twitter.com/2/users/${twitterUserId}/tweets?max_results=5&tweet.fields=created_at,text&exclude=retweets,replies`,
          { headers: { Authorization: `Bearer ${token}` } },
        );
        if (!tweetsRes.ok) continue;

        const tweetsData = (await tweetsRes.json()) as {
          data?: { id: string; text: string; created_at: string }[];
        };

        for (const tweet of tweetsData.data ?? []) {
          const text = tweet.text.trim();
          if (text.length < 20) continue;
          if (!this.looksLikeProjectUpdate(text)) continue;

          const headline = text.slice(0, 180).toUpperCase();
          const sourceUrl = `https://x.com/${handle}/status/${tweet.id}`;

          const existing = await this.prisma.founderUpdate.findUnique({
            where: { externalId: tweet.id },
          });
          if (existing) continue;

          const pinnedUntil = new Date(Date.now() + PIN_HOURS * 60 * 60 * 1000);
          await this.prisma.founderUpdate.create({
            data: {
              founderId: founder.id,
              projectId: founder.projects[0]?.id,
              sourceUrl,
              headline,
              summary: text,
              externalId: tweet.id,
              publishedAt: new Date(tweet.created_at),
              pinned: true,
              pinnedUntil,
            },
          });
          created += 1;

          if (this.xPosting.isConfigured()) {
            const project = founder.projects[0];
            this.xPosting
              .repostFounderTweet(tweet.id, {
                founderName: founder.name,
                projectName: project?.name ?? founder.name,
                projectSlug: project?.slug ?? 'projects',
              })
              .catch((err) => this.logger.warn(`X repost failed for @${handle}: ${err}`));
          }
        }
      } catch (err) {
        this.logger.warn(`X sync failed for @${handle}: ${err}`);
      }
    }

    if (created > 0) {
      await this.notifications.notifyAllUsers({
        type: 'FOUNDER_UPDATES',
        title: 'New doxxed founder updates',
        body: `${created} new update(s) from verified founders on X. See what teams are building in real time.`,
        link: '/feed',
      });
    }

    return { created, skipped: '' };
  }

  private looksLikeProjectUpdate(text: string): boolean {
    const lower = text.toLowerCase();
    const keywords = [
      'launch',
      'ship',
      'mainnet',
      'testnet',
      'partnership',
      'audit',
      'release',
      'update',
      'build',
      'product',
      'token',
      'community',
      'roadmap',
      'milestone',
    ];
    return keywords.some((word) => lower.includes(word));
  }
}
