import { Body, Controller, Get, Post } from '@nestjs/common';
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

  @Post('copilot/ask')
  ask(@CurrentUser() user: AuthUser, @Body() body: { prompt: string }) {
    return this.copilot.ask(user.id, body.prompt);
  }

  @Post('copilot/hands-free')
  handsFree(@CurrentUser() user: AuthUser, @Body() body: { prompt: string }) {
    return this.copilot.handsFree(user.id, body.prompt);
  }
}
