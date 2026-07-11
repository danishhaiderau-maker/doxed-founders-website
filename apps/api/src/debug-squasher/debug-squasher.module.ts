import { Module } from '@nestjs/common';
import { DebugSquasherController } from './debug-squasher.controller';
import { DebugSquasherService } from './debug-squasher.service';
import { DebugSquasherCron } from './debug-squasher.cron';
import { DemoModule } from '../demo/demo.module';
import { AiProxyModule } from '../ai-proxy/ai-proxy.module';
import { PrismaModule } from '../prisma/prisma.module';

/**
 * Debug Squasher module — wires the harness + AI Gateway into a daily
 * health-check + bug diagnostician.
 *
 * Imports DemoModule (for DemoHarnessService) and AiProxyModule (for
 * AiProxyRuntimeService) — both are exported by their respective modules.
 */
@Module({
  imports: [DemoModule, AiProxyModule, PrismaModule],
  controllers: [DebugSquasherController],
  providers: [DebugSquasherService, DebugSquasherCron],
  exports: [DebugSquasherService],
})
export class DebugSquasherModule {}
