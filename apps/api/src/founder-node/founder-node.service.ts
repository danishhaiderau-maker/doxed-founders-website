import {
  BadRequestException,
  Inject,
  Injectable,
  Logger,
  NotFoundException,
  UnauthorizedException,
  forwardRef,
} from '@nestjs/common';
import type { DeviceMemoryPayload } from '@dcf/utils';
import { parseFounderCloudState, type BridgeSession, type BridgeWorkspace } from '@dcf/utils';
import type {
  DeviceCodeGrant,
  DeviceCodePollResponse,
  FounderNodeHeartbeat,
  FounderStackRuntimeStatus,
  LogoutResponse,
  RevokeNodeResponse,
  RotateTokenResponse,
} from '@dcf/founder-vault';
import { ComputePlaneMode, Prisma } from '@prisma/client';
import * as bcrypt from 'bcrypt';
import { randomBytes } from 'node:crypto';
import { PrismaService } from '../prisma/prisma.service';
import { FounderCopilotService } from '../events/founder-copilot.service';
import { FounderNodeVaultSyncService } from './founder-node-vault-sync.service';
import { DesktopBridgeService } from '../desktop-bridge/desktop-bridge.service';
import type { VaultMergePatch } from '@dcf/utils';

const PAIRING_TTL_MS = 30 * 60 * 1000;
const ONLINE_WINDOW_MS = 5 * 60 * 1000;

/** Phase 2 — device-code (RFC 8628) lifetimes and polling. */
const DEVICE_CODE_TTL_MS = 15 * 60 * 1000;
const DEVICE_CODE_INTERVAL_S = 5;
const DEVICE_CODE_USER_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';

/**
 * Phase 2 — node token TTL. 30 days. Founder Node auto-rotates when within
 * ROTATION_WINDOW_MS of expiry. Historically tokens never expired (legacy
 * rows have null `tokenExpiresAt`); new pairs and rotations set this. The
 * 30-day window balances "revocable identity" against "user shouldn't have
 * to re-pair every week".
 */
const NODE_TOKEN_TTL_MS = 30 * 24 * 60 * 60 * 1000;
/** Auto-rotate when within 7 days of expiry. */
const TOKEN_ROTATION_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;

/** Phase 3 — IPC secret byte length. 32 bytes = 256 bits, well above the
 *  128-bit brute-force floor. Encoded as 64 hex chars in node-config.json. */
const IPC_SECRET_BYTES = 32;

@Injectable()
export class FounderNodeService {
  private readonly logger = new Logger(FounderNodeService.name);

  constructor(
    private readonly prisma: PrismaService,
    @Inject(forwardRef(() => FounderCopilotService))
    private readonly copilot: FounderCopilotService,
    private readonly vaultSync: FounderNodeVaultSyncService,
    private readonly desktopBridge: DesktopBridgeService,
  ) {}

  async createPairingCode(userId: string, targetPlatform?: 'desktop' | 'mobile') {
    await this.prisma.founderNodePairingCode.deleteMany({
      where: { userId, usedAt: null, expiresAt: { lt: new Date() } },
    });

    const code = this.generatePairingCode();
    const expiresAt = new Date(Date.now() + PAIRING_TTL_MS);
    const normalizedTarget =
      targetPlatform === 'mobile' || targetPlatform === 'desktop' ? targetPlatform : null;

    await this.prisma.founderNodePairingCode.create({
      data: { userId, code, expiresAt, targetPlatform: normalizedTarget },
    });

    return {
      code,
      expiresAt: expiresAt.toISOString(),
      targetPlatform: normalizedTarget,
    };
  }

  async getStatus(userId: string) {
    const nodes = await this.prisma.founderNode.findMany({
      where: { userId },
      orderBy: { lastSeenAt: 'desc' },
    });

    const [bridges, workspaces, sessions] = await Promise.all([
      this.desktopBridge.listForUser(userId).catch(() => []),
      this.desktopBridge.listWorkspaces(userId).catch(() => []),
      this.desktopBridge.listSessions(userId).catch(() => []),
    ]);

    const now = Date.now();
    const providers = new Set<string>();
    for (const w of workspaces) {
      const p = (w.ideProvider ?? 'cursor').toLowerCase();
      providers.add(p === 'vscode' || p === 'founder-ide' || p === 'founderide' ? 'founder-ide' : p);
    }
    for (const s of sessions) {
      const p = (s.ideProvider ?? 'cursor').toLowerCase();
      providers.add(p === 'vscode' || p === 'founder-ide' || p === 'founderide' ? 'founder-ide' : p);
    }
    // Heartbeat bridge present ⇒ at least one desktop IDE path is live.
    const bridgeFresh = bridges.some(
      (b) => now - new Date(b.updatedAt).getTime() < ONLINE_WINDOW_MS,
    );
    if (bridgeFresh && providers.size === 0) {
      providers.add('cursor');
    }

    const connectedIdes = Array.from(providers).map((id) => ({
      id,
      label:
        id === 'founder-ide'
          ? 'Founder IDE'
          : id === 'cursor'
            ? 'Cursor'
            : id === 'claude-code'
              ? 'Claude Code'
              : id === 'openhands'
                ? 'OpenHands'
                : id,
      connected: true,
    }));

    // Always surface Founder IDE as a known target (connected when reported).
    if (!connectedIdes.some((c) => c.id === 'founder-ide')) {
      connectedIdes.push({
        id: 'founder-ide',
        label: 'Founder IDE',
        connected: false,
      });
    }
    if (!connectedIdes.some((c) => c.id === 'cursor')) {
      connectedIdes.push({
        id: 'cursor',
        label: 'Cursor',
        connected: bridgeFresh,
      });
    }

    return {
      // Map defensively so a single malformed row degrades to a placeholder
      // instead of 500-ing the whole endpoint. The original error + offending
      // row are logged so the root cause is visible in Railway logs.
      nodes: nodes.map((n) => this.toStatusRowSafe(n)),
      connectedIdes,
      bridgeOnline: bridgeFresh,
    };
  }

