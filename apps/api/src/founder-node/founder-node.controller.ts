import {
  BadRequestException,
  Body,
  Controller,
  Delete,
  Get,
  Param,
  Patch,
  Post,
  Query,
  Req,
  Res,
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
import { IdeBridgeService } from '../ide-bridge/ide-bridge.service';
import type { Response } from 'express';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { FounderPromoService } from '../founder-os/founder-promo.service';
import {
  toFounderIdeEntitlements,
  type FounderIdeEntitlements,
} from './founder-node-entitlements';
import {
  FounderAgentCoordinationService,
  type ClaimFounderPathInput,
  type StartFounderTaskInput,
} from '../founder-os/founder-agent-coordination.service';

const MANIFEST_RELATIVE_PATH = [
  'packages',
  'founder-ide',
  'updates',
  'founder-stack-updates.json',
];

const MANIFEST_CACHE_TTL_MS = 60_000;
let manifestCache: { at: number; body: unknown } | null = null;

function resolveManifestPath(): string {
  const configured = process.env.FOUNDER_IDE_MANIFEST_PATH?.trim();
  if (configured) return configured;

  const repoRootCandidate = join(process.cwd(), ...MANIFEST_RELATIVE_PATH);
  if (existsSync(repoRootCandidate)) return repoRootCandidate;

  return join(process.cwd(), '..', '..', ...MANIFEST_RELATIVE_PATH);
}

function readManifestBody(): unknown {
  const now = Date.now();
  if (manifestCache && now - manifestCache.at < MANIFEST_CACHE_TTL_MS) {
    return manifestCache.body;
  }
  const raw = readFileSync(resolveManifestPath(), 'utf8');
  const body = JSON.parse(raw);
  manifestCache = { at: now, body };
  return body;
}

/** Test-only hook to reset the cache between assertions. */
export function __resetFounderManifestCacheForTests(): void {
  manifestCache = null;
}

@Controller('founder-node')
export class FounderNodeController {
  constructor(
    private readonly nodes: FounderNodeService,
    private readonly inference: FounderNodeInferenceService,
    private readonly syncJobs: FounderNodeSyncService,
    private readonly vaultSync: FounderNodeVaultSyncService,
    private readonly ideBridge: IdeBridgeService,
    private readonly founderPromo: FounderPromoService,
    private readonly coordination: FounderAgentCoordinationService,
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

  /**
   * Phase 3 / Workstream C — truthful runtime status (canonical shape).
   *
   * Returns the `FounderStackRuntimeStatus` contract (10 fields, all required,
   * flat object). Every field is derived from live/persisted authoritative
   * signals — see `FounderNodeService.getRuntimeStatus()` JSDoc for the
   * per-field source + staleness rules.
   *
   * `latestVersion` is sourced from the manifest cache (60s TTL) so the
   * service stays I/O-free except for DB reads.
   *
   * Backwards-compat: the legacy `GET /status` endpoint is unchanged. New
   * surfaces (IDE status bar, web settings) should consume this endpoint;
   * legacy surfaces continue to consume `/status` until they migrate.
   */
  @Get('runtime-status')
  async runtimeStatus(@CurrentUser() user: AuthUser) {
    const manifest = readManifestBody() as { latestVersion?: string } | null;
    const latestVersion =
      typeof manifest?.latestVersion === 'string' ? manifest.latestVersion : '';
    return this.nodes.getRuntimeStatus(user.id, latestVersion);
  }

  @Public()
  @Get('latest-release')
  async getLatestRelease() {
    const repo = process.env.FOUNDER_NODE_REPO || 'danishhaiderau-maker/doxed-founders-website';
    const token = process.env.GITHUB_TOKEN;
    const headers: Record<string, string> = { 'User-Agent': 'founder-os-api' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`https://api.github.com/repos/${repo}/releases/latest`, { headers });
    if (!res.ok) throw new Error(`GitHub API returned ${res.status}`);
    return res.json();
  }

  @Public()
  @Get('manifest')
  getManifest(@Res({ passthrough: true }) res: Response): unknown {
    res.setHeader('Cache-Control', 'public, max-age=60');
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    return readManifestBody();
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
      installId?: string;
      ipcSecret?: string;
    },
  ) {
    if (!body.code?.trim() || !body.nodeId?.trim()) {
      throw new BadRequestException('code and nodeId required');
    }
    return this.nodes.pair(body);
  }

  // ─── Phase 2 — device-code (RFC 8628) first-run flow ──────────────────────

  /**
   * Start a device-authorization grant. The Founder Node tray calls this on
   * first run (no node-config.json present). Returns the RFC 8628 shape:
   *   { deviceCode, userCode, verificationUri, verificationUriComplete,
   *     expiresAt, interval }
   * The tray displays the userCode and polls /device-code/poll with deviceCode.
   *
   * Public endpoint per RFC 8628 §3.1 — the device isn't authenticated yet
   * (that's the point of the flow). The userId is bound to the grant only
   * when the founder authorizes in the browser via /device-code/authorize.
   *
   * The installId is stashed on the grant row so authorize() can pair a
   * fresh node with the caller's installId in one shot. The founderId /
   * nodeId / nodeToken are minted at authorize time, not here.
   */
  @Public()
  @Post('device-code')
  createDeviceCode(
    @Body() body?: { installId?: string; ipcSecret?: string },
  ) {
    // Anonymous grant: userId stays null on the row until the founder
    // authorizes in the browser. ipcSecret (if provided) is hashed and
    // stored on the eventual FounderNode at authorize time — not on the
    // grant, because the grant is single-use and short-lived.
    return this.nodes.createDeviceCode({
      installId: body?.installId,
    });
  }

  /**
   * Poll a device-authorization grant. The Founder Node tray calls this every
   * `interval` seconds with the deviceCode until status === 'authorized'.
   *
   * RFC 8628 §3.5 status mapping:
   *   - pending     → HTTP 202 + Retry-After: <interval>
   *   - slow_down   → HTTP 429 + Retry-After: <interval + 5>
   *   - authorized  → HTTP 200 + token body
   *   - expired     → HTTP 400 + { status, error }
   *   - denied      → HTTP 403 + { status, error }
   */
  @Public()
  @Post('device-code/poll')
  async pollDeviceCode(
    @Body() body: { deviceCode: string },
    @Res({ passthrough: true }) res: Response,
  ) {
    if (!body.deviceCode?.trim()) {
      throw new BadRequestException('deviceCode required');
    }
    const result = await this.nodes.pollDeviceCode(body.deviceCode.trim());
    if (result.status === 'pending') {
      res.status(202);
      res.setHeader('Retry-After', String(FounderNodeService.DEVICE_CODE_INTERVAL_S));
    } else if (result.status === 'slow_down') {
      // RFC 8628 §3.5: slow_down requires the client to increase its polling
      // interval by 5 seconds. Retry-After carries the new (larger) interval.
      res.status(429);
      res.setHeader('Retry-After', String(FounderNodeService.DEVICE_CODE_INTERVAL_S + 5));
    } else if (result.status === 'expired') {
      res.status(400);
    } else if (result.status === 'denied') {
      res.status(403);
    }
    // authorized falls through to default 200.
    return result;
  }

  /**
   * Web app calls this when the founder clicks "Authorize" on the verification
   * page. Requires a logged-in Founder OS user (CurrentUser). Pairs a fresh
   * node, flips the grant to 'authorized', stashes the tokens for the next
   * /device-code/poll to return.
   *
   * The web UI (apps/web/) is Worker 1's territory if it lives there; this is
   * just the API. The web page calls POST /founder-node/device-code/authorize
   * with { userCode, label }. The API mints nodeId when older clients do not
   * supply one, keeping the browser flow free of local secrets.
   */
  @Post('device-code/authorize')
  authorizeDeviceCode(
    @CurrentUser() user: AuthUser,
    @Body() body: { userCode: string; nodeId?: string; label?: string; platform?: string; appVersion?: string },
  ) {
    if (!body.userCode?.trim()) {
      throw new BadRequestException('userCode required');
    }
    return this.nodes.authorizeDeviceCode(user.id, body.userCode, {
      nodeId: body.nodeId,
      label: body.label ?? 'Founder IDE',
      platform: body.platform,
      appVersion: body.appVersion,
    });
  }

  /** Web app calls this when the founder clicks "Deny". */
  @Post('device-code/deny')
  denyDeviceCode(
    @CurrentUser() user: AuthUser,
    @Body() body: { userCode: string },
  ) {
    if (!body.userCode?.trim()) {
      throw new BadRequestException('userCode required');
    }
    return this.nodes.denyDeviceCode(user.id, body.userCode);
  }

  // ─── Phase 2 — token lifecycle: rotate / revoke / logout ──────────────────

  /**
   * Issue a new nodeToken, invalidate the old one. Called by Founder Node
   * proactively when within 7 days of expiry, or by the user via "Rotate
   * token" in the tray. Requires the current valid token.
   */
  @UseGuards(FounderNodeGuard)
  @Post('rotate-token')
  rotateToken(@Req() req: { founderNode: FounderNodeRequestUser }) {
    return this.nodes.rotateToken(req.founderNode.nodeId);
  }

  @UseGuards(FounderNodeGuard)
  @Get('ide-entitlements')
  async ideEntitlements(
    @Req() req: { founderNode: FounderNodeRequestUser },
  ): Promise<FounderIdeEntitlements> {
    const status = await this.founderPromo.getUserPromoStatus(
      req.founderNode.userId,
    );
    return toFounderIdeEntitlements(status);
  }

  @UseGuards(FounderNodeGuard)
  @Post('coordination/tasks')
  startCoordinationTask(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Body() body: StartFounderTaskInput,
  ) {
    return this.coordination.startTask(req.founderNode.userId, body);
  }

  @UseGuards(FounderNodeGuard)
  @Get('coordination/tasks')
  listCoordinationTasks(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Query('workspaceKey') workspaceKey: string,
  ) {
    return this.coordination.listTasks(req.founderNode.userId, workspaceKey);
  }

  @UseGuards(FounderNodeGuard)
  @Post('coordination/tasks/:taskId/heartbeat')
  heartbeatCoordinationTask(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Param('taskId') taskId: string,
    @Body() body: { status?: 'ACTIVE' | 'WAITING' },
  ) {
    return this.coordination.heartbeat(
      req.founderNode.userId,
      taskId,
      body?.status ?? 'ACTIVE',
    );
  }

  @UseGuards(FounderNodeGuard)
  @Post('coordination/tasks/:taskId/claims')
  claimCoordinationPath(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Param('taskId') taskId: string,
    @Body() body: ClaimFounderPathInput,
  ) {
    return this.coordination.claimPath(req.founderNode.userId, taskId, body);
  }

  @UseGuards(FounderNodeGuard)
  @Delete('coordination/tasks/:taskId/claims')
  releaseCoordinationPath(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Param('taskId') taskId: string,
    @Query('path') claimedPath: string,
  ) {
    return this.coordination.releasePath(req.founderNode.userId, taskId, claimedPath);
  }

  @UseGuards(FounderNodeGuard)
  @Patch('coordination/tasks/:taskId/finish')
  finishCoordinationTask(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Param('taskId') taskId: string,
    @Body() body: { status?: 'COMPLETE' | 'CANCELED' },
  ) {
    return this.coordination.finishTask(
      req.founderNode.userId,
      taskId,
      body?.status ?? 'COMPLETE',
    );
  }

  /**
   * Revoke a node identity — server-side, permanent. Called by the user via
   * "Revoke this node" in Founder OS settings. The DELETE :nodeId endpoint
   * below already does this; this is the explicit /revoke alias for clarity
   * and so the tray can call POST /revoke (some corporate proxies block
   * DELETE bodies).
   */
  @Post('revoke')
  revokeAlias(@CurrentUser() user: AuthUser, @Body() body: { nodeId: string }) {
    if (!body.nodeId?.trim()) {
      throw new BadRequestException('nodeId required');
    }
    return this.nodes.revokeNode(user.id, body.nodeId);
  }

  /**
   * Logout — local-only invalidation. The server-side identity stays revocable
   * separately. The tray calls this when the user clicks "Sign out"; the API
   * records the timestamp so the status panel shows "logged out" rather than
   * "revoked". The actual node-config.json deletion happens client-side.
   */
  @UseGuards(FounderNodeGuard)
  @Post('logout')
  logout(@Req() req: { founderNode: FounderNodeRequestUser }) {
    return this.nodes.logout(req.founderNode.nodeId);
  }

  @UseGuards(FounderNodeGuard)
  @Post('heartbeat')
  heartbeat(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Body() body: FounderNodeHeartbeat,
  ) {
    return this.nodes.heartbeat(req.founderNode.nodeDbId, body);
  }

  /**
   * Phase 3 — IDE↔node named-pipe IPC handshake state report. The Founder
   * Node tray POSTs to this endpoint when its IPC client emits 'connected'
   * or 'disconnected'. The API keeps the latest state in memory so the
   * FounderIdeAdapter can decide isConnected() in real time.
   */
  @UseGuards(FounderNodeGuard)
  @Post('ide-handshake')
  reportIdeHandshake(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Body() body: { active: boolean },
  ) {
    this.nodes.setIdeHandshakeState(req.founderNode.nodeId, Boolean(body?.active));
    return { ok: true, active: Boolean(body?.active) };
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

  /**
   * Batched token-usage reports for local inference run on a paired Founder
   * Node (Ollama / BYO local model). Each entry is persisted as an
   * `AiTokenUsageLog` row so it counts toward the platform adoption chart.
   * Authenticated via FounderNodeGuard (nodeToken). Best-effort: the endpoint
   * always returns 200 even if some entries fail to record, so the node can
   * clear its buffer without retry storms.
   */
  @UseGuards(FounderNodeGuard)
  @Post('inference-usage')
  reportInferenceUsage(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Body()
    body: {
      entries?: Array<{
        promptTokens: number;
        completionTokens: number;
        provider?: string;
        model?: string;
        source?: string;
        billingSource?: string;
        projectId?: string | null;
      }>;
    },
  ) {
    const entries = Array.isArray(body?.entries) ? body.entries : [];
    return this.inference.recordUsageBatch(
      req.founderNode.userId,
      entries.map((e) => ({
        promptTokens: Number(e?.promptTokens ?? 0),
        completionTokens: Number(e?.completionTokens ?? 0),
        provider: e?.provider ?? 'ollama',
        model: e?.model,
        source: e?.source,
        billingSource: e?.billingSource,
        projectId: e?.projectId ?? null,
      })),
    );
  }

  @UseGuards(FounderNodeGuard)
  @Get('sync-jobs/pending')
  async pendingSyncJob(@Req() req: { founderNode: FounderNodeRequestUser }) {
    // Explicit JSON shape so the response body is always parseable. Returning
    // null from a NestJS controller serializes as a 200 with an EMPTY body, not
    // the JSON literal `null`, which made the Founder Node client's res.json()
    // throw "Unexpected end of JSON input" on every 1.5s poll. `{ status: 'idle' }`
    // has no `id`, so the client's existing `if (!body?.id || !body.kind)` check
    // treats it as "no job" — fully backwards-compatible.
    const job = await this.syncJobs.claimPending(req.founderNode.nodeId);
    return job ?? { status: 'idle' as const };
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

  /**
   * Founder Node polls this on each sync cycle to pick up prompts the user
   * typed into the Founder OS sidebar while a Cursor chat session was
   * selected. Each returned dispatch should be opened in the local Cursor
   * IDE and then reported via /dispatch/:id/complete.
   */
  @UseGuards(FounderNodeGuard)
  @Get('pending-dispatches')
  pendingDispatches(@Req() req: { founderNode: FounderNodeRequestUser }) {
    return this.ideBridge.getPendingDispatches(
      req.founderNode.userId,
      req.founderNode.nodeId,
    );
  }

  /** Claim one pending dispatch before executing — prevents double paste races. */
  @UseGuards(FounderNodeGuard)
  @Post('dispatch/:id/claim')
  claimDispatch(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Param('id') id: string,
  ) {
    return this.ideBridge.claimDispatch(
      req.founderNode.userId,
      id,
      req.founderNode.nodeId,
    );
  }

  /**
   * Founder Node calls this after it has typed the prompt into Cursor (or
   * given up). Atomically flips the dispatch row PENDING → DISPATCHED and
   * records a short result string.
   */
  @UseGuards(FounderNodeGuard)
  @Post('dispatch/:id/complete')
  completeDispatch(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Param('id') id: string,
    @Body() body: { result?: string; error?: string },
  ) {
    const result = body.error ? `error: ${body.error}` : body.result;
    return this.ideBridge.markDispatched(
      id,
      result,
      req.founderNode.userId,
      req.founderNode.nodeId,
    );
  }
}
