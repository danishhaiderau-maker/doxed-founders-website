import { Controller, Get, Query } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AuthUser } from '../auth/auth.types';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import { ReputationService } from './reputation.service';

@SkipThrottle()
@Controller('reputation')
export class ReputationController {
  constructor(private readonly reputation: ReputationService) {}

  @Public()
  @Get('leaderboard')
  leaderboard(@Query('limit') limit?: string) {
    const parsed = limit ? Math.min(50, Math.max(1, parseInt(limit, 10) || 50)) : 50;
    return this.reputation.getLeaderboard(parsed);
  }

  @Get('me')
  me(@CurrentUser() user: AuthUser) {
    return this.reputation.getMe(user.id);
  }
}
