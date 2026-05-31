import { Module } from '@nestjs/common';
import { BuildQueueModule } from '../build-queue/build-queue.module';
import { BuilderModule } from '../builder/builder.module';
import { AgentsController } from './agents.controller';
import { AgentsService } from './agents.service';

@Module({
  imports: [BuildQueueModule, BuilderModule],
  controllers: [AgentsController],
  providers: [AgentsService],
  exports: [AgentsService],
})
export class AgentsModule {}
