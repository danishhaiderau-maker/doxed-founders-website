import {
  Body,
  Controller,
  Get,
  HttpCode,
  Param,
  Patch,
  Post,
  Query,
  UseGuards,
} from '@nestjs/common';
import { DeploymentMode } from '@prisma/client';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { FounderNodeGuard } from '../founder-node/founder-node.guard';
import { DeploymentModesService } from './deployment-modes.service';
import type {
  PublishPlan,
  RuntimeStatusReport,
  UpdateDeploymentConfigInput,
} from './deployment-modes.types';

/**
 * Phase 7 — Deployment Modes HTTP surface.
 *
 * Endpoints (all under /api):
 *   GET   /deployment-modes/:projectId              → mode + config + latest publish job
 *   PATCH /deployment-modes/:projectId              → update config / flip mode
 *   POST  /deployment-modes/:projectId/publish      → kick off Hybrid → Public publish
 *   GET   /deployment-modes/:projectId/publish/status?jobId=   → poll a publish job
 *   POST  /deployment-modes/:projectId/runtime-status          → Founder Node reports in
 *
 * The runtime-status endpoint is FounderNode-guarded (desktop calls it with
 * the FounderNode auth header), everything else is JWT-guarded by the global
 * APP_GUARD. See docs/DEPLOYMENT-MODES-UX.md §3 (panel) and §5 (publish).
 */
@Controller('deployment-modes')
export class DeploymentModesController {
  constructor(private readonly service: DeploymentModesService) {}

  @Get(':projectId')
  getProjectDeployment(@Param('projectId') projectId: string) {
    return this.service.getProjectDeployment(projectId);
  }

  @Patch(':projectId')
  @HttpCode(200)
  async updateConfig(
    @Param('projectId') projectId: string,
    @Body() body: UpdateDeploymentConfigInput & { deploymentMode?: DeploymentMode },
  ) {
    // Reject unknown enum values early so we return 400 not 500.
    if (
      body.deploymentMode &&
      !Object.values(DeploymentMode).includes(body.deploymentMode)
    ) {
      throw new Error(`Invalid deploymentMode: ${String(body.deploymentMode)}`);
    }
    const plan = body.publishPlan as PublishPlan | null | undefined;
    return this.service.updateConfig(projectId, { ...body, publishPlan: plan });
  }

  @Post(':projectId/publish')
  @HttpCode(202)
  publish(@Param('projectId') projectId: string) {
    return this.service.startPublish(projectId);
  }

  @Get(':projectId/publish/status')
  getPublishStatus(
    @Param('projectId') projectId: string,
    @Query('jobId') jobId?: string,
  ) {
    return this.service.getPublishJob(projectId, jobId);
  }

  /**
   * Founder Node reports Private-mode runtime status (Forgejo / SQLite /
   * cloudflared / Tailscale presence). Guarded by FounderNodeGuard so only a
   * paired node can write here. Slice 7.3.
   *
   * @CurrentUser is accepted so the global JwtAuthGuard DI graph is satisfied
   * even on the FounderNode-protected route — FounderNode auth header short-
   * circuits the JWT guard (see auth/guards.ts).
   */
  @Post(':projectId/runtime-status')
  @UseGuards(FounderNodeGuard)
  @HttpCode(204)
  async reportRuntimeStatus(
    @CurrentUser() _user: AuthUser,
    @Param('projectId') projectId: string,
    @Body() body: RuntimeStatusReport,
  ) {
    await this.service.reportRuntimeStatus(projectId, body);
  }
}
