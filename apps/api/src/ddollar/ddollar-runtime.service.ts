import { Injectable, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { RewardEngine } from './reward-engine.service';
import { SpendingEngine } from './spending-engine.service';
import type { DdollarTreasuryAudit, DdollarWalletSnapshot } from './ddollar.constants';
import { DDOLLAR_ACTION_KEYS } from './ddollar.constants';

@Injectable()
export class DdollarRuntimeService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly rewardEngine: RewardEngine,
    private readonly spendingEngine: SpendingEngine,
  ) {}

  async award(userId: string, amount: number, actionKey?: string): Promise<void> {
    return this.rewardEngine.award(userId, amount, actionKey);
  }

  async spend(
    userId: string,
    amount: number,
    actionKey?: string,
    aiSpend = false,
  ): Promise<void> {
    return this.spendingEngine.spend(userId, amount, actionKey, { aiSpend });
  }

  async purchaseMarketplace(
    userId: string,
    amount: number,
    listingKey: string,
    label?: string,
  ): Promise<void> {
    return this.spendingEngine.spend(userId, amount, DDOLLAR_ACTION_KEYS.MARKETPLACE_PURCHASE, {
      marketplaceListingKey: listingKey,
      marketplaceLabel: label,
    });
  }

  async getWallet(userId: string): Promise<DdollarWalletSnapshot> {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: {
        id: true,
        reputationPoints: true,
        lifetimeContributionEarned: true,
        contributorLevel: true,
      },
    });
    if (!user) throw new NotFoundException('User not found');
    return {
      userId: user.id,
      spendableBalance: user.reputationPoints,
      lifetimeContributionEarned: user.lifetimeContributionEarned,
      contributorLevel: user.contributorLevel,
    };
  }

  async getTreasuryAudit(limit = 20): Promise<DdollarTreasuryAudit> {
    const [agg, recent] = await Promise.all([
      this.prisma.founderTreasuryLedgerEntry.aggregate({ _sum: { amountDdollar: true }, _count: true }),
      this.prisma.founderTreasuryLedgerEntry.findMany({
        orderBy: { createdAt: 'desc' },
        take: limit,
        select: {
          id: true,
          amountDdollar: true,
          actionKey: true,
          label: true,
          createdAt: true,
        },
      }),
    ]);

    return {
      totalInflowDdollar: agg._sum.amountDdollar ?? 0,
      entryCount: agg._count,
      recentEntries: recent.map((row) => ({
        id: row.id,
        amountDdollar: row.amountDdollar,
        actionKey: row.actionKey,
        label: row.label,
        createdAt: row.createdAt.toISOString(),
      })),
    };
  }

  async getDailyEmissions(limit = 7) {
    const rows = await this.prisma.ddollarDailyEmission.findMany({
      orderBy: { emissionDate: 'desc' },
      take: limit,
    });
    return rows.map((row) => ({
      id: row.id,
      emissionDate: row.emissionDate.toISOString().slice(0, 10),
      amountIssued: row.amountIssued,
      usersAwarded: row.usersAwarded,
      note: row.note,
    }));
  }

  /** Stub for future cron — records zero row if missing for today. */
  async ensureDailyEmissionStub() {
    const today = new Date();
    today.setUTCHours(0, 0, 0, 0);
    return this.prisma.ddollarDailyEmission.upsert({
      where: { emissionDate: today },
      create: {
        emissionDate: today,
        amountIssued: 0,
        usersAwarded: 0,
        note: 'Stub — wire DDollar daily emission worker (Slice 3)',
      },
      update: {},
    });
  }
}
