import { Module } from '@nestjs/common';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { FounderOsMemoryService } from './founder-os-memory.service';
import { GitHubApiService } from './github-api.service';

@Module({
  providers: [CredentialsCryptoService, GitHubApiService, FounderOsMemoryService],
  exports: [GitHubApiService, CredentialsCryptoService, FounderOsMemoryService],
})
export class GitHubModule {}
