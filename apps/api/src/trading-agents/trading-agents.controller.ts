import { BadRequestException, Body, ConflictException, Controller, Delete, Get, Headers, Param, Post, Query, Req, Res, UseGuards } from '@nestjs/common';
import type { Request, Response } from 'express';
import { Throttle } from '@nestjs/throttler';
import { TradingAgentKind } from '@prisma/client';
import { AuthUser } from '../auth/auth.types';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { AdminGuard } from '../auth/guards';
import { CopyRelaySimService } from './copy-relay-sim.service';
import { ShowcaseRelayEventsService, type ShowcaseRelayEventBody } from './showcase-relay-events.service';
import { TradingAgentInstancesService } from './trading-agent-instances.service';
import { SignalSubscriberExecutionService } from './signal-subscriber-execution.service';
import { TradingAgentsService } from './trading-agents.service';

@Controller('trading-agents')
export class TradingAgentsController {
  constructor(
    private readonly tradingAgents: TradingAgentsService,
    private readonly instances: TradingAgentInstancesService,
    private readonly relaySim: CopyRelaySimService,
    private readonly showcaseRelay: ShowcaseRelayEventsService,
    private readonly execution: SignalSubscriberExecutionService,
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
  @Get('showcase-host')
  showcaseHost() {
    // Fly is the production strategy owner. Local is an explicit development
    // override only; an omitted Railway env must not make production claim that
    // the desktop owns execution.
    const host = (process.env.SHOWCASE_HOST?.trim() || 'fly').toLowerCase();
    return { host: host === 'fly' ? 'fly' : 'local' };
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

  @Public()
  @Get(':slug/session-summary')
  sessionSummary(@Param('slug') slug: string) {
    return this.tradingAgents.getAnalyzerSummary(slug);
  }

  @Public()
  @Get(':slug/analyzer-summary')
  analyzerSummary(@Param('slug') slug: string) {
    return this.tradingAgents.getAnalyzerSummary(slug);
  }

  @Public()
  @Get(':slug/bot-health')
  botHealth(@Param('slug') slug: string) {
    return this.tradingAgents.getBotHealth(slug);
  }

  /**
   * Ops-only relay visibility without a user cookie.
   * Auth: `X-Bot-Admin-Token: $BOT_ADMIN_TOKEN` (or Bearer).
   * Requires `userId` so responses never dump other users' instances.
   */
  @Public()
  @Get(':slug/ops/relay-status')
  opsRelayStatus(
    @Param('slug') slug: string,
    @Query('userId') userId: string | undefined,
    @Headers('x-bot-admin-token') adminHeader?: string,
    @Headers('authorization') authorization?: string,
  ) {
    return this.tradingAgents.getOpsRelayStatus(slug, userId, adminHeader, authorization);
  }

  /** Immutable, user-scoped relay evidence for the offline research mirror. */
  @Public()
  @Get(':slug/ops/relay-evidence')
  opsRelayEvidence(
    @Param('slug') slug: string,
    @Query('userId') userId: string | undefined,
    @Headers('x-bot-admin-token') adminHeader?: string,
    @Headers('authorization') authorization?: string,
  ) {
    return this.tradingAgents.exportOpsRelayEvidence(slug, userId, adminHeader, authorization);
  }

  /**
   * Cookie-free, operator-only recovery for an already-paused mismatch.
   * This never resumes the instance. The execution service additionally
   * refuses BOT_ADMIN recovery when any promoted OPEN lot exists, so this
   * route can only reconcile an unattributed pending-entry residual.
   */
  @Public()
  @Throttle({ default: { limit: 2, ttl: 60000 } })
  @Post(':slug/ops/emergency-reconcile')
  async opsEmergencyReconcile(
    @Param('slug') slug: string,
    @Query('userId') userId: string | undefined,
    @Headers('x-bot-admin-token') adminHeader: string | undefined,
    @Headers('authorization') authorization: string | undefined,
    @Body() body: { confirmation?: string; reason?: string },
  ) {
    const status = await this.tradingAgents.getOpsRelayStatus(
      slug, userId, adminHeader, authorization,
    );
    if (body?.confirmation !== 'FLATTEN_PAUSED_UNATTRIBUTED_RESIDUAL') {
      throw new BadRequestException(
        'confirmation must equal FLATTEN_PAUSED_UNATTRIBUTED_RESIDUAL',
      );
    }
    if (status.status !== 'PAUSED') {
      throw new ConflictException('Ops emergency reconcile requires a PAUSED instance');
    }
    if (!status.positionMismatchDetectedAt && !status.lastError) {
      throw new ConflictException('No persisted relay mismatch evidence is present');
    }
    const result = await this.execution.requestExecutorEmergencyReconcile(
      userId!.trim(),
      slug,
      body.reason?.trim() || 'PAUSED_RELAY_MISMATCH_RECOVERY',
    );
    return { ...result, status: 'PAUSED', resumed: false };
  }

  /** No-close terminal recovery for exact stale PENDING_ENTRY participants. */
  @Public()
  @Throttle({ default: { limit: 5, ttl: 60000 } })
  @Post(':slug/ops/recover-already-flat')
  async opsRecoverAlreadyFlat(
    @Param('slug') slug: string,
    @Body() body: { userId?: string; participantIds?: string[]; confirmation?: string },
    @Headers('x-bot-admin-token') adminHeader?: string,
    @Headers('authorization') authorization?: string,
  ) {
    await this.tradingAgents.getOpsRelayStatus(
      slug, body?.userId, adminHeader, authorization,
    );
    if (body?.confirmation !== 'RECOVER_ALREADY_FLAT_PENDING_WITHOUT_CLOSE') {
      throw new BadRequestException(
        'confirmation must equal RECOVER_ALREADY_FLAT_PENDING_WITHOUT_CLOSE',
      );
    }
    return this.execution.recoverAlreadyFlatPendingEntries(
      body.userId!.trim(),
      slug,
      Array.isArray(body.participantIds) ? body.participantIds : [],
    );
  }

  /**
   * Cookie-free operator pause that retains the ordinary money-path contract:
   * managed entries are cancelled and settled before their participants are
   * expired.  Authentication deliberately runs before confirmation or any
   * instance/exchange mutation.
   */
  @Public()
  @Throttle({ default: { limit: 5, ttl: 60000 } })
  @Post(':slug/ops/pause')
  async opsPause(
    @Param('slug') slug: string,
    @Body() body: { userId?: string; confirmation?: string },
    @Headers('x-bot-admin-token') adminHeader?: string,
    @Headers('authorization') authorization?: string,
  ) {
    await this.tradingAgents.getOpsRelayStatus(
      slug, body?.userId, adminHeader, authorization,
    );
    if (body?.confirmation !== 'PAUSE_AND_SETTLE_MANAGED_ENTRIES') {
      throw new BadRequestException(
        'confirmation must equal PAUSE_AND_SETTLE_MANAGED_ENTRIES',
      );
    }
    return this.instances.setInstancePaused(body.userId!.trim(), slug, true);
  }

  @Public()
  @Get(':slug/analyzer-genome')
  analyzerGenome(@Param('slug') slug: string) {
    return this.tradingAgents.getAnalyzerGenome(slug);
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

  @Post(':slug/sync-protection/breach')
  async syncProtectionBreach(
    @CurrentUser() user: AuthUser,
    @Param('slug') slug: string,
    @Body() body: { flatten?: boolean },
  ) {
    const pause = await this.instances.setInstancePaused(user.id, slug, true);
    let flattened = 0;
    if (body?.flatten) {
      const res = await this.execution.emergencyFlattenOpenCopyLots(user.id, slug);
      flattened = res.flattened;
    }
    return { ...pause, flattened };
  }

  @Post(':slug/instance/renew')
  renewRental(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    return this.instances.renewLiveCopyRental(user.id, slug);
  }

  @Public()
  @Throttle({ default: { limit: 20, ttl: 60000 } })
  @Post(':slug/showcase-relay-event')
  showcaseRelayEvent(
    @Param('slug') slug: string,
    @Headers('x-bot-control-secret') secret: string | undefined,
    @Headers('x-showcase-signature') signature: string | undefined,
    @Req() req: Request,
    @Body() body: ShowcaseRelayEventBody,
  ) {
    this.showcaseRelay.assertAuthorized(secret);
    // N1 (intent-mirror) — forward the raw body + signature so ingest() can
    // HMAC-verify the payload before any state mutation. rawBody is populated
    // by Nest's rawBody: true middleware (see main.ts).
    return this.showcaseRelay.ingest(slug, body, {
      rawBody: (req as unknown as { rawBody?: Buffer }).rawBody,
      signatureHeader: signature,
    });
  }

  @Post(':slug/relay-sim/start')
  startRelaySim(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    return this.relaySim.startRelaySim(user.id, slug);
  }

  @Post(':slug/relay-sim/stop')
  stopRelaySim(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    return this.relaySim.stopRelaySim(user.id, slug);
  }

  @Post(':slug/relay-sim/reset')
  resetRelaySim(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    return this.relaySim.resetRelaySim(user.id, slug);
  }

  @Get(':slug/relay-sim/export')
  async exportRelaySim(
    @CurrentUser() user: AuthUser,
    @Param('slug') slug: string,
    @Res({ passthrough: false }) res: Response,
  ) {
    const csv = await this.relaySim.exportRelaySimAuditCsv(user.id, slug);
    res.setHeader('Content-Type', 'text/csv; charset=utf-8');
    res.setHeader('Content-Disposition', `attachment; filename="${slug}-relay-sim-audit.csv"`);
    res.send(csv);
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
