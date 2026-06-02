import { Controller, Param, Post, UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/guards';
import { CurrentUser } from '../auth/current-user.decorator';
import type { AuthUser } from '../auth/auth.types';
import { ProjectsService } from './projects.service';

@Controller('projects')
export class ProjectsClaimController {
  constructor(private readonly projects: ProjectsService) {}

  @Post(':slug/claim')
  @UseGuards(JwtAuthGuard)
  claim(@Param('slug') slug: string, @CurrentUser() user: AuthUser) {
    return this.projects.claimProject(user.id, slug);
  }
}
