import { Module, forwardRef } from '@nestjs/common';
import { EventsModule } from '../events/events.module';
import { FounderNodeController } from './founder-node.controller';
import { FounderNodeGuard } from './founder-node.guard';
import { FounderNodeInferenceService } from './founder-node-inference.service';
import { FounderNodeSyncService } from './founder-node-sync.service';
import { FounderNodeService } from './founder-node.service';

@Module({
  imports: [forwardRef(() => EventsModule)],
  controllers: [FounderNodeController],
  providers: [FounderNodeService, FounderNodeGuard, FounderNodeInferenceService, FounderNodeSyncService],
  exports: [FounderNodeService, FounderNodeInferenceService, FounderNodeSyncService],
})
export class FounderNodeModule {}
