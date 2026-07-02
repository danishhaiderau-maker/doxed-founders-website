import { Controller, Get, Param } from '@nestjs/common';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { IdeBridgeService } from './ide-bridge.service';

@Controller('ide-bridge')
export class IdeBridgeController {
  constructor(private readonly ideBridge: IdeBridgeService) {}

  @Get('recent-agents')
  recentAgents(@CurrentUser() user: AuthUser) {
    return this.ideBridge.getRecentAgents(user.id);
  }

  @Get('capabilities')
  getCapabilities(@CurrentUser() user: AuthUser) {
    return this.ideBridge.getCapabilities(user.id);
  }

  @Get('workspaces')
  getWorkspaces(@CurrentUser() user: AuthUser) {
    return this.ideBridge.getWorkspaces(user.id);
  }

  @Get('sessions')
  getSessions(@CurrentUser() user: AuthUser) {
    return this.ideBridge.getSessions(user.id);
  }

  @Get('sessions/:sessionId/messages')
  getSessionMessages(
    @CurrentUser() user: AuthUser,
    @Param('sessionId') sessionId: string,
  ) {
    return this.ideBridge.getSessionMessages(user.id, sessionId);
  }
}

/**
 * Backward-compat alias — preserves the legacy `/cursor-bridge/recent-agents` URL
 * after the module was renamed to ide-bridge. Delegates to IdeBridgeService.
 */
@Controller('cursor-bridge')
export class CursorBridgeAliasController {
  constructor(private readonly ideBridge: IdeBridgeService) {}

  @Get('recent-agents')
  recentAgents(@CurrentUser() user: AuthUser) {
    return this.ideBridge.getRecentAgents(user.id);
  }
}
