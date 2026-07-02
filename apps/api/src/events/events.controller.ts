import { Body, Controller, Get, Post, Res } from '@nestjs/common';
import type { Response } from 'express';
import type { DeviceMemoryPayload } from '@dcf/utils';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { EventsService } from './events.service';
import { FounderAutopilotService } from './founder-autopilot.service';
import { FounderCopilotService } from './founder-copilot.service';
import { FounderCommandCenterService } from './founder-command-center.service';

@Controller()
export class EventsController {
  constructor(
    private readonly events: EventsService,
    private readonly copilot: FounderCopilotService,
    private readonly autopilot: FounderAutopilotService,
    private readonly commandCenter: FounderCommandCenterService,
  ) {}

  @Get('events')
  list(@CurrentUser() user: AuthUser) {
    return this.events.listForUser(user.id);
  }

  @Get('events/activity')
  activity(@CurrentUser() user: AuthUser) {
    return this.events.getActivityFeed(user.id);
  }

  @Get('copilot/memory')
  memory(@CurrentUser() user: AuthUser) {
    return this.copilot.getProjectMemory(user.id);
  }

  @Get('copilot/mission-intelligence')
  missionIntelligence(@CurrentUser() user: AuthUser) {
    return this.copilot.computeMissionIntelligenceForUser(user.id);
  }

  @Get('copilot/founder-queue')
  founderQueue(@CurrentUser() user: AuthUser) {
    return this.commandCenter.getFounderQueue(user.id);
  }

  @Get('copilot/attention')
  attentionCenter(@CurrentUser() user: AuthUser) {
    return this.commandCenter.getAttentionCenter(user.id);
  }

  @Post('copilot/queue-action')
  queueAction(@CurrentUser() user: AuthUser, @Body() body: { itemId: string }) {
    return this.commandCenter.executeQueueAction(user.id, body.itemId?.trim() ?? '');
  }

  @Get('copilot/active-agent-run')
  activeAgentRun(@CurrentUser() user: AuthUser) {
    return this.commandCenter.getActiveAgentRun(user.id);
  }

  @Get('copilot/founder-graph')
  founderGraph(@CurrentUser() user: AuthUser) {
    return this.copilot.getFounderGraphForUser(user.id);
  }

  @Get('copilot/project-timeline')
  projectTimeline(@CurrentUser() user: AuthUser) {
    return this.copilot.getProjectTimelineForUser(user.id, 30);
  }

  @Get('copilot/deploy-intelligence')
  deployIntelligence(@CurrentUser() user: AuthUser) {
    return this.copilot.getDeployIntelligenceForUser(user.id, 30);
  }

  @Get('copilot/desktop-bridge')
  desktopBridge(@CurrentUser() user: AuthUser) {
    return this.copilot.getDesktopBridgeForUser(user.id);
  }

  @Get('copilot/memory-graph')
  memoryGraph(@CurrentUser() user: AuthUser) {
    return this.copilot.getMemoryGraph(user.id);
  }

  @Post('copilot/memory-graph')
  patchMemoryGraph(
    @CurrentUser() user: AuthUser,
    @Body() body: Record<string, unknown>,
  ) {
    return this.copilot.patchMemoryGraph(user.id, body);
  }

  @Get('copilot/decisions')
  decisionLog(@CurrentUser() user: AuthUser) {
    return this.copilot.getDecisionLog(user.id).then((entries) => ({ entries, count: entries.length }));
  }

  @Post('copilot/decisions')
  appendDecision(
    @CurrentUser() user: AuthUser,
    @Body() body: { decision?: string; reason?: string; source?: string },
  ) {
    return this.copilot.appendDecision(user.id, {
      decision: body.decision?.trim() ?? '',
      reason: body.reason?.trim(),
      source: body.source?.trim(),
    });
  }

  @Post('copilot/memory-graph/after-build')
  memoryGraphAfterBuild(
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      task: string;
      status: string;
      result?: string | null;
      branch?: string | null;
      prUrl?: string | null;
    },
  ) {
    return this.copilot.applyMemoryGraphAfterBuild(user.id, body);
  }

  @Get('copilot/standup')
  standup(@CurrentUser() user: AuthUser) {
    return this.copilot.getDailyStandup(user.id);
  }

  @Get('copilot/chief-of-staff-nudges')
  chiefOfStaffNudges(@CurrentUser() user: AuthUser) {
    return this.copilot.getChiefOfStaffNudges(user.id);
  }

  @Post('copilot/resume')
  resume(@CurrentUser() user: AuthUser) {
    return this.copilot.resumeWork(user.id);
  }

  @Post('copilot/mission-build')
  missionBuild(
    @CurrentUser() user: AuthUser,
    @Body() body: { worker?: 'CURSOR' | 'OPENHANDS' },
  ) {
    return this.copilot.runMissionBuild(user.id, body);
  }

  @Post('copilot/ask')
  ask(
    @CurrentUser() user: AuthUser,
    @Body() body: { prompt: string; agentTemplate?: string; provider?: string },
  ) {
    return this.copilot.ask(user.id, body.prompt, {
      agentTemplate: body.agentTemplate,
      provider: body.provider,
    });
  }

  @Post('copilot/ask/stream')
  async askStream(
    @CurrentUser() user: AuthUser,
    @Body() body: { prompt: string; agentTemplate?: string; provider?: string },
    @Res({ passthrough: false }) res: Response,
  ) {
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('X-Accel-Buffering', 'no');
    // Prevent any upstream gzip/buffering from holding the first chunk.
    res.flushHeaders?.();

    const writeEvent = (eventName: string, data: unknown) => {
      res.write(`event: ${eventName}\n`);
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    try {
      await this.copilot.askStream(
        user.id,
        body.prompt,
        { agentTemplate: body.agentTemplate, provider: body.provider },
        (event) => {
          // Strip the discriminator `type` from the payload — it's encoded as
          // the SSE event name, keeping `data:` clean for the frontend parser.
          const { type: _type, ...payload } = event as { type: string } & Record<
            string,
            unknown
          >;
          writeEvent(_type, payload);
        },
      );
      res.end();
    } catch (err) {
      writeEvent('error', {
        message: err instanceof Error ? err.message : String(err),
      });
      res.end();
    }
  }

  @Post('copilot/social-draft')
  socialDraft(
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      provider?: string;
      audience?: 'trader' | 'developer';
      achievement?: { title: string; detail: string; kind?: string };
    },
  ) {
    return this.copilot.draftSocialUpdate(user.id, {
      provider: body.provider,
      audience: body.audience,
      achievement: body.achievement,
    });
  }

  @Post('copilot/hands-free')
  handsFree(@CurrentUser() user: AuthUser, @Body() body: { prompt: string }) {
    return this.copilot.handsFree(user.id, body.prompt);
  }

  @Get('copilot/platform-sync')
  platformSync(@CurrentUser() user: AuthUser) {
    return this.autopilot.getPlatformSyncStatus(user.id);
  }

  @Post('copilot/autopilot')
  runAutopilot(@CurrentUser() user: AuthUser, @Body() body: { prompt?: string }) {
    return this.autopilot.runAutopilot(user.id, body.prompt);
  }

  @Get('copilot/memory/device-sync')
  deviceMemoryGet(@CurrentUser() user: AuthUser) {
    return this.copilot.getDeviceMemorySync(user.id);
  }

  @Post('copilot/memory/device-sync')
  deviceMemorySave(@CurrentUser() user: AuthUser, @Body() body: DeviceMemoryPayload) {
    return this.copilot.saveDeviceMemorySync(user.id, body);
  }
}