  /**
   * Phase 3 / Workstream C — truthful runtime status.
   *
   * Returns the canonical `FounderStackRuntimeStatus` shape (10 fields, all
   * required, no `?`). Every field is derived from a documented authoritative
   * source and obeys a staleness rule (see `STATUS_STALENESS_MS`). Consumers
   * MUST tolerate stale values — this method never throws on missing data;
   * it falls back to safe defaults so a status panel can always render.
   *
   * Field-by-field source:
   *
   *   - `installedVersion`: most-recent heartbeat's `appVersion`. Source:
   *     `FounderNode.appVersion` (set on pair, refreshed on every heartbeat).
   *     Freshness: real-time (changes only after an update + restart).
   *   - `latestVersion`: passed in by the controller from the manifest cache
   *     (60s TTL — see `founder-node.controller.ts`). Empty string if the
   *     manifest couldn't be read (status panel shows "unknown").
   *   - `founderNodeOnline`: heartbeat timestamp within `ONLINE_WINDOW_MS`
   *     (5 minutes). Source: `FounderNode.lastSeenAt`.
   *   - `ideHandshakeActive`: from the desktop-bridge payload (IDE → node IPC
   *     heartbeat). Workstream B will plumb the IPC handshake state through
   *     here; for now we derive from the latest bridge payload's session
   *     activity (updatedAt within the staleness window). Freshness: ≤15s.
   *   - `gatewayReachable`: from the latest heartbeat's `gatewayReachable`
   *     field if present (Workstream B will add a real probe); otherwise
   *     false. Freshness: ≤30s.
   *   - `paired`: at least one non-revoked node row exists for this user
   *     (paired nodes have a non-null `tokenExpiresAt` and haven't been
   *     revoked). Real-time (DB read).
   *   - `workspace`: most-recent workspace path reported by the IDE bridge,
   *     or null if no IDE has reported. Freshness: ≤15s.
   *   - `lastHeartbeat`: ISO timestamp of the most recent node `lastSeenAt`,
   *     or epoch zero if never seen.
   *   - `updateState`: hardcoded `idle` for now. Workstream E (updater) will
   *     own the state machine; the value lives in a separate `update_state`
   *     table once that lands.
   *   - `executionConsentState`: hardcoded `expired` after 5 minutes of
   *     inactivity. Workstream B (consent state machine) will wire live
   *     values; until then we err on the safe side (no execution without
   *     explicit recent consent).
   *
   * @param userId  The founder's user id (used to scope node + bridge rows).
   * @param latestVersion  Latest installable version from the manifest cache
   *     (passed in by the controller so this method stays I/O-free except
   *     for DB reads). Empty string if the manifest is unavailable.
   */
  async getRuntimeStatus(
    userId: string,
    latestVersion: string,
  ): Promise<FounderStackRuntimeStatus> {
    // Most-recently-seen node carries `installedVersion` + `lastHeartbeat`.
    const nodes = await this.prisma.founderNode.findMany({
      where: { userId, status: { not: 'revoked' } },
      orderBy: { lastSeenAt: 'desc' },
      take: 1,
    });
    const latestNode = nodes[0];

    // Bridge heartbeat + workspace — surfaces whether an IDE is currently
    // connected (IPC handshake) and which workspace is open.
    const [bridges, workspaces] = await Promise.all([
      this.desktopBridge.listForUser(userId).catch(() => []),
      this.desktopBridge.listWorkspaces(userId).catch(() => []),
    ]);
    const now = Date.now();
    const freshBridge = bridges.find(
      (b) => now - new Date(b.updatedAt).getTime() < ONLINE_WINDOW_MS,
    );
    const latestWorkspace =
      workspaces[0]?.repository ?? workspaces[0]?.title ?? null;

    const lastSeenAt = latestNode?.lastSeenAt;
    const lastSeenMs = lastSeenAt ? new Date(lastSeenAt).getTime() : 0;
    const founderNodeOnline =
      Number.isFinite(lastSeenMs) && lastSeenMs > 0 && now - lastSeenMs < ONLINE_WINDOW_MS;

    return {
      installedVersion: latestNode?.appVersion ?? '',
      latestVersion,
      founderNodeOnline,
      ideHandshakeActive: !!freshBridge,
      gatewayReachable: this.extractGatewayReachable(latestNode),
      paired: nodes.length > 0,
      workspace: latestWorkspace,
      lastHeartbeat: lastSeenAt
        ? new Date(lastSeenAt).toISOString()
        : new Date(0).toISOString(),
      updateState: 'idle',
      executionConsentState: 'expired',
    };
  }

