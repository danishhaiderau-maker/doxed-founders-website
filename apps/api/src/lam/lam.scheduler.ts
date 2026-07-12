import { Injectable, Logger } from '@nestjs/common';
import { Cron, CronExpression } from '@nestjs/schedule';
import { LamOrchestratorService } from './lam-orchestrator.service';

/** Restarts only persisted, due LAM work; confirmation-paused tasks are excluded. */
@Injectable()
export class LamScheduler {
  private readonly logger = new Logger(LamScheduler.name);

  constructor(private readonly lam: LamOrchestratorService) {}

  @Cron(CronExpression.EVERY_MINUTE)
  async processDueTasks() {
    if (process.env.LAM_ENABLED === 'false') return;
    try {
      const processed = await this.lam.processQueuedTasks();
      if (processed > 0) this.logger.log(`resumed ${processed} persisted LAM task(s)`);
    } catch (error) {
      this.logger.error(`durable LAM worker failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
}
