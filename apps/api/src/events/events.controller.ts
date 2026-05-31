import { Body, Controller, Get, Post } from '@nestjs/common';
import type { DeviceMemoryPayload } from '@dcf/utils';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { EventsService } from './events.service';
import { FounderCopilotService } from './founder-copilot.service';

@Controller()
export class EventsController {
  constructor(
    private readonly events: EventsService,
    private readonly copilot: FounderCopilotService,
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

  @Get('copilot/standup')
  standup(@CurrentUser() user: AuthUser) {
    return this.copilot.getDailyStandup(user.id);
  }

  @Post('copilot/resume')
  resume(@CurrentUser() user: AuthUser) {
    return this.copilot.resumeWork(user.id);
  }

  @Post('copilot/ask')
  ask(@CurrentUser() user: AuthUser, @Body() body: { prompt: string; agentTemplate?: string }) {
    return this.copilot.ask(user.id, body.prompt, { agentTemplate: body.agentTemplate });
  }

  @Post('copilot/hands-free')
  handsFree(@CurrentUser() user: AuthUser, @Body() body: { prompt: string }) {
    return this.copilot.handsFree(user.id, body.prompt);
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
