import { Module } from '@nestjs/common';
import { JwtModule } from '@nestjs/jwt';
import { FounderOsMemoryService } from './founder-os-memory.service';
import { GitHubApiService } from './github-api.service';
import { GitHubOAuthService } from './github-oauth.service';
import { WorkspaceActivityService } from './workspace-activity.service';

@Module({
  imports: [
    JwtModule.register({
      secret: process.env.JWT_SECRET ?? 'dev-secret-change-in-production',
      signOptions: { expiresIn: '10m' },
    }),
  ],
  providers: [
    GitHubApiService,
    FounderOsMemoryService,
    GitHubOAuthService,
    WorkspaceActivityService,
  ],
  exports: [
    GitHubApiService,
    FounderOsMemoryService,
    GitHubOAuthService,
    WorkspaceActivityService,
  ],
})
export class GitHubModule {}
