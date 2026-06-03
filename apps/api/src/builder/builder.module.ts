import { Module, forwardRef } from '@nestjs/common';
import { AttestationModule } from '../attestation/attestation.module';
import { FounderNodeModule } from '../founder-node/founder-node.module';
import { GitHubModule } from '../github/github.module';
import { ProjectsModule } from '../projects/projects.module';
import { BuilderController } from './builder.controller';
import { BuilderService } from './builder.service';

@Module({
  imports: [GitHubModule, forwardRef(() => FounderNodeModule), AttestationModule, ProjectsModule],
  controllers: [BuilderController],
  providers: [BuilderService],
  exports: [BuilderService],
})
export class BuilderModule {}
