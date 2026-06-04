import { Body, Controller, Get, Post } from '@nestjs/common';
import type { DeviceMemoryPayload } from '@dcf/utils';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { EventsService } from './events.service';
import { FounderAutopilotService } from './founder-autopilot.service';
import { FounderCopilotService } from './founder-copilot.service';

@Controller()
export class EventsController {
  constructor(
    private readonly events: EventsService,
    private readonly copilot: FounderCopilotService,
    private readonly autopilot: FounderAutopilotService,
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
  ask(@CurrentUser() user: AuthUser, @Body() body: { prompt: string; agentTemplate?: string }) {
    return this.copilot.ask(user.id, body.prompt, { agentTemplate: body.agentTemplate });
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
