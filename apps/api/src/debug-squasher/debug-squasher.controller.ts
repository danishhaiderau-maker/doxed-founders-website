import {
  BadRequestException,
  Body,
  Controller,
  Get,
  HttpCode,
  Logger,
  Post,
  Query,
  UseGuards,
} from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AdminGuard } from '../auth/guards';
import { CurrentUser } from '../auth/current-user.decorator';
import type { AuthUser } from '../auth/auth.types';
import {
  DebugSquasherService,
  type DebugSquasherRunSummary,
} from './debug-squasher.service';

/**
 * Debug Squasher HTTP surface.
 *
 * - GET  /api/debug-squasher/latest   — last run summary (any logged-in user).
 * - GET  /api/debug-squasher/history  — recent runs (admin only).
 * - POST /api/debug-squasher/run      — manually trigger a run (admin only).
 * - GET  /api/debug-squasher/consent  — read current user's opt-in state.
 * - POST /api/debug-squasher/consent  — record opt-in decision.
 *
 * The cron job (debug-squasher.cron.ts) is the primary driver; these routes
 * exist so the dashboard and the IDE extension can render results and so the
 * admin can force a fresh run on demand.
 */
@SkipThrottle()
@Controller('debug-squasher')
export class DebugSquasherController {
  private readonly logger = new Logger(DebugSquasherController.name);

  constructor(private readonly squasher: DebugSquasherService) {}

  /** Latest run summary — the IDE status bar + dashboard card poll this. */
  @Get('latest')
  async latest(): Promise<{ run: DebugSquasherRunSummary | null }> {
    const run = await this.squasher.getLatest();
    return { run };
  }

  /** Run history for the admin panel. */
  @Get('history')
  @UseGuards(AdminGuard)
  async history(
    @Query('limit') limit?: string,
  ): Promise<{ history: Awaited<ReturnType<DebugSquasherService['getHistory']>> }> {
    const n = limit ? Number(limit) : 20;
    if (!Number.isFinite(n)) throw new BadRequestException('limit must be numeric');
    const history = await this.squasher.getHistory(n);
    return { history };
  }

  /** Manually trigger a run. Admin-only — this can take 30-90s. */
  @Post('run')
  @UseGuards(AdminGuard)
  @HttpCode(200)
  async run(): Promise<{ run: DebugSquasherRunSummary }> {
    this.logger.log('Manual debug-squasher run triggered via HTTP');
    const run = await this.squasher.run('manual');
    return { run };
  }

  /** Read the calling user's consent state (used by the pop-up to decide whether to show). */
  @Get('consent')
  async getConsent(@CurrentUser() user: AuthUser): Promise<{
    consent: string;
    consentAt: string | null;
  }> {
    return this.squasher.getConsent(user.id);
  }

  /**
   * Record the user's opt-in decision. Body.choice is one of
   * 'accepted' | 'declined' | 'later'. 'later' means "ask me again tomorrow"
   * — the pop-up will resurface; the cron will NOT run for 'later' users.
   */
  @Post('consent')
  @HttpCode(200)
  async setConsent(
    @CurrentUser() user: AuthUser,
    @Body() body: { choice?: string },
  ): Promise<{ ok: true; consent: string }> {
    const choice = body?.choice;
    if (choice !== 'accepted' && choice !== 'declined' && choice !== 'later') {
      throw new BadRequestException(
        'body.choice must be one of: accepted, declined, later',
      );
    }
    return this.squasher.setConsent(user.id, choice);
  }
}
