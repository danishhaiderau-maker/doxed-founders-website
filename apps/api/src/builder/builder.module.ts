import { Module, forwardRef } from '@nestjs/common';
import { FounderNodeModule } from '../founder-node/founder-node.module';
import { GitHubModule } from '../github/github.module';
import { BuilderController } from './builder.controller';
import { BuilderService } from './builder.service';

@Module({
  imports: [GitHubModule, forwardRef(() => FounderNodeModule)],
  controllers: [BuilderController],
  providers: [BuilderService],
  exports: [BuilderService],
})
export class BuilderModule {}
