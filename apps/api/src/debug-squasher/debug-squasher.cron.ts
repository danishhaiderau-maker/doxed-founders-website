import { Injectable, Logger } from '@nestjs/common';
import { Cron, CronExpression } from '@nestjs/schedule';
import { DebugSquasherService } from './debug-squasher.service';

/**
 * Daily Debug Squasher cron.
 *
 * Runs at 06:00 UTC every day. The job is registered unconditionally (NestJS
 * needs the decorator present at startup), but it short-circuits unless:
 *   1. DEBUG_SQUASHER_ENABLED is not 'false' (default ON in dev, opt-in in
 *      prod via the consent flow), AND
 *   2. At least one admin has opted in via POST /api/debug-squasher/consent
 *      with choice='accepted'.
 *
 * In dev (NODE_ENV != 'production'), the consent gate is skipped so the
 * founder sees daily reports as soon as they boot the stack locally.
 */
@Injectable()
export class DebugSquasherCron {
  private readonly logger = new Logger(DebugSquasherCron.name);
  /** Guard so two cron ticks can't overlap if a run takes >24h (impossible). */
  private running = false;

  constructor(private readonly squasher: DebugSquasherService) {}

  /**
   * 06:00 UTC daily. timeZone defaults to 'UTC' via the Cron decorator when
   * the project's ScheduleModule is configured for UTC (Nest default).
   */
  @Cron('0 6 * * *', {
    name: 'debug-squasher-daily',
    timeZone: 'UTC',
  })
  async handleDaily(): Promise<void> {
    if (this.running) {
      this.logger.warn('Previous debug-squasher run still in progress — skipping tick.');
      return;
    }

    if (!this.squasher.isFeatureEnabled()) {
      this.logger.debug('Skipped — DEBUG_SQUASHER_ENABLED is false.');
      return;
    }

    // Prod requires opt-in; dev runs regardless so the founder sees value fast.
    const isProd = process.env.NODE_ENV === 'production';
    if (isProd) {
      const optedIn = await this.squasher.hasAdminOptedIn();
      if (!optedIn) {
        this.logger.log('Skipped — no admin has opted in to daily debug-squasher runs.');
        return;
      }
    }

    this.running = true;
    const startedAt = Date.now();
    try {
      const summary = await this.squasher.run('cron');
      this.logger.log(
        `Daily cron run complete — overall=${summary.overall} in ${Date.now() - startedAt}ms`,
      );
    } catch (err) {
      this.logger.error(
        `Daily debug-squasher run crashed: ${err instanceof Error ? err.message : err}`,
      );
    } finally {
      this.running = false;
    }
  }
}

// Re-export CronExpression so consumers can import from one place if needed.
void CronExpression;
