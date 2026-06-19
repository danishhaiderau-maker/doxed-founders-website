import { Body, Controller, Delete, Get, Param, Post, Query, Res, UseGuards } from '@nestjs/common';
import type { Response } from 'express';
import { TradingAgentKind } from '@prisma/client';
import { AuthUser } from '../auth/auth.types';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { CopyRelaySimService } from './copy-relay-sim.service';
import { TradingAgentInstancesService } from './trading-agent-instances.service';
import { TradingAgentsService } from './trading-agents.service';

@Controller('trading-agents')
export class TradingAgentsController {
  constructor(
    private readonly tradingAgents: TradingAgentsService,
    private readonly instances: TradingAgentInstancesService,
    private readonly relaySim: CopyRelaySimService,
  ) {}

  @Public()
  @Get()
  list(@Query('kind') kind?: TradingAgentKind) {
    return this.tradingAgents.list(kind);
  }

  @Public()
  @Get('leaderboard')
  leaderboard() {
    return this.tradingAgents.leaderboard();
  }

  @Public()
  @Get('showcase-default-settings')
  showcaseDefaults() {
    return this.tradingAgents.getShowcaseDefaultSettings();
  }

  @Public()
  @Get('bot/status')
  botStatus() {
    return this.tradingAgents.getBotBridgeStatus();
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get(':slug')
  getBySlug(@Param('slug') slug: string, @CurrentUser() user?: AuthUser) {
    return this.tradingAgents.getBySlug(slug, user?.id);
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get(':slug/dashboard')
  dashboard(@Param('slug') slug: string, @CurrentUser() user?: AuthUser) {
    return this.tradingAgents.getPublicDashboard(slug, user?.id, user?.role);
  }

  @Get(':slug/my-dashboard')
  myDashboard(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    return this.instances.getMyDashboard(user.id, slug);
  }

  @Get(':slug/live-trades/export')
  async exportLiveTrades(
    @CurrentUser() user: AuthUser,
    @Param('slug') slug: string,
    @Query('format') format: string | undefined,
    @Res() res: Response,
  ) {
    const fmt = format === 'json' ? 'json' : 'csv';
    const result = await this.tradingAgents.exportUserBitfinexLiveTrades(user.id, slug, fmt);
    res.setHeader('Content-Disposition', `attachment; filename="${result.filename}"`);
    if (fmt === 'json') {
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      res.send(JSON.stringify(result.payload, null, 2));
      return;
    }
    res.setHeader('Content-Type', 'text/csv; charset=utf-8');
    res.send(result.csv ?? '');
  }

  @Post(':slug/paper-track')
  paperTrack(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    return this.instances.paperTrackAgent(user.id, slug, 500);
  }

  @Post(':slug/instance/pause')
  pauseInstance(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    return this.instances.setInstancePaused(user.id, slug, true);
  }

  @Post(':slug/instance/resume')
  resumeInstance(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    return this.instances.setInstancePaused(user.id, slug, false);
  }

  @Post(':slug/relay-sim/start')
  startRelaySim(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    return this.relaySim.startRelaySim(user.id, slug);
  }

  @Post(':slug/relay-sim/stop')
  stopRelaySim(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    return this.relaySim.stopRelaySim(user.id, slug);
  }

  @Get(':slug/relay-sim/status')
  relaySimStatus(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    return this.relaySim.getRelaySimStatus(user.id, slug);
  }

  @Post(':id/hire')
  hire(
    @CurrentUser() user: AuthUser,
    @Param('id') id: string,
    @Body()
    body: {
      exchangeProvider: string;
      apiKey: string;
      apiSecret: string;
      passphrase?: string;
      testnet?: boolean;
      aiMode?: 'platform' | 'own';
      aiProvider?: string;
      aiApiKey?: string;
    },
  ) {
    return this.instances.hireAgent(user.id, id, body);
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get(':slug/activity')
  activity(
    @Param('slug') slug: string,
    @Query('limit') limit?: string,
    @CurrentUser() user?: AuthUser,
  ) {
    const n = limit ? Number(limit) : 30;
    return this.tradingAgents.listActivity(slug, Number.isFinite(n) ? n : 30, true, user?.id);
  }

  @Post(':id/follow')
  follow(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.tradingAgents.follow(user.id, id);
  }

  @Delete(':id/follow')
  unfollow(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.tradingAgents.unfollow(user.id, id);
  }
}
