import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import {
  slugify,
  formatPublicAccountLabel,
  POINTS,
  STARTING_CASH_USD,
  RESTRICTED_CASH_THRESHOLD_USD,
  TOP_UP_FEE_USD,
  computeMissedAlpha,
  computeTrustWeight,
  computePostExitStory,
} from '@dcf/utils';
import { isSolanaTopUpConfigured, resolveSolanaTreasuryAddress } from '../payments/platform-treasury';
import {
  AnalyticsEventType,
  LeaderboardPeriod,
  PaperTradeSide,
  Prisma,
  ProjectLifecycleStage,
  ProjectSource,
} from '@prisma/client';
import { randomUUID } from 'node:crypto';
import { AnalyticsService } from '../analytics/analytics.service';
import { DexscreenerService } from '../dexscreener/dexscreener.service';
import { parseDexScreenerUrl } from '../dexscreener/dexscreener.types';
import { PrismaService } from '../prisma/prisma.service';
import { FeedService } from '../feed/feed.service';
import { HotBuyService } from '../feed/hot-buy.service';
import { PointsService } from '../points/points.service';
import { NotificationsService } from '../notifications/notifications.service';
import { SocialSignalsService } from '../x-social/social-signals.service';
import { PaperTradeDto } from './dto/paper-trading.dto';
import { createPaperSessionToken } from './paper-session.util';

const STARTING_CASH = STARTING_CASH_USD;
const MIN_SELL_USD = 0.01;
const JOURNEY_DEFAULT_DAYS = 60;

export type TradingTimelineEventType =
  | 'BUY'
  | 'SELL'
  | 'ADD'
  | 'REDUCE'
  | 'THESIS_UPDATE'
  | 'MILESTONE';

