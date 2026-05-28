import { Controller, Get, Post, UseGuards } from '@nestjs/common';
import { Public } from '../auth/public.decorator';
import { AdminGuard } from '../auth/guards';
import { SocialSignalsService } from './social-signals.service';
import { XPostingService } from './x-posting.service';
import { getXAutomationStatus } from './x-social-status.util';

@Controller('x-social')
export class XSocialController {
  constructor(
    private readonly signals: SocialSignalsService,
    private readonly xPosting: XPostingService,
  ) {}

  @Public()
  @Get('status')
  status() {
    return getXAutomationStatus();
  }

  @UseGuards(AdminGuard)
  @Post('daily-run')
  dailyRun() {
    return this.signals.runDailySocialJob().then((social) => ({
      xPostingConfigured: this.xPosting.isConfigured(),
      social,
    }));
  }

  @UseGuards(AdminGuard)
  @Post('scan-trending')
  scanTrending() {
    return this.signals.scanTrendingBuys();
  }

  @UseGuards(AdminGuard)
  @Post('scan-trader-wins')
  scanTraderWins() {
    return this.signals.scanTraderWins();
  }
}
