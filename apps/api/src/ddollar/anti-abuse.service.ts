import { BadRequestException, Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

const MAX_SINGLE_AWARD = Number.parseInt(process.env.DDOLLAR_MAX_SINGLE_AWARD ?? '50000', 10);
const MAX_DAILY_AWARD = Number.parseInt(process.env.DDOLLAR_MAX_DAILY_AWARD ?? '100000', 10);

@Injectable()
export class AntiAbuseService {
  constructor(private readonly prisma: PrismaService) {}

  async assertAwardAllowed(userId: string, amount: number, actionKey?: string): Promise<void> {
    if (amount <= 0) return;
    if (amount > MAX_SINGLE_AWARD) {
      throw new BadRequestException(`Single award exceeds cap (${MAX_SINGLE_AWARD} DDollar)`);
    }

    const since = new Date(Date.now() - 24 * 60 * 60 * 1000);
    const daily = await this.prisma.pointLedger.aggregate({
      where: {
        userId,
        amount: { gt: 0 },
        createdAt: { gte: since },
        ...(actionKey ? { actionKey: actionKey.split(':')[0] ?? actionKey } : {}),
      },
      _sum: { amount: true },
    });
    const dailyTotal = daily._sum.amount ?? 0;
    if (dailyTotal + amount > MAX_DAILY_AWARD) {
      throw new BadRequestException('Daily DDollar earn cap reached — try again tomorrow');
    }
  }
}
