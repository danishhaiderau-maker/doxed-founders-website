/**
 * DDollar Engine — grants DDollar on Economic activity events.
 *
 * Integrates with the existing DDollar ledger by:
 *   - bumping `User.reputationPoints` (the canonical DDollar balance), and
 *   - writing a `FounderTreasuryLedgerEntry` row for audit, and
 *   - writing a `DDollarGrant` row indexed by (activityType, activityId, userId)
 *     so the settlement job can export a clean snapshot.
 *
 * Hard rule (see `ddollar-scoring.ts`): speculative actions NEVER earn DDollar.
 * The engine refuses any actionKey that matches the speculative exclusion list.
 */

import { Injectable, Logger, BadRequestException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import {
  DDOLLAR_ACTIVITY_SPECS,
  clampDdollarAmount,
  isSpeculativeAction,
  type DDollarActivityType,
} from '@dcf/utils';

@Injectable()
export class DdollarEngineService {
  private readonly logger = new Logger(DdollarEngineService.name);

  constructor(private readonly prisma: PrismaService) {}

  /**
   * Grant DDollar for an Economic activity.
   *
   * Idempotent — if a grant already exists for (activityType, activityId, userId)
   * and was not reverted, this is a no-op (returns the existing grant).
   */
  async grant(
    userId: string,
    activityType: DDollarActivityType,
    activityId: string,
    requestedAmount: number,
    options?: { proofType?: string; proofData?: unknown; label?: string },
  ): Promise<{ id: string; amount: number; isNew: boolean }> {
    if (isSpeculativeAction(activityType)) {
      throw new BadRequestException(
        `Speculative action ${activityType} never earns DDollar (Economic vs Speculative separation).`,
      );
    }
    const spec = DDOLLAR_ACTIVITY_SPECS[activityType];
    if (!spec) {
      throw new BadRequestException(`Unknown DDollar activity type: ${activityType}`);
    }
    if (spec.requiresProof && !options?.proofType) {
      throw new BadRequestException(
        `${activityType} requires a proofType (Proof of Success).`,
      );
    }
    const amount = clampDdollarAmount(spec, requestedAmount);
    if (amount <= 0) {
      return { id: '', amount: 0, isNew: false };
    }

    const existing = await this.prisma.dDollarGrant.findUnique({
      where: {
        activityType_activityId_userId: { activityType, activityId, userId },
      },
    });
    if (existing && !existing.reverted) {
      return { id: existing.id, amount: existing.amount, isNew: false };
    }

    const grant = await this.prisma.dDollarGrant.upsert({
      where: {
        activityType_activityId_userId: { activityType, activityId, userId },
      },
      create: {
        userId,
        activityType,
        activityId,
        amount,
        proofType: options?.proofType,
        proofData: options?.proofData ? (options.proofData as object) : undefined,
      },
      update: {
        amount,
        proofType: options?.proofType,
        proofData: options?.proofData ? (options.proofData as object) : undefined,
        reverted: false,
      },
    });

    // Bump canonical DDollar ledger (User.reputationPoints) + audit row.
    await this.prisma.$transaction([
      this.prisma.user.update({
        where: { id: userId },
        data: { reputationPoints: { increment: amount } },
      }),
      this.prisma.founderTreasuryLedgerEntry.create({
        data: {
          userId,
          amountDdollar: amount,
          actionKey: `FOUNDER_ECONOMICS:${activityType}`,
          label: options?.label ?? spec.label,
          metadata: { activityId, activityType, proofType: options?.proofType },
        },
      }),
    ]);

    this.logger.log(
      `Granted ${amount} DDollar to ${userId} for ${activityType}:${activityId}`,
    );
    return { id: grant.id, amount, isNew: true };
  }

  /**
   * Apply a negative DDollar penalty. See `negative-ddollar.ts` for spec.
   * Requires a reason that matches a NegativeDDollarSpec.
   */
  async penalize(
    userId: string,
    reason: string,
    requestedAmount: number,
    options?: { governanceVoteId?: string; label?: string },
  ): Promise<{ id: string; amount: number }> {
    const amount = -Math.abs(Math.round(requestedAmount));
    if (amount === 0) {
      return { id: '', amount: 0 };
    }
    const activityId = options?.governanceVoteId ?? `penalty-${reason}-${Date.now()}`;
    const grant = await this.prisma.dDollarGrant.create({
      data: {
        userId,
        activityType: `PENALTY:${reason}`,
        activityId,
        amount,
        proofData: options?.governanceVoteId
          ? { governanceVoteId: options.governanceVoteId }
          : undefined,
      },
    });

    await this.prisma.$transaction([
      this.prisma.user.update({
        where: { id: userId },
        data: { reputationPoints: { increment: amount } },
      }),
      this.prisma.founderTreasuryLedgerEntry.create({
        data: {
          userId,
          amountDdollar: amount,
          actionKey: `FOUNDER_ECONOMICS_PENALTY:${reason}`,
          label: options?.label ?? `Penalty: ${reason}`,
          metadata: { reason, governanceVoteId: options?.governanceVoteId },
        },
      }),
    ]);

    this.logger.warn(
      `Applied ${amount} DDollar penalty to ${userId} for ${reason}`,
    );
    return { id: grant.id, amount };
  }

  /**
   * Export a DDollar snapshot for the settlement job. Returns every founder
   * with a positive reputationPoints balance plus their reputation multiplier
   * inputs (so the active DistributionModel can recompute on the fly).
   */
  async exportSnapshot(epochNumber: number) {
    const users = await this.prisma.user.findMany({
      where: { reputationPoints: { gt: 0 }, banned: false },
      select: {
        id: true,
        reputationPoints: true,
        contributorLevel: true,
        builderScore: true,
        createdAt: true,
        emailVerified: true,
        walletConnections: { select: { address: true, chain: true }, take: 1 },
      },
    });
    const verifiedMilestoneCounts = await this.prisma.proofOfSuccess.groupBy({
      by: ['userId'],
      _count: true,
      where: { userId: { in: users.map((u) => u.id) } },
    });
    const milestoneCount = new Map(
      verifiedMilestoneCounts.map((r) => [r.userId, r._count]),
    );
    const now = Date.now();
    return {
      epochNumber,
      snapshotAt: new Date().toISOString(),
      founders: users.map((u) => {
        const walletAddress = u.walletConnections[0]?.address ?? '';
        const accountAgeDays = Math.max(
          0,
          Math.floor((now - u.createdAt.getTime()) / 86_400_000),
        );
        return {
          userId: u.id,
          walletAddress,
          rawDdollar: u.reputationPoints,
          reputationMultiplierInputs: {
            verifiedAccount: !!u.emailVerified,
            contributorLevel: u.contributorLevel,
            reputationPoints: u.reputationPoints,
            accountAgeDays,
            verifiedMilestoneCount: milestoneCount.get(u.id) ?? 0,
            builderScore: u.builderScore,
          },
        };
      }),
    };
  }
}