  /**
   * Pull `gatewayReachable` off the most-recent node row. The field doesn't
   * exist on the Prisma schema yet (Workstream B will add it once the IPC
   * handshake state is wired); for now we read it defensively off the row
   * if present (some clients stuff extra fields into the heartbeat payload)
   * and fall back to false.
   */
  private extractGatewayReachable(
    node:
      | (Record<string, unknown> & { appVersion?: string | null })
      | undefined,
  ): boolean {
    if (!node) return false;
    const v = (node as Record<string, unknown>).gatewayReachable;
    return v === true || v === 'true' || v === 1;
  }

  async pair(input: {
    code: string;
    nodeId: string;
    label: string;
    platform?: string;
    appVersion?: string;
    installId?: string;
    ipcSecret?: string;
  }) {
    const normalizedCode = input.code.trim().toUpperCase();
    const row = await this.prisma.founderNodePairingCode.findUnique({
      where: { code: normalizedCode },
    });

    if (!row || row.usedAt || row.expiresAt < new Date()) {
      throw new UnauthorizedException('Invalid or expired pairing code');
    }

    const nodeToken = `fn_${randomBytes(32).toString('hex')}`;
    const secretHash = await bcrypt.hash(nodeToken, 10);
    const tokenExpiresAt = new Date(Date.now() + NODE_TOKEN_TTL_MS);

    // Phase 3 — bcrypt-hash the IPC secret before storing. Plaintext is only
    // ever held in node-config.json on the device. If the caller didn't send
    // one (legacy client), installId/ipcSecret are null and IPC is disabled
    // until re-pair.
    const ipcSecretHash = input.ipcSecret
      ? await bcrypt.hash(input.ipcSecret, 10)
      : null;

    const node = await this.prisma.founderNode.upsert({
      where: { nodeId: input.nodeId },
      create: {
        userId: row.userId,
        nodeId: input.nodeId,
        label: input.label.trim() || 'Founder Node',
        secretHash,
        status: 'online',
        lastSeenAt: new Date(),
        platform: input.platform ?? null,
        appVersion: input.appVersion ?? null,
        vaultHealthy: true,
        // Phase 2 — record founderId (= userId today) + token lifecycle.
        founderId: row.userId,
        tokenExpiresAt,
        tokenRotatedAt: new Date(),
        // Phase 3 — record per-install IPC identity.
        installId: input.installId ?? null,
        ipcSecretHash,
      },
      update: {
        userId: row.userId,
        label: input.label.trim() || 'Founder Node',
        secretHash,
        status: 'online',
        lastSeenAt: new Date(),
        platform: input.platform ?? null,
        appVersion: input.appVersion ?? null,
        vaultHealthy: true,
        // Phase 2 — refresh founderId + token lifecycle on re-pair.
        founderId: row.userId,
        tokenExpiresAt,
        tokenRotatedAt: new Date(),
        // Phase 3 — refresh installId + ipcSecret on re-pair.
        installId: input.installId ?? null,
        ipcSecretHash,
      },
    });

    await this.prisma.founderNodePairingCode.update({
      where: { id: row.id },
      data: { usedAt: new Date() },
    });

    await this.prisma.founderBuilderSettings.upsert({
      where: { userId: row.userId },
      create: { userId: row.userId, memoryStorageMode: 'FOUNDER_NODE' },
      update: { memoryStorageMode: 'FOUNDER_NODE' },
    });

    return {
      nodeToken,
      nodeId: node.nodeId,
      userId: row.userId,
      founderId: row.userId,
      tokenExpiresAt: tokenExpiresAt.toISOString(),
      installId: input.installId,
    };
  }

  async validateNodeToken(nodeId: string, nodeToken: string) {
    const node = await this.prisma.founderNode.findUnique({ where: { nodeId } });
    if (!node) throw new UnauthorizedException('Unknown Founder Node');
    const ok = await bcrypt.compare(nodeToken, node.secretHash);
    if (!ok) throw new UnauthorizedException('Invalid Founder Node token');
    return node;
  }

  // ─── Phase 2 — device-code (RFC 8628) first-run flow ──────────────────────

