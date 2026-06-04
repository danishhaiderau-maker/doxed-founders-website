import { Module, forwardRef } from '@nestjs/common';
import { EventsModule } from '../events/events.module';
import { FounderNodeController } from './founder-node.controller';
import { FounderNodeGuard } from './founder-node.guard';
import { FounderNodeInferenceService } from './founder-node-inference.service';
import { FounderNodeSyncService } from './founder-node-sync.service';
import { FounderNodeVaultSyncService } from './founder-node-vault-sync.service';
import { FounderNodeService } from './founder-node.service';
import { DesktopBridgeModule } from '../desktop-bridge/desktop-bridge.module';

@Module({
  imports: [forwardRef(() => EventsModule), DesktopBridgeModule],
  controllers: [FounderNodeController],
  providers: [
    FounderNodeService,
    FounderNodeGuard,
    FounderNodeInferenceService,
    FounderNodeSyncService,
    FounderNodeVaultSyncService,
  ],
  exports: [
    FounderNodeService,
    FounderNodeInferenceService,
    FounderNodeSyncService,
    FounderNodeVaultSyncService,
  ],
})
export class FounderNodeModule {}
