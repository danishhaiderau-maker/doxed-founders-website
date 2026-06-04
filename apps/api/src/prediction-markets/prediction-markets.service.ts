import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import {
  buildOracleLeaderboard,
  computeParimutuelPayout,
  computeScoutConviction,
  formatPublicAccountLabel,
  generatePredictionQuestions,
  computePredictionHeatScore,
  predictionHeatLabel,
  sortPredictionMarketsByHeat,
  PREDICTION_MARKET_HOURS,
  PREDICTION_MARKET_MIN_STAKE_USD,
  predictionMarketOutcome,
  RESTRICTED_CASH_THRESHOLD_USD,
  TOP_UP_FEE_USD,
  STARTING_CASH_USD,
} from '@dcf/utils';
import {
  FounderEventType,
  NotificationType,
  Prisma,
  ScoutMarketStatus,
} from '@prisma/client';
import { EventsService } from '../events/events.service';
import { HighValueInsightsService } from '../notifications/high-value-insights.service';
import { NotificationsService } from '../notifications/notifications.service';
import { PointsService } from '../points/points.service';
import { PrismaService } from '../prisma/prisma.service';

export type PredictionMarketView = {
  id: string;
  question: string;
  status: string;
  source: string;
  yesPoolUsd: number;
  noPoolUsd: number;
  totalPoolUsd: number;
  conviction: number;
  participantCount: number;
  resolvesAt: string | null;
  hoursLeft: number | null;
  outcome: boolean | null;
  project: {
    slug: string;
    name: string;
    ticker: string;
    logoUrl: string | null;
  };
  creatorName: string | null;
  viewerPosition: { side: string; amountUsd: number } | null;
  heatScore: number;
  heatLabel: 'Blazing' | 'Heating up' | null;
};