  /**
   * Create a device-authorization grant. Per RFC 8628 §3.1, the
   * device-authorization request itself is anonymous — the tray calls this
   * with no `userId`, and the grant is created with `userId = null`. The
   * founder's userId is stamped onto the row by `authorizeDeviceCode` when
   * they click "Authorize" in the browser.
   *
   * Returns the RFC 8628 shape: the Founder Node tray displays `userCode` +
   * `verificationUri` and polls /device-code/poll with `deviceCode` until
   * status === 'authorized'.
   *
   * The `userCode` is `ABCD-1234` (4 chars, dash, 4 chars) — readable over the
   * phone, hard to confuse (no 0/O/1/I). The `deviceCode` is 32 hex bytes.
   *
   * `userId` is optional: if the web app calls this while the founder is
   * already logged in, it's bound eagerly; otherwise it stays null until
   * authorize time.
   */
  async createDeviceCode(
    opts?: { userId?: string; installId?: string },
  ): Promise<DeviceCodeGrant> {
    // Expire any prior pending device codes for this user so only one is
    // active at a time — avoids "which code did I just generate?" confusion.
    // Skip when userId is null (anonymous flow) — there's no per-user
    // dedup possible until authorize binds the founder.
    if (opts?.userId) {
      await this.prisma.founderNodeDeviceCode.updateMany({
        where: { userId: opts.userId, status: 'pending' },
        data: { status: 'expired' },
      });
    }

    const deviceCode = randomBytes(32).toString('hex');
    const userCode = this.generateUserCode();
    const expiresAt = new Date(Date.now() + DEVICE_CODE_TTL_MS);
    const verificationUri = this.buildVerificationUri(userCode);

    await this.prisma.founderNodeDeviceCode.create({
      data: {
        // Null until the founder authorizes in the browser. Schema column is
        // nullable for exactly this reason.
        userId: opts?.userId ?? null,
        deviceCode,
        userCode,
        verificationUri,
        expiresAt,
        interval: DEVICE_CODE_INTERVAL_S,
        status: 'pending',
        // Stash the installId so when the founder authorizes, we can pair a
        // fresh node with the caller's installId + ipcSecret in one shot.
        installId: opts?.installId ?? null,
      },
    });

    return {
      deviceCode,
      userCode,
      verificationUri,
      verificationUriComplete: `${verificationUri}?user_code=${encodeURIComponent(userCode)}`,
      expiresAt: expiresAt.toISOString(),
      interval: DEVICE_CODE_INTERVAL_S,
    };
  }

  /**
   * Poll a device-authorization grant. Returns the RFC 8628 status:
   *   - pending   — founder hasn't authorized yet; client should wait `interval`
   *                 seconds and poll again.
   *   - slow_down — client is polling too fast; treat like pending but back off
   *                 by `interval + 5` seconds (RFC 8628 §3.5).
   *   - expired   — grant expired; client must start a new device-code flow.
   *   - denied    — founder explicitly denied the request.
   *   - authorized — founder authorized; response includes founderId/nodeId/
   *                 nodeToken. The grant row is single-use: we clear the
   *                 nodeToken after first successful read so a leaked deviceCode
   *                 can't be replayed.
   *
   * On each poll we opportunistically delete grants that have been expired for
   * longer than DEVICE_CODE_TTL_MS × 2 (one extra window past natural expiry).
   * This keeps the table from growing unbounded — pending grants are kept
   * until they expire naturally, and expired/denied rows are reaped shortly
   * after.
   */
  async pollDeviceCode(
    deviceCode: string,
    opts?: { now?: number },
  ): Promise<DeviceCodePollResponse> {
    // Opportunistic cleanup: sweep grants that have been expired for over
    // twice the TTL. Cheap (indexed on expiresAt + status) and bounded.
    await this.cleanupExpiredGrants();

    const row = await this.prisma.founderNodeDeviceCode.findUnique({
      where: { deviceCode },
    });
    if (!row) throw new UnauthorizedException('Unknown device code');

    const now = opts?.now ?? Date.now();

    if (row.status === 'expired' || row.expiresAt.getTime() < now) {
      // Mark expired lazily so the next sweep sees it as a reaping candidate.
      if (row.status !== 'expired') {
        await this.prisma.founderNodeDeviceCode.update({
          where: { id: row.id },
          data: { status: 'expired' },
        });
      }
      return { status: 'expired', error: 'Device code expired — start a new sign-in' };
    }
    if (row.status === 'denied') {
      return { status: 'denied', error: 'Founder denied the sign-in request' };
    }
    if (row.status === 'pending') {
      // slow_down: if the client polled again within `interval` seconds of
      // the last poll, return slow_down. RFC 8628 §3.5 requires the client
      // to back off by interval + 5s. We track lastPolledAt on the row.
      const intervalMs = row.interval * 1000;
      if (row.lastPolledAt && now - row.lastPolledAt.getTime() < intervalMs) {
        await this.prisma.founderNodeDeviceCode.update({
          where: { id: row.id },
          data: { lastPolledAt: new Date(now) },
        });
        return { status: 'slow_down', interval: row.interval + 5 };
      }
      await this.prisma.founderNodeDeviceCode.update({
        where: { id: row.id },
        data: { lastPolledAt: new Date(now) },
      });
      return { status: 'pending', interval: row.interval };
    }
    if (row.status !== 'authorized') {
      // Defensive — unknown status on the column.
      return { status: 'expired', error: `Device code in unexpected state: ${row.status}` };
    }

    // Authorized — hand over the tokens. Single-use: clear nodeToken so a
    // leaked deviceCode can't be replayed. Keep the row until natural expiry
    // so a duplicate poll returns expired (not 404) — matches RFC 8628.
    if (row.nodeToken == null || row.nodeId == null || row.founderId == null) {
      return {
        status: 'expired',
        error: 'Authorized device code has no token (already consumed) — start a new sign-in',
      };
    }

    // Capture the token into a local BEFORE the single-use clear. Prisma's
    // real update() returns a fresh object (doesn't mutate the row reference),
    // so reading row.nodeToken after the await would still work in production —
    // but capturing the local is more robust against stubs/in-memory tests that
    // mutate in place, and makes the single-use semantics obvious to readers.
    const issuedNodeToken = row.nodeToken;
    const issuedNodeId = row.nodeId;
    const issuedFounderId = row.founderId;
    const issuedTokenExpiresAt = row.tokenExpiresAt?.toISOString();
    const issuedInstallId = row.installId ?? undefined;

    await this.prisma.founderNodeDeviceCode.update({
      where: { id: row.id },
      data: { nodeToken: null },
    });

    return {
      status: 'authorized',
      founderId: issuedFounderId,
      nodeId: issuedNodeId,
      nodeToken: issuedNodeToken,
      tokenExpiresAt: issuedTokenExpiresAt,
      installId: issuedInstallId,
    };
  }

