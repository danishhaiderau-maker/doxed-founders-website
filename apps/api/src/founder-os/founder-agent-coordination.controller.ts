import { Body, Controller, Delete, Get, Param, Patch, Post, Query } from '@nestjs/common';
import { CurrentUser } from '../auth/current-user.decorator';
import type { AuthUser } from '../auth/auth.types';
import {
  FounderAgentCoordinationService,
  type ClaimFounderPathInput,
  type StartFounderTaskInput,
} from './founder-agent-coordination.service';

@Controller('founder-coordination')
export class FounderAgentCoordinationController {
  constructor(private readonly coordination: FounderAgentCoordinationService) {}

  @Post('tasks')
  start(@CurrentUser() user: AuthUser, @Body() body: StartFounderTaskInput) {
    return this.coordination.startTask(user.id, body);
  }

  @Get('tasks')
  list(@CurrentUser() user: AuthUser, @Query('workspaceKey') workspaceKey: string) {
    return this.coordination.listTasks(user.id, workspaceKey);
  }

  @Post('tasks/:taskId/heartbeat')
  heartbeat(
    @CurrentUser() user: AuthUser,
    @Param('taskId') taskId: string,
    @Body() body: { status?: 'ACTIVE' | 'WAITING' },
  ) {
    return this.coordination.heartbeat(user.id, taskId, body?.status ?? 'ACTIVE');
  }

  @Post('tasks/:taskId/claims')
  claim(
    @CurrentUser() user: AuthUser,
    @Param('taskId') taskId: string,
    @Body() body: ClaimFounderPathInput,
  ) {
    return this.coordination.claimPath(user.id, taskId, body);
  }

  @Delete('tasks/:taskId/claims')
  release(
    @CurrentUser() user: AuthUser,
    @Param('taskId') taskId: string,
    @Query('path') path: string,
  ) {
    return this.coordination.releasePath(user.id, taskId, path);
  }

  @Patch('tasks/:taskId/finish')
  finish(
    @CurrentUser() user: AuthUser,
    @Param('taskId') taskId: string,
    @Body() body: { status?: 'COMPLETE' | 'CANCELED' },
  ) {
    return this.coordination.finishTask(user.id, taskId, body?.status ?? 'COMPLETE');
  }

  @Get('audit')
  audit(@CurrentUser() user: AuthUser, @Query('workspaceKey') workspaceKey?: string) {
    return this.coordination.auditTrail(user.id, workspaceKey);
  }
}
