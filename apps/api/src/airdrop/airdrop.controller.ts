import { Controller, Get, Query } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AuthUser } from '../auth/auth.types';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import { AirdropService } from './airdrop.service';

@SkipThrottle()
@Controller('airdrop')
export class AirdropController {
  constructor(private readonly airdrop: AirdropService) {}

  @Public()
  @Get('runway')
  runway(@Query('limit') limit?: string) {
    const parsed = limit ? Math.min(150, Math.max(1, parseInt(limit, 10) || 100)) : 100;
    return this.airdrop.getRunwayLeaderboard(parsed);
  }

  @Get('runway/me')
  runwayMe(@CurrentUser() user: AuthUser) {
    return this.airdrop.getRunwayMe(user.id);
  }
}
