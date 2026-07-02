import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class RateLimiterService {
  constructor(private prisma: PrismaService) {}

  async checkLimit(
    userId: string,
    endpoint: string,
  ): Promise<{ allowed: boolean; remaining: number; resetInMs: number; reason?: string }> {
    const settings = await this.prisma.platformSettings.findFirst();
    const dailyLimit = settings?.rateLimitDaily ?? 50;
    const hourlyLimit = settings?.rateLimitHourly ?? 10;

    const now = new Date();
    const hourStart = new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate(),
      now.getHours(),
      0,
      0,
      0,
    );
    const dayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);

    const [hourlyCount, dailyCount] = await Promise.all([
      this.prisma.rateLimit.aggregate({
        where: { userId, endpoint, windowStart: { gte: hourStart } },
        _sum: { count: true },
      }),
      this.prisma.rateLimit.aggregate({
        where: { userId, endpoint, windowStart: { gte: dayStart } },
        _sum: { count: true },
      }),
    ]);

    const hourlyUsed = hourlyCount._sum.count ?? 0;
    const dailyUsed = dailyCount._sum.count ?? 0;

    if (hourlyUsed >= hourlyLimit) {
      const nextHour = new Date(hourStart.getTime() + 3600000);
      return {
        allowed: false,
        remaining: 0,
        resetInMs: nextHour.getTime() - now.getTime(),
        reason: 'Hourly limit exceeded',
      };
    }
    if (dailyUsed >= dailyLimit) {
      const nextDay = new Date(dayStart.getTime() + 86400000);
      return {
        allowed: false,
        remaining: 0,
        resetInMs: nextDay.getTime() - now.getTime(),
        reason: 'Daily limit exceeded',
      };
    }

    // Increment counter
    await this.prisma.rateLimit.upsert({
      where: { userId_endpoint_windowStart: { userId, endpoint, windowStart: hourStart } },
      create: { userId, endpoint, windowStart: hourStart, count: 1 },
      update: { count: { increment: 1 } },
    });

    return {
      allowed: true,
      remaining: Math.min(hourlyLimit - hourlyUsed - 1, dailyLimit - dailyUsed - 1),
      resetInMs: 0,
    };
  }
}
