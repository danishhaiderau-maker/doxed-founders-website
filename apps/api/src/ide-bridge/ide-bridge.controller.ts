import { Body, Controller, Get, Param, Post } from '@nestjs/common';
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

  @Post('sessions/:sessionId/dispatch')
  dispatchToIde(
    @CurrentUser() user: AuthUser,
    @Param('sessionId') sessionId: string,
    @Body() body: { prompt: string; ideProvider?: string; targetNodeId?: string },
  ) {
    return this.ideBridge.createDispatch(
      user.id,
      sessionId,
      body.prompt,
      body.ideProvider ?? 'cursor',
      body.targetNodeId,
    );
  }

  @Get('dispatch/:dispatchId')
  getDispatchStatus(
    @CurrentUser() user: AuthUser,
    @Param('dispatchId') dispatchId: string,
  ) {
    return this.ideBridge.getDispatchStatus(user.id, dispatchId);
  }

  @Post('dispatch/:dispatchId/cancel')
  cancelDispatch(
    @CurrentUser() user: AuthUser,
    @Param('dispatchId') dispatchId: string,
    @Body() body: { reason?: string },
  ) {
    return this.ideBridge.cancelDispatch(user.id, dispatchId, body?.reason);
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
