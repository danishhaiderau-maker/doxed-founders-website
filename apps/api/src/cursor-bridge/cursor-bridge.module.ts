import { Module } from '@nestjs/common';
import { CursorBridgeController } from './cursor-bridge.controller';
import { CursorBridgeService } from './cursor-bridge.service';
import { DesktopBridgeModule } from '../desktop-bridge/desktop-bridge.module';
import { FounderAgentRunModule } from '../founder-agent-run/founder-agent-run.module';

@Module({
  imports: [DesktopBridgeModule, FounderAgentRunModule],
  controllers: [CursorBridgeController],
  providers: [CursorBridgeService],
  exports: [CursorBridgeService],
})
export class CursorBridgeModule {}
