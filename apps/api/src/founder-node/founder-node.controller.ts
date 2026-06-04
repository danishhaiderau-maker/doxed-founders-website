import {
  BadRequestException,
  Body,
  Controller,
  Delete,
  Get,
  Param,
  Post,
  Req,
  UseGuards,
} from '@nestjs/common';
import type { DeviceMemoryPayload } from '@dcf/utils';
import type { FounderNodeHeartbeat } from '@dcf/founder-vault';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import { AuthUser } from '../auth/auth.types';
import { FounderNodeGuard, type FounderNodeRequestUser } from './founder-node.guard';
import { FounderNodeInferenceService } from './founder-node-inference.service';
import { FounderNodeSyncService } from './founder-node-sync.service';
import { FounderNodeService } from './founder-node.service';
import { FounderNodeVaultSyncService } from './founder-node-vault-sync.service';

@Controller('founder-node')
export class FounderNodeController {
  constructor(
    private readonly nodes: FounderNodeService,
    private readonly inference: FounderNodeInferenceService,
    private readonly syncJobs: FounderNodeSyncService,
    private readonly vaultSync: FounderNodeVaultSyncService,
  ) {}

  @Post('pairing-code')
  createPairingCode(
    @CurrentUser() user: AuthUser,
    @Body() body?: { targetPlatform?: 'desktop' | 'mobile' },
  ) {
    return this.nodes.createPairingCode(user.id, body?.targetPlatform);
  }

  @Get('vault-relays')
  vaultRelays(@CurrentUser() user: AuthUser) {
    return this.nodes.listVaultRelays(user.id);
  }

  @Get('vault-relays/:nodeId')
  vaultRelayForNode(@CurrentUser() user: AuthUser, @Param('nodeId') nodeId: string) {
    return this.nodes.pullVaultRelayForNode(user.id, nodeId);
  }

  @Get('status')
  status(@CurrentUser() user: AuthUser) {
    return this.nodes.getStatus(user.id);
  }

  @Get('ollama-status')
  ollamaStatus(@CurrentUser() user: AuthUser) {
    return this.inference.getOllamaStatus(user.id);
  }

  @Get('v2-status')
  v2Status(@CurrentUser() user: AuthUser) {
    return this.syncJobs.getV2Status(user.id);
  }

  @Post('sync-jobs/push-goal')
  pushGoal(@CurrentUser() user: AuthUser, @Body() body: { goal: string }) {
    return this.syncJobs.enqueuePushGoal(user.id, body.goal);
  }

  @Post('sync-jobs/push-task')
  pushTask(
    @CurrentUser() user: AuthUser,
    @Body() body: { title: string; taskId?: string },
  ) {
    return this.syncJobs.enqueuePushTask(user.id, body.title, body.taskId);
  }

  @Post('sync-jobs/vault-search')
  vaultSearch(
    @CurrentUser() user: AuthUser,
    @Body() body: { query: string; topK?: number },
  ) {
    return this.syncJobs.searchVault(user.id, body.query, body.topK);
  }

  @Post('sync-jobs/run-agent')
  runAgent(
    @CurrentUser() user: AuthUser,
    @Body() body: { agent: string; goal?: string; query?: string },
  ) {
    return this.syncJobs.runAgent(user.id, body.agent, body);
  }

  @Delete(':nodeId')
  revoke(@CurrentUser() user: AuthUser, @Param('nodeId') nodeId: string) {
    return this.nodes.revokeNode(user.id, nodeId);
  }

  @Public()
  @Post('pair')
  pair(
    @Body()
    body: {
      code: string;
      nodeId: string;
      label: string;
      platform?: string;
      appVersion?: string;
    },
  ) {
    if (!body.code?.trim() || !body.nodeId?.trim()) {
      throw new BadRequestException('code and nodeId required');
    }
    return this.nodes.pair(body);
  }

  @UseGuards(FounderNodeGuard)
  @Post('heartbeat')
  heartbeat(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Body() body: FounderNodeHeartbeat,
  ) {
    return this.nodes.heartbeat(req.founderNode.nodeDbId, body);
  }

  @UseGuards(FounderNodeGuard)
  @Post('sync')
  sync(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Body() body: DeviceMemoryPayload,
  ) {
    return this.nodes.syncFromNode(req.founderNode.userId, req.founderNode.nodeDbId, body);
  }

  @UseGuards(FounderNodeGuard)
  @Get('inference/pending')
  pendingInference(@Req() req: { founderNode: FounderNodeRequestUser }) {
    return this.inference.claimPending(req.founderNode.nodeId);
  }

  @UseGuards(FounderNodeGuard)
  @Post('inference/:jobId/complete')
  completeInference(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Param('jobId') jobId: string,
    @Body() body: { result?: string; error?: string },
  ) {
    return this.inference.completeJob(req.founderNode.nodeId, jobId, body);
  }

  @UseGuards(FounderNodeGuard)
  @Get('sync-jobs/pending')
  pendingSyncJob(@Req() req: { founderNode: FounderNodeRequestUser }) {
    return this.syncJobs.claimPending(req.founderNode.nodeId);
  }

  @UseGuards(FounderNodeGuard)
  @Post('sync-jobs/:jobId/complete')
  completeSyncJob(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Param('jobId') jobId: string,
    @Body() body: { result?: Record<string, unknown>; error?: string },
  ) {
    return this.syncJobs.completeJob(req.founderNode.nodeId, jobId, body);
  }

  @UseGuards(FounderNodeGuard)
  @Get('vault-sync/plan')
  vaultSyncPlan(@Req() req: { founderNode: FounderNodeRequestUser }) {
    return this.vaultSync.getVaultSyncPlan(req.founderNode.userId, req.founderNode.nodeId);
  }

  @UseGuards(FounderNodeGuard)
  @Get('vault-sync/merge/:sourceNodeId')
  vaultSyncMerge(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Param('sourceNodeId') sourceNodeId: string,
  ) {
    return this.vaultSync.getMergePatch(
      req.founderNode.userId,
      req.founderNode.nodeId,
      sourceNodeId,
    );
  }

  @UseGuards(FounderNodeGuard)
  @Post('vault-sync/ack')
  vaultSyncAck(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Body() body: { sourceNodeId: string; vaultSyncVersion: number },
  ) {
    if (!body.sourceNodeId?.trim() || !body.vaultSyncVersion) {
      throw new BadRequestException('sourceNodeId and vaultSyncVersion required');
    }
    return this.vaultSync.ackMerge(
      req.founderNode.userId,
      req.founderNode.nodeId,
      body.sourceNodeId.trim(),
      body.vaultSyncVersion,
    );
  }
}
