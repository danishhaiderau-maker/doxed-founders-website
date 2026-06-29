import { Body, Controller, Get, Put } from '@nestjs/common';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { WorkspaceSessionService } from './workspace-session.service';

@Controller('workspace-session')
export class WorkspaceSessionController {
  constructor(private readonly sessions: WorkspaceSessionService) {}

  @Get()
  get(@CurrentUser() user: AuthUser) {
    return this.sessions.getForUser(user.id);
  }

  @Put()
  async patch(
    @CurrentUser() user: AuthUser,
    @Body() body: Record<string, unknown>,
  ) {
    return this.sessions.patchForUser(user.id, body ?? {});
  }
}
