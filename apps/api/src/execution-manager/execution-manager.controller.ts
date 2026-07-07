import { Controller, Get, UseGuards } from '@nestjs/common';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { ExecutionManagerService } from './execution-manager.service';
import type { ExecutionTargetId } from './execution-manager.types';

/**
 * Read-only REST view over the Execution Manager. The kernel writes /
 * executes via ExecutionManagerService.execute — never through this
 * controller. This endpoint exists so the Founder OS shell's status
 * bar can show which execution targets are registered and connected.
 *
 * Auth: optional JWT, matching the Flight Recorder controller's pattern.
 */
@Controller('execution-manager')
@UseGuards(OptionalJwtAuthGuard)
export class ExecutionManagerController {
  constructor(private readonly executionManager: ExecutionManagerService) {}

  /**
   * GET /api/execution-manager/health
   * Returns every registered adapter target plus the subset currently
   * reporting connected. Cheap O(n) over the registry, safe to poll.
   */
  @Get('health')
  async health(): Promise<{
    adapters: ExecutionTargetId[];
    connected: ExecutionTargetId[];
  }> {
    return {
      adapters: this.executionManager.listTargets(),
      connected: this.executionManager.listConnectedTargets(),
    };
  }
}