  /**
   * Delete grants that have been expired or denied for over twice the TTL.
   * Called lazily on each poll — cheap and bounded because of the index on
   * expiresAt + status. Pending grants are NEVER reaped here; only terminal
   * states that are also past their natural expiry.
   */
  async cleanupExpiredGrants(opts?: { now?: number }): Promise<number> {
    const now = opts?.now ?? Date.now();
    const cutoff = new Date(now - DEVICE_CODE_TTL_MS * 2);
    const result = await this.prisma.founderNodeDeviceCode.deleteMany({
      where: {
        status: { in: ['expired', 'denied'] },
        expiresAt: { lt: cutoff },
      },
    });
    return result.count;
  }

  /**
   * Web app calls this when the founder clicks "Authorize" on the verification
   * page. Pairs a fresh node with the grant's installId, flips the grant to
   * 'authorized', and stashes the tokens so the next poll returns them.
   *
   * This is the user-facing authorize endpoint — it requires a logged-in
   * Founder OS user (CurrentUser), NOT a nodeToken. The web UI calls it.
   */
  async authorizeDeviceCode(
    userId: string,
    userCode: string,
    opts: { nodeId: string; label: string; platform?: string; appVersion?: string },
  ): Promise<{ authorized: true; founderId: string }> {
    const normalized = userCode.trim().toUpperCase();
    const row = await this.prisma.founderNodeDeviceCode.findUnique({
      where: { userCode: normalized },
    });
    // A grant is claimable by this founder if it's anonymous (userId null,
    // the standard RFC 8628 tray flow) OR if it was pre-bound to them by the
    // web app. A grant pre-bound to a DIFFERENT user is not claimable — that
    // would let any logged-in founder hijack another's pending grant by
    // guessing the userCode.
    if (!row || (row.userId !== null && row.userId !== userId)) {
      throw new NotFoundException('Device code not found');
    }
    if (row.status === 'expired' || row.expiresAt < new Date()) {
      throw new BadRequestException('Device code expired — start a new sign-in');
    }
    if (row.status === 'authorized') {
      throw new BadRequestException('Device code already authorized');
    }
    if (row.status === 'denied') {
      throw new BadRequestException('Device code was denied — start a new sign-in');
    }

    const nodeToken = `fn_${randomBytes(32).toString('hex')}`;
    const secretHash = await bcrypt.hash(nodeToken, 10);
    const tokenExpiresAt = new Date(Date.now() + NODE_TOKEN_TTL_MS);

    const node = await this.prisma.founderNode.upsert({
      where: { nodeId: opts.nodeId },
      create: {
        userId,
        nodeId: opts.nodeId,
        label: opts.label.trim() || 'Founder Node',
        secretHash,
        status: 'online',
        lastSeenAt: new Date(),
        platform: opts.platform ?? null,
        appVersion: opts.appVersion ?? null,
        vaultHealthy: true,
        founderId: userId,
        tokenExpiresAt,
        tokenRotatedAt: new Date(),
        installId: row.installId,
      },
      update: {
        userId,
        label: opts.label.trim() || 'Founder Node',
        secretHash,
        status: 'online',
        lastSeenAt: new Date(),
        platform: opts.platform ?? null,
        appVersion: opts.appVersion ?? null,
        vaultHealthy: true,
        founderId: userId,
        tokenExpiresAt,
        tokenRotatedAt: new Date(),
        installId: row.installId,
      },
    });

    await this.prisma.founderNodeDeviceCode.update({
      where: { id: row.id },
      data: {
        status: 'authorized',
        // Bind the founder's userId to the (previously anonymous) grant.
        // This is the moment RFC 8628 §3.3 calls "device authorization grant
        // approved" — the resource owner has authenticated and consented.
        userId,
        nodeToken,
        nodeId: node.nodeId,
        founderId: userId,
        tokenExpiresAt,
      },
    });

    return { authorized: true, founderId: userId };
  }

  /**
   * Web app calls this when the founder clicks "Deny". For anonymous grants
   * (the standard RFC 8628 tray flow), any logged-in founder who presents
   * the correct userCode can deny — the userCode is the binding secret in
   * that case. For pre-bound grants, only the bound founder can deny.
   */
  async denyDeviceCode(userId: string, userCode: string): Promise<{ denied: true }> {
    const normalized = userCode.trim().toUpperCase();
    const row = await this.prisma.founderNodeDeviceCode.findUnique({
      where: { userCode: normalized },
    });
    if (!row || (row.userId !== null && row.userId !== userId)) {
      throw new NotFoundException('Device code not found');
    }
    await this.prisma.founderNodeDeviceCode.update({
      where: { id: row.id },
      data: { status: 'denied' },
    });
    return { denied: true };
  }

