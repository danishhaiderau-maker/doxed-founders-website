import { Controller, Get } from '@nestjs/common';
import { Public } from '../auth/public.decorator';
import { PrismaService } from '../prisma/prisma.service';

@Public()
@Controller('health')
export class HealthController {
  constructor(private readonly prisma: PrismaService) {}

  @Get()
  async check() {
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

    const payload = {
      status: dbStatus === 'ok' ? 'ok' : 'degraded',
      timestamp: new Date().toISOString(),
      services: {
        api: 'ok',
        database: dbStatus,
      },
      pendingListings: pendingListings >= 0 ? pendingListings : undefined,
    };

    return payload;
  }
}
