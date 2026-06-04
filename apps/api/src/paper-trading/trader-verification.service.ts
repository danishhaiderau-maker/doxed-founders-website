import { Injectable } from '@nestjs/common';
import { PaperTradeSide, Prisma } from '@prisma/client';
import {
  STARTING_CASH_USD,
  computeTraderVerifiedStats,
  defaultStopLossUsd,
  verifyClosedTrade,
  type ClosedVerifiedTrade,
  type TraderVerifiedStats,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class TraderVerificationService {
  constructor(private readonly prisma: PrismaService) {}

  async buildVerifiedStats(
    userId: string,
    currentRoiPct: number,
  ): Promise<TraderVerifiedStats> {
    const sells = await this.prisma.paperTrade.findMany({
      where: {
        userId,
        side: PaperTradeSide.SELL,
        realizedPnlUsd: { not: null },
      },
      orderBy: { createdAt: 'asc' },
      include: {
        project: { select: { ticker: true } },
      },
    });

    const thirtyDaysAgo = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
    const recentSells = sells.filter((s) => s.createdAt >= thirtyDaysAgo);

    const closedTrades: ClosedVerifiedTrade[] = sells.map((s) => {
      const exit = Number(s.priceUsd);
      const realized = Number(s.realizedPnlUsd ?? 0);
      const invested =
        realized !== 0 && exit > 0
          ? Math.max(
              Number(s.totalUsd) - realized,
              Number(s.totalUsd) * 0.5,
            )
          : Number(s.totalUsd);
      const entry =
        s.entryPriceSnapshot != null
          ? Number(s.entryPriceSnapshot)
          : invested > 0 && Number(s.quantity) > 0
            ? invested / Number(s.quantity)
            : exit;

      return {
        id: s.id,
        closedAt: s.createdAt.toISOString(),
        entryPriceUsd: entry,
        exitPriceUsd: exit,
        investedUsd: invested,
        realizedPnlUsd: realized,
        takeProfitUsd:
          s.takeProfitSnapshot != null ? Number(s.takeProfitSnapshot) : null,
        stopLossUsd:
          s.stopLossSnapshot != null ? Number(s.stopLossSnapshot) : null,
        peakPriceUsd: s.peakPriceUsd != null ? Number(s.peakPriceUsd) : null,
      };
    });

    let roi30dPct: number | null = null;
    if (recentSells.length > 0) {
      const net30 = recentSells.reduce(
        (sum, s) => sum + Number(s.realizedPnlUsd ?? 0),
        0,
      );
      roi30dPct =
        Math.round((net30 / STARTING_CASH_USD) * 1000) / 10;
    }

    return computeTraderVerifiedStats({
      closedTrades,
      startingCashUsd: STARTING_CASH_USD,
      currentRoiPct,
      roi30dPct,
    });
  }

  /** Persist verification snapshot on SELL (platform-signed exit record). */
  snapshotSellVerification(input: {
    entryPriceUsd: number;
    exitPriceUsd: number;
    investedUsd: number;
    realizedPnlUsd: number;
    takeProfitUsd?: number | null;
    stopLossUsd?: number | null;
    peakPriceUsd?: number | null;
  }) {
    const closed: ClosedVerifiedTrade = {
      id: 'pending',
      closedAt: new Date().toISOString(),
      entryPriceUsd: input.entryPriceUsd,
      exitPriceUsd: input.exitPriceUsd,
      investedUsd: input.investedUsd,
      realizedPnlUsd: input.realizedPnlUsd,
      takeProfitUsd: input.takeProfitUsd,
      stopLossUsd: input.stopLossUsd,
      peakPriceUsd: input.peakPriceUsd,
    };

    const v = verifyClosedTrade(closed);

    return {
      verifiedOutcome: v.outcome,
      entryPriceSnapshot: new Prisma.Decimal(input.entryPriceUsd),
      takeProfitSnapshot:
        input.takeProfitUsd != null
          ? new Prisma.Decimal(input.takeProfitUsd)
          : null,
      stopLossSnapshot:
        input.stopLossUsd != null
          ? new Prisma.Decimal(input.stopLossUsd)
          : null,
      achievedRR:
        v.achievedRR != null ? new Prisma.Decimal(v.achievedRR) : null,
    };
  }

  resolveStopForEntry(entryPriceUsd: number, explicitStop?: number | null): number {
    if (explicitStop != null && explicitStop > 0) return explicitStop;
    return defaultStopLossUsd(entryPriceUsd);
  }
}
