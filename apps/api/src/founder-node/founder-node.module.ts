import { Module, forwardRef } from '@nestjs/common';
import { EventsModule } from '../events/events.module';
import { FounderNodeController } from './founder-node.controller';
import { FounderNodeGuard } from './founder-node.guard';
import { FounderNodeService } from './founder-node.service';

@Module({
  imports: [forwardRef(() => EventsModule)],
  controllers: [FounderNodeController],
  providers: [FounderNodeService, FounderNodeGuard],
  exports: [FounderNodeService],
})
export class FounderNodeModule {}
