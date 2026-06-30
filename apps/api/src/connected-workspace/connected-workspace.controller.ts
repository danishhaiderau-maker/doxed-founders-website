import { Body, Controller, Delete, Get, Param, Patch, Post, Put } from '@nestjs/common';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { ConnectedWorkspaceService } from './connected-workspace.service';

@Controller('connected-workspace')
export class ConnectedWorkspaceController {
  constructor(private readonly workspaces: ConnectedWorkspaceService) {}

  @Get()
  list(@CurrentUser() user: AuthUser) {
    return this.workspaces.listForUser(user.id);
  }

  @Post()
  create(@CurrentUser() user: AuthUser, @Body() body: Record<string, unknown>) {
    return this.workspaces.create(user.id, body ?? {});
  }

  @Patch(':id')
  update(
    @CurrentUser() user: AuthUser,
    @Param('id') id: string,
    @Body() body: Record<string, unknown>,
  ) {
    return this.workspaces.update(user.id, id, body ?? {});
  }

  @Delete(':id')
  async delete(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    await this.workspaces.delete(user.id, id);
    return { ok: true };
  }

  @Get(':id/session')
  session(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.workspaces.getOrCreateSession(user.id, id);
  }

  @Put(':id/session')
  updateSession(
    @CurrentUser() user: AuthUser,
    @Param('id') id: string,
    @Body() body: Record<string, unknown>,
  ) {
    return this.workspaces.updateSession(user.id, id, body ?? {});
  }
}
