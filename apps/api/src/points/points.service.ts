import {
  BadRequestException,
  Injectable,
  Logger,
  ServiceUnavailableException,
} from '@nestjs/common';
import { contributorLevelFromPoints, pointActionLabel } from '@dcf/utils';
import { UserRole } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

const DB_DOWN_RE = /connection|timeout|unreachable|refused|terminated/i;

function isDbDownError(err: unknown): boolean {
  if (err == null) return false;
  const code = (err as { code?: string }).code;
  if (code === 'P1001' || code === 'P1008' || code === 'P1017') return true;
  const name = (err as { name?: string }).name;
  if (name === 'PrismaClientInitializationError') return true;
  const msg = err instanceof Error ? err.message : String(err);
  return DB_DOWN_RE.test(msg);
}

@Injectable()
export class PointsService {
  private readonly logger = new Logger(PointsService.name);

  constructor(private readonly prisma: PrismaService) {}

  async award(userId: string, amount: number, actionKey?: string) {
    if (amount <= 0) return;

    const user = await this.prisma.user.update({
      where: { id: userId },
      data: { reputationPoints: { increment: amount } },
      select: { reputationPoints: true },
    });

    const level = contributorLevelFromPoints(user.reputationPoints);
    await this.prisma.user.update({
      where: { id: userId },
      data: { contributorLevel: level },
    });

    if (actionKey) {
      await this.prisma.pointLedger.create({
        data: {
          userId,
          amount,
          actionKey: actionKey.split(':')[0] ?? actionKey,
          label: pointActionLabel(actionKey),
        },
      });
    }
  }

  /**
   * Spend DDollar (reputation points) — throws if balance too low.
   *
   * Atomic: uses a single conditional `updateMany` with a `WHERE balance >=
   * amount` clause so parallel requests can't all pass the check before any
   * decrement lands (the previous read-check-then-update race that let users
   * drive balances negative).
   *
   * Fail CLOSED on DB-down: a Neon outage throws (503) instead of silently
   * letting the AI call proceed against an unverifiable balance.
   */
  async spend(userId: string, amount: number, actionKey?: string) {
    if (amount <= 0) return;

    let result;
    try {
      result = await this.prisma.user.updateMany({
        where: { id: userId, reputationPoints: { gte: amount } },
        data: { reputationPoints: { decrement: amount } },
      });
    } catch (err) {
      if (isDbDownError(err)) {
        this.logger.error(
          `PointsService.spend DB-down — fail CLOSED, rejecting spend user=${userId}: ${err instanceof Error ? err.message : String(err)}`,
        );
        throw new ServiceUnavailableException(
          'Balance check unavailable — please try again',
        );
      }
      throw err;
    }

    if (result.count === 0) {
      // Either the user doesn't exist or the balance was insufficient at the
      // atomic check moment — treat both as insufficient (matches the previous
      // user-facing error so the frontend keeps rendering the earn-more hint).
      throw new BadRequestException(
        `Need ${amount.toLocaleString()} DDollar — earn more by scouting, trading, and validating listings.`,
      );
    }

    // Re-read for the contributor-level recompute + ledger. Best-effort: if the
    // DB goes down between the atomic decrement and this read, the spend has
    // already been committed atomically and the AI call may proceed — the
    // level recompute is cosmetic, not a security gate.
    const updated = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { reputationPoints: true },
    });
    if (updated) {
      const level = contributorLevelFromPoints(updated.reputationPoints);
      await this.prisma.user.update({
        where: { id: userId },
        data: { contributorLevel: level },
      });
    }

    if (actionKey) {
      await this.prisma.pointLedger.create({
        data: {
          userId,
          amount: -amount,
          actionKey: actionKey.split(':')[0] ?? actionKey,
          label: pointActionLabel(actionKey),
        },
      });
    }
  }

  /** Credit platform admin when a user pays a hire/rental fee. */
  async creditAdminFee(amount: number, sourceKey: string) {
    if (amount <= 0) return null;

    const admin = await this.prisma.user.findFirst({
      where: { role: UserRole.ADMIN },
      orderBy: { createdAt: 'asc' },
      select: { id: true },
    });
    if (!admin) return null;

    await this.award(admin.id, amount, `PLATFORM_FEE:${sourceKey}`);
    return admin.id;
  }

  /** Idempotent award — returns true if points were granted, false if already awarded. */
  async awardOnce(userId: string, actionKey: string, amount: number): Promise<boolean> {
    if (amount <= 0) return false;

    try {
      await this.prisma.reputationAward.create({
        data: { userId, actionKey, amount },
      });
    } catch (err: unknown) {
      const code = (err as { code?: string })?.code;
      if (code === 'P2002') return false;
      throw err;
    }

    await this.award(userId, amount, actionKey);
    return true;
  }
}
