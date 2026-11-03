import { Controller, Get } from '@nestjs/common';
import { Public } from '../auth/public.decorator';
import { PrismaService } from '../prisma/prisma.service';

/**
 * Liveness vs DB probe split (RK 21:05 / HM 40c8fc59831d):
 * - GET /api/health      — process up only (no Neon). ChainSupervisor polls this.
 * - GET /api/health/db   — optional explicit DB check (wakes Neon).
 * - GET /api/health/live — Railway liveness (unchanged, no DB).
 */
@Public()
@Controller('health')
export class HealthController {
  constructor(private readonly prisma: PrismaService) {}

  /** Process liveness — never touches Neon. */
  @Get()
  check() {
    return {
      status: 'ok',
      timestamp: new Date().toISOString(),
      services: {
        api: 'ok',
        database: 'skipped',
      },
    };
  }

  /** Explicit DB probe — operators / rare checks only; wakes Neon. */
  @Get('db')
  async checkDb() {
    let dbStatus = 'error';

    if (await this.prisma.connect()) {
      try {
        await this.prisma.$queryRaw`SELECT 1`;
        dbStatus = 'ok';
      } catch {
        dbStatus = 'error';
      }
    }

    const pendingListings = await this.prisma.listingApplication.count({
      where: { status: 'PENDING' },
    }).catch(() => -1);

    return {
      status: dbStatus === 'ok' ? 'ok' : 'degraded',
      timestamp: new Date().toISOString(),
      services: {
        api: 'ok',
        database: dbStatus,
      },
      pendingListings: pendingListings >= 0 ? pendingListings : undefined,
    };
  }

  /** Railway liveness — no DB; passes as soon as Nest is listening. */
  @Get('live')
  live() {
    return { status: 'ok', timestamp: new Date().toISOString() };
  }
}