  // ─── Phase 2 — token lifecycle: rotate / revoke / logout ──────────────────

  /**
   * Issue a new nodeToken, invalidate the old one. Called by Founder Node
   * proactively when within TOKEN_ROTATION_WINDOW_MS of expiry, or by the
   * user via "Rotate token" in the tray. Requires the current valid token.
   */
  async rotateToken(nodeId: string): Promise<RotateTokenResponse> {
    const node = await this.prisma.founderNode.findUnique({ where: { nodeId } });
    if (!node) throw new NotFoundException('Node not found');

    const nodeToken = `fn_${randomBytes(32).toString('hex')}`;
    const secretHash = await bcrypt.hash(nodeToken, 10);
    const tokenExpiresAt = new Date(Date.now() + NODE_TOKEN_TTL_MS);
    const tokenRotatedAt = new Date();

    await this.prisma.founderNode.update({
      where: { id: node.id },
      data: { secretHash, tokenExpiresAt, tokenRotatedAt },
    });

    return {
      nodeId: node.nodeId,
      nodeToken,
      founderId: node.founderId ?? node.userId,
      tokenExpiresAt: tokenExpiresAt.toISOString(),
      tokenRotatedAt: tokenRotatedAt.toISOString(),
    };
  }

  /**
   * Invalidate the node identity entirely — server-side revocation. The node's
   * next request will 401 and the tray will prompt re-pair. Called by the user
   * via "Revoke this node" in Founder OS settings, or automatically on
   * suspected compromise. Deletes the row (cascade clears relay/sync/acks).
   *
   * Distinct from /logout: revoke is server-side and permanent; logout is
   * local-only (clears node-config.json) and leaves the server identity
   * revocable separately.
   */
  async revokeNode(userId: string, nodeId: string): Promise<RevokeNodeResponse> {
    const node = await this.prisma.founderNode.findFirst({
      where: { userId, nodeId },
    });
    if (!node) throw new NotFoundException('Node not found');
    const founderId = node.founderId ?? node.userId;
    await this.prisma.founderNodeVaultRelay.deleteMany({ where: { nodeId: node.nodeId } });
    await this.prisma.founderNodeVaultSyncAck.deleteMany({
      where: { OR: [{ nodeId: node.nodeId }, { sourceNodeId: node.nodeId }] },
    });
    await this.prisma.founderNode.delete({ where: { id: node.id } });
    return {
      nodeId: node.nodeId,
      founderId,
      revokedAt: new Date().toISOString(),
    };
  }

  /**
   * Logout — local-only invalidation. The server-side identity stays revocable
   * separately. The tray calls this when the user clicks "Sign out"; the API
   * records the timestamp so the status endpoint can show "logged out" rather
   * than "revoked". The actual node-config.json deletion happens client-side
   * (the tray can't reach the user's vault from the API).
   */
  async logout(nodeId: string): Promise<LogoutResponse> {
    const node = await this.prisma.founderNode.findUnique({ where: { nodeId } });
    if (!node) throw new NotFoundException('Node not found');
    // Mark status offline + clear lastSeenAt so the status panel shows the
    // node as "logged out" rather than "online". The row is preserved so the
    // user can re-pair the same nodeId without a code if they choose to.
    await this.prisma.founderNode.update({
      where: { id: node.id },
      data: { status: 'offline', lastSeenAt: new Date() },
    });
    return {
      nodeId: node.nodeId,
      founderId: node.founderId ?? node.userId,
      loggedOutAt: new Date().toISOString(),
      serverSideRevocable: true as const,
    };
  }

  /**
   * Phase 2 — should the node auto-rotate its token? Returns true when within
   * TOKEN_ROTATION_WINDOW_MS of tokenExpiresAt. Called by the tray on each
   * sync cycle. Legacy rows (null tokenExpiresAt) return false — they rotate
   * on next 401 instead.
   */
  shouldAutoRotate(node: { tokenExpiresAt: Date | null }): boolean {
    if (!node.tokenExpiresAt) return false;
    const msUntilExpiry = node.tokenExpiresAt.getTime() - Date.now();
    return msUntilExpiry <= TOKEN_ROTATION_WINDOW_MS;
  }

  /** Phase 3 — look up a node by installId (for IPC pipe resolution). */
  async findNodeByInstallId(installId: string) {
    return this.prisma.founderNode.findFirst({ where: { installId } });
  }

  /** Phase 3 — verify the IPC secret matches the stored hash. */
  async verifyIpcSecret(nodeId: string, ipcSecret: string): Promise<boolean> {
    const node = await this.prisma.founderNode.findUnique({ where: { nodeId } });
    if (!node?.ipcSecretHash) return false;
    return bcrypt.compare(ipcSecret, node.ipcSecretHash);
  }

  /** Expose the TTL constants so the tray + tests can reference them. */
  static readonly NODE_TOKEN_TTL_MS = NODE_TOKEN_TTL_MS;
  static readonly TOKEN_ROTATION_WINDOW_MS = TOKEN_ROTATION_WINDOW_MS;
  static readonly DEVICE_CODE_TTL_MS = DEVICE_CODE_TTL_MS;
  static readonly DEVICE_CODE_INTERVAL_S = DEVICE_CODE_INTERVAL_S;

