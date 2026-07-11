import { Injectable, Logger } from '@nestjs/common';
import { Cron, CronExpression } from '@nestjs/schedule';
import { TokenLaunchStatus } from '@prisma/client';
import { TokenLaunchService } from './token-launch.service';
import { PrismaService } from '../prisma/prisma.service';

/**
 * Daily launch-window closer. Once per day at 03:00 UTC:
 *
 *   1. Finalize launches whose 15-day commitment window expired → computes
 *      pro-rata allocations from the 5% pledge pool, moves status → LIVE.
 *   2. Refund pledges on launches that have been in PLEDGING >90 days with
 *      no progress (the anti-rug mechanic from spec §5).
 *
 * Discipline: idempotent + defensive. A failed finalize on one launch must
 * not block the rest. All errors are logged and swallowed.
 */
@Injectable()
export class TokenLaunchCron {
  private readonly logger = new Logger(TokenLaunchCron.name);
  private static readonly REFUND_INACTIVITY_DAYS = 90;

  constructor(
    private readonly tokenLaunch: TokenLaunchService,
    private readonly prisma: PrismaService,
  ) {}

  /** 03:00 UTC daily — finalize expired commitment windows. */
  @Cron('0 3 * * *', { timeZone: 'UTC' })
  async dailyFinalizeExpiredWindows(): Promise<void> {
    try {
      const closed = await this.tokenLaunch.finalizeExpiredWindows();
      if (closed > 0) {
        this.logger.log(`daily finalize: ${closed} launch(es) moved to LIVE`);
      }
    } catch (err) {
      this.logger.error(
        `daily finalize failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }

    try {
      const refunded = await this.refundStalePledgingLaunches();
      if (refunded > 0) {
        this.logger.log(
          `daily refund sweep: ${refunded} abandoned launch(es) refunded`,
        );
      }
    } catch (err) {
      this.logger.error(
        `daily refund sweep failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  /** Heartbeat so the scheduler doesn't prune this provider. */
  @Cron(CronExpression.EVERY_HOUR)
  async heartbeat(): Promise<void> {
    this.logger.debug('token-launch cron heartbeat');
  }

  /**
   * Refund pledges on launches stuck in PLEDGING longer than the inactivity
   * window (spec §5: 90 days). Returns the count of launches refunded.
   */
  private async refundStalePledgingLaunches(): Promise<number> {
    const cutoff = new Date(
      Date.now() - TokenLaunchCron.REFUND_INACTIVITY_DAYS * 86400000,
    );
    const stale = await this.prisma.tokenLaunch.findMany({
      where: {
        status: TokenLaunchStatus.PLEDGING,
        createdAt: { lt: cutoff },
      },
      select: { id: true },
    });

    let count = 0;
    for (const launch of stale) {
      // refundPledges always closes the launch when called, whether or not
      // pledges existed. Either way the launch is processed — don't loop on it.
      await this.tokenLaunch.refundPledges(
        launch.id,
        'abandoned — 90-day inactivity refund',
      );
      count += 1;
    }
    return count;
  }
}
