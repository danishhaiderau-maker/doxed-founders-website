import { Controller, Get, Post, UseGuards } from '@nestjs/common';
import { AdminGuard } from '../auth/guards';
import { Public } from '../auth/public.decorator';
import { EngagementRewardsService } from './engagement-rewards.service';

@Controller('engagement-rewards')
export class EngagementRewardsController {
  constructor(private readonly engagementRewards: EngagementRewardsService) {}

  @Public()
  @Get('stats')
  stats() {
    return this.engagementRewards.getEngagementStats();
  }

  @Public()
  @Get('latest-lottery')
  latestLottery() {
    return this.engagementRewards.getLatestLottery();
  }

  @UseGuards(AdminGuard)
  @Post('daily-lottery')
  dailyLottery() {
    return this.engagementRewards.runDailyLottery();
  }
}
