import { Controller, Get } from '@nestjs/common';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { CursorBridgeService } from './cursor-bridge.service';

@Controller('cursor-bridge')
export class CursorBridgeController {
  constructor(private readonly cursorBridge: CursorBridgeService) {}

  @Get('recent-agents')
  recentAgents(@CurrentUser() user: AuthUser) {
    return this.cursorBridge.getRecentAgents(user.id);
  }
}
