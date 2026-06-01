import { BadRequestException, Injectable } from '@nestjs/common';
import { contributorLevelFromPoints, pointActionLabel } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class PointsService {
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

  /** Spend DDollar (reputation points) — throws if balance too low. */
  async spend(userId: string, amount: number, actionKey?: string) {
    if (amount <= 0) return;

    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { reputationPoints: true },
    });
    if (!user || user.reputationPoints < amount) {
      throw new BadRequestException(`Need ${amount.toLocaleString()} DDollar — earn more by scouting, trading, and validating listings.`);
    }

    await this.prisma.user.update({
      where: { id: userId },
      data: { reputationPoints: { decrement: amount } },
    });

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
