import { Controller, Get, Query } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AuthUser } from '../auth/auth.types';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import { AirdropService } from './airdrop.service';

@SkipThrottle()
@Controller('builder-rewards')
export class BuilderRewardsController {
  constructor(private readonly rewards: AirdropService) {}

  @Public()
  @Get('leaderboard')
  leaderboard(@Query('limit') limit?: string) {
    const parsed = limit ? Math.min(150, Math.max(1, parseInt(limit, 10) || 100)) : 100;
    return this.rewards.getLeaderboard(parsed);
  }

  @Get('me')
  me(@CurrentUser() user: AuthUser) {
    return this.rewards.getMe(user.id);
  }
}
