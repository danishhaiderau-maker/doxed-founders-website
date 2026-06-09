import { Body, Controller, Get, Patch, Post, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AdminGuard } from '../auth/guards';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { Public } from '../auth/public.decorator';
import { AdminControlService } from './admin-control.service';
import { ShowcaseRuntimeService } from './showcase-runtime.service';
import { TradingAgentsService } from '../trading-agents/trading-agents.service';

@SkipThrottle()
@Controller('admin-control')
export class AdminControlController {
  constructor(
    private readonly adminControl: AdminControlService,
    private readonly showcaseRuntime: ShowcaseRuntimeService,
    private readonly tradingAgents: TradingAgentsService,
  ) {}

  @Public()
  @Get('share-footer')
  shareFooter() {
    return this.adminControl.getShareFooter().then((footer) => ({ footer }));
  }

  @Public()
  @Get('agent-status')
  publicAgentStatus() {
    return this.adminControl.getPublicAgentStatus();
  }

  @UseGuards(AdminGuard)
  @Get('overview')
  overview() {
    return this.adminControl.getAgentControlOverview();
  }

  @UseGuards(AdminGuard)
  @Patch('share-footer')
  updateShareFooter(@CurrentUser() user: AuthUser, @Body() body: { footer?: string }) {
    return this.adminControl.updateShareFooter(user.id, body.footer ?? '');
  }

  @UseGuards(AdminGuard)
  @Post('agent/pause')
  pauseAgent() {
    return this.adminControl.pauseAgentTrading();
  }

  @UseGuards(AdminGuard)
  @Post('agent/resume')
  resumeAgent() {
    return this.adminControl.resumeAgentTrading();
  }

  @UseGuards(AdminGuard)
  @Post('agent/restart')
  restartAgent() {
    return this.adminControl.restartAgentRuntime();
  }

  @UseGuards(AdminGuard)
  @Post('agent/reset-simulation')
  resetSimulation() {
    return this.adminControl.resetShowcaseSimulation();
  }

  @UseGuards(AdminGuard)
  @Get('agent-research-dashboard')
  agentResearchDashboard() {
    return this.tradingAgents.getAdminResearchDashboard('conservative-btc');
  }

  @UseGuards(AdminGuard)
  @Patch('showcase-config')
  updateShowcase(
    @CurrentUser() user: AuthUser,
    @Body() body: { exchangeProvider?: string; aiProvider?: string; agentShowcaseDefaultSettings?: string },
  ) {
    return this.adminControl.updateShowcaseConfig(user.id, body);
  }

  @UseGuards(AdminGuard)
  @Get('showcase-credentials')
  showcaseCredentials() {
    return this.showcaseRuntime.getCredentialsStatus();
  }

  @UseGuards(AdminGuard)
  @Post('showcase-credentials')
  saveShowcaseCredentials(
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      exchangeProvider?: string;
      aiProvider?: string;
      exchangeApiKey?: string;
      exchangeApiSecret?: string;
      exchangePassphrase?: string;
      testnet?: boolean;
      aiApiKey?: string;
      botPublicUrl?: string;
    },
  ) {
    return this.showcaseRuntime.saveShowcaseCredentials(user.id, body).then(() =>
      this.adminControl.getAgentControlOverview(),
    );
  }

  @UseGuards(AdminGuard)
  @Post('showcase-credentials/clear')
  clearShowcaseCredentials(
    @CurrentUser() user: AuthUser,
    @Body() body: { target?: 'exchange' | 'ai' | 'all' },
  ) {
    return this.showcaseRuntime
      .clearShowcaseCredentials(user.id, body.target ?? 'all')
      .then(() => this.adminControl.getAgentControlOverview());
  }

  @UseGuards(AdminGuard)
  @Post('showcase-runtime/push')
  pushShowcaseRuntime(@CurrentUser() user: AuthUser) {
    return this.showcaseRuntime.pushToRailwayRuntime(user.id);
  }
}
