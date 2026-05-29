import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { FounderOsService } from './founder-os.service';

@Controller('founder-os')
export class FounderOsController {
  constructor(private readonly founderOs: FounderOsService) {}

  @Get('dashboard')
  dashboard(@CurrentUser() user: AuthUser) {
    return this.founderOs.getDashboard(user.id);
  }

  @Post('github/connect')
  connectGitHub(@CurrentUser() user: AuthUser, @Body() body: { repoFullName: string }) {
    return this.founderOs.connectGitHubRepo(user.id, body.repoFullName);
  }

  @Post('github/sync')
  syncGitHub(@CurrentUser() user: AuthUser) {
    return this.founderOs.syncGitHubCommits(user.id);
  }

  @Post('suggestions/:id/publish')
  publishSuggestion(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.founderOs.publishSuggestedUpdate(user.id, id);
  }

  @Post('projects/:projectId/comments/:commentId/helpful')
  markHelpful(
    @CurrentUser() user: AuthUser,
    @Param('projectId') projectId: string,
    @Param('commentId') commentId: string,
  ) {
    return this.founderOs.markCommentHelpful(user.id, projectId, commentId);
  }

  @Post('projects/:projectId/bounties')
  createBounty(
    @CurrentUser() user: AuthUser,
    @Param('projectId') projectId: string,
    @Body() body: { title: string; description: string; rewardCredits: number; rewardPoints?: number },
  ) {
    return this.founderOs.createBounty(user.id, projectId, body);
  }

  @Post('bounties/:bountyId/award')
  awardBounty(
    @CurrentUser() user: AuthUser,
    @Param('bountyId') bountyId: string,
    @Body() body: { awardeeUserId: string },
  ) {
    return this.founderOs.awardBounty(user.id, bountyId, body.awardeeUserId);
  }
}
