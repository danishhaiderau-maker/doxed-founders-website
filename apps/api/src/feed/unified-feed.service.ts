import { Injectable } from '@nestjs/common';
import {
  FounderEventType,
  ListingStatus,
  ScoutMarketStatus,
  SimulatedRaiseStatus,
  ProjectSource,
} from '@prisma/client';
import {
  PlatformPulseItem,
  UnifiedFeedCategory,
  UnifiedFeedItem,
  founderEventToUnifiedItem,
  sortUnifiedFeedItems,
  unifiedFeedTier,
  computePredictionHeatScore,
  predictionHeatLabel,
  sortPredictionMarketsByHeat,
  HotPredictionItem,
  EngagementFlash,
} from '@dcf/utils';
import { formatUsd } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { FeedService } from './feed.service';
import { FeedShareService } from './feed-share.service';
import { HotBuyService } from './hot-buy.service';

@Injectable()
export class UnifiedFeedService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly feed: FeedService,
    private readonly hotBuy: HotBuyService,
    private readonly feedShare: FeedShareService,
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
      include: { project: { select: { slug: true, name: true, ticker: true } }, positions: true },
      take: 20,
    });
    const hotSorted = sortPredictionMarketsByHeat(
      scoutVotes.map((vote) => ({
        vote,
        totalPoolUsd: Number(vote.yesPoolUsd) + Number(vote.noPoolUsd),
        participantCount: vote.positions.filter((p) => Number(p.amountUsd) > 0).length,
        createdAt: vote.createdAt.toISOString(),
      })),
    ).slice(0, 2);
    for (const row of hotSorted) {
      const vote = row.vote;
      const pool = row.totalPoolUsd;
      items.push({
        id: `pulse-predict-${vote.id}`,
        emoji: pool >= 100 ? '🔥' : '🔮',
        headline: `${vote.project.name}: hot prediction`,
        detail: vote.question.slice(0, 80),
        link: `/predict`,
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
    const [hotQuestions, scoutListings] = await Promise.all([
      this.getHotQuestions(8),
      this.getOpenScoutListings(6),
    ]);
    return {
      category,
      items: sorted,
      pulse: await this.getPulse(),
      hotQuestions,
      scoutListings,
    };
  }

  async getHotQuestions(limit = 8): Promise<HotPredictionItem[]> {
    const markets = await this.prisma.scoutMarket.findMany({
      where: { status: ScoutMarketStatus.OPEN },
      include: {
        project: { select: { slug: true, name: true, ticker: true } },
        positions: true,
      },
      take: 40,
    });

    const mapped = markets.map((m) => {
      const yesPool = Number(m.yesPoolUsd);
      const noPool = Number(m.noPoolUsd);
      const totalPoolUsd = yesPool + noPool;
      const participantCount = m.positions.filter((p) => Number(p.amountUsd) > 0).length;
      const hoursLeft =
        m.resolvesAt != null
          ? Math.max(0, Math.ceil((m.resolvesAt.getTime() - Date.now()) / 3_600_000))
          : null;
      return {
        id: m.id,
        question: m.question,
        projectName: m.project.name,
        projectSlug: m.project.slug,
        projectTicker: m.project.ticker,
        totalPoolUsd,
        participantCount,
        conviction: totalPoolUsd > 0 ? Math.round((yesPool / totalPoolUsd) * 100) : 50,
        heatLabel: predictionHeatLabel(totalPoolUsd, participantCount),
        hoursLeft,
        heatScore: computePredictionHeatScore(totalPoolUsd, participantCount),
        createdAt: m.createdAt.toISOString(),
      };
    });

    return sortPredictionMarketsByHeat(mapped)
      .slice(0, limit)
      .map(({ heatScore: _heatScore, createdAt: _createdAt, ...rest }) => rest);
  }

  async getOpenScoutListings(limit = 6) {
    const now = new Date();
    const apps = await this.prisma.listingApplication.findMany({
      where: {
        status: ListingStatus.COMMUNITY_VOTING,
        OR: [{ votingClosesAt: null }, { votingClosesAt: { gt: now } }],
      },
      orderBy: { createdAt: 'desc' },
      take: limit,
      select: {
        id: true,
        projectName: true,
        ticker: true,
        whyList: true,
        createdAt: true,
        _count: { select: { votes: true } },
      },
    });

    return apps.map((a) => ({
      id: a.id,
      projectName: a.projectName,
      ticker: a.ticker,
      whyList: a.whyList?.slice(0, 120) ?? null,
      voteCount: a._count.votes,
      at: a.createdAt.toISOString(),
    }));
  }

  async getEngagementFlashes(since?: string): Promise<EngagementFlash[]> {
    const sinceDate = since ? new Date(since) : new Date(Date.now() - 30_000);
    const flashes: EngagementFlash[] = [];

    const [listings, markets, comments, stakes, paperBuys] = await Promise.all([
      this.prisma.project.findMany({
        where: {
          approved: true,
          source: ProjectSource.CURATED,
          createdAt: { gte: sinceDate },
        },
        orderBy: { createdAt: 'desc' },
        take: 3,
        select: { id: true, name: true, ticker: true, slug: true, createdAt: true },
      }),
      this.prisma.scoutMarket.findMany({
        where: { status: ScoutMarketStatus.OPEN, createdAt: { gte: sinceDate } },
        orderBy: { createdAt: 'desc' },
        take: 5,
        include: { project: { select: { ticker: true } } },
      }),
      this.prisma.feedComment.findMany({
        where: { createdAt: { gte: sinceDate } },
        orderBy: { createdAt: 'desc' },
        take: 5,
        include: {
          user: { select: { name: true, email: true } },
          feedPost: { include: { project: { select: { ticker: true } } } },
        },
      }),
      this.prisma.virtualEconomyEvent.findMany({
        where: { type: 'PREDICTION_STAKE', createdAt: { gte: sinceDate } },
        orderBy: { createdAt: 'desc' },
        take: 5,
      }),
      this.prisma.paperTrade.findMany({
        where: { side: 'BUY', createdAt: { gte: sinceDate } },
        orderBy: { createdAt: 'desc' },
        take: 5,
        include: {
          user: { select: { name: true, email: true } },
          project: { select: { ticker: true, slug: true, source: true, founderId: true } },
        },
      }),
    ]);

    for (const p of listings) {
      flashes.push({
        id: `flash-listing-${p.id}`,
        emoji: '🚀',
        message: `${p.name} (${p.ticker}) just went live on Doxxed Crypto`,
        link: `/project/${p.slug}`,
        at: p.createdAt.toISOString(),
      });
    }

    for (const m of markets) {
      flashes.push({
        id: `flash-market-${m.id}`,
        emoji: '🔮',
        message: `New prediction open for ${m.project.ticker} — stake on /predict`,
        link: '/predict',
        at: m.createdAt.toISOString(),
      });
    }

    for (const c of comments) {
      const name = c.user.name ?? c.user.email.split('@')[0];
      const ticker = c.feedPost?.project?.ticker ?? 'a trade';
      flashes.push({
        id: `flash-comment-${c.id}`,
        emoji: '💬',
        message: `${name} commented on ${ticker}`,
        link: '/feed',
        at: c.createdAt.toISOString(),
      });
    }

    for (const s of stakes) {
      flashes.push({
        id: `flash-stake-${s.id}`,
        emoji: '⚡',
        message: `Someone just staked on a prediction — jump in before the window closes`,
        link: '/predict',
        at: s.createdAt.toISOString(),
      });
    }

    for (const buy of paperBuys) {
      if (buy.project.source !== ProjectSource.DYNAMIC || buy.project.founderId) continue;
      const name = buy.user.name ?? buy.user.email.split('@')[0];
      flashes.push({
        id: `flash-buy-${buy.id}`,
        emoji: '📈',
        message: `${name} paper-bought ${buy.project.ticker} — not a verified listing yet`,
        link: '/paper-trading',
        at: buy.createdAt.toISOString(),
      });
    }

    return flashes
      .sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime())
      .slice(0, 5);
  }

  private async loadFounderEvents(): Promise<UnifiedFeedItem[]> {
    const take = 25;
    const now = new Date();
    const [buildPosts, videos, pinnedUpdates] = await Promise.all([
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
      this.prisma.founderUpdate.findMany({
        where: {
          pinned: true,
          OR: [{ pinnedUntil: null }, { pinnedUntil: { gt: now } }],
        },
        orderBy: { publishedAt: 'desc' },
        take: 12,
        include: {
          founder: { select: { slug: true, name: true, twitterUrl: true } },
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

    const pinnedItems: UnifiedFeedItem[] = pinnedUpdates
      .filter((u) => u.founder)
      .map((u) => ({
      id: `founder-x-${u.id}`,
      tier: unifiedFeedTier('founder_x_update'),
      category: 'founder' as const,
      eventType: 'founder_x_update',
      emoji: '📌',
      headline: u.headline,
      detail: `${u.founder!.name}${u.project ? ` · ${u.project.ticker}` : ''} · synced from X`,
      at: u.publishedAt.toISOString(),
      link: u.project ? `/project/${u.project.slug}` : `/founder/${u.founder!.slug}`,
      projectSlug: u.project?.slug,
      projectTicker: u.project?.ticker,
      founderSlug: u.founder!.slug,
      pinned: true,
      sourceUrl: u.sourceUrl ?? undefined,
    }));

    const platformEvents = await this.loadFounderPlatformEvents();
    return [...pinnedItems, ...platformEvents, ...buildItems, ...videoItems];
  }

  private async loadFounderPlatformEvents(): Promise<UnifiedFeedItem[]> {
    const events = await this.prisma.founderEvent.findMany({
      where: {
        project: { approved: true },
        type: {
          in: [
            FounderEventType.GITHUB_COMMIT,
            FounderEventType.GITHUB_PR_MERGED,
            FounderEventType.DEPLOY_SUCCESS,
            FounderEventType.DEPLOY_STARTED,
            FounderEventType.BUILD_PUBLISHED,
            FounderEventType.CURSOR_BUILD_SESSION,
            FounderEventType.BUILD_QUEUE_CAPTURED,
            FounderEventType.AGENT_RUN_COMPLETE,
          ],
        },
      },
      orderBy: { createdAt: 'desc' },
      take: 20,
      include: {
        founder: { select: { slug: true, name: true } },
        project: { select: { slug: true, name: true, ticker: true } },
      },
    });

    return events.map((e) =>
      founderEventToUnifiedItem({
        id: e.id,
        type: e.type,
        title: e.title,
        createdAt: e.createdAt,
        founder: e.founder,
        project: e.project,
        payload: e.payload as Record<string, unknown> | null,
      }),
    );
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
        tradeSide: post.side,
        traderName: post.trader.name,
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
      const shareCtx = await this.feedShare.loadProjectShareContext(hb.projectId);
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
        recentBuyerNames: (hb.recentBuyers ?? []).map((b) => b.displayName),
        shareContext: shareCtx
          ? {
              projectName: shareCtx.projectName,
              pctOfActive: hb.pctOfActive,
              detailLine: this.hotBuy.formatHotBuyDetail(hb),
              scoutHighlight: shareCtx.scoutHighlight,
              scoutThesis: shareCtx.scoutThesis,
              summary: shareCtx.summary,
              communitySnippets: shareCtx.communitySnippets,
            }
          : {
              projectName: hb.projectName,
              pctOfActive: hb.pctOfActive,
              detailLine: this.hotBuy.formatHotBuyDetail(hb),
            },
      });
    }

    const scoutVotes = await this.prisma.scoutMarket.findMany({
      where: { status: ScoutMarketStatus.OPEN },
      include: {
        project: { select: { slug: true, name: true, ticker: true } },
        positions: true,
      },
      take: 20,
    });

    const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
    const newListings = await this.prisma.project.findMany({
      where: {
        approved: true,
        source: ProjectSource.CURATED,
        createdAt: { gte: weekAgo },
      },
      orderBy: { createdAt: 'desc' },
      take: 5,
      select: { slug: true, name: true, ticker: true, createdAt: true },
    });
    for (const listing of newListings) {
      items.push({
        id: `market-listing-${listing.slug}`,
        tier: unifiedFeedTier('listing_live'),
        category: 'market',
        eventType: 'listing_live',
        emoji: '🚀',
        headline: `New listing: ${listing.name} (${listing.ticker})`,
        detail: 'Verified founder project is now live — predict, trade, follow',
        at: listing.createdAt.toISOString(),
        link: `/project/${listing.slug}`,
        projectSlug: listing.slug,
        projectTicker: listing.ticker,
      });
    }

    const hotPredictions = sortPredictionMarketsByHeat(
      scoutVotes.map((vote) => ({
        vote,
        totalPoolUsd: Number(vote.yesPoolUsd) + Number(vote.noPoolUsd),
        participantCount: vote.positions.filter((p) => Number(p.amountUsd) > 0).length,
        createdAt: vote.createdAt.toISOString(),
      })),
    );

    for (const row of hotPredictions) {
      const vote = row.vote;
      const pool = row.totalPoolUsd;
      const label = predictionHeatLabel(pool, row.participantCount);
      const eventType = pool > 0 ? 'prediction_staked' : 'hot_prediction';
      items.push({
        id: `market-predict-${vote.id}`,
        tier: unifiedFeedTier(label ? 'hot_prediction' : eventType),
        category: 'market',
        eventType: label ? 'hot_prediction' : eventType,
        emoji: label === 'Blazing' ? '🔥' : '🔮',
        headline: label
          ? `${label}: ${vote.project.ticker}`
          : `Predict: ${vote.project.name}`,
        detail: vote.question.slice(0, 100),
        at: vote.updatedAt.toISOString(),
        link: `/predict`,
        projectSlug: vote.project.slug,
        projectTicker: vote.project.ticker,
        amountUsd: pool,
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
