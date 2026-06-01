import { Body, Controller, Get, Patch, Post, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AdminGuard } from '../auth/guards';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { Public } from '../auth/public.decorator';
import { AdminControlService } from './admin-control.service';

@SkipThrottle()
@Controller('admin-control')
export class AdminControlController {
  constructor(private readonly adminControl: AdminControlService) {}

  @Public()
  @Get('share-footer')
  shareFooter() {
    return this.adminControl.getShareFooter().then((footer) => ({ footer }));
  }

  @Public()
  @Get('agent-status')
  publicAgentStatus() {
    return this.adminControl.getPublicAgentStatus();
  }

  @UseGuards(AdminGuard)
  @Get('overview')
  overview() {
    return this.adminControl.getAgentControlOverview();
  }

  @UseGuards(AdminGuard)
  @Patch('share-footer')
  updateShareFooter(@CurrentUser() user: AuthUser, @Body() body: { footer?: string }) {
    return this.adminControl.updateShareFooter(user.id, body.footer ?? '');
  }

  @UseGuards(AdminGuard)
  @Post('agent/pause')
  pauseAgent() {
    return this.adminControl.pauseAgentTrading();
  }

  @UseGuards(AdminGuard)
  @Post('agent/resume')
  resumeAgent() {
    return this.adminControl.resumeAgentTrading();
  }
}