@Injectable()
export class PaperTradingService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly dexscreener: DexscreenerService,
    private readonly feedService: FeedService,
    private readonly hotBuy: HotBuyService,
    private readonly analytics: AnalyticsService,
    private readonly points: PointsService,
    private readonly notifications: NotificationsService,
    private readonly socialSignals: SocialSignalsService,
  ) {}

  async createSession(displayName?: string) {
    const guestId = randomUUID().slice(0, 8);
    const { token, hash } = createPaperSessionToken();
    const user = await this.prisma.user.create({
      data: {
        email: `paper-${guestId}@guest.local`,
        name: displayName?.trim() || `Trader ${guestId}`,
        paperPortfolio: {
          create: {
            cashBalance: STARTING_CASH,
            totalValue: STARTING_CASH,
            sessionTokenHash: hash,
          },
        },
      },
      include: { paperPortfolio: true },
    });

    return {
      userId: user.id,
      sessionToken: token,
      displayName: user.name,
      cashBalance: Number(user.paperPortfolio!.cashBalance),
      totalValue: Number(user.paperPortfolio!.totalValue),
      startingCash: STARTING_CASH,
    };
  }

  async ensurePortfolio(userId: string) {
    const user = await this.prisma.user.findUnique({ where: { id: userId } });
    if (!user) {
      throw new NotFoundException('User not found');
    }

    const existing = await this.prisma.paperPortfolio.findUnique({ where: { userId } });
    if (existing) return existing;

    const portfolio = await this.prisma.paperPortfolio.create({
      data: {
        userId,
        cashBalance: STARTING_CASH,
        totalValue: STARTING_CASH,
      },
    });

    await this.prisma.virtualEconomyEvent.create({
      data: {
        userId,
        type: 'INITIAL_GRANT',
        amountUsd: new Prisma.Decimal(STARTING_CASH),
        note: 'Signup paper trading grant',
      },
    });

    return portfolio;
  }

  async getPortfolio(userId: string) {
    await this.consolidateDuplicatePositions(userId);

    let portfolio = await this.prisma.paperPortfolio.findUnique({
      where: { userId },
      include: {
        positions: true,
      },
    });

    if (!portfolio) {
      await this.ensurePortfolio(userId);
      portfolio = await this.prisma.paperPortfolio.findUnique({
        where: { userId },
        include: { positions: true },
      });
    }

    if (!portfolio) {
      throw new NotFoundException('Paper portfolio not found');
    }

    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { name: true, email: true, reputationPoints: true, contributorLevel: true },
    });

    const positions = await Promise.all(
      portfolio.positions.map(async (position) => {
        const project = await this.prisma.project.findUnique({
          where: { id: position.projectId },
          include: {
            metrics: true,
            socials: true,
            founder: { include: { verifications: { where: { verified: true } } } },
            chain: true,
          },
        });
        const price = Number(project?.metrics?.priceUsd ?? position.avgBuyPrice);
        const quantity = Number(position.quantity);
        const storedPeak = position.peakPriceUsd ? Number(position.peakPriceUsd) : Number(position.avgBuyPrice);
        const peakPriceUsd = Math.max(storedPeak, price);
        if (peakPriceUsd > storedPeak + 1e-9) {
          await this.prisma.paperPosition.update({
            where: { id: position.id },
            data: { peakPriceUsd: new Prisma.Decimal(peakPriceUsd) },
          });
        }
        const marketValue = quantity * price;
        const costBasis = quantity * Number(position.avgBuyPrice);
        const pnl = marketValue - costBasis;
        const pnlPercent = costBasis > 0 ? (pnl / costBasis) * 100 : 0;

        return {
          projectId: project!.id,
          name: project!.name,
          ticker: project!.ticker,
          logoUrl: project!.logoUrl,
          dexscreenerUrl: project!.dexscreenerUrl,
          contractAddress: project!.contractAddress,
          websiteUrl: project!.websiteUrl,
          chainSlug: project!.chain.slug,
          twitterUrl: project!.socials?.twitterUrl ?? project!.founder?.twitterUrl ?? null,
          telegramUrl: project!.socials?.telegramUrl ?? null,
          isDoxxedCurated:
            project!.source === ProjectSource.CURATED && Boolean(project!.founderId),
          founderName: project!.founder?.name ?? null,
          quantity,
          avgBuyPrice: Number(position.avgBuyPrice),
          priceUsd: price,
          marketValue,
          pnl,
          pnlPercent,
          marketCap: project?.metrics?.marketCap ? Number(project.metrics.marketCap) : null,
          liquidity: project?.metrics?.liquidity ? Number(project.metrics.liquidity) : null,
          volume24h: project?.metrics?.volume24h ? Number(project.metrics.volume24h) : null,
          convictionThesis: position.convictionThesis,
          convictionCatalyst: position.convictionCatalyst,
          convictionTargetUsd: position.convictionTargetUsd
            ? Number(position.convictionTargetUsd)
            : null,
          convictionTimeHorizon: position.convictionTimeHorizon,
          convictionRecordedAt: position.convictionRecordedAt?.toISOString() ?? null,
          positionOpenedAt: position.createdAt.toISOString(),
        };
      }),
    );

    const positionsValue = positions.reduce((sum, p) => sum + p.marketValue, 0);
    const cashBalance = Number(portfolio.cashBalance);
    const totalValue = cashBalance + positionsValue;
    const pnl = totalValue - STARTING_CASH;
    const roi = (pnl / STARTING_CASH) * 100;

    await this.prisma.paperPortfolio.update({
      where: { id: portfolio.id },
      data: { totalValue },
    });

    return {
      userId,
      accountName: user?.name ?? null,
      accountEmail: user?.email ?? null,
      reputationPoints: user?.reputationPoints ?? 0,
      contributorLevel: user?.contributorLevel ?? 1,
      cashBalance,
      totalValue,
      pnl,
      roi,
      startingCash: STARTING_CASH,
      positions,
      isBusted: cashBalance < RESTRICTED_CASH_THRESHOLD_USD,
      isRestricted: cashBalance < RESTRICTED_CASH_THRESHOLD_USD,
      restrictedThresholdUsd: RESTRICTED_CASH_THRESHOLD_USD,
      resetFeeUsd: TOP_UP_FEE_USD,
      recentTrades: await this.getRecentTrades(userId),
    };
  }

  async getRecentTrades(userId: string, limit = 10) {
    const trades = await this.prisma.paperTrade.findMany({
      where: { userId },
      orderBy: { createdAt: 'desc' },
      take: limit,
      include: { project: { select: { ticker: true, name: true } } },
    });
    return trades.map((t) => ({
      id: t.id,
      side: t.side,
      ticker: t.project.ticker,
      projectName: t.project.name,
      quantity: Number(t.quantity),
      priceUsd: Number(t.priceUsd),
      totalUsd: Number(t.totalUsd),
      realizedPnlUsd: t.realizedPnlUsd != null ? Number(t.realizedPnlUsd) : null,
      whatIfHeldPct: t.whatIfHeldPct != null ? Number(t.whatIfHeldPct) : null,
      missedAlphaPct: t.missedAlphaPct != null ? Number(t.missedAlphaPct) : null,
      convictionScore: t.convictionScore ?? null,
      createdAt: t.createdAt.toISOString(),
    }));
  }

  async getMissedAlphaLeaderboard(limit = 30) {
    const trades = await this.prisma.paperTrade.findMany({
      where: {
        side: PaperTradeSide.SELL,
        missedAlphaPct: { gt: 0 },
      },
      orderBy: { missedAlphaPct: 'desc' },
      take: Math.min(limit, 50),
      include: {
        user: { select: { id: true, name: true, email: true, twitterHandle: true } },
        project: { select: { ticker: true, name: true } },
      },
    });

    return trades.map((t, index) => ({
      rank: index + 1,
      userId: t.userId,
      displayName: formatPublicAccountLabel(t.user.name, t.user.email),
      twitterHandle: t.user.twitterHandle,
      ticker: t.project.ticker,
      projectName: t.project.name,
      realizedPnlUsd: t.realizedPnlUsd != null ? Number(t.realizedPnlUsd) : 0,
      missedAlphaPct: Number(t.missedAlphaPct ?? 0),
      whatIfHeldPct: Number(t.whatIfHeldPct ?? 0),
      convictionScore: t.convictionScore ?? null,
      closedAt: t.createdAt.toISOString(),
    }));
  }

  async getProjectLivePrice(projectId: string) {
    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      include: { metrics: true, chain: true },
    });
    if (!project) throw new NotFoundException('Project not found');

    const input = project.dexscreenerUrl ?? project.contractAddress;
    if (input) {
      try {
        const preview = await this.previewTokenInput(input);
        const price = Number(preview.marketPreview.priceUsd);
        if (price > 0) return { priceUsd: price, preview };
      } catch {
        /* fall through */
      }
    }

    const cached = Number(project.metrics?.priceUsd ?? 0);
    return { priceUsd: cached, preview: null };
  }

  /** Merge multiple positions for the same ticker into one row (fixes duplicate dynamic listings). */
  async consolidateDuplicatePositions(userId: string) {
    const portfolio = await this.prisma.paperPortfolio.findUnique({
      where: { userId },
      include: {
        positions: { include: { project: { select: { id: true, ticker: true } } } },
      },
    });
    if (!portfolio || portfolio.positions.length < 2) return;

    const groups = new Map<string, typeof portfolio.positions>();
    for (const pos of portfolio.positions) {
      const key = pos.project.ticker.toUpperCase();
      const list = groups.get(key) ?? [];
      list.push(pos);
      groups.set(key, list);
    }

    for (const [, positions] of groups) {
      if (positions.length < 2) continue;

      positions.sort((a, b) => Number(b.quantity) - Number(a.quantity));
      const primary = positions[0];
      let totalQty = Number(primary.quantity);
      let costSum = totalQty * Number(primary.avgBuyPrice);

      for (const dup of positions.slice(1)) {
        const q = Number(dup.quantity);
        costSum += q * Number(dup.avgBuyPrice);
        totalQty += q;
        await this.prisma.paperPosition.delete({ where: { id: dup.id } });
      }

      const newAvg = totalQty > 0 ? costSum / totalQty : Number(primary.avgBuyPrice);
      await this.prisma.paperPosition.update({
        where: { id: primary.id },
        data: {
          quantity: new Prisma.Decimal(totalQty),
          avgBuyPrice: new Prisma.Decimal(newAvg),
        },
      });
    }
  }

  async closePosition(
    userId: string,
    projectId: string,
    opts?: { comment?: string; sellPercent?: number },
  ) {
    const position = await this.prisma.paperPosition.findFirst({
      where: { projectId, portfolio: { userId } },
      include: { project: true, portfolio: true },
    });
    if (!position) throw new NotFoundException('Position not found');

    const { priceUsd: price, preview: livePreview } = await this.getProjectLivePrice(projectId);
    if (!price || price <= 0) {
      throw new BadRequestException('Could not resolve live price to close position');
    }

    const held = Number(position.quantity);
    const sellPercent = Math.min(100, Math.max(0.01, opts?.sellPercent ?? 100));
    const sellQty = held * (sellPercent / 100);
    let amountUsd = sellQty * price;
    amountUsd = Math.round(amountUsd * 100) / 100;

    if (sellQty <= 0) {
      throw new BadRequestException('Nothing to sell');
    }

    const dexUrl = position.project.dexscreenerUrl ?? position.project.contractAddress;
    if (!dexUrl) {
      throw new BadRequestException('No DexScreener link for this token');
    }

    const avgBuy = Number(position.avgBuyPrice);
    const peakPrice = Math.max(
      Number(position.peakPriceUsd ?? position.avgBuyPrice),
      price,
    );
    const costBasisUsd = avgBuy * sellQty;
    const realizedPnlUsd = Math.round((price - avgBuy) * sellQty * 100) / 100;
    const missedAlpha = computeMissedAlpha({
      entryPriceUsd: avgBuy,
      exitPriceUsd: price,
      peakPriceUsd: peakPrice,
      investedUsd: costBasisUsd,
      proceedsUsd: amountUsd,
    });

    const result = await this.executeTradeInternal({
      userId,
      projectId: position.projectId,
      dexscreenerUrl: dexUrl,
      side: PaperTradeSide.SELL,
      amountUsd: Math.max(amountUsd, MIN_SELL_USD),
      quantityOverride: sellQty,
      realizedPnlUsd,
      whatIfHeldPct: missedAlpha.whatIfHeldReturnPct,
      missedAlphaPct: missedAlpha.missedAlphaPct,
      convictionScore: missedAlpha.convictionScore,
      peakPriceUsd: peakPrice,
      comment: opts?.comment ?? (sellPercent >= 99 ? 'Closed position' : `Closed ${sellPercent}%`),
      marketPreview: livePreview?.marketPreview,
    });

    if (Math.abs(realizedPnlUsd) >= MIN_SELL_USD) {
      await this.prisma.virtualEconomyEvent.create({
        data: {
          userId,
          type: 'REALIZED_PNL',
          amountUsd: new Prisma.Decimal(realizedPnlUsd),
          note: `${position.project.ticker} close · ${realizedPnlUsd >= 0 ? '+' : ''}$${realizedPnlUsd.toFixed(2)}`,
        },
      });
    }

    return {
      ...result,
      realizedPnlUsd,
      proceedsUsd: result.amountUsd,
      ticker: position.project.ticker,
      missedAlpha,
    };
  }

  async swapTokens(
    userId: string,
    fromProjectId: string,
    toDexscreenerUrl: string,
    opts?: { comment?: string },
  ) {
    const closed = await this.closePosition(userId, fromProjectId, {
      comment: opts?.comment ?? 'Swap — sell leg',
      sellPercent: 100,
    });

    const buyAmount = Math.floor(closed.proceedsUsd * 100) / 100;
    if (buyAmount < 1) {
      throw new BadRequestException('Proceeds too small to swap — position may be dust');
    }

    const bought = await this.executeTrade({
      userId,
      dexscreenerUrl: toDexscreenerUrl.trim(),
      side: PaperTradeSide.BUY,
      amountUsd: buyAmount,
      comment: opts?.comment ? `${opts.comment} (swap buy)` : 'Swap — buy leg',
    });

    return { sell: closed, buy: bought };
  }

  private async executeTradeInternal(input: {
    userId: string;
    projectId?: string;
    dexscreenerUrl: string;
    side: PaperTradeSide;
    amountUsd: number;
    quantityOverride?: number;
    realizedPnlUsd?: number;
    whatIfHeldPct?: number;
    missedAlphaPct?: number;
    convictionScore?: number;
    peakPriceUsd?: number;
    comment?: string;
    catalyst?: string;
    targetUsd?: number;
    timeHorizon?: string;
    marketPreview?: {
      priceUsd?: string;
      marketCap?: number;
      volume24h?: number;
      liquidityUsd?: number;
      priceChange24h?: number;
    };
  }) {
    const preview = await this.previewTokenInput(input.dexscreenerUrl.trim());
    const parsed = parseDexScreenerUrl(preview.dexscreenerUrl);
    if (!parsed) throw new BadRequestException('Invalid DexScreener URL');

    const price = Number(preview.marketPreview.priceUsd);
    if (!price || price <= 0) {
      throw new BadRequestException('Could not resolve live price from DexScreener');
    }

    const project = input.projectId
      ? await this.prisma.project.findUniqueOrThrow({ where: { id: input.projectId } })
      : await this.ensureDynamicProject(preview, parsed.address);

    let amountUsd = input.amountUsd;
    const quantity =
      input.quantityOverride != null ? input.quantityOverride : amountUsd / price;

    await this.ensurePortfolio(input.userId);
    const portfolio = await this.prisma.paperPortfolio.findUnique({
      where: { userId: input.userId },
      include: { positions: true },
    });
    if (!portfolio) throw new NotFoundException('Paper portfolio not found');

    if (input.side === PaperTradeSide.SELL) {
      const existing = portfolio.positions.find((p) => p.projectId === project.id);
      const held = existing ? Number(existing.quantity) : 0;
      if (held <= 0) throw new BadRequestException('No tokens to sell');
      if (quantity > held * 1.0001) {
        throw new BadRequestException('Sell quantity exceeds holdings');
      }
      amountUsd = Math.round(quantity * price * 100) / 100;
    }

    if (input.side === PaperTradeSide.BUY) {
      if (Number(portfolio.cashBalance) < RESTRICTED_CASH_THRESHOLD_USD) {
        throw new BadRequestException(
          `Cash below $${RESTRICTED_CASH_THRESHOLD_USD.toLocaleString()}. Top up for $${TOP_UP_FEE_USD} to continue buying.`,
        );
      }
      if (Number(portfolio.cashBalance) < amountUsd) {
        throw new BadRequestException('Insufficient paper cash balance');
      }
    }

    const result = await this.prisma.$transaction(async (tx) => {
      const existingPosition = await tx.paperPosition.findUnique({
        where: {
          portfolioId_projectId: { portfolioId: portfolio.id, projectId: project.id },
        },
      });

      let cashDelta = 0;
      if (input.side === PaperTradeSide.BUY) {
        cashDelta = -amountUsd;
        const prevQty = existingPosition ? Number(existingPosition.quantity) : 0;
        const prevAvg = existingPosition ? Number(existingPosition.avgBuyPrice) : 0;
        const newQty = prevQty + quantity;
        const newAvg = newQty > 0 ? (prevQty * prevAvg + amountUsd) / newQty : price;

        const convictionFields =
          input.comment?.trim() ||
          input.catalyst?.trim() ||
          input.targetUsd ||
          input.timeHorizon?.trim()
            ? {
                convictionThesis: input.comment?.trim() || undefined,
                convictionCatalyst: input.catalyst?.trim() || undefined,
                convictionTargetUsd: input.targetUsd
                  ? new Prisma.Decimal(input.targetUsd)
                  : undefined,
                convictionTimeHorizon: input.timeHorizon?.trim() || undefined,
                convictionRecordedAt: new Date(),
              }
            : {};

        await tx.paperPosition.upsert({
          where: {
            portfolioId_projectId: { portfolioId: portfolio.id, projectId: project.id },
          },
          create: {
            portfolioId: portfolio.id,
            projectId: project.id,
            quantity: new Prisma.Decimal(newQty),
            avgBuyPrice: new Prisma.Decimal(newAvg),
            peakPriceUsd: new Prisma.Decimal(price),
            ...convictionFields,
          },
          update: {
            quantity: new Prisma.Decimal(newQty),
            avgBuyPrice: new Prisma.Decimal(newAvg),
            peakPriceUsd: new Prisma.Decimal(
              Math.max(Number(existingPosition?.peakPriceUsd ?? price), price),
            ),
            ...convictionFields,
          },
        });
      } else {
        cashDelta = amountUsd;
        const prevQty = Number(existingPosition!.quantity);
        const newQty = prevQty - quantity;
        if (newQty <= 0.00000001) {
          await tx.paperPosition.delete({ where: { id: existingPosition!.id } });
        } else {
          await tx.paperPosition.update({
            where: { id: existingPosition!.id },
            data: { quantity: new Prisma.Decimal(newQty) },
          });
        }
      }

      const updatedPortfolio = await tx.paperPortfolio.update({
        where: { id: portfolio.id },
        data: { cashBalance: { increment: cashDelta } },
      });

      const trade = await tx.paperTrade.create({
        data: {
          userId: input.userId,
          projectId: project.id,
          side: input.side,
          quantity: new Prisma.Decimal(quantity),
          priceUsd: new Prisma.Decimal(price),
          totalUsd: new Prisma.Decimal(amountUsd),
          realizedPnlUsd:
            input.realizedPnlUsd != null
              ? new Prisma.Decimal(input.realizedPnlUsd)
              : undefined,
          whatIfHeldPct:
            input.whatIfHeldPct != null ? new Prisma.Decimal(input.whatIfHeldPct) : undefined,
          missedAlphaPct:
            input.missedAlphaPct != null ? new Prisma.Decimal(input.missedAlphaPct) : undefined,
          convictionScore: input.convictionScore ?? undefined,
          peakPriceUsd:
            input.peakPriceUsd != null ? new Prisma.Decimal(input.peakPriceUsd) : undefined,
          ...(input.side === PaperTradeSide.SELL
            ? {
                postExitPeakPriceUsd: new Prisma.Decimal(price),
                postExitTroughPriceUsd: new Prisma.Decimal(price),
                postExitUpdatedAt: new Date(),
              }
            : {}),
        },
      });

      const mp = input.marketPreview ?? preview.marketPreview;
      await tx.project.update({
        where: { id: project.id },
        data: {
          lastTradeAt: new Date(),
          trackingActive: true,
          metrics: {
            upsert: {
              create: {
                priceUsd: new Prisma.Decimal(price),
                marketCap: mp.marketCap ? new Prisma.Decimal(mp.marketCap) : undefined,
                volume24h: mp.volume24h ? new Prisma.Decimal(mp.volume24h) : undefined,
                liquidity: mp.liquidityUsd ? new Prisma.Decimal(mp.liquidityUsd) : undefined,
                priceChange24h: mp.priceChange24h
                  ? new Prisma.Decimal(mp.priceChange24h)
                  : undefined,
              },
              update: {
                priceUsd: new Prisma.Decimal(price),
                marketCap: mp.marketCap ? new Prisma.Decimal(mp.marketCap) : undefined,
                volume24h: mp.volume24h ? new Prisma.Decimal(mp.volume24h) : undefined,
                liquidity: mp.liquidityUsd ? new Prisma.Decimal(mp.liquidityUsd) : undefined,
                priceChange24h: mp.priceChange24h
                  ? new Prisma.Decimal(mp.priceChange24h)
                  : undefined,
              },
            },
          },
        },
      });

      return { updatedPortfolio, trade };
    });

    const feedPost = await this.feedService.createPostForTrade(
      result.trade.id,
      input.userId,
      project.id,
      input.comment,
    );

    await this.analytics.track(
      input.side === PaperTradeSide.BUY
        ? AnalyticsEventType.PAPER_TRADE_BUY
        : AnalyticsEventType.PAPER_TRADE_SELL,
      {
        userId: input.userId,
        projectId: project.id,
        metadata: { ticker: project.ticker, amountUsd, priceUsd: price },
      },
    );

    await this.points.award(input.userId, POINTS.PAPER_TRADE, 'PAPER_TRADE');
    await this.cleanupInactiveTracking();

    return {
      success: true,
      side: input.side,
      projectId: project.id,
      ticker: project.ticker,
      quantity,
      priceUsd: price,
      amountUsd,
      cashBalance: Number(result.updatedPortfolio.cashBalance),
      feedPostId: feedPost.id,
      feedPostCommentCount: feedPost.commentCount,
      realizedPnlUsd: input.realizedPnlUsd ?? null,
    };
  }

  async getPublicPortfolio(userId: string, opts?: { includeOlder?: boolean }) {
    const portfolio = await this.getPortfolio(userId);
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: {
        twitterHandle: true,
        createdAt: true,
        reputationPoints: true,
        contributorLevel: true,
        email: true,
        oauthAccounts: { select: { id: true }, take: 1 },
        passwordHash: true,
        _count: { select: { followers: true } },
      },
    });

    const accountAgeDays = user
      ? Math.floor((Date.now() - user.createdAt.getTime()) / (24 * 60 * 60 * 1000))
      : 0;
    const trustWeight = computeTrustWeight({
      verifiedAccount: Boolean(user?.passwordHash || user?.oauthAccounts.length),
      contributorLevel: user?.contributorLevel ?? portfolio.contributorLevel,
      reputationPoints: user?.reputationPoints ?? portfolio.reputationPoints,
      accountAgeDays,
    });
    const trustScore = Math.min(100, trustWeight * 10);

    const journeyCutoff = opts?.includeOlder
      ? undefined
      : new Date(Date.now() - JOURNEY_DEFAULT_DAYS * 24 * 60 * 60 * 1000);

    await this.refreshPostExitPrices(userId, journeyCutoff);

    const olderTradeCount = journeyCutoff
      ? await this.prisma.paperTrade.count({
          where: { userId, createdAt: { lt: journeyCutoff } },
        })
      : 0;

    const journey = await this.buildTradingJourney(userId, journeyCutoff);

    const sellScores = journey.closedTrades
      .map((t) => t.convictionScore)
      .filter((s): s is number => s != null);
    const openThesisCount = portfolio.positions.filter(
      (p) => p.convictionThesis || p.convictionCatalyst,
    ).length;
    let convictionScore = 50;
    if (sellScores.length > 0) {
      convictionScore = Math.round(
        sellScores.reduce((a, b) => a + b, 0) / sellScores.length,
      );
    } else if (openThesisCount > 0) {
      convictionScore = Math.min(
        85,
        55 + Math.round((openThesisCount / Math.max(1, portfolio.positions.length)) * 30),
      );
    }

    return {
      userId: portfolio.userId,
      displayName: formatPublicAccountLabel(
        portfolio.accountName,
        portfolio.accountEmail,
      ),
      reputationPoints: portfolio.reputationPoints,
      contributorLevel: portfolio.contributorLevel,
      twitterHandle: user?.twitterHandle ?? null,
      cashBalance: portfolio.cashBalance,
      totalValue: portfolio.totalValue,
      pnl: portfolio.pnl,
      roi: portfolio.roi,
      startingCash: portfolio.startingCash,
      positionCount: portfolio.positions.length,
      followersCount: user?._count.followers ?? 0,
      trustScore,
      convictionScore,
      journeyDays: JOURNEY_DEFAULT_DAYS,
      hasOlderHistory: olderTradeCount > 0,
      olderTradeCount,
      timeline: journey.timeline,
      closedTrades: journey.closedTrades,
      tradeJourneys: journey.tradeJourneys,
      positions: portfolio.positions.map((p) => ({
        projectId: p.projectId,
        ticker: p.ticker,
        name: p.name,
        logoUrl: p.logoUrl,
        dexscreenerUrl: p.dexscreenerUrl,
        contractAddress: p.contractAddress,
        websiteUrl: p.websiteUrl,
        chainSlug: p.chainSlug,
        twitterUrl: p.twitterUrl,
        telegramUrl: p.telegramUrl,
        isDoxxedCurated: p.isDoxxedCurated,
        founderName: p.founderName,
        quantity: p.quantity,
        avgBuyPrice: p.avgBuyPrice,
        priceUsd: p.priceUsd,
        marketValue: p.marketValue,
        pnl: p.pnl,
        pnlPercent: p.pnlPercent,
        marketCap: p.marketCap,
        liquidity: p.liquidity,
        volume24h: p.volume24h,
        convictionThesis: p.convictionThesis,
        convictionCatalyst: p.convictionCatalyst,
        convictionTargetUsd: p.convictionTargetUsd,
        convictionTimeHorizon: p.convictionTimeHorizon,
        convictionRecordedAt: p.convictionRecordedAt,
        positionOpenedAt: p.positionOpenedAt,
        daysHeld: p.positionOpenedAt
          ? Math.max(
              0,
              Math.floor(
                (Date.now() - new Date(p.positionOpenedAt).getTime()) / (24 * 60 * 60 * 1000),
              ),
            )
          : 0,
        convictionLevel:
          p.convictionThesis && p.convictionCatalyst
            ? ('High' as const)
            : p.convictionThesis || p.convictionCatalyst
              ? ('Medium' as const)
              : ('Low' as const),
      })),
    };
  }

  private async refreshPostExitPrices(userId: string, since?: Date) {
    const sells = await this.prisma.paperTrade.findMany({
      where: {
        userId,
        side: PaperTradeSide.SELL,
        ...(since ? { createdAt: { gte: since } } : {}),
      },
      select: { id: true, projectId: true, priceUsd: true, postExitPeakPriceUsd: true, postExitTroughPriceUsd: true },
    });

    await Promise.all(
      sells.map(async (trade) => {
        try {
          const { priceUsd: livePrice } = await this.getProjectLivePrice(trade.projectId);
          if (!livePrice || livePrice <= 0) return;

          const exitPrice = Number(trade.priceUsd);
          const prevPeak = Number(trade.postExitPeakPriceUsd ?? exitPrice);
          const prevTrough = Number(trade.postExitTroughPriceUsd ?? exitPrice);

          await this.prisma.paperTrade.update({
            where: { id: trade.id },
            data: {
              postExitPeakPriceUsd: new Prisma.Decimal(Math.max(prevPeak, livePrice)),
              postExitTroughPriceUsd: new Prisma.Decimal(Math.min(prevTrough, livePrice)),
              postExitUpdatedAt: new Date(),
            },
          });
        } catch {
          /* skip tokens we cannot price */
        }
      }),
    );
  }

  private async buildTradingJourney(userId: string, since?: Date) {
    const trades = await this.prisma.paperTrade.findMany({
      where: {
        userId,
        ...(since ? { createdAt: { gte: since } } : {}),
      },
      orderBy: { createdAt: 'asc' },
      include: {
        project: { select: { id: true, ticker: true, name: true, logoUrl: true } },
        feedPost: { select: { id: true, initialComment: true } },
      },
    });

    const livePriceByProject = new Map<string, number>();
    const resolveLivePrice = async (projectId: string, fallback: number) => {
      if (livePriceByProject.has(projectId)) return livePriceByProject.get(projectId)!;
      try {
        const { priceUsd } = await this.getProjectLivePrice(projectId);
        const price = priceUsd > 0 ? priceUsd : fallback;
        livePriceByProject.set(projectId, price);
        return price;
      } catch {
        livePriceByProject.set(projectId, fallback);
        return fallback;
      }
    };

    const holdings = new Map<string, number>();
    const lastThesis = new Map<string, string>();
    const timeline: Array<{
      id: string;
      type: TradingTimelineEventType;
      createdAt: string;
      ticker: string;
      projectName: string;
      logoUrl: string | null;
      amountUsd: number;
      priceUsd: number;
      quantity: number;
      thesis: string | null;
      catalyst: string | null;
      feedPostId: string | null;
      realizedPnlUsd: number | null;
      realizedReturnPct: number | null;
      whatIfHeldPct: number | null;
      missedAlphaPct: number | null;
      peakPriceUsd: number | null;
      convictionScore: number | null;
      postExitPeakPriceUsd: number | null;
      postExitTroughPriceUsd: number | null;
      pumpAfterExitPct: number | null;
      dropAfterExitPct: number | null;
      whatIfHeldTotalPct: number | null;
      missedAfterExitPct: number | null;
      currentVsExitPct: number | null;
      avoidedLossPct: number | null;
      exitNarrative: 'regret' | 'smart' | 'neutral' | null;
    }> = [];

    const closedTrades: Array<{
      id: string;
      ticker: string;
      projectName: string;
      logoUrl: string | null;
      closedAt: string;
      openedAt: string | null;
      durationDays: number;
      entryPriceUsd: number;
      exitPriceUsd: number;
      investedUsd: number;
      proceedsUsd: number;
      realizedReturnPct: number;
      whatIfHeldPct: number;
      missedAlphaPct: number;
      peakPriceUsd: number | null;
      convictionScore: number | null;
      thesis: string | null;
      postExitPeakPriceUsd: number;
      postExitTroughPriceUsd: number;
      pumpAfterExitPct: number;
      dropAfterExitPct: number;
      whatIfHeldTotalPct: number;
      missedAfterExitPct: number;
      currentVsExitPct: number;
      avoidedLossPct: number;
      exitNarrative: 'regret' | 'smart' | 'neutral';
    }> = [];

    const cycleOpenedAt = new Map<string, string>();

    for (const trade of trades) {
      const projectId = trade.projectId;
      const prevQty = holdings.get(projectId) ?? 0;
      const qty = Number(trade.quantity);
      const priceUsd = Number(trade.priceUsd);
      const totalUsd = Number(trade.totalUsd);
      const thesis = trade.feedPost?.initialComment?.trim() || null;
      const feedPostId = trade.feedPost?.id ?? null;

      if (trade.side === PaperTradeSide.BUY) {
        const isAdd = prevQty > 0.000001;
        if (thesis && lastThesis.get(projectId) && lastThesis.get(projectId) !== thesis) {
          timeline.push({
            id: `${trade.id}-thesis`,
            type: 'THESIS_UPDATE',
            createdAt: trade.createdAt.toISOString(),
            ticker: trade.project.ticker,
            projectName: trade.project.name,
            logoUrl: trade.project.logoUrl,
            amountUsd: totalUsd,
            priceUsd,
            quantity: qty,
            thesis,
            catalyst: null,
            feedPostId,
            realizedPnlUsd: null,
            realizedReturnPct: null,
            whatIfHeldPct: null,
            missedAlphaPct: null,
            peakPriceUsd: null,
            convictionScore: null,
            postExitPeakPriceUsd: null,
            postExitTroughPriceUsd: null,
            pumpAfterExitPct: null,
            dropAfterExitPct: null,
            whatIfHeldTotalPct: null,
            missedAfterExitPct: null,
            currentVsExitPct: null,
            avoidedLossPct: null,
            exitNarrative: null,
          });
        }
        if (thesis) lastThesis.set(projectId, thesis);

        timeline.push({
          id: trade.id,
          type: isAdd ? 'ADD' : 'BUY',
          createdAt: trade.createdAt.toISOString(),
          ticker: trade.project.ticker,
          projectName: trade.project.name,
          logoUrl: trade.project.logoUrl,
          amountUsd: totalUsd,
          priceUsd,
          quantity: qty,
          thesis,
          catalyst: null,
          feedPostId,
          realizedPnlUsd: null,
          realizedReturnPct: null,
          whatIfHeldPct: null,
          missedAlphaPct: null,
          peakPriceUsd: null,
          convictionScore: null,
          postExitPeakPriceUsd: null,
          postExitTroughPriceUsd: null,
          pumpAfterExitPct: null,
          dropAfterExitPct: null,
          whatIfHeldTotalPct: null,
          missedAfterExitPct: null,
          currentVsExitPct: null,
          avoidedLossPct: null,
          exitNarrative: null,
        });
        if (!isAdd) {
          cycleOpenedAt.set(projectId, trade.createdAt.toISOString());
        }
        holdings.set(projectId, prevQty + qty);
      } else {
        const remaining = Math.max(0, prevQty - qty);
        const isFullExit = remaining <= 0.000001;
        const realizedPnlUsd =
          trade.realizedPnlUsd != null ? Number(trade.realizedPnlUsd) : null;
        const costBasis =
          realizedPnlUsd != null ? Math.max(0, totalUsd - realizedPnlUsd) : totalUsd;
        const realizedReturnPct =
          costBasis > 0 && realizedPnlUsd != null
            ? Math.round((realizedPnlUsd / costBasis) * 1000) / 10
            : null;
        const whatIfHeldPct =
          trade.whatIfHeldPct != null ? Number(trade.whatIfHeldPct) : null;
        const missedAlphaPct =
          trade.missedAlphaPct != null ? Number(trade.missedAlphaPct) : null;
        const peakPriceUsd =
          trade.peakPriceUsd != null ? Number(trade.peakPriceUsd) : null;
        const convictionScore = trade.convictionScore ?? null;
        const entryPriceUsd =
          costBasis > 0 && qty > 0 ? costBasis / qty : priceUsd;

        let postExitStory: ReturnType<typeof computePostExitStory> | null = null;
        if (isFullExit) {
          const currentPrice = await resolveLivePrice(projectId, priceUsd);
          const postExitPeak = Math.max(
            Number(trade.postExitPeakPriceUsd ?? priceUsd),
            currentPrice,
          );
          const postExitTrough = Number(trade.postExitTroughPriceUsd ?? priceUsd);
          postExitStory = computePostExitStory({
            exitPriceUsd: priceUsd,
            postExitPeakPriceUsd: postExitPeak,
            postExitTroughPriceUsd: postExitTrough,
            currentPriceUsd: currentPrice,
          });
        }

        timeline.push({
          id: trade.id,
          type: isFullExit ? 'SELL' : 'REDUCE',
          createdAt: trade.createdAt.toISOString(),
          ticker: trade.project.ticker,
          projectName: trade.project.name,
          logoUrl: trade.project.logoUrl,
          amountUsd: totalUsd,
          priceUsd,
          quantity: qty,
          thesis,
          catalyst: null,
          feedPostId,
          realizedPnlUsd,
          realizedReturnPct,
          whatIfHeldPct,
          missedAlphaPct,
          peakPriceUsd,
          convictionScore,
          postExitPeakPriceUsd: postExitStory?.postExitPeakPriceUsd ?? null,
          postExitTroughPriceUsd: postExitStory?.postExitTroughPriceUsd ?? null,
          pumpAfterExitPct: postExitStory?.pumpAfterExitPct ?? null,
          dropAfterExitPct: postExitStory?.dropAfterExitPct ?? null,
          whatIfHeldTotalPct: postExitStory?.pumpAfterExitPct ?? null,
          missedAfterExitPct: postExitStory?.missedAfterExitPct ?? null,
          currentVsExitPct: postExitStory?.currentVsExitPct ?? null,
          avoidedLossPct: postExitStory?.avoidedLossPct ?? null,
          exitNarrative: postExitStory?.narrative ?? null,
        });

        if (isFullExit && postExitStory) {
          const openedAt = cycleOpenedAt.get(projectId) ?? null;
          const durationDays = openedAt
            ? Math.max(
                0,
                Math.floor(
                  (trade.createdAt.getTime() - new Date(openedAt).getTime()) /
                    (24 * 60 * 60 * 1000),
                ),
              )
            : 0;
          closedTrades.push({
            id: trade.id,
            ticker: trade.project.ticker,
            projectName: trade.project.name,
            logoUrl: trade.project.logoUrl,
            closedAt: trade.createdAt.toISOString(),
            openedAt,
            durationDays,
            entryPriceUsd: Math.round(entryPriceUsd * 1e8) / 1e8,
            exitPriceUsd: priceUsd,
            investedUsd: Math.round(costBasis * 100) / 100,
            proceedsUsd: totalUsd,
            realizedReturnPct: realizedReturnPct ?? 0,
            whatIfHeldPct: whatIfHeldPct ?? 0,
            missedAlphaPct: missedAlphaPct ?? 0,
            peakPriceUsd,
            convictionScore,
            thesis,
            postExitPeakPriceUsd: postExitStory.postExitPeakPriceUsd,
            postExitTroughPriceUsd: postExitStory.postExitTroughPriceUsd,
            pumpAfterExitPct: postExitStory.pumpAfterExitPct,
            dropAfterExitPct: postExitStory.dropAfterExitPct,
            whatIfHeldTotalPct: postExitStory.pumpAfterExitPct,
            missedAfterExitPct: postExitStory.missedAfterExitPct,
            currentVsExitPct: postExitStory.currentVsExitPct,
            avoidedLossPct: postExitStory.avoidedLossPct,
            exitNarrative: postExitStory.narrative,
          });
          lastThesis.delete(projectId);
          cycleOpenedAt.delete(projectId);
        }

        holdings.set(projectId, remaining);
      }
    }

    timeline.reverse();
    closedTrades.sort(
      (a, b) => new Date(b.closedAt).getTime() - new Date(a.closedAt).getTime(),
    );

    const chronological = [...timeline].reverse();
    const tradeJourneys = closedTrades.map((closed) => {
      const sellIdx = chronological.findIndex((e) => e.id === closed.id);
      let startIdx = sellIdx >= 0 ? sellIdx : 0;
      if (sellIdx >= 0) {
        for (let i = sellIdx - 1; i >= 0; i--) {
          if (chronological[i].ticker !== closed.ticker) break;
          if (chronological[i].type === 'SELL') break;
          startIdx = i;
        }
      }
      const events =
        sellIdx >= 0 ? chronological.slice(startIdx, sellIdx + 1) : [chronological[sellIdx]].filter(Boolean);
      return {
        closedTradeId: closed.id,
        ticker: closed.ticker,
        projectName: closed.projectName,
        logoUrl: closed.logoUrl,
        events,
        closed,
      };
    });

    return { timeline, closedTrades, tradeJourneys };
  }

  async previewToken(url: string) {
    return this.previewTokenInput(url);
  }

  async previewTokenInput(input: string) {
    const trimmed = input.trim();
    const preview = await this.dexscreener.previewFromInput(trimmed);

    const curated = await this.prisma.project.findFirst({
      where: {
        approved: true,
        source: ProjectSource.CURATED,
        founderId: { not: null },
        OR: [
          { dexscreenerUrl: preview.dexscreenerUrl?.trim() },
          { ticker: preview.ticker.toUpperCase() },
          ...(preview.contractAddress
            ? [{ contractAddress: preview.contractAddress }]
            : []),
        ],
      },
      select: { slug: true, name: true },
    });

    return {
      ...preview,
      isDoxxedCurated: Boolean(curated),
      curatedProjectSlug: curated?.slug ?? null,
    };
  }

  async resetPortfolio(userId: string, source: 'dev' | 'stripe' | 'crypto' = 'dev') {
    if (source === 'dev' && process.env.STRIPE_SECRET_KEY?.trim()) {
      throw new BadRequestException(
        'Payment required. Use Stripe checkout or on-chain USDC to restart your portfolio.',
      );
    }

    await this.assertRestrictedForTopUp(userId);

    const portfolio = await this.prisma.paperPortfolio.findUnique({
      where: { userId },
    });
    if (!portfolio) {
      throw new NotFoundException('Paper portfolio not found');
    }

    const priorCash = Number(portfolio.cashBalance);
    const creditUsd = STARTING_CASH - priorCash;

    await this.prisma.$transaction(async (tx) => {
      await tx.paperPortfolio.update({
        where: { id: portfolio.id },
        data: {
          cashBalance: STARTING_CASH,
          totalValue: { increment: creditUsd > 0 ? creditUsd : 0 },
        },
      });
      await tx.virtualEconomyEvent.create({
        data: {
          userId,
          type: 'TOP_UP_CREDIT',
          amountUsd: new Prisma.Decimal(creditUsd > 0 ? creditUsd : 0),
          note: `Portfolio top-up: cash restored to $${STARTING_CASH}`,
        },
      });
      await tx.virtualEconomyEvent.create({
        data: {
          userId,
          type: 'TOP_UP_FEE',
          amountUsd: new Prisma.Decimal(TOP_UP_FEE_USD),
          note: 'Real-money top-up fee (virtual economy sink)',
        },
      });
    });

    return {
      success: true,
      resetFeeUsd: TOP_UP_FEE_USD,
      message:
        source === 'stripe'
          ? 'Payment received. Virtual cash restored to $10,000 — trade and allocate again.'
          : source === 'crypto'
            ? 'On-chain payment confirmed. Virtual cash restored to $10,000 — trade and allocate again.'
            : 'Top-up complete (dev mode). Virtual cash restored to $10,000.',
      cashBalance: STARTING_CASH,
    };
  }

  async assertRestrictedForTopUp(userId: string) {
    const portfolio = await this.prisma.paperPortfolio.findUnique({
      where: { userId },
      include: { positions: true },
    });

    if (!portfolio) {
      throw new NotFoundException('Paper portfolio not found');
    }

    const cashBalance = Number(portfolio.cashBalance);
    const isRestricted = cashBalance < RESTRICTED_CASH_THRESHOLD_USD;

    if (!isRestricted) {
      throw new BadRequestException(
        `Top-up only required when cash falls below $${RESTRICTED_CASH_THRESHOLD_USD.toLocaleString()}.`,
      );
    }

    return portfolio;
  }

  /** @deprecated use assertRestrictedForTopUp */
  async assertBustedForReset(userId: string) {
    return this.assertRestrictedForTopUp(userId);
  }

  async migrateGuestPortfolio(guestUserId: string, targetUserId: string) {
    if (guestUserId === targetUserId) {
      return { migrated: false, positionsMerged: 0 };
    }

    const guest = await this.prisma.user.findUnique({
      where: { id: guestUserId },
      include: { paperPortfolio: { include: { positions: true } } },
    });

    if (!guest?.paperPortfolio) {
      return { migrated: false, positionsMerged: 0 };
    }

    const guestPf = guest.paperPortfolio;
    const guestCash = Number(guestPf.cashBalance);
    const hasActivity =
      guestPf.positions.length > 0 || Math.abs(guestCash - STARTING_CASH) > 0.01;

    if (!hasActivity) {
      return { migrated: false, positionsMerged: 0 };
    }

    await this.ensurePortfolio(targetUserId);

    const positionsMerged = await this.prisma.$transaction(async (tx) => {
      const target = await tx.paperPortfolio.findUnique({
        where: { userId: targetUserId },
        include: { positions: true },
      });
      if (!target) {
        throw new NotFoundException('Target portfolio not found');
      }

      const cashDelta = guestCash - STARTING_CASH;
      if (Math.abs(cashDelta) > 0.01) {
        await tx.paperPortfolio.update({
          where: { id: target.id },
          data: { cashBalance: { increment: cashDelta } },
        });
      }

      let merged = 0;
      for (const gp of guestPf.positions) {
        const existing = target.positions.find((p) => p.projectId === gp.projectId);
        const gQty = Number(gp.quantity);
        const gAvg = Number(gp.avgBuyPrice);

        if (existing) {
          const tQty = Number(existing.quantity);
          const tAvg = Number(existing.avgBuyPrice);
          const newQty = tQty + gQty;
          const newAvg = newQty > 0 ? (tQty * tAvg + gQty * gAvg) / newQty : gAvg;
          await tx.paperPosition.update({
            where: { id: existing.id },
            data: {
              quantity: new Prisma.Decimal(newQty),
              avgBuyPrice: new Prisma.Decimal(newAvg),
            },
          });
        } else {
          await tx.paperPosition.create({
            data: {
              portfolioId: target.id,
              projectId: gp.projectId,
              quantity: gp.quantity,
              avgBuyPrice: gp.avgBuyPrice,
            },
          });
        }

        await tx.paperPosition.delete({ where: { id: gp.id } });
        merged += 1;
      }

      await tx.paperPortfolio.update({
        where: { id: guestPf.id },
        data: { cashBalance: STARTING_CASH, totalValue: STARTING_CASH },
      });

      await tx.paperTrade.updateMany({
        where: { userId: guestUserId },
        data: { userId: targetUserId },
      });

      return merged;
    });

    return { migrated: true, positionsMerged };
  }

  async executeTrade(dto: PaperTradeDto) {
    const preview = await this.previewTokenInput(dto.dexscreenerUrl.trim());
    const parsed = parseDexScreenerUrl(preview.dexscreenerUrl);
    if (!parsed) {
      throw new BadRequestException('Invalid DexScreener URL from token preview');
    }

    const price = Number(preview.marketPreview.priceUsd);
    if (!price || price <= 0) {
      throw new BadRequestException('Could not resolve live price from DexScreener');
    }

    const project = await this.ensureDynamicProject(preview, parsed.address);
    let amountUsd = dto.amountUsd;

    await this.ensurePortfolio(dto.userId);

    const portfolio = await this.prisma.paperPortfolio.findUnique({
      where: { userId: dto.userId },
      include: { positions: true },
    });
    if (!portfolio) {
      throw new NotFoundException('Paper portfolio not found');
    }

    let quantity = amountUsd / price;
    let realizedPnlUsd: number | undefined;

    if (dto.side === PaperTradeSide.SELL) {
      const existing = portfolio.positions.find((p) => p.projectId === project.id);
      const held = existing ? Number(existing.quantity) : 0;
      const maxSellUsd = held * price * 0.999;
      if (maxSellUsd <= 0) {
        throw new BadRequestException('No tokens to sell for this position');
      }
      if (amountUsd > maxSellUsd) {
        amountUsd = Math.floor(maxSellUsd * 100) / 100;
      }
      quantity = amountUsd / price;
      if (quantity <= 0) {
        throw new BadRequestException('Sell amount too small');
      }
      const avgBuy = existing ? Number(existing.avgBuyPrice) : 0;
      realizedPnlUsd = Math.round((price - avgBuy) * quantity * 100) / 100;
    }

    const result = await this.executeTradeInternal({
      userId: dto.userId,
      projectId: project.id,
      dexscreenerUrl: dto.dexscreenerUrl.trim(),
      side: dto.side,
      amountUsd,
      quantityOverride: quantity,
      realizedPnlUsd,
      comment: dto.comment,
      catalyst: dto.catalyst,
      targetUsd: dto.targetUsd,
      timeHorizon: dto.timeHorizon,
      marketPreview: preview.marketPreview,
    });

    if (
      dto.side === PaperTradeSide.SELL &&
      realizedPnlUsd != null &&
      Math.abs(realizedPnlUsd) >= MIN_SELL_USD
    ) {
      await this.prisma.virtualEconomyEvent.create({
        data: {
          userId: dto.userId,
          type: 'REALIZED_PNL',
          amountUsd: new Prisma.Decimal(realizedPnlUsd),
          note: `${project.ticker} sell · ${realizedPnlUsd >= 0 ? '+' : ''}$${realizedPnlUsd.toFixed(2)}`,
        },
      });
    }

    if (result.feedPostCommentCount > 0) {
      await this.feedService.refreshHighlights();
    }

    if (dto.side === PaperTradeSide.BUY) {
      this.hotBuy.checkAfterBuy(project.id).catch(() => undefined);
      void this.notifications.notifyFollowersOfTraderBuy(dto.userId, {
        ticker: project.ticker,
        amountUsd: result.amountUsd,
        projectSlug: project.slug,
      });
      void this.socialSignals.onPaperBuy(project.id);
    }

    return {
      success: true,
      side: dto.side,
      projectId: project.id,
      ticker: project.ticker,
      quantity: result.quantity,
      priceUsd: result.priceUsd,
      amountUsd: result.amountUsd,
      cashBalance: result.cashBalance,
      feedPostId: result.feedPostId,
      realizedPnlUsd: result.realizedPnlUsd ?? null,
    };
  }

  async getLeaderboard(limit = 20) {
    const portfolios = await this.prisma.paperPortfolio.findMany({
      include: { user: true },
    });

    const ranked = await Promise.all(
      portfolios.map(async (portfolio) => {
        const snapshot = await this.getPortfolio(portfolio.userId);
        return {
          userId: portfolio.userId,
          displayName: formatPublicAccountLabel(
            portfolio.user.name,
            portfolio.user.email,
          ),
          twitterHandle: portfolio.user.twitterHandle,
          totalValue: snapshot.totalValue,
          pnl: snapshot.pnl,
          roi: snapshot.roi,
        };
      }),
    );

    ranked.sort((a, b) => b.totalValue - a.totalValue);

    void this.syncLeaderboardEntries(ranked.slice(0, 100));

    return ranked.slice(0, limit).map((entry, index) => ({
      rank: index + 1,
      ...entry,
      period: LeaderboardPeriod.ALL_TIME,
    }));
  }

  private async syncLeaderboardEntries(
    ranked: Array<{ userId: string; totalValue: number; pnl: number; roi: number }>,
  ) {
    await Promise.all(
      ranked.map((entry, index) =>
        this.prisma.leaderboardEntry.upsert({
          where: {
            userId_period: { userId: entry.userId, period: LeaderboardPeriod.ALL_TIME },
          },
          create: {
            userId: entry.userId,
            period: LeaderboardPeriod.ALL_TIME,
            rank: index + 1,
            pnl: entry.pnl,
            roi: entry.roi,
          },
          update: {
            rank: index + 1,
            pnl: entry.pnl,
            roi: entry.roi,
            computedAt: new Date(),
          },
        }),
      ),
    );
  }

  async getBustedTraders(limit = 30) {
    const portfolios = await this.prisma.paperPortfolio.findMany({
      include: { user: true },
    });

    const ranked = await Promise.all(
      portfolios.map(async (portfolio) => {
        const snapshot = await this.getPortfolio(portfolio.userId);
        return {
          userId: portfolio.userId,
          displayName: formatPublicAccountLabel(
            portfolio.user.name,
            portfolio.user.email,
          ),
          twitterHandle: portfolio.user.twitterHandle,
          totalValue: snapshot.totalValue,
          pnl: snapshot.pnl,
          roi: snapshot.roi,
          isBusted: snapshot.isBusted,
        };
      }),
    );

    return ranked
      .filter((entry) => entry.pnl < 0 || entry.isBusted)
      .sort((a, b) => a.pnl - b.pnl)
      .slice(0, limit)
      .map((entry, index) => ({ rank: index + 1, ...entry }));
  }

  async getResetInfo() {
    const stripeEnabled = Boolean(process.env.STRIPE_SECRET_KEY?.trim());
    const treasuryAddress = await resolveSolanaTreasuryAddress(this.prisma);
    const cryptoEnabled = isSolanaTopUpConfigured(treasuryAddress);

    return {
      available: true,
      resetFeeUsd: TOP_UP_FEE_USD,
      restrictedThresholdUsd: RESTRICTED_CASH_THRESHOLD_USD,
      stripeEnabled,
      cryptoEnabled,
      treasuryAddress,
      message: cryptoEnabled
        ? `Cash below $${RESTRICTED_CASH_THRESHOLD_USD.toLocaleString()}? Pay $${TOP_UP_FEE_USD} USDC/SOL on-chain or via Stripe to restore $10,000 virtual cash.`
        : stripeEnabled
          ? `Cash below $${RESTRICTED_CASH_THRESHOLD_USD.toLocaleString()}? Pay $${TOP_UP_FEE_USD} via Stripe to restore $10,000 virtual cash.`
          : `Cash below $${RESTRICTED_CASH_THRESHOLD_USD.toLocaleString()}? Pay $${TOP_UP_FEE_USD} to restore $10,000 virtual cash (dev mode simulates payment).`,
    };
  }

  private async ensureDynamicProject(
    preview: Awaited<ReturnType<DexscreenerService['previewFromUrl']>>,
    poolAddress: string,
  ) {
    if (!preview.chainSlug) {
      throw new BadRequestException(
        'Unsupported chain for this token. Supported: Ethereum, Solana, Polygon, Arbitrum, Optimism, Base, Avalanche, BNB.',
      );
    }

    const chain = await this.prisma.chain.findUnique({
      where: { slug: preview.chainSlug },
    });
    if (!chain) {
      throw new BadRequestException('Chain not configured in database');
    }

    const existingByContract = await this.prisma.project.findFirst({
      where: {
        contractAddress: preview.contractAddress,
        chainId: chain.id,
      },
    });
    const liveFromPreview =
      preview.marketPreview.priceUsd != null &&
      Number(preview.marketPreview.priceUsd) > 0;

    if (existingByContract) {
      return this.prisma.project.update({
        where: { id: existingByContract.id },
        data: {
          dexscreenerUrl: preview.dexscreenerUrl,
          trackingActive: true,
          logoUrl: preview.logoUrl ?? existingByContract.logoUrl,
          lastTradeAt: new Date(),
          ...(liveFromPreview
            ? { isLiveToken: true, lifecycleStage: ProjectLifecycleStage.LIVE_TRADING }
            : {}),
        },
      });
    }

    const baseSlug = slugify(`${preview.ticker}-${preview.chainSlug}`);
    let slug = baseSlug;
    let suffix = 1;
    while (await this.prisma.project.findUnique({ where: { slug } })) {
      slug = `${baseSlug}-${suffix++}`;
    }

    return this.prisma.project.create({
      data: {
        slug,
        name: preview.projectName,
        ticker: preview.ticker.toUpperCase(),
        summary: preview.summary,
        logoUrl: preview.logoUrl,
        websiteUrl: preview.websiteUrl,
        contractAddress: preview.contractAddress,
        dexscreenerUrl: preview.dexscreenerUrl,
        chainId: chain.id,
        source: ProjectSource.DYNAMIC,
        approved: true,
        trackingActive: true,
        lastTradeAt: new Date(),
        isLiveToken: liveFromPreview,
        lifecycleStage: liveFromPreview ? ProjectLifecycleStage.LIVE_TRADING : ProjectLifecycleStage.IDEA,
        metrics: {
          create: {
            priceUsd: preview.marketPreview.priceUsd
              ? new Prisma.Decimal(preview.marketPreview.priceUsd)
              : undefined,
            marketCap: preview.marketPreview.marketCap
              ? new Prisma.Decimal(preview.marketPreview.marketCap)
              : undefined,
            volume24h: preview.marketPreview.volume24h
              ? new Prisma.Decimal(preview.marketPreview.volume24h)
              : undefined,
            liquidity: preview.marketPreview.liquidityUsd
              ? new Prisma.Decimal(preview.marketPreview.liquidityUsd)
              : undefined,
            priceChange24h: preview.marketPreview.priceChange24h
              ? new Prisma.Decimal(preview.marketPreview.priceChange24h)
              : undefined,
          },
        },
        socials: {
          create: {
            twitterUrl: preview.founderTwitter,
            telegramUrl: preview.telegramUrl,
          },
        },
      },
    });
  }

  private async cleanupInactiveTracking() {
    const cutoff = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
    const dynamicProjects = await this.prisma.project.findMany({
      where: {
        source: ProjectSource.DYNAMIC,
        trackingActive: true,
      },
      include: {
        paperPositions: true,
      },
    });

    for (const project of dynamicProjects) {
      const hasHoldings = project.paperPositions.some(
        (p) => Number(p.quantity) > 0.00000001,
      );
      if (hasHoldings) continue;

      const stale =
        !project.lastTradeAt || project.lastTradeAt.getTime() < cutoff.getTime();
      if (stale) {
        await this.prisma.project.update({
          where: { id: project.id },
          data: { trackingActive: false },
        });
      }
    }
  }
}
