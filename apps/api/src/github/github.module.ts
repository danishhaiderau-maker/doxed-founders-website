import { Module } from '@nestjs/common';
import { JwtModule } from '@nestjs/jwt';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { FounderOsMemoryService } from './founder-os-memory.service';
import { GitHubApiService } from './github-api.service';
import { GitHubOAuthService } from './github-oauth.service';

@Module({
  imports: [
    JwtModule.register({
      secret: process.env.JWT_SECRET ?? 'dev-secret-change-in-production',
      signOptions: { expiresIn: '10m' },
    }),
  ],
  providers: [CredentialsCryptoService, GitHubApiService, FounderOsMemoryService, GitHubOAuthService],
  exports: [GitHubApiService, CredentialsCryptoService, FounderOsMemoryService, GitHubOAuthService],
})
export class GitHubModule {}
