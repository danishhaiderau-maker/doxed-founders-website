import { Body, Controller, Get, Param, Post, UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/guards';
import { CurrentUser } from '../auth/current-user.decorator';
import type { AuthUser } from '../auth/auth.types';
import { ProjectsService } from './projects.service';

@Controller('projects')
export class ProjectsClaimController {
  constructor(private readonly projects: ProjectsService) {}

  @Get(':slug/claim-context')
  @UseGuards(JwtAuthGuard)
  claimContext(@Param('slug') slug: string, @CurrentUser() user: AuthUser) {
    return this.projects.getClaimContext(user.id, slug);
  }

  @Post(':slug/claim')
  @UseGuards(JwtAuthGuard)
  claim(@Param('slug') slug: string, @CurrentUser() user: AuthUser) {
    return this.projects.claimProject(user.id, slug);
  }

  @Post(':slug/lock-profile')
  @UseGuards(JwtAuthGuard)
  lockProfile(
    @Param('slug') slug: string,
    @CurrentUser() user: AuthUser,
    @Body() body: { password: string },
  ) {
    return this.projects.lockProjectProfile(user.id, slug, body.password);
  }

  @Post(':slug/unlock-profile')
  @UseGuards(JwtAuthGuard)
  unlockProfile(
    @Param('slug') slug: string,
    @CurrentUser() user: AuthUser,
    @Body() body: { password: string },
  ) {
    return this.projects.unlockProjectProfile(user.id, slug, body.password);
  }
}
