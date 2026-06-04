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
  isMajorShipHeadline,
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

    const recentListings = await this.prisma.project.findMany({
      where: {
        approved: true,
        source: ProjectSource.CURATED,
        createdAt: { gte: weekAgo },
      },
      orderBy: { createdAt: 'desc' },
      take: 1,
      select: { slug: true, name: true, ticker: true },
    });
    for (const listing of recentListings) {
      items.push({
        id: `pulse-listing-${listing.slug}`,
        emoji: '🚀',
        headline: `New listing: ${listing.ticker}`,
        detail: listing.name,
        link: `/project/${listing.slug}`,
        tier: 1,
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

    // Money Feed: trading + market only. Founder tab = major milestones (no commits).
    if (category === 'founder') {
      items.push(...(await this.loadFounderMilestones()));
    }

    if (category === 'all' || category === 'trading') {
      items.push(...(await this.loadTradingEvents()));
    }

    if (category === 'all' || category === 'market') {
      items.push(...(await this.loadMarketEvents()));
    }

    if (category === 'community') {
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

  /** Major founder milestones only — ships & verification (no commits / agent noise). */
  private async loadFounderMilestones(): Promise<UnifiedFeedItem[]> {
    const weekAgo = new Date(Date.now() - 14 * 24 * 60 * 60 * 1000);

    const [shipPosts, platformEvents, verifications] = await Promise.all([
      this.prisma.founderBuildPost.findMany({
        where: { publishedAt: { gte: weekAgo } },
        orderBy: { publishedAt: 'desc' },
        take: 30,
        include: {
          founder: { select: { slug: true, name: true } },
          project: { select: { slug: true, name: true, ticker: true, createdAt: true } },
        },
      }),
      this.loadFounderPlatformEvents(),
      this.prisma.founderVerification.findMany({
        where: { verified: true, verifiedAt: { gte: weekAgo } },
        orderBy: { verifiedAt: 'desc' },
        take: 10,
        include: {
          founder: {
            select: {
              slug: true,
              name: true,
              projects: { where: { approved: true }, take: 1, select: { slug: true, ticker: true, name: true } },
            },
          },
        },
      }),
    ]);

    const shipItems: UnifiedFeedItem[] = shipPosts
      .filter((p) => isMajorShipHeadline(p.headline))
      .map((p) => ({
        id: `founder-ship-${p.id}`,
        tier: 1 as const,
        category: 'founder' as const,
        eventType: 'project_shipped',
        emoji: '🚀',
        headline: p.headline,
        detail: `${p.founder.name}${p.project ? ` · ${p.project.ticker}` : ''}`,
        at: p.publishedAt.toISOString(),
        link: p.project ? `/project/${p.project.slug}` : `/founder/${p.founder.slug}`,
        projectSlug: p.project?.slug,
        projectTicker: p.project?.ticker,
        founderSlug: p.founder.slug,
      }));

    const verifyItems: UnifiedFeedItem[] = verifications.map((v) => {
      const project = v.founder.projects[0];
      return {
        id: `founder-verified-${v.id}`,
        tier: 1 as const,
        category: 'founder' as const,
        eventType: 'founder_verified',
        emoji: '✅',
        headline: `Founder verified: ${v.founder.name}`,
        detail: project ? `${project.name} (${project.ticker})` : 'Identity verified',
        at: (v.verifiedAt ?? new Date()).toISOString(),
        link: project ? `/project/${project.slug}` : `/founder/${v.founder.slug}`,
        projectSlug: project?.slug,
        projectTicker: project?.ticker,
        founderSlug: v.founder.slug,
      };
    });

    const majorDeploys = platformEvents.filter((e) => e.eventType === 'project_shipped');
    return [...shipItems, ...majorDeploys, ...verifyItems];
  }

  private async loadFounderPlatformEvents(): Promise<UnifiedFeedItem[]> {
    const events = await this.prisma.founderEvent.findMany({
      where: {
        project: { approved: true },
        type: FounderEventType.DEPLOY_SUCCESS,
      },
      orderBy: { createdAt: 'desc' },
      take: 20,
      include: {
        founder: { select: { slug: true, name: true } },
        project: { select: { slug: true, name: true, ticker: true } },
      },
    });

    return events
      .map((e) =>
        founderEventToUnifiedItem({
          id: e.id,
          type: e.type,
          title: e.title,
          createdAt: e.createdAt,
          founder: e.founder,
          project: e.project,
          payload: e.payload as Record<string, unknown> | null,
        }),
      )
      .filter((item) => item.eventType === 'project_shipped');
  }

  private async loadTradingEvents(): Promise<UnifiedFeedItem[]> {
    const { posts } = await this.feed.getFeed('recent');
    const chronological = [...posts].sort(
      (a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime(),
    );
    const priorBuyKey = new Set<string>();

    const items = chronological.map((post) => {
      const key = `${post.trader.id}:${post.project.id}`;
      let eventType: string;
      if (post.side === 'BUY') {
        if (post.initialComment) {
          eventType = 'conviction_posted';
        } else if (priorBuyKey.has(key)) {
          eventType = 'position_added';
        } else {
          eventType = 'position_opened';
          priorBuyKey.add(key);
        }
      } else {
        eventType = priorBuyKey.has(key) ? 'position_reduced' : 'position_closed';
      }

      const isBuy = post.side === 'BUY';
      const verb =
        eventType === 'conviction_posted'
          ? 'conviction buy'
          : eventType === 'position_added'
            ? 'added to'
            : eventType === 'position_opened'
              ? 'opened'
              : eventType === 'position_reduced'
                ? 'reduced'
                : 'closed';

      return {
        id: `trade-${post.id}`,
        tier: unifiedFeedTier(
          eventType === 'conviction_posted' ? 'conviction_posted' : 'position_opened',
        ),
        category: 'trading' as const,
        eventType,
        emoji: isBuy ? '🟢' : '🔴',
        headline: `${post.trader.name} ${verb} ${post.project.ticker}`,
        detail: post.initialComment?.slice(0, 120) ?? `${formatUsd(post.amountUsd, 0)} paper`,
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
    return items.reverse();
  }

  private async loadMarketEvents(): Promise<UnifiedFeedItem[]> {
    const items: UnifiedFeedItem[] = [];
    const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);

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

    const newMarkets = await this.prisma.scoutMarket.findMany({
      where: { createdAt: { gte: weekAgo } },
      orderBy: { createdAt: 'desc' },
      take: 8,
      include: {
        project: { select: { slug: true, name: true, ticker: true } },
        creator: { select: { name: true, email: true } },
      },
    });
    for (const market of newMarkets) {
      const creator =
        market.creator?.name ?? market.creator?.email?.split('@')[0] ?? 'A founder';
      items.push({
        id: `market-created-${market.id}`,
        tier: unifiedFeedTier('scout_vote_opened'),
        category: 'market',
        eventType: 'scout_vote_opened',
        emoji: '🔮',
        headline: `New prediction market: ${market.project.ticker}`,
        detail: `${creator} opened · ${market.question.slice(0, 96)}`,
        at: market.createdAt.toISOString(),
        link: '/predict',
        projectSlug: market.project.slug,
        projectTicker: market.project.ticker,
      });
    }

    const recentStakes = await this.prisma.scoutMarketPosition.findMany({
      where: {
        createdAt: { gte: weekAgo },
        amountUsd: { gt: 0 },
      },
      orderBy: { createdAt: 'desc' },
      take: 15,
      include: {
        user: { select: { name: true, email: true } },
        market: { include: { project: { select: { slug: true, ticker: true, name: true } } } },
      },
    });
    for (const stake of recentStakes) {
      const name = stake.user.name ?? stake.user.email.split('@')[0];
      const amount = Number(stake.amountUsd);
      items.push({
        id: `market-stake-${stake.id}`,
        tier: unifiedFeedTier('prediction_staked'),
        category: 'market',
        eventType: 'prediction_staked',
        emoji: stake.side === 'YES' ? '✅' : '❌',
        headline: `${name} staked ${formatUsd(amount, 0)} on ${stake.market.project.ticker}`,
        detail: `${stake.side} · ${stake.market.project.name}`,
        at: stake.createdAt.toISOString(),
        link: '/predict',
        projectSlug: stake.market.project.slug,
        projectTicker: stake.market.project.ticker,
        amountUsd: amount,
        traderName: name,
      });
    }

    const scoutListingVotes = await this.prisma.listingApplication.findMany({
      where: {
        status: ListingStatus.COMMUNITY_VOTING,
        createdAt: { gte: weekAgo },
      },
      orderBy: { createdAt: 'desc' },
      take: 6,
      select: {
        id: true,
        projectName: true,
        ticker: true,
        createdAt: true,
        _count: { select: { votes: true } },
      },
    });
    for (const app of scoutListingVotes) {
      items.push({
        id: `market-scout-listing-${app.id}`,
        tier: unifiedFeedTier('scout_vote_opened'),
        category: 'market',
        eventType: 'scout_vote_opened',
        emoji: '🔭',
        headline: `Scout vote: ${app.projectName} (${app.ticker})`,
        detail: `${app._count.votes} vote${app._count.votes === 1 ? '' : 's'} · open on Trust Center`,
        at: app.createdAt.toISOString(),
        link: '/trust-center?tab=scout-voting',
        projectTicker: app.ticker,
      });
    }

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

    const dayAgo = new Date(Date.now() - 24 * 60 * 60 * 1000);
    const watchlistSpikes = await this.prisma.projectFollow.groupBy({
      by: ['projectId'],
      where: { createdAt: { gte: dayAgo } },
      _count: { id: true },
      orderBy: { _count: { id: 'desc' } },
      take: 6,
    });
    for (const spike of watchlistSpikes) {
      if (spike._count.id < 8) continue;
      const project = await this.prisma.project.findUnique({
        where: { id: spike.projectId },
        select: { slug: true, name: true, ticker: true },
      });
      if (!project) continue;
      items.push({
        id: `market-watchlist-${spike.projectId}`,
        tier: unifiedFeedTier('watchlist_surge'),
        category: 'market',
        eventType: 'watchlist_surge',
        emoji: '🔥',
        headline: `Watchlist alert: ${project.ticker}`,
        detail: `+${spike._count.id} watchlists today`,
        at: new Date().toISOString(),
        link: `/project/${project.slug}`,
        projectSlug: project.slug,
        projectTicker: project.ticker,
      });
    }

    const resolvedMarkets = await this.prisma.scoutMarket.findMany({
      where: {
        status: ScoutMarketStatus.RESOLVED,
        updatedAt: { gte: weekAgo },
      },
      orderBy: { updatedAt: 'desc' },
      take: 8,
      include: {
        project: { select: { slug: true, name: true, ticker: true } },
        positions: true,
      },
    });
    for (const market of resolvedMarkets) {
      const yesPool = Number(market.yesPoolUsd);
      const noPool = Number(market.noPoolUsd);
      const total = yesPool + noPool;
      const yesPct = total > 0 ? Math.round((yesPool / total) * 100) : 50;
      const winning = yesPool >= noPool ? 'YES' : 'NO';
      items.push({
        id: `market-resolved-${market.id}`,
        tier: 1,
        category: 'market',
        eventType: 'prediction_resolved',
        emoji: '🏆',
        headline: `Market resolved: ${market.project.ticker}`,
        detail: `${winning} (${winning === 'YES' ? yesPct : 100 - yesPct}%) · ${formatUsd(total, 0)} pool · ${market.question.slice(0, 72)}`,
        at: market.updatedAt.toISOString(),
        link: '/predict',
        projectSlug: market.project.slug,
        projectTicker: market.project.ticker,
        amountUsd: total,
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
    return [];
  }
}
