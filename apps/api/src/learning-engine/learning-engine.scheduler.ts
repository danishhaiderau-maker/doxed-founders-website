import { Injectable, Logger } from '@nestjs/common';
import { Cron } from '@nestjs/schedule';
import { LearningEngineService } from './learning-engine.service';

/**
 * Scheduler that triggers the Learning Engine rollup every 6 hours.
 *
 * Why 6h (not every minute):
 *   - The EMA alpha is 0.05, so updates accumulate gradually — running more
 *     often would just nudge the same value by epsilon.
 *   - Production safety: a slow scheduler cannot degrade request serving
 *     even if the rollup query plan regresses.
 *   - The retry detector (the high-frequency signal) runs in real time and
 *     writes `retried: true` flags immediately; the rollup just consumes
 *     those flags in batches.
 */
@Injectable()
export class LearningEngineScheduler {
  private readonly logger = new Logger(LearningEngineScheduler.name);

  constructor(private readonly learningEngine: LearningEngineService) {}

  /** Every 6 hours at minute 0 (00:00, 06:00, 12:00, 18:00 UTC). */
  @Cron('0 */6 * * *')
  async runRollup(): Promise<void> {
    this.logger.log('scheduled rollup starting');
    try {
      const result = await this.learningEngine.rollup();
      this.logger.log(
        `scheduled rollup complete: processed=${result.processed} updated=${result.updated}`,
      );
    } catch (err) {
      // Best-effort: a failed rollup must NOT crash the scheduler thread.
      // The next tick will retry.
      this.logger.error(
        `scheduled rollup failed: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  }
}
