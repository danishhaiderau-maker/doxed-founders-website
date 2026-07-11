import { Module } from '@nestjs/common';
import { FounderNodeModule } from '../founder-node/founder-node.module';
import { DeploymentModesController } from './deployment-modes.controller';
import { DeploymentModesService } from './deployment-modes.service';

/**
 * Phase 7 — Deployment Modes module.
 *
 * The controller's POST /:projectId/runtime-status route uses FounderNodeGuard,
 * whose deps (FounderNodeService) are provided by FounderNodeModule. We import
 * it explicitly so the guard's DI graph resolves — same fix pattern as the
 * MemoryEngineModule / AiProxyModule bugs called out in the task brief.
 */
@Module({
  imports: [FounderNodeModule],
  controllers: [DeploymentModesController],
  providers: [DeploymentModesService],
  exports: [DeploymentModesService],
})
export class DeploymentModesModule {}