  private generateUserCode(): string {
    // ABCD-1234 — 4 chars, dash, 4 chars. No 0/O/1/I to avoid phone confusion.
    const part = (): string => {
      let s = '';
      for (let i = 0; i < 4; i += 1) {
        s += DEVICE_CODE_USER_CODE_ALPHABET[randomBytes(1)[0]! % DEVICE_CODE_USER_CODE_ALPHABET.length];
      }
      return s;
    };
    return `${part()}-${part()}`;
  }

  private buildVerificationUri(userCode: string): string {
    const base = process.env.FOUNDER_OS_WEB_URL?.replace(/\/$/, '') ?? 'https://doxxedcrypto.digital';
    return `${base}/founder-id/authorize?user_code=${encodeURIComponent(userCode)}`;
  }

  async heartbeat(nodeDbId: string, input: FounderNodeHeartbeat) {
    const node = await this.prisma.founderNode.update({
      where: { id: nodeDbId },
      data: {
        status: 'online',
        lastSeenAt: new Date(),
        label: input.label,
        ramGb: input.ramGb ?? null,
        storageGb: input.storageGb ?? null,
        storageFreeGb: input.storageFreeGb ?? null,
        vaultHealthy: input.vaultHealthy,
        platform: input.platform,
        appVersion: input.appVersion,
        ollamaEnabled: input.ollamaEnabled ?? false,
        ollamaBaseUrl: input.ollamaBaseUrl ?? null,
        ollamaModel: input.ollamaModel ?? null,
      },
    });
    void this.vaultSync.onNodeHeartbeat(node.userId, node.nodeId);
    const workspaces = (input as FounderNodeHeartbeat & {
      workspaces?: BridgeWorkspace[];
    }).workspaces;
    const sessions = (input as FounderNodeHeartbeat & {
      sessions?: BridgeSession[];
    }).sessions;
    await this.desktopBridge.saveBridgePayload(node.userId, node.nodeId, input.label, {
      bridge: input.desktopBridge,
      workspaces: Array.isArray(workspaces) ? workspaces : undefined,
      sessions: Array.isArray(sessions) ? sessions : undefined,
    });
    if (input.founderCloud) {
      void this.persistFounderCloudFromHeartbeat(node.userId, input.label, input.founderCloud);
    }
    return { success: true, status: 'online' as const };
  }

