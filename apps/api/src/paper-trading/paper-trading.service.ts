import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { slugify, formatPublicAccountLabel, POINTS, STARTING_CASH_USD, RESTRICTED_CASH_THRESHOLD_USD, TOP_UP_FEE_USD } from '@dcf/utils';
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

const STARTING_CASH = STARTING_CASH_USD;

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
    const user = await this.prisma.user.create({
      data: {
        email: `paper-${guestId}@guest.local`,
        name: displayName?.trim() || `Trader ${guestId}`,
        paperPortfolio: {
          create: {
            cashBalance: STARTING_CASH,
            totalValue: STARTING_CASH,
          },
        },
      },
      include: { paperPortfolio: true },
    });

    return {
      userId: user.id,
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
    };
  }

  async getPublicPortfolio(userId: string) {
    const portfolio = await this.getPortfolio(userId);
    return {
      userId: portfolio.userId,
      displayName: formatPublicAccountLabel(
        portfolio.accountName,
        portfolio.accountEmail,
      ),
      reputationPoints: portfolio.reputationPoints,
      contributorLevel: portfolio.contributorLevel,
      twitterHandle: (
        await this.prisma.user.findUnique({
          where: { id: userId },
          select: { twitterHandle: true },
        })
      )?.twitterHandle ?? null,
      cashBalance: portfolio.cashBalance,
      totalValue: portfolio.totalValue,
      pnl: portfolio.pnl,
      roi: portfolio.roi,
      startingCash: portfolio.startingCash,
      positionCount: portfolio.positions.length,
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
      })),
    };
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
    }

    const quantity = amountUsd / price;

    if (dto.side === PaperTradeSide.BUY) {
      if (Number(portfolio.cashBalance) < RESTRICTED_CASH_THRESHOLD_USD) {
        throw new BadRequestException(
          `Cash below $${RESTRICTED_CASH_THRESHOLD_USD.toLocaleString()}. Top up for $${TOP_UP_FEE_USD} to continue buying.`,
        );
      }
      if (Number(portfolio.cashBalance) < amountUsd) {
        throw new BadRequestException('Insufficient paper cash balance');
      }
    } else if (quantity <= 0) {
      throw new BadRequestException('Sell amount too small');
    }

    const result = await this.prisma.$transaction(async (tx) => {
      const existingPosition = await tx.paperPosition.findUnique({
        where: {
          portfolioId_projectId: {
            portfolioId: portfolio.id,
            projectId: project.id,
          },
        },
      });

      let cashDelta = 0;
      if (dto.side === PaperTradeSide.BUY) {
        cashDelta = -amountUsd;
        const prevQty = existingPosition ? Number(existingPosition.quantity) : 0;
        const prevAvg = existingPosition ? Number(existingPosition.avgBuyPrice) : 0;
        const newQty = prevQty + quantity;
        const newAvg =
          newQty > 0 ? (prevQty * prevAvg + amountUsd) / newQty : price;

        await tx.paperPosition.upsert({
          where: {
            portfolioId_projectId: {
              portfolioId: portfolio.id,
              projectId: project.id,
            },
          },
          create: {
            portfolioId: portfolio.id,
            projectId: project.id,
            quantity: new Prisma.Decimal(newQty),
            avgBuyPrice: new Prisma.Decimal(newAvg),
            ...(dto.comment?.trim() || dto.catalyst?.trim() || dto.targetUsd || dto.timeHorizon?.trim()
              ? {
                  convictionThesis: dto.comment?.trim() || undefined,
                  convictionCatalyst: dto.catalyst?.trim() || undefined,
                  convictionTargetUsd: dto.targetUsd
                    ? new Prisma.Decimal(dto.targetUsd)
                    : undefined,
                  convictionTimeHorizon: dto.timeHorizon?.trim() || undefined,
                  convictionRecordedAt: new Date(),
                }
              : {}),
          },
          update: {
            quantity: new Prisma.Decimal(newQty),
            avgBuyPrice: new Prisma.Decimal(newAvg),
            ...(dto.comment?.trim() || dto.catalyst?.trim() || dto.targetUsd || dto.timeHorizon?.trim()
              ? {
                  convictionThesis: dto.comment?.trim() || undefined,
                  convictionCatalyst: dto.catalyst?.trim() || undefined,
                  convictionTargetUsd: dto.targetUsd
                    ? new Prisma.Decimal(dto.targetUsd)
                    : undefined,
                  convictionTimeHorizon: dto.timeHorizon?.trim() || undefined,
                  convictionRecordedAt: new Date(),
                }
              : {}),
          },
        });
      } else {
        cashDelta = amountUsd;
        const prevQty = Number(existingPosition!.quantity);
        const newQty = prevQty - quantity;
        if (newQty <= 0.00000001) {
          await tx.paperPosition.delete({
            where: { id: existingPosition!.id },
          });
        } else {
          await tx.paperPosition.update({
            where: { id: existingPosition!.id },
            data: { quantity: new Prisma.Decimal(newQty) },
          });
        }
      }

      const updatedPortfolio = await tx.paperPortfolio.update({
        where: { id: portfolio.id },
        data: {
          cashBalance: { increment: cashDelta },
        },
      });

      const trade = await tx.paperTrade.create({
        data: {
          userId: dto.userId,
          projectId: project.id,
          side: dto.side,
          quantity: new Prisma.Decimal(quantity),
          priceUsd: new Prisma.Decimal(price),
          totalUsd: new Prisma.Decimal(amountUsd),
        },
      });

      await tx.project.update({
        where: { id: project.id },
        data: {
          lastTradeAt: new Date(),
          trackingActive: true,
          metrics: {
            upsert: {
              create: {
                priceUsd: new Prisma.Decimal(price),
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
              update: {
                priceUsd: new Prisma.Decimal(price),
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
          },
        },
      });

      return { updatedPortfolio, trade };
    });

    const feedPost = await this.feedService.createPostForTrade(
      result.trade.id,
      dto.userId,
      project.id,
      dto.comment,
    );

    if (feedPost.commentCount > 0) {
      await this.feedService.refreshHighlights();
    }

    if (dto.side === PaperTradeSide.BUY) {
      this.hotBuy.checkAfterBuy(project.id).catch(() => undefined);
    }

    await this.analytics.track(
      dto.side === PaperTradeSide.BUY
        ? AnalyticsEventType.PAPER_TRADE_BUY
        : AnalyticsEventType.PAPER_TRADE_SELL,
      {
        userId: dto.userId,
        projectId: project.id,
        metadata: {
          ticker: project.ticker,
          amountUsd,
          priceUsd: price,
        },
      },
    );

    await this.cleanupInactiveTracking();

    await this.points.award(dto.userId, POINTS.PAPER_TRADE, 'PAPER_TRADE');

    if (dto.side === PaperTradeSide.BUY) {
      void this.notifications.notifyFollowersOfTraderBuy(dto.userId, {
        ticker: project.ticker,
        amountUsd,
        projectSlug: project.slug,
      });
    }

    if (dto.side === PaperTradeSide.BUY) {
      void this.socialSignals.onPaperBuy(project.id);
    }

    return {
      success: true,
      side: dto.side,
      projectId: project.id,
      ticker: project.ticker,
      quantity,
      priceUsd: price,
      amountUsd,
      cashBalance: Number(result.updatedPortfolio.cashBalance),
      feedPostId: feedPost.id,
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

    const busted = await Promise.all(
      portfolios.map(async (portfolio) => {
        const snapshot = await this.getPortfolio(portfolio.userId);
        if (!snapshot.isBusted) return null;
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
          bustedAt: portfolio.updatedAt,
        };
      }),
    );

    return busted
      .filter(Boolean)
      .sort((a, b) => (a!.totalValue ?? 0) - (b!.totalValue ?? 0))
      .slice(0, limit)
      .map((entry, index) => ({ rank: index + 1, ...entry! }));
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
