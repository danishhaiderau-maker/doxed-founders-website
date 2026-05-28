import { Injectable } from '@nestjs/common';
import { contributorLevelFromPoints } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class PointsService {
  constructor(private readonly prisma: PrismaService) {}

  async award(userId: string, amount: number) {
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
  }
}
