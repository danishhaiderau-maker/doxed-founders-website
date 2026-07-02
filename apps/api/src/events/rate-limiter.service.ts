import { Injectable, Logger, ServiceUnavailableException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

/**
 * Env override (emergency debug only). When `RATE_LIMIT_FAIL_OPEN=true` the
 * limiter reverts to the legacy fail-open behavior so a DB outage does NOT
 * block AI traffic. Default (`false`) = fail CLOSED → 503 on DB-down, which
 * prevents a Neon outage from opening the floodgates on the platform AI keys.
 */
const FAIL_OPEN = (process.env.RATE_LIMIT_FAIL_OPEN ?? 'false').toLowerCase() === 'true';

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
export class RateLimiterService {
  private readonly logger = new Logger(RateLimiterService.name);

  constructor(private prisma: PrismaService) {}

  async checkLimit(
    userId: string,
    endpoint: string,
  ): Promise<{ allowed: boolean; remaining: number; resetInMs: number; reason?: string }> {
    try {
      return await this.checkLimitInner(userId, endpoint);
    } catch (err) {
      if (isDbDownError(err)) {
        if (FAIL_OPEN) {
          this.logger.warn(
            `Rate limiter DB-down — FAIL_OPEN=true, allowing request for ${endpoint} user=${userId}`,
          );
          return { allowed: true, remaining: 0, resetInMs: 0 };
        }
        this.logger.error(
          `Rate limiter DB-down — fail CLOSED (RATE_LIMIT_FAIL_OPEN=false). Rejecting ${endpoint} user=${userId}: ${err instanceof Error ? err.message : String(err)}`,
        );
        throw new ServiceUnavailableException(
          'Rate limiter unavailable — please try again',
        );
      }
      throw err;
    }
  }

  private async checkLimitInner(
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
