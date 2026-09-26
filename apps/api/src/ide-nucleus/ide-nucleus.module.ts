import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { EventsModule } from '../events/events.module';
import { FounderNodeModule } from '../founder-node/founder-node.module';
import { IdeNucleusAuthGuard } from './ide-nucleus-auth.guard';
import { IdeNucleusController } from './ide-nucleus.controller';

@Module({
  imports: [AuthModule, FounderNodeModule, EventsModule],
  controllers: [IdeNucleusController],
  providers: [IdeNucleusAuthGuard],
})
export class IdeNucleusModule {}
