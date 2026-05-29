import { Body, Controller, Get, Param, Patch, Post } from '@nestjs/common';
import { BuildQueueStatus } from '@prisma/client';
import { CommandBarIntent } from '@dcf/utils';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { BuildQueueService } from './build-queue.service';

@Controller('build-queue')
export class BuildQueueController {
  constructor(private readonly buildQueue: BuildQueueService) {}

  @Get()
  list(@CurrentUser() user: AuthUser) {
    return this.buildQueue.listItems(user.id);
  }

  @Get('room')
  room(@CurrentUser() user: AuthUser) {
    return this.buildQueue.getBuildRoom(user.id);
  }

  @Post('quick-build')
  quickBuild(
    @CurrentUser() user: AuthUser,
    @Body() body: { prompt: string; source?: 'QUICK_BUILD' | 'VOICE' },
  ) {
    return this.buildQueue.quickBuild(user.id, body);
  }

  @Post('command')
  command(
    @CurrentUser() user: AuthUser,
    @Body() body: { intent: CommandBarIntent; prompt?: string },
  ) {
    return this.buildQueue.runCommand(user.id, body);
  }

  @Patch(':id')
  update(
    @CurrentUser() user: AuthUser,
    @Param('id') id: string,
    @Body() body: { status?: BuildQueueStatus; title?: string },
  ) {
    return this.buildQueue.updateItem(user.id, id, body);
  }

  @Post(':id/dismiss')
  dismiss(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.buildQueue.dismissItem(user.id, id);
  }

  @Get(':id/cursor-copy')
  cursorCopy(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.buildQueue.getCursorCopy(user.id, id);
  }
}
