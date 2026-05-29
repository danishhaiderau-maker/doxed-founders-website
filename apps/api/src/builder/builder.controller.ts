import { Body, Controller, Delete, Get, Param, Patch, Post } from '@nestjs/common';
import { AiProvider } from '@prisma/client';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { GitHubApiService } from '../github/github-api.service';
import { BuilderService } from './builder.service';

@Controller('builder')
export class BuilderController {
  constructor(
    private readonly builder: BuilderService,
    private readonly github: GitHubApiService,
  ) {}

  @Get('settings')
  settings(@CurrentUser() user: AuthUser) {
    return this.builder.getSettings(user.id);
  }

  @Patch('settings')
  updateSettings(
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      defaultProvider?: AiProvider;
      preferredModel?: string;
      autoCreateGitHubIssues?: boolean;
      autoPublishOnEvent?: boolean;
      currentGoalFocus?: string;
    },
  ) {
    return this.builder.updateSettings(user.id, body);
  }

  @Post('providers/connect')
  connectProvider(@CurrentUser() user: AuthUser, @Body() body: { provider: string; apiKey: string }) {
    return this.builder.connectAiProvider(user.id, body.provider, body.apiKey);
  }

  @Post('providers/:provider/disconnect')
  disconnectProvider(@CurrentUser() user: AuthUser, @Param('provider') provider: string) {
    return this.builder.disconnectAiProvider(user.id, provider);
  }

  @Post('github-token')
  connectGitHubToken(@CurrentUser() user: AuthUser, @Body() body: { token: string }) {
    return this.github.verifyAndStoreToken(user.id, body.token);
  }

  @Delete('github-token')
  disconnectGitHubToken(@CurrentUser() user: AuthUser) {
    return this.github.clearToken(user.id);
  }
}
