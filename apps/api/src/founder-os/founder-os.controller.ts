import { Body, Controller, Get, Headers, Param, Post, Req } from '@nestjs/common';
import { createHmac, timingSafeEqual } from 'crypto';
import type { Request } from 'express';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { Public } from '../auth/public.decorator';
import { FounderOsService } from './founder-os.service';
import { GithubAutoSyncService } from './github-auto-sync.service';

@Controller('founder-os')
export class FounderOsController {
  constructor(
    private readonly founderOs: FounderOsService,
    private readonly githubSync: GithubAutoSyncService,
  ) {}

  @Get('dashboard')
  dashboard(@CurrentUser() user: AuthUser) {
    return this.founderOs.getDashboard(user.id);
  }

  @Get('integrations')
  integrations() {
    return this.founderOs.getIntegrationProviders();
  }

  @Get('onboarding')
  onboarding(@CurrentUser() user: AuthUser) {
    return this.founderOs.getOnboardingStatus(user.id);
  }

  @Post('github/connect')
  connectGitHub(@CurrentUser() user: AuthUser, @Body() body: { repoFullName: string }) {
    return this.founderOs.connectGitHubRepo(user.id, body.repoFullName);
  }

  @Get('github/repos')
  listGitHubRepos(@CurrentUser() user: AuthUser) {
    return this.founderOs.listGitHubRepos(user.id);
  }

  @Get('github/templates')
  repoTemplates() {
    return this.founderOs.listRepoStarterTemplates();
  }

  @Post('github/scaffold')
  scaffoldRepo(
    @CurrentUser() user: AuthUser,
    @Body() body: { templateKey: string; repoName: string },
  ) {
    return this.founderOs.scaffoldGitHubRepo(user.id, body);
  }

  @Post('github/sync')
  syncGitHub(@CurrentUser() user: AuthUser) {
    return this.founderOs.syncGitHubCommits(user.id);
  }

  @Post('memory/sync')
  syncMemory(@CurrentUser() user: AuthUser) {
    return this.founderOs.syncProjectMemory(user.id);
  }

  @Post('integrations/connect')
  connectIntegration(
    @CurrentUser() user: AuthUser,
    @Body() body: { provider: string; token?: string; projectName?: string },
  ) {
    return this.founderOs.connectIntegration(user.id, body);
  }

  @Post('integrations/:provider/disconnect')
  disconnectIntegration(@CurrentUser() user: AuthUser, @Param('provider') provider: string) {
    return this.founderOs.disconnectIntegration(user.id, provider);
  }

  @Post('build-room')
  buildRoom(
    @CurrentUser() user: AuthUser,
    @Body() body: { title: string; prompt: string },
  ) {
    return this.founderOs.runCursorBuildRoom(user.id, body);
  }

  @Post('suggestions/:id/publish')
  publishSuggestion(
    @CurrentUser() user: AuthUser,
    @Param('id') id: string,
    @Body() body: { buildFeed?: boolean; x?: boolean; community?: boolean },
  ) {
    return this.founderOs.publishSuggestedUpdate(user.id, id, body);
  }

  @Post('suggestions/:id/dismiss')
  dismissSuggestion(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.founderOs.dismissSuggestion(user.id, id);
  }

  @Public()
  @Post('webhooks/deploy/:secret')
  deployWebhook(
    @Param('secret') secret: string,
    @Body() body: { provider?: string; projectName?: string; environment?: string },
  ) {
    return this.founderOs.handleDeployWebhook(secret, body);
  }

  @Public()
  @Post('webhooks/github')
  async githubPushWebhook(
    @Req() req: Request & { rawBody?: Buffer },
    @Headers('x-hub-signature-256') signature: string | undefined,
    @Headers('x-github-event') event: string | undefined,
    @Body() body: { repository?: { full_name?: string }; ref?: string },
  ) {
    const secret = process.env.GITHUB_WEBHOOK_SECRET?.trim();
    if (secret && signature && req.rawBody) {
      const expected = `sha256=${createHmac('sha256', secret).update(req.rawBody).digest('hex')}`;
      const a = Buffer.from(expected);
      const b = Buffer.from(signature);
      if (a.length !== b.length || !timingSafeEqual(a, b)) {
        return { ok: false, reason: 'invalid_signature' };
      }
    }
    if (event !== 'push') return { ok: true, ignored: event ?? 'unknown' };
    return this.githubSync.handlePushWebhook(body);
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
