import { Controller, Get, Query, UseGuards } from '@nestjs/common';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { FlightRecorderService } from './flight-recorder.service';

/**
 * Read-only REST view over the Flight Recorder. Used by the Founder OS
 * shell's Decision Log viewer (/founder-os/decisions). The kernel itself
 * writes via FlightRecorderService.record / updateOutcome — never via
 * this controller.
 *
 * Auth: optional JWT. Anonymous callers get an empty list; authenticated
 * founders get their own decisions only (no admin all-users view yet —
 * that's the Observatory's job, Phase 2.5+).
 */
@Controller('flight-recorder')
@UseGuards(OptionalJwtAuthGuard)
export class FlightRecorderController {
  constructor(private readonly flightRecorder: FlightRecorderService) {}

  /**
   * GET /api/flight-recorder/recent?limit=50
   * Returns the founder's most recent routing decisions (newest first).
   */
  @Get('recent')
  async recent(
    @CurrentUser() user: AuthUser | null,
    @Query('limit') limit?: string,
  ) {
    if (!user?.id) return [];
    const parsedLimit = Math.max(1, Math.min(200, Number(limit ?? '50') || 50));
    return this.flightRecorder.findRecent({
      userId: user.id,
      limit: parsedLimit,
    });
  }
}
