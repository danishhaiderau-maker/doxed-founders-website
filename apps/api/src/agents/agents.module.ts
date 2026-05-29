import { Module } from '@nestjs/common';
import { BuildQueueModule } from '../build-queue/build-queue.module';
import { AgentsController } from './agents.controller';
import { AgentsService } from './agents.service';

@Module({
  imports: [BuildQueueModule],
  controllers: [AgentsController],
  providers: [AgentsService],
  exports: [AgentsService],
})
export class AgentsModule {}
