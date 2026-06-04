import { Module, forwardRef } from '@nestjs/common';
import { AttestationModule } from '../attestation/attestation.module';
import { FounderNodeModule } from '../founder-node/founder-node.module';
import { GitHubModule } from '../github/github.module';
import { ProjectsModule } from '../projects/projects.module';
import { FounderMemoryGraphModule } from '../founder-memory/founder-memory-graph.module';
import { FounderAgentRunModule } from '../founder-agent-run/founder-agent-run.module';
import { BuilderController } from './builder.controller';
import { BuilderService } from './builder.service';

@Module({
  imports: [
    GitHubModule,
    forwardRef(() => FounderNodeModule),
    AttestationModule,
    ProjectsModule,
    FounderMemoryGraphModule,
    FounderAgentRunModule,
  ],
  controllers: [BuilderController],
  providers: [BuilderService],
  exports: [BuilderService],
})
export class BuilderModule {}
