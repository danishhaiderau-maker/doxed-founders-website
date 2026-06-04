import { Module } from '@nestjs/common';
import { DesktopBridgeService } from './desktop-bridge.service';

@Module({
  providers: [DesktopBridgeService],
  exports: [DesktopBridgeService],
})
export class DesktopBridgeModule {}
