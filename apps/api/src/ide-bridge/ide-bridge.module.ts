import { Module } from '@nestjs/common';
import { IdeBridgeController, CursorBridgeAliasController } from './ide-bridge.controller';
import { IdeBridgeService } from './ide-bridge.service';
import { DesktopBridgeModule } from '../desktop-bridge/desktop-bridge.module';
import { FounderAgentRunModule } from '../founder-agent-run/founder-agent-run.module';

@Module({
  imports: [DesktopBridgeModule, FounderAgentRunModule],
  controllers: [IdeBridgeController, CursorBridgeAliasController],
  providers: [IdeBridgeService],
  exports: [IdeBridgeService],
})
export class IdeBridgeModule {}
