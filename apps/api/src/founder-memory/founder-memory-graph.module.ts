import { Module } from '@nestjs/common';
import { GitHubModule } from '../github/github.module';
import { FounderMemoryGraphService } from './founder-memory-graph.service';

@Module({
  imports: [GitHubModule],
  providers: [FounderMemoryGraphService],
  exports: [FounderMemoryGraphService],
})
export class FounderMemoryGraphModule {}
