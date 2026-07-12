import { Injectable, Logger } from '@nestjs/common';
import { Cron, CronExpression } from '@nestjs/schedule';
import { ConfigService } from '@nestjs/config';
import { IdeaValidatorService } from './idea-validator.service';
import { ideaValidatorEnabled } from './idea-validator.module';

/**
 * Daily proactive pop-up cron (Part C).
 *
 * Once per day, finds users with unviewed COMPLETED IdeaChecks. Those
 * users' next load of the Founder OS shell will see the daily pop-up
 * (the frontend polls GET /idea-validator/latest-for-user).
 *
 * Discipline:
 *   - One check surfaced per user per 24h (the `viewed`/`dismissed` flags
 *     gate it — a check only surfaces once).
 *   - Only fires when IDEA_VALIDATOR_ENABLED is on (kill switch).
 *   - Never re-runs research; this is purely a surfacing signal. The
 *     research already happened async at check-creation time.
 *
 * The cron runs at 09:00 server-local. The endpoint
 * POST /idea-validator/cron/daily-pop-up lets admins trigger it manually.
 */
@Injectable()
export class IdeaValidatorDailyCron {
  private readonly logger = new Logger(IdeaValidatorDailyCron.name);

  constructor(
    private readonly ideaValidator: IdeaValidatorService,
    private readonly config: ConfigService,
  ) {}

  /**
   * 09:00 daily. Low frequency by design — the pop-up is a gentle nudge,
   * not a notification storm.
   */
  @Cron('0 9 * * *')
  async dailyPopUpSweep(): Promise<void> {
    if (!ideaValidatorEnabled(this.config)) {
      return;
    }
    try {
      const userIds = await this.ideaValidator.usersWithUnviewedCompletedChecks();
      if (userIds.length === 0) return;
      this.logger.log(
        `daily pop-up sweep: ${userIds.length} user(s) have unviewed completed idea checks`,
      );
      // No action needed here — the frontend polls /latest-for-user on
      // load and renders the pop-up when there's an unviewed row. This
      // cron exists as the explicit "once per 24h" cadence gate and as
      // a log line for observability.
    } catch (err) {
      this.logger.warn(
        `daily pop-up sweep failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  /**
   * Periodic health check that the cron is registered. Every hour, no-op.
   * Exists so @nestjs/schedule doesn't prune the cron if it's the only
   * one and so we get a heartbeat log.
   */
  @Cron(CronExpression.EVERY_HOUR)
  async heartbeat(): Promise<void> {
    this.logger.debug('idea-validator cron heartbeat');
  }

  /** Durable queue worker. Queued checks are picked up after API restarts and
   * transient provider/browser failures are retried by the service. */
  @Cron(CronExpression.EVERY_MINUTE)
  async processQueue(): Promise<void> {
    if (!ideaValidatorEnabled(this.config)) return;
    try {
      const { processed } = await this.ideaValidator.processPending();
      if (processed > 0) this.logger.log(`idea-validator worker processed ${processed} check(s)`);
    } catch (err) {
      this.logger.warn(
        `idea-validator worker failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }
}
