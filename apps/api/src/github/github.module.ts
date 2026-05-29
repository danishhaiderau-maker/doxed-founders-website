import { Module } from '@nestjs/common';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { GitHubApiService } from './github-api.service';

@Module({
  providers: [CredentialsCryptoService, GitHubApiService],
  exports: [GitHubApiService, CredentialsCryptoService],
})
export class GitHubModule {}
