import { Module } from '@nestjs/common';
import { GitHubModule } from '../github/github.module';
import { FounderAgentRunModule } from '../founder-agent-run/founder-agent-run.module';
import { FounderGraphService } from './founder-graph.service';

@Module({
  imports: [GitHubModule, FounderAgentRunModule],
  providers: [FounderGraphService],
  exports: [FounderGraphService],
})
export class FounderGraphModule {}
