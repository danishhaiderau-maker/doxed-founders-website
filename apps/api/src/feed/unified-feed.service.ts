import { Injectable } from '@nestjs/common';
import { ScoutMarketStatus, SimulatedRaiseStatus } from '@prisma/client';
import {
  PlatformPulseItem,
  UnifiedFeedCategory,
  UnifiedFeedItem,
  sortUnifiedFeedItems,
  unifiedFeedTier,
} from '@dcf/utils';
import { formatUsd } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { FeedService } from './feed.service';
import { HotBuyService } from './hot-buy.service';

@Injectable()
export class UnifiedFeedService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly feed: FeedService,
    private readonly hotBuy: HotBuyService,
  ) {}

  async getPulse(): Promise<PlatformPulseItem[]> {
    const items: PlatformPulseItem[] = [];
    const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);

    const hotBuys = await this.hotBuy.listHotBuys(3);
    for (const hb of hotBuys) {
      items.push({
        id: `pulse-hot-${hb.projectId}`,
        emoji: '🔥',
        headline: this.hotBuy.formatHotBuyHeadline(hb),
        detail: this.hotBuy.formatHotBuyDetail(hb),
        link: `/project/${hb.projectSlug}`,
        tier: 1,
      });
    }

    const scoutVotes = await this.prisma.scoutMarket.findMany({
      where: { status: ScoutMarketStatus.OPEN },
      orderBy: { createdAt: 'desc' },
      take: 2,
      include: { project: { select: { slug: true, name: true, ticker: true } } },
    });
    for (const vote of scoutVotes) {
      items.push({
        id: `pulse-scout-${vote.id}`,
        emoji: '🗳️',
        headline: `${vote.project.name} entered Scout Vote`,
        detail: vote.question.slice(0, 80),
        link: `/scout-votes`,
        tier: 1,
      });
    }

    const followerSpikes = await this.prisma.projectFollow.groupBy({
      by: ['projectId'],
      where: { createdAt: { gte: weekAgo } },
      _count: { id: true },
      orderBy: { _count: { id: 'desc' } },
      take: 2,
    });
    for (const spike of followerSpikes) {
      if (spike._count.id < 5) continue;
      const project = await this.prisma.project.findUnique({
        where: { id: spike.projectId },
        select: { slug: true, ticker: true, name: true },
      });
      if (!project) continue;
      items.push({
        id: `pulse-follow-${spike.projectId}`,
        emoji: '📈',
        headline: `${project.ticker} +${spike._count.id} followers`,
        detail: 'Growing community interest this week',
        link: `/project/${project.slug}`,
        tier: 2,
      });
    }

    const recentBuilds = await this.prisma.founderBuildPost.findMany({
      orderBy: { publishedAt: 'desc' },
      take: 2,
      include: {
        founder: { select: { slug: true, name: true } },
        project: { select: { slug: true, ticker: true } },
      },
    });
    for (const post of recentBuilds) {
      items.push({
        id: `pulse-build-${post.id}`,
        emoji: '🚀',
        headline: `${post.founder.name} shipped${post.project ? ` ${post.project.ticker}` : ''}`,
        detail: post.headline.slice(0, 72),
        link: post.project ? `/project/${post.project.slug}` : `/founder/${post.founder.slug}`,
        tier: 2,
      });
    }

    const recentRaises = await this.prisma.simulatedRaise.findMany({
      where: { status: SimulatedRaiseStatus.ACTIVE, createdAt: { gte: weekAgo } },
      orderBy: { createdAt: 'desc' },
      take: 2,
      include: { project: { select: { slug: true, name: true } } },
    });
    for (const raise of recentRaises) {
      items.push({
        id: `pulse-raise-${raise.id}`,
        emoji: '💰',
        headline: `${raise.project.name} opened Raise Room`,
        detail: `Goal ${formatUsd(Number(raise.goalUsd), 0)} · validate demand`,
        link: `/raise-room`,
        tier: 1,
      });
    }

    return items.slice(0, 8);
  }

  async getUnifiedFeed(category: UnifiedFeedCategory = 'all', limit = 50) {
    const items: UnifiedFeedItem[] = [];

    if (category === 'all' || category === 'founder' || category === 'community') {
      items.push(...(await this.loadFounderEvents()));
    }

    if (category === 'all' || category === 'trading') {
      items.push(...(await this.loadTradingEvents()));
    }

    if (category === 'all' || category === 'market') {
      items.push(...(await this.loadMarketEvents()));
    }

    if (category === 'all' || category === 'community') {
      items.push(...(await this.loadCommunityEvents()));
    }

    const sorted = sortUnifiedFeedItems(items).slice(0, limit);
    return { category, items: sorted, pulse: await this.getPulse() };
  }

  private async loadFounderEvents(): Promise<UnifiedFeedItem[]> {
    const take = 25;
    const [buildPosts, videos] = await Promise.all([
      this.prisma.founderBuildPost.findMany({
        orderBy: { publishedAt: 'desc' },
        take,
        include: {
          founder: { select: { slug: true, name: true } },
          project: { select: { slug: true, name: true, ticker: true } },
        },
      }),
      this.prisma.founderVideo.findMany({
        orderBy: { publishedAt: 'desc' },
        take: 10,
        include: {
          founder: { select: { slug: true, name: true } },
          project: { select: { slug: true, name: true, ticker: true } },
        },
      }),
    ]);

    const buildItems: UnifiedFeedItem[] = buildPosts.map((p) => {
      const eventType = p.githubUrl ? 'github_milestone' : 'build_update';
      return {
        id: `founder-build-${p.id}`,
        tier: unifiedFeedTier(eventType),
        category: 'founder',
        eventType,
        emoji: '🔨',
        headline: p.headline,
        detail: `${p.founder.name}${p.project ? ` · ${p.project.ticker}` : ''}`,
        at: p.publishedAt.toISOString(),
        link: p.project ? `/project/${p.project.slug}` : `/founder/${p.founder.slug}`,
        projectSlug: p.project?.slug,
        projectTicker: p.project?.ticker,
        founderSlug: p.founder.slug,
      };
    });

    const videoItems: UnifiedFeedItem[] = videos.map((v) => ({
      id: `founder-video-${v.id}`,
      tier: unifiedFeedTier('founder_video'),
      category: 'founder',
      eventType: 'founder_video',
      emoji: '🎥',
      headline: v.title,
      detail: `${v.founder.name} uploaded intro video`,
      at: v.publishedAt.toISOString(),
      link: v.project ? `/project/${v.project.slug}` : `/founder/${v.founder.slug}`,
      projectSlug: v.project?.slug,
      founderSlug: v.founder.slug,
    }));

    return [...buildItems, ...videoItems];
  }

  private async loadTradingEvents(): Promise<UnifiedFeedItem[]> {
    const { posts } = await this.feed.getFeed('recent');
    return posts.map((post) => {
      const eventType =
        post.side === 'BUY'
          ? post.initialComment
            ? 'conviction_posted'
            : 'position_opened'
          : 'position_closed';
      return {
        id: `trade-${post.id}`,
        tier: unifiedFeedTier(eventType),
        category: 'trading',
        eventType,
        emoji: post.side === 'BUY' ? '📈' : '📉',
        headline: `${post.trader.name} ${post.side === 'BUY' ? 'opened' : 'closed'} ${post.project.ticker}`,
        detail: post.initialComment?.slice(0, 120) ?? `${formatUsd(post.amountUsd, 0)} paper trade`,
        at: typeof post.createdAt === 'string' ? post.createdAt : new Date(post.createdAt).toISOString(),
        link: `/portfolio/${post.trader.id}`,
        tradePostId: post.id,
        projectSlug: post.project.slug,
        projectTicker: post.project.ticker,
        amountUsd: post.amountUsd,
      };
    });
  }

  private async loadMarketEvents(): Promise<UnifiedFeedItem[]> {
    const items: UnifiedFeedItem[] = [];

    const hotBuys = await this.hotBuy.listHotBuys(8);
    for (const hb of hotBuys) {
      const eventType = hb.topTraderCount >= 3 ? 'top_trader_buy' : 'hot_buy';
      items.push({
        id: `market-hot-${hb.projectId}`,
        tier: 1,
        category: 'market',
        eventType,
        emoji: '🔥',
        headline: this.hotBuy.formatHotBuyHeadline(hb),
        detail: this.hotBuy.formatHotBuyDetail(hb),
        at: new Date().toISOString(),
        link: `/project/${hb.projectSlug}`,
        projectSlug: hb.projectSlug,
        projectTicker: hb.projectTicker,
      });
    }

    const scoutVotes = await this.prisma.scoutMarket.findMany({
      where: { status: ScoutMarketStatus.OPEN },
      orderBy: { createdAt: 'desc' },
      take: 10,
      include: { project: { select: { slug: true, name: true, ticker: true } } },
    });
    for (const vote of scoutVotes) {
      items.push({
        id: `market-scout-${vote.id}`,
        tier: unifiedFeedTier('scout_vote_opened'),
        category: 'market',
        eventType: 'scout_vote_opened',
        emoji: '🗳️',
        headline: `Scout Vote opened: ${vote.project.name}`,
        detail: vote.question.slice(0, 100),
        at: vote.createdAt.toISOString(),
        link: '/scout-votes',
        projectSlug: vote.project.slug,
        projectTicker: vote.project.ticker,
      });
    }

    const allocations = await this.prisma.raiseAllocation.findMany({
      orderBy: { createdAt: 'desc' },
      take: 15,
      include: {
        raise: { include: { project: { select: { slug: true, name: true, ticker: true } } } },
      },
    });
    for (const a of allocations) {
      items.push({
        id: `market-demand-${a.id}`,
        tier: unifiedFeedTier('demand_allocated'),
        category: 'market',
        eventType: 'demand_allocated',
        emoji: '💰',
        headline: `${formatUsd(Number(a.amountUsd))} allocated to ${a.raise.project.ticker}`,
        detail: 'Raise Room · paper demand signal',
        at: a.createdAt.toISOString(),
        link: `/project/${a.raise.project.slug}`,
        projectSlug: a.raise.project.slug,
        projectTicker: a.raise.project.ticker,
        amountUsd: Number(a.amountUsd),
      });
    }

    return items;
  }

  private async loadCommunityEvents(): Promise<UnifiedFeedItem[]> {
    const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
    const follows = await this.prisma.projectFollow.findMany({
      where: { createdAt: { gte: weekAgo } },
      orderBy: { createdAt: 'desc' },
      take: 20,
      include: {
        project: { select: { slug: true, name: true, ticker: true } },
        user: { select: { id: true, name: true, email: true } },
      },
    });

    return follows.map((f) => ({
      id: `community-follow-${f.id}`,
      tier: 3,
      category: 'community',
      eventType: 'project_followed',
      emoji: '👥',
      headline: `${f.user.name ?? f.user.email.split('@')[0]} followed ${f.project.ticker}`,
      detail: f.project.name,
      at: f.createdAt.toISOString(),
      link: `/project/${f.project.slug}`,
      projectSlug: f.project.slug,
      projectTicker: f.project.ticker,
    }));
  }
}
