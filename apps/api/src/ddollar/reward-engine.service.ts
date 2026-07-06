import { Injectable, Logger } from '@nestjs/common';
import { contributorLevelFromPoints, pointActionLabel } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { AntiAbuseService } from './anti-abuse.service';

@Injectable()
export class RewardEngine {
  private readonly logger = new Logger(RewardEngine.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly antiAbuse: AntiAbuseService,
  ) {}

  async award(userId: string, amount: number, actionKey?: string): Promise<void> {
    if (amount <= 0) return;

    await this.antiAbuse.assertAwardAllowed(userId, amount, actionKey);

    const user = await this.prisma.user.update({
      where: { id: userId },
      data: {
        reputationPoints: { increment: amount },
        lifetimeContributionEarned: { increment: amount },
      },
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
}
