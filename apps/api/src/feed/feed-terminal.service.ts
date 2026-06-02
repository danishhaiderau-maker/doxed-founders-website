import { Injectable } from '@nestjs/common';
import {
  ListingStatus,
  PaperTradeSide,
  ProjectSource,
} from '@prisma/client';
import {
  computePostExitStory,
  feedCardMatchesTab,
  formatPublicAccountLabel,
  type FeedTerminalCardKind,
  type FeedTerminalTab,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { HotBuyService } from './hot-buy.service';

export type FeedTerminalCard = {
  id: string;
  kind: FeedTerminalCardKind;
  at: string;
  traderName?: string;
  traderId?: string;
  projectSlug?: string;
  projectTicker?: string;
  projectName?: string;
  projectLogoUrl?: string | null;
  amountUsd?: number;
  priceUsd?: number;
  currentPriceUsd?: number;
  reason?: string;
  convictionLabel?: string;
  pnlPct?: number;
  pnlUsd?: number;
  missedAlphaPct?: number;
  avoidedLossPct?: number;
  commentCount?: number;
  followerSpike?: number;
  link?: string;
  feedPostId?: string;
};

export type FeedTerminalResponse = {
  tab: FeedTerminalTab;
  projectSlug: string | null;
  cards: FeedTerminalCard[];
  stats: {
    buys24h: number;
    sells24h: number;
    buysPct: number;
    sellsPct: number;
    volume24h: number;
    volumePct: number;
    newTraders24h: number;
    newTradersPct: number;
    missedAlphaCount: number;
    smartExitCount: number;
  };
  topTraders: { userId: string; name: string; pnlUsd: number }[];
  projectChats: {
    slug: string;
    ticker: string;
    name: string;
    activeCount: number;
    latestMessage: string;
  }[];
  scoutPending: number;
};

@Injectable()
export class FeedTerminalService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly hotBuy: HotBuyService,
  ) {}

  async getTerminal(tab: FeedTerminalTab = 'all', projectSlug?: string) {
    const dayAgo = new Date(Date.now() - 24 * 60 * 60 * 1000);
    const twoDaysAgo = new Date(Date.now() - 48 * 60 * 60 * 1000);
    const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);

    let projectIdFilter: string | undefined;
    if (projectSlug) {
      const p = await this.prisma.project.findUnique({
        where: { slug: projectSlug },
        select: { id: true },
      });
      projectIdFilter = p?.id;
    }

    const tradeWhere = {
      createdAt: { gte: weekAgo },
      ...(projectIdFilter ? { projectId: projectIdFilter } : {}),
    };

    const [
      feedPosts,
      hotBuys,
      followerSpikes,
      listings,
      buildPosts,
      trades24h,
      tradesPrior24h,
      newTraders24h,
      newTradersPrior,
      scoutPending,
      topTraderRows,
      communityThreads,
    ] = await Promise.all([
      this.prisma.feedPost.findMany({
        where: projectIdFilter ? { projectId: projectIdFilter } : undefined,
        orderBy: { createdAt: 'desc' },
        take: 60,
        include: {
          user: { select: { id: true, name: true, email: true } },
          project: {
            include: { metrics: true, chain: { select: { slug: true } } },
          },
          paperTrade: true,
        },
      }),
      this.hotBuy.listHotBuys(12),
      this.prisma.projectFollow.groupBy({
        by: ['projectId'],
        where: { createdAt: { gte: dayAgo } },
        _count: { id: true },
        orderBy: { _count: { id: 'desc' } },
        take: 8,
      }),
      this.prisma.project.findMany({
        where: {
          approved: true,
          source: ProjectSource.CURATED,
          createdAt: { gte: weekAgo },
        },
        orderBy: { createdAt: 'desc' },
        take: 6,
        select: { id: true, slug: true, name: true, ticker: true, logoUrl: true, createdAt: true },
      }),
      this.prisma.founderBuildPost.findMany({
        where: {
          publishedAt: { gte: weekAgo },
          ...(projectIdFilter ? { projectId: projectIdFilter } : {}),
        },
        orderBy: { publishedAt: 'desc' },
        take: 10,
        include: {
          founder: { select: { name: true } },
          project: { select: { slug: true, name: true, ticker: true, logoUrl: true } },
        },
      }),
      this.prisma.paperTrade.groupBy({
        by: ['side'],
        where: { createdAt: { gte: dayAgo } },
        _count: { id: true },
        _sum: { totalUsd: true },
      }),
      this.prisma.paperTrade.groupBy({
        by: ['side'],
        where: { createdAt: { gte: twoDaysAgo, lt: dayAgo } },
        _sum: { totalUsd: true },
      }),
      this.prisma.user.count({ where: { createdAt: { gte: dayAgo } } }),
      this.prisma.user.count({ where: { createdAt: { gte: twoDaysAgo, lt: dayAgo } } }),
      this.prisma.listingApplication.count({
        where: { status: ListingStatus.COMMUNITY_VOTING },
      }),
      this.prisma.paperTrade.groupBy({
        by: ['userId'],
        where: {
          createdAt: { gte: dayAgo },
          side: PaperTradeSide.SELL,
          realizedPnlUsd: { not: null },
        },
        _sum: { realizedPnlUsd: true },
        orderBy: { _sum: { realizedPnlUsd: 'desc' } },
        take: 5,
      }),
      this.prisma.communityThread.findMany({
        orderBy: { createdAt: 'desc' },
        take: 20,
        include: {
          project: { select: { slug: true, ticker: true, name: true } },
          comments: { orderBy: { createdAt: 'desc' }, take: 1 },
        },
      }),
    ]);

    const cards: FeedTerminalCard[] = [];

    for (const post of feedPosts) {
      const traderName =
        post.user.name ?? formatPublicAccountLabel(post.user.email);
      const currentPrice = post.project.metrics?.priceUsd
        ? Number(post.project.metrics.priceUsd)
        : Number(post.paperTrade.priceUsd);
      const trade = post.paperTrade;
      const amountUsd = Number(trade.totalUsd);
      const priceUsd = Number(trade.priceUsd);
      const base = {
        at: post.createdAt.toISOString(),
        traderName,
        traderId: post.user.id,
        projectSlug: post.project.slug,
        projectTicker: post.project.ticker,
        projectName: post.project.name,
        projectLogoUrl: post.project.logoUrl,
        amountUsd,
        priceUsd,
        currentPriceUsd: currentPrice,
        reason: post.initialComment ?? undefined,
        commentCount: post.commentCount,
        link: `/portfolio/${post.user.id}`,
        feedPostId: post.id,
      };

      if (trade.side === PaperTradeSide.BUY) {
        if (post.initialComment) {
          cards.push({
            id: `thesis-${post.id}`,
            kind: 'THESIS',
            convictionLabel: 'High Conviction',
            ...base,
          });
        }
        cards.push({
          id: `buy-${post.id}`,
          kind: 'BUY',
          convictionLabel: post.initialComment ? 'High Conviction' : undefined,
          ...base,
        });
      } else {
        const realizedPnl = trade.realizedPnlUsd ? Number(trade.realizedPnlUsd) : 0;
        const pnlPct =
          amountUsd > 0 ? Math.round((realizedPnl / amountUsd) * 1000) / 10 : 0;
        const exitPrice = priceUsd;
        const story = computePostExitStory({
          exitPriceUsd: exitPrice,
          postExitPeakPriceUsd: trade.postExitPeakPriceUsd
            ? Number(trade.postExitPeakPriceUsd)
            : exitPrice,
          postExitTroughPriceUsd: trade.postExitTroughPriceUsd
            ? Number(trade.postExitTroughPriceUsd)
            : exitPrice,
          currentPriceUsd: currentPrice,
        });

        if (story.narrative === 'regret' && story.missedAfterExitPct >= 5) {
          cards.push({
            id: `regret-${post.id}`,
            kind: 'MISSED_ALPHA',
            pnlPct,
            pnlUsd: realizedPnl,
            missedAlphaPct: story.missedAfterExitPct,
            ...base,
          });
        } else if (story.narrative === 'smart') {
          cards.push({
            id: `smart-${post.id}`,
            kind: 'SMART_EXIT',
            pnlPct,
            pnlUsd: realizedPnl,
            avoidedLossPct: story.avoidedLossPct,
            ...base,
          });
        } else if (realizedPnl < 0) {
          cards.push({
            id: `loss-${post.id}`,
            kind: 'LOSS',
            pnlPct,
            pnlUsd: realizedPnl,
            ...base,
          });
        } else {
          cards.push({
            id: `sell-${post.id}`,
            kind: 'SELL',
            pnlPct,
            pnlUsd: realizedPnl,
            ...base,
          });
        }
      }
    }

    for (const hb of hotBuys) {
      if (projectIdFilter && hb.projectId !== projectIdFilter) continue;
      cards.push({
        id: `hot-${hb.projectId}`,
        kind: 'HOT_BUY',
        at: new Date().toISOString(),
        projectSlug: hb.projectSlug,
        projectTicker: hb.projectTicker,
        projectName: hb.projectName,
        reason: this.hotBuy.formatHotBuyDetail(hb),
        link: `/project/${hb.projectSlug}`,
        followerSpike: hb.buyerCount,
      });
    }

    for (const spike of followerSpikes) {
      if (spike._count.id < 3) continue;
      const project = await this.prisma.project.findUnique({
        where: { id: spike.projectId },
        select: { slug: true, name: true, ticker: true, logoUrl: true },
      });
      if (!project) continue;
      if (projectIdFilter && spike.projectId !== projectIdFilter) continue;
      cards.push({
        id: `follow-${spike.projectId}`,
        kind: 'FOLLOWER_SPIKE',
        at: new Date().toISOString(),
        projectSlug: project.slug,
        projectTicker: project.ticker,
        projectName: project.name,
        projectLogoUrl: project.logoUrl,
        followerSpike: spike._count.id,
        reason: `${spike._count.id} traders followed ${project.ticker}`,
        link: `/project/${project.slug}`,
      });
    }

    for (const listing of listings) {
      if (projectIdFilter && listing.id !== projectIdFilter) continue;
      cards.push({
        id: `listing-${listing.id}`,
        kind: 'LISTING',
        at: listing.createdAt.toISOString(),
        projectSlug: listing.slug,
        projectTicker: listing.ticker,
        projectName: listing.name,
        projectLogoUrl: listing.logoUrl,
        reason: 'New community-validated listing',
        link: `/project/${listing.slug}`,
      });
    }

    for (const bp of buildPosts) {
      if (!bp.project) continue;
      cards.push({
        id: `build-${bp.id}`,
        kind: 'MAJOR_UPDATE',
        at: bp.publishedAt.toISOString(),
        traderName: bp.founder.name,
        projectSlug: bp.project.slug,
        projectTicker: bp.project.ticker,
        projectName: bp.project.name,
        projectLogoUrl: bp.project.logoUrl,
        reason: bp.headline,
        link: `/project/${bp.project.slug}`,
      });
    }

    cards.sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());

    const filtered = cards.filter((c) => {
      if (!feedCardMatchesTab(c.kind, tab)) return false;
      if (projectSlug && c.projectSlug !== projectSlug) return false;
      return true;
    });

    const buys24h = trades24h.find((t) => t.side === PaperTradeSide.BUY)?._count.id ?? 0;
    const sells24h = trades24h.find((t) => t.side === PaperTradeSide.SELL)?._count.id ?? 0;
    const volume24h = trades24h.reduce((s, t) => s + Number(t._sum.totalUsd ?? 0), 0);
    const volumePrior = tradesPrior24h.reduce((s, t) => s + Number(t._sum.totalUsd ?? 0), 0);
    const missedAlphaCount = cards.filter((c) => c.kind === 'MISSED_ALPHA').length;
    const smartExitCount = cards.filter((c) => c.kind === 'SMART_EXIT').length;

    const pctChange = (current: number, prior: number) =>
      prior <= 0 ? (current > 0 ? 100 : 0) : Math.round(((current - prior) / prior) * 100);

    const topTraders = await Promise.all(
      topTraderRows.map(async (row) => {
        const user = await this.prisma.user.findUnique({
          where: { id: row.userId },
          select: { name: true, email: true },
        });
        return {
          userId: row.userId,
          name: user?.name ?? formatPublicAccountLabel(user?.email ?? 'trader'),
          pnlUsd: Number(row._sum.realizedPnlUsd ?? 0),
        };
      }),
    );

    const chatMap = new Map<string, FeedTerminalResponse['projectChats'][0]>();
    for (const t of communityThreads) {
      if (chatMap.has(t.projectId)) continue;
      const latest = t.comments[0]?.body ?? t.title;
      chatMap.set(t.projectId, {
        slug: t.project.slug,
        ticker: t.project.ticker,
        name: t.project.name,
        activeCount: t.comments.length + 1,
        latestMessage: latest.slice(0, 80),
      });
    }
    for (const bp of buildPosts.slice(0, 5)) {
      if (!bp.project || chatMap.has(bp.project.slug)) continue;
      chatMap.set(bp.project.slug, {
        slug: bp.project.slug,
        ticker: bp.project.ticker,
        name: bp.project.name,
        activeCount: 1,
        latestMessage: bp.headline.slice(0, 80),
      });
    }

    return {
      tab,
      projectSlug: projectSlug ?? null,
      cards: filtered.slice(0, 50),
      stats: {
        buys24h,
        sells24h,
        buysPct: pctChange(buys24h, 0),
        sellsPct: pctChange(sells24h, 0),
        volume24h,
        volumePct: pctChange(volume24h, volumePrior),
        newTraders24h,
        newTradersPct: pctChange(newTraders24h, newTradersPrior),
        missedAlphaCount,
        smartExitCount,
      },
      topTraders,
      projectChats: [...chatMap.values()].slice(0, 4),
      scoutPending,
    } satisfies FeedTerminalResponse;
  }
}
