import { Controller, Get, UseGuards } from '@nestjs/common';
import { AdminGuard } from '../auth/guards';
import { RetryDetectorService } from './retry-detector.service';
import { LearningEngineService } from './learning-engine.service';

/**
 * Read-only admin view over the Learning Engine. Powers the Observatory's
 * "is the loop actually running?" panel — and gives on-call engineers a
 * single URL to hit when investigating whether reputation updates are
 * flowing.
 *
 * Auth: AdminGuard. No founder-scoped variant yet — the per-founder
 * personalization surface arrives in Phase 4.5.
 */
@Controller('learning-engine')
@UseGuards(AdminGuard)
export class LearningEngineController {
  constructor(
    private readonly learningEngine: LearningEngineService,
    private readonly retryDetector: RetryDetectorService,
  ) {}

  /**
   * GET /api/learning-engine/status
   * Returns the last rollup's watermark + counts plus the current in-memory
   * retry-window size. Useful for the Observatory and for sanity-checking
   * that the scheduler is firing.
   */
  @Get('status')
  status(): {
    lastRollupAt: string | null;
    lastProcessedCount: number;
    lastUpdatedCount: number;
    trackedPromptHashes: number;
  } {
    return {
      ...this.learningEngine.getStatus(),
      trackedPromptHashes: this.retryDetector.trackedPromptHashes,
    };
  }
}
