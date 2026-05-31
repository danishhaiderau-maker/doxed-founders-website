import { Module } from '@nestjs/common';
import { FounderNodeModule } from '../founder-node/founder-node.module';
import { GitHubModule } from '../github/github.module';
import { BuilderController } from './builder.controller';
import { BuilderService } from './builder.service';

@Module({
  imports: [GitHubModule, FounderNodeModule],
  controllers: [BuilderController],
  providers: [BuilderService],
  exports: [BuilderService],
})
export class BuilderModule {}