@Injectable()
export class PredictionMarketsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly points: PointsService,
    private readonly events: EventsService,
    private readonly notifications: NotificationsService,
    private readonly insights: HighValueInsightsService,
  ) {}

  private mapMarket(
    m: {
      id: string;
      question: string;
      status: ScoutMarketStatus;
      source: string;
      yesPoolUsd: Prisma.Decimal;
      noPoolUsd: Prisma.Decimal;
      outcome: boolean | null;
      resolvesAt: Date | null;
      creator?: { name: string | null; email: string } | null;
      project: { slug: string; name: string; ticker: string; logoUrl: string | null };
      positions: { userId: string; side: string; amountUsd: Prisma.Decimal }[];
    },
    viewerUserId?: string,
  ): PredictionMarketView {
    const yesPool = Number(m.yesPoolUsd);
    const noPool = Number(m.noPoolUsd);
    const totalPoolUsd = yesPool + noPool;
    const participantCount = m.positions.filter((p) => Number(p.amountUsd) > 0).length;
    const viewerPosition = viewerUserId
      ? m.positions.find((p) => p.userId === viewerUserId)
      : undefined;
    const hoursLeft =
      m.resolvesAt && m.status === ScoutMarketStatus.OPEN
        ? Math.max(0, Math.ceil((m.resolvesAt.getTime() - Date.now()) / 3_600_000))
        : null;

    return {
      id: m.id,
      question: m.question,
      status: m.status,
      source: m.source,
      yesPoolUsd: yesPool,
      noPoolUsd: noPool,
      totalPoolUsd,
      conviction: computeScoutConviction(yesPool, noPool),
      participantCount,
      resolvesAt: m.resolvesAt?.toISOString() ?? null,
      hoursLeft,
      outcome: m.outcome,
      project: m.project,
      creatorName: m.creator
        ? formatPublicAccountLabel(m.creator.name, m.creator.email)
        : null,
      viewerPosition: viewerPosition
        ? { side: viewerPosition.side, amountUsd: Number(viewerPosition.amountUsd) }
        : null,
      heatScore: computePredictionHeatScore(totalPoolUsd, participantCount),
      heatLabel: predictionHeatLabel(totalPoolUsd, participantCount),
    };
  }

  /** AI-seeded markets do not broadcast — human markets use HighValueInsightsService. */
  private async notifyNewPredictions(_projectId: string, _count: number) {
    return;
  }

  async settleExpiredMarkets() {
    const expired = await this.prisma.scoutMarket.findMany({
      where: {
        status: ScoutMarketStatus.OPEN,
        resolvesAt: { lte: new Date() },
      },
      include: { positions: true },
    });

    for (const market of expired) {
      await this.settleMarket(market.id);
    }

    return expired.length;
  }

  async settleMarket(marketId: string) {
    const market = await this.prisma.scoutMarket.findUnique({
      where: { id: marketId },
      include: { positions: true, project: { include: { founder: true } } },
    });
    if (!market || market.status !== ScoutMarketStatus.OPEN) return null;

    const yesPool = Number(market.yesPoolUsd);
    const noPool = Number(market.noPoolUsd);
    const total = yesPool + noPool;

    if (total <= 0) {
      await this.prisma.scoutMarket.update({
        where: { id: marketId },
        data: { status: ScoutMarketStatus.CANCELLED },
      });
      return { settled: false, reason: 'empty_pool' };
    }

    const yesWins = predictionMarketOutcome(yesPool, noPool);
    const winningSide = yesWins ? 'YES' : 'NO';
    const winningPool = yesWins ? yesPool : noPool;
    const winners = market.positions.filter((p) => p.side === winningSide);

    await this.prisma.$transaction(async (tx) => {
      for (const pos of winners) {
        const stake = Number(pos.amountUsd);
        const payout = computeParimutuelPayout(stake, winningPool, total);
        if (payout <= 0) continue;

        await tx.paperPortfolio.update({
          where: { userId: pos.userId },
          data: { cashBalance: { increment: payout } },
        });
        await tx.virtualEconomyEvent.create({
          data: {
            userId: pos.userId,
            type: 'PREDICTION_WIN',
            amountUsd: new Prisma.Decimal(payout),
            note: `Won ${winningSide} on: ${market.question.slice(0, 80)}`,
          },
        });
      }

      await tx.scoutMarket.update({
        where: { id: marketId },
        data: {
          status: ScoutMarketStatus.RESOLVED,
          outcome: yesWins,
        },
      });
    });

    for (const pos of winners) {
      const stake = Number(pos.amountUsd);
      const payout = computeParimutuelPayout(stake, winningPool, total);
      await this.notifications.notifyUser(pos.userId, {
        type: NotificationType.POINTS_EARNED,
        title: `Prediction won: ${market.project.ticker}`,
        body: `Your ${winningSide} stake paid out ${payout.toFixed(0)} paper dollars.`,
        link: '/predict',
      });
    }

    const openLeft = await this.prisma.scoutMarket.count({
      where: { projectId: market.projectId, status: ScoutMarketStatus.OPEN },
    });
    if (openLeft === 0) {
      await this.seedMarketsForProject(market.projectId, { isNewListing: false });
    }

    return { settled: true, outcome: yesWins, winners: winners.length, totalPool: total };
  }

  async listHot(viewerUserId?: string, limit = 8) {
    const markets = await this.listGlobal(viewerUserId, 60);
    return sortPredictionMarketsByHeat(markets).slice(0, limit);
  }

  async seedMarketsForProject(projectId: string, options?: { isNewListing?: boolean }) {
    const existing = await this.prisma.scoutMarket.count({
      where: { projectId, status: ScoutMarketStatus.OPEN },
    });
    if (existing > 0) return existing;

    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      include: {
        founder: { select: { name: true } },
        metrics: true,
      },
    });
    if (!project) return 0;

    const priorQuestions = await this.prisma.scoutMarket.findMany({
      where: { projectId },
      select: { question: true },
    });
    const seen = new Set(priorQuestions.map((q) => q.question.trim()));

    const isLive = project.isLiveToken;
    const questions = generatePredictionQuestions({
      projectName: project.name,
      ticker: project.ticker,
      founderName: project.founder?.name,
      priceChange24h: project.metrics?.priceChange24h
        ? Number(project.metrics.priceChange24h)
        : null,
      marketCap: project.metrics?.marketCap ? Number(project.metrics.marketCap) : null,
      liquidityUsd: project.metrics?.liquidity
        ? Number(project.metrics.liquidity)
        : null,
      volume24h: project.metrics?.volume24h ? Number(project.metrics.volume24h) : null,
      isNewListing: (options?.isNewListing ?? false) && !isLive,
      isLiveToken: isLive,
    }).filter((q) => !seen.has(q));

    if (questions.length === 0) return existing;

    const resolvesAt = new Date(Date.now() + PREDICTION_MARKET_HOURS * 3_600_000);

    await this.prisma.scoutMarket.createMany({
      data: questions.map((question) => ({
        projectId,
        question,
        source: 'AI',
        resolvesAt,
      })),
    });

    await this.notifyNewPredictions(projectId, questions.length);

    return existing + questions.length;
  }

  async listGlobal(viewerUserId?: string, limit = 40) {
    await this.settleExpiredMarkets();

    const openCount = await this.prisma.scoutMarket.count({
      where: { status: ScoutMarketStatus.OPEN },
    });
    if (openCount < 5) {
      const projects = await this.prisma.project.findMany({
        where: { approved: true },
        select: { id: true },
        take: 25,
      });
      for (const p of projects) {
        await this.seedMarketsForProject(p.id, { isNewListing: false });
      }
    }

    const markets = await this.prisma.scoutMarket.findMany({
      where: { status: ScoutMarketStatus.OPEN },
      orderBy: [{ resolvesAt: 'asc' }, { createdAt: 'desc' }],
      take: limit * 2,
      include: {
        project: { select: { slug: true, name: true, ticker: true, logoUrl: true } },
        creator: { select: { name: true, email: true } },
        positions: true,
      },
    });

    const mapped = markets.map((m) => this.mapMarket(m, viewerUserId));
    return sortPredictionMarketsByHeat(mapped).slice(0, limit);
  }

  async listForProject(slug: string, viewerUserId?: string) {
    await this.settleExpiredMarkets();

    const project = await this.prisma.project.findUnique({ where: { slug } });
    if (!project) throw new NotFoundException('Project not found');

    let markets = await this.prisma.scoutMarket.findMany({
      where: { projectId: project.id, status: ScoutMarketStatus.OPEN },
      orderBy: { createdAt: 'asc' },
      include: {
        project: { select: { slug: true, name: true, ticker: true, logoUrl: true } },
        creator: { select: { name: true, email: true } },
        positions: true,
      },
    });

    if (markets.length === 0) {
      await this.seedMarketsForProject(project.id, { isNewListing: false });
      markets = await this.prisma.scoutMarket.findMany({
        where: { projectId: project.id, status: ScoutMarketStatus.OPEN },
        orderBy: { createdAt: 'asc' },
        include: {
          project: { select: { slug: true, name: true, ticker: true, logoUrl: true } },
          creator: { select: { name: true, email: true } },
          positions: true,
        },
      });
    }

    return sortPredictionMarketsByHeat(
      markets.map((m) => this.mapMarket(m, viewerUserId)),
    );
  }

  async createMarket(userId: string, input: { projectSlug: string; question: string }) {
    const question = input.question.trim();
    if (question.length < 12) {
      throw new BadRequestException('Question must be at least 12 characters');
    }
    if (question.length > 280) {
      throw new BadRequestException('Question must be under 280 characters');
    }

    const project = await this.prisma.project.findUnique({
      where: { slug: input.projectSlug },
    });
    if (!project?.approved) throw new NotFoundException('Project not found');

    const resolvesAt = new Date(Date.now() + PREDICTION_MARKET_HOURS * 3_600_000);

    const market = await this.prisma.scoutMarket.create({
      data: {
        projectId: project.id,
        question,
        source: 'USER',
        createdByUserId: userId,
        resolvesAt,
      },
      include: {
        project: { select: { slug: true, name: true, ticker: true, logoUrl: true } },
        creator: { select: { name: true, email: true } },
        positions: true,
      },
    });

    void this.insights.notifyHumanPredictionMarket(userId, {
      question,
      ticker: project.ticker,
      projectSlug: project.slug,
    });

    return this.mapMarket(market, userId);
  }

  async stake(userId: string, marketId: string, side: 'YES' | 'NO', amountUsd: number) {
    if (amountUsd < PREDICTION_MARKET_MIN_STAKE_USD) {
      throw new BadRequestException(`Minimum stake is $${PREDICTION_MARKET_MIN_STAKE_USD}`);
    }
    if (side !== 'YES' && side !== 'NO') {
      throw new BadRequestException('Side must be YES or NO');
    }

    await this.settleExpiredMarkets();

    const market = await this.prisma.scoutMarket.findUnique({
      where: { id: marketId },
      include: { project: { include: { founder: true } }, positions: true },
    });
    if (!market || market.status !== ScoutMarketStatus.OPEN) {
      throw new BadRequestException('Prediction market is not open');
    }
    if (market.resolvesAt && market.resolvesAt.getTime() <= Date.now()) {
      throw new BadRequestException('This market has closed for staking');
    }

    const portfolio = await this.prisma.paperPortfolio.findUnique({ where: { userId } });
    if (!portfolio) throw new BadRequestException('Start a paper trading session first');

    const cash = Number(portfolio.cashBalance);
    if (cash < RESTRICTED_CASH_THRESHOLD_USD) {
      throw new BadRequestException(
        `Paper cash below $${RESTRICTED_CASH_THRESHOLD_USD.toLocaleString()}. Top up for $${TOP_UP_FEE_USD} real money to get $${STARTING_CASH_USD.toLocaleString()} paper dollars and keep playing.`,
      );
    }

    const existing = market.positions.find((p) => p.userId === userId);
    const existingAmt = existing ? Number(existing.amountUsd) : 0;
    const delta = amountUsd - existingAmt;

    if (delta > cash) {
      throw new BadRequestException('Insufficient paper cash — earn points or top up your portfolio');
    }

    await this.prisma.$transaction(async (tx) => {
      await tx.scoutMarketPosition.upsert({
        where: { marketId_userId: { marketId, userId } },
        create: {
          marketId,
          userId,
          side,
          amountUsd: new Prisma.Decimal(amountUsd),
        },
        update: {
          side,
          amountUsd: new Prisma.Decimal(amountUsd),
        },
      });

      const prevSide = existing?.side;
      const prevAmt = existingAmt;
      let yesDelta = 0;
      let noDelta = 0;

      if (prevSide === 'YES') yesDelta -= prevAmt;
      if (prevSide === 'NO') noDelta -= prevAmt;
      if (side === 'YES') yesDelta += amountUsd;
      if (side === 'NO') noDelta += amountUsd;

      await tx.scoutMarket.update({
        where: { id: marketId },
        data: {
          yesPoolUsd: { increment: yesDelta },
          noPoolUsd: { increment: noDelta },
        },
      });

      if (delta !== 0) {
        await tx.paperPortfolio.update({
          where: { userId },
          data: { cashBalance: { increment: -delta } },
        });
        await tx.virtualEconomyEvent.create({
          data: {
            userId,
            type: delta > 0 ? 'PREDICTION_STAKE' : 'PREDICTION_UNSTAKE',
            amountUsd: new Prisma.Decimal(Math.abs(delta)),
            note: `Prediction ${side}: ${market.question.slice(0, 60)}`,
          },
        });
      }
    });

    if (market.project.founder) {
      await this.events.emit({
        founderId: market.project.founder.id,
        projectId: market.projectId,
        userId,
        type: FounderEventType.SCOUT_MARKET_STAKE,
        source: 'prediction-markets',
        title: `Prediction: ${side} on "${market.question.slice(0, 60)}"`,
        payload: { marketId, side, amountUsd },
      });
    }

    await this.points.awardOnce(userId, `PREDICTION_STAKE:${marketId}`, 5);

    const updated = await this.prisma.scoutMarket.findUnique({ where: { id: marketId } });
    const yesPool = Number(updated?.yesPoolUsd ?? 0);
    const noPool = Number(updated?.noPoolUsd ?? 0);

    return {
      success: true,
      marketId,
      conviction: computeScoutConviction(yesPool, noPool),
      yesPoolUsd: yesPool,
      noPoolUsd: noPool,
      totalPoolUsd: yesPool + noPool,
      heatLabel: predictionHeatLabel(yesPool + noPool, market.positions.length + (existing ? 0 : 1)),
    };
  }

  /** Oracle Rank — risk-adjusted forecasting from resolved markets only. */
  async oracleLeaderboard(limit = 30) {
    await this.settleExpiredMarkets();

    const resolved = await this.prisma.scoutMarket.findMany({
      where: { status: ScoutMarketStatus.RESOLVED, outcome: { not: null } },
      include: {
        positions: {
          where: { amountUsd: { gt: 0 } },
          include: {
            user: {
              select: {
                id: true,
                name: true,
                email: true,
                platformHandle: true,
                twitterHandle: true,
              },
            },
          },
        },
      },
      orderBy: { updatedAt: 'desc' },
      take: 200,
    });

    const rows: Parameters<typeof buildOracleLeaderboard>[0] = [];

    for (const market of resolved) {
      const yesPool = Number(market.yesPoolUsd);
      const noPool = Number(market.noPoolUsd);
      const total = yesPool + noPool;
      const yesWins = market.outcome === true;
      const winningSide = yesWins ? 'YES' : 'NO';
      const winningPool = yesWins ? yesPool : noPool;

      for (const pos of market.positions) {
        const stake = Number(pos.amountUsd);
        if (stake <= 0) continue;
        const won = pos.side === winningSide;
        const sidePool = pos.side === 'YES' ? yesPool : noPool;
        const implied =
          total > 0 ? sidePool / total : 0.5;
        const payout = won
          ? computeParimutuelPayout(stake, winningPool, total)
          : 0;

        rows.push({
          userId: pos.userId,
          displayName: formatPublicAccountLabel(
            pos.user.name,
            pos.user.email,
            pos.user.platformHandle,
            pos.user.twitterHandle,
          ),
          won,
          stakeUsd: stake,
          impliedWinProbability: implied,
          payoutUsd: payout,
        });
      }
    }

    return buildOracleLeaderboard(rows).slice(0, limit);
  }
}