  private async persistFounderCloudFromHeartbeat(
    userId: string,
    nodeLabel: string,
    founderCloud: NonNullable<FounderNodeHeartbeat['founderCloud']>,
  ) {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { founderCloudState: true },
    });
    const state = parseFounderCloudState(settings?.founderCloudState) ?? {
      import: null,
      localStack: null,
    };
    state.localStack = {
      enabled: founderCloud.enabled,
      running: founderCloud.stackRunning,
      webUrl: founderCloud.webUrl,
      apiUrl: founderCloud.apiUrl,
      repoPath: founderCloud.repoPath,
      nodeLabel,
      updatedAt: new Date().toISOString(),
    };
    await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: {
        userId,
        founderCloudState: state as unknown as Prisma.InputJsonValue,
        computePlaneMode: founderCloud.enabled ? ComputePlaneMode.LOCAL : ComputePlaneMode.CLOUD,
      },
      update: {
        founderCloudState: state as unknown as Prisma.InputJsonValue,
        ...(founderCloud.enabled ? { computePlaneMode: ComputePlaneMode.LOCAL } : {}),
      },
    });
  }

  async syncFromNode(userId: string, nodeDbId: string, payload: DeviceMemoryPayload) {
    const node = await this.prisma.founderNode.findUnique({ where: { id: nodeDbId } });
    if (!node) throw new NotFoundException('Node not found');

    await this.prisma.founderNode.update({
      where: { id: nodeDbId },
      data: {
        status: 'online',
        lastSeenAt: new Date(),
        vaultHealthy: true,
      },
    });

    const encryptedVaultBlob =
      payload && typeof payload === 'object' && 'encryptedVaultBlob' in payload
        ? String((payload as { encryptedVaultBlob?: string }).encryptedVaultBlob ?? '').trim()
        : '';

    if (encryptedVaultBlob.length > 0) {
      await this.prisma.founderNodeVaultRelay.upsert({
        where: { nodeId: node.nodeId },
        create: {
          userId,
          nodeId: node.nodeId,
          label: node.label,
          platform: node.platform,
          encryptedVaultBlob,
        },
        update: {
          label: node.label,
          platform: node.platform,
          encryptedVaultBlob,
        },
      });
    }

    const mergePatch =
      payload && typeof payload === 'object' && 'mergePatch' in payload
        ? (payload as { mergePatch?: VaultMergePatch }).mergePatch
        : undefined;
    if (mergePatch?.version === 1) {
      await this.vaultSync.recordRelayMerge(userId, node.nodeId, {
        label: node.label,
        platform: node.platform,
        mergePatch,
        fileManifest: mergePatch.fileManifest,
      });
    }

    return this.copilot.saveDeviceMemorySync(userId, {
      ...payload,
      deviceLabel: payload.deviceLabel ?? node.label ?? 'Founder Node',
    });
  }

  async listVaultRelays(userId: string) {
    const rows = await this.prisma.founderNodeVaultRelay.findMany({
      where: { userId },
      orderBy: { updatedAt: 'desc' },
    });
    return {
      relays: rows.map((r) => ({
        nodeId: r.nodeId,
        label: r.label,
        platform: r.platform,
        updatedAt: r.updatedAt.toISOString(),
        blobBytes: r.encryptedVaultBlob.length,
        vaultSyncVersion: r.vaultSyncVersion,
        hasMergePatch: r.mergePatch != null,
      })),
    };
  }

  async pullVaultRelayForNode(userId: string, nodeId: string) {
    const row = await this.prisma.founderNodeVaultRelay.findFirst({
      where: { userId, nodeId },
    });
    if (!row) throw new NotFoundException('No vault relay for this device');
    return {
      nodeId: row.nodeId,
      label: row.label,
      platform: row.platform,
      encryptedVaultBlob: row.encryptedVaultBlob,
      updatedAt: row.updatedAt.toISOString(),
    };
  }

  private generatePairingCode(): string {
    const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    let code = '';
    for (let i = 0; i < 8; i += 1) {
      code += alphabet[randomBytes(1)[0]! % alphabet.length];
    }
    return code;
  }

  private toStatusRow(n: {
    id: string;
    nodeId: string;
    label: string;
    status: string;
    lastSeenAt: Date | null;
    ramGb: number | null;
    storageGb: number | null;
    storageFreeGb: number | null;
    vaultHealthy: boolean;
    platform: string | null;
    appVersion: string | null;
    founderId: string | null;
    tokenExpiresAt: Date | null;
    tokenRotatedAt: Date | null;
    installId: string | null;
  }) {
    const lastSeenAt = this.toIsoOrNull(n.lastSeenAt);
    const online =
      lastSeenAt != null && Date.now() - new Date(lastSeenAt).getTime() < ONLINE_WINDOW_MS;
    return {
      id: n.id,
      nodeId: n.nodeId,
      label: n.label,
      status: online ? ('online' as const) : ('offline' as const),
      lastSeenAt,
      ramGb: n.ramGb,
      storageGb: n.storageGb,
      storageFreeGb: n.storageFreeGb,
      vaultHealthy: n.vaultHealthy,
      platform: n.platform,
      appVersion: n.appVersion,
      // Phase 2 — surface founderId + token lifecycle so the status panel
      // can show "Founder ID: <id>" and "token expires in N days".
      founderId: n.founderId,
      tokenExpiresAt: this.toIsoOrNull(n.tokenExpiresAt),
      tokenRotatedAt: this.toIsoOrNull(n.tokenRotatedAt),
      // Phase 3 — surface installId so the IDE extension can detect whether
      // the node it's talking to has IPC enabled.
      installId: n.installId,
    };
  }

  /**
   * Defensive wrapper around toStatusRow. If a row contains data that makes
   * mapping throw (e.g. lastSeenAt coming back as something that is not a
   * valid Date — schema drift, raw string, NaN epoch), we log the full row
   * and emit a clearly-marked placeholder so the caller still gets a 200 with
   * the rest of the nodes intact. Without this, a single bad row made
   * GET /api/founder-node/status return 500 for the entire user.
   */
  private toStatusRowSafe(n: {
    id: string;
    nodeId: string;
    label: string;
    status: string;
    lastSeenAt: Date | null;
    ramGb: number | null;
    storageGb: number | null;
    storageFreeGb: number | null;
    vaultHealthy: boolean;
    platform: string | null;
    appVersion: string | null;
    founderId: string | null;
    tokenExpiresAt: Date | null;
    tokenRotatedAt: Date | null;
    installId: string | null;
  }) {
    try {
      return this.toStatusRow(n);
    } catch (err) {
      const lastSeenType =
        n.lastSeenAt === null
          ? 'null'
          : n.lastSeenAt instanceof Date
            ? `Date(isValid=${!Number.isNaN(n.lastSeenAt.getTime())})`
            : typeof n.lastSeenAt;
      this.logger.error(
        `toStatusRow failed for node id=${n.id} nodeId=${n.nodeId} ` +
          `label=${n.label} lastSeenAt=<${lastSeenType}> ` +
          `lastSeenAtRaw=${JSON.stringify(n.lastSeenAt)}: ` +
          `${err instanceof Error ? err.message : String(err)}`,
      );
      return {
        id: n.id,
        nodeId: n.nodeId,
        label: n.label,
        status: 'offline' as const,
        lastSeenAt: null,
        ramGb: n.ramGb,
        storageGb: n.storageGb,
        storageFreeGb: n.storageFreeGb,
        vaultHealthy: n.vaultHealthy,
        platform: n.platform,
        appVersion: n.appVersion,
        founderId: n.founderId,
        tokenExpiresAt: null,
        tokenRotatedAt: null,
        installId: n.installId,
      };
    }
  }

  /**
   * Coerce a Prisma DateTime column to ISO string or null. Handles the normal
   * Date case plus the historical failure mode where Neon returned the value
   * as a string (rare, but it produced the original 500).
   */
  private toIsoOrNull(value: Date | string | null | undefined): string | null {
    if (value == null) return null;
    if (value instanceof Date) {
      const ms = value.getTime();
      if (Number.isNaN(ms)) return null;
      return new Date(ms).toISOString();
    }
    // String fallback — parse defensively.
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
  }
}
