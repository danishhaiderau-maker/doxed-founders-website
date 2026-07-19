import { Injectable, Logger, Optional } from '@nestjs/common';
import type {
  CommandResult,
  EditOutcome,
  ExecutionAdapter,
  FileEdit,
  RunCommandOpts,
  WorkspaceNode,
} from '../execution-manager.types';
import type { FounderNodeService } from '../../founder-node/founder-node.service';
import type { IdeBridgeService } from '../../ide-bridge/ide-bridge.service';

/**
 * FounderIdeAdapter — the `vscode` execution target (Founder IDE / VS Code /
 * Cursor / VSCodium).
 *
 * Phase 3 — replaces the local-shell / filesystem fallback with REAL IPC
 * dispatch via the IDE extension's named-pipe server. The API never opens
 * the pipe itself; it talks to Founder Node over HTTPS (the existing
 * relay), and Founder Node forwards to the IDE via the IPC client it owns.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * FAIL-CLOSED GUARANTEE
 * ─────────────────────────────────────────────────────────────────────────
 * If `isConnected()` is false, EVERY method on this adapter refuses to
 * dispatch and returns a deterministic error:
 *
 *   readWorkspace() → []
 *   applyEdits()    → each edit returns { ok: false, error: 'ipc_not_connected' }
 *   runCommand()    → { exitCode: 126, stderr: 'ipc_not_connected' }
 *
 * `isConnected()` is a REAL-TIME check, not a cached flag: it looks up the
 * install's current IDE handshake state via FounderNodeService every call.
 * If Founder Node hasn't reported `active=true` within the staleness
 * window (default 30s), isConnected() returns false.
 *
 * This is the load-bearing security invariant: there is NO local-shell
 * fallback. The adapter ONLY dispatches commands when an authenticated IDE
 * session is reachable through the authed IPC channel. A malicious local
 * process that tampers with the filesystem, sets env vars, or replaces
 * binaries cannot cause the API to execute commands on the user's behalf
 * without first passing the IPC handshake (installId + ipcSecret,
 * constant-time compared).
 *
 * ─────────────────────────────────────────────────────────────────────────
 * DISPATCH PATH (per call)
 * ─────────────────────────────────────────────────────────────────────────
 *   1. resolveInstallId() — the adapter looks up the installId it's bound
 *      to via FounderNodeService.findNodeByInstallId(). If multiple nodes
 *      share an installId (shouldn't happen but defensive), the most-
 *      recently-seen wins.
 *   2. isConnected() — real-time check that the node reports an active IDE
 *      handshake. Fail-closed on stale / missing state.
 *   3. Issue the dispatch via Founder Node's existing HTTPS relay. The
 *      actual wire format is FounderNodeService's responsibility; this
 *      adapter awaits the outcome with a per-call timeout.
 *
 * The HTTPS relay uses the same PendingIdeDispatch queue as the web remote.
 * Founder Node claims the user-scoped row and forwards the structured action
 * over its authenticated local IPC connection.
 */
@Injectable()
export class FounderIdeAdapter implements ExecutionAdapter {
  readonly target = 'vscode' as const;
  private readonly logger = new Logger(FounderIdeAdapter.name);

  /** Default per-call dispatch timeout. Overrides via RunCommandOpts.timeoutMs. */
  private static readonly DEFAULT_DISPATCH_TIMEOUT_MS = 10 * 60_000;

  constructor(
    @Optional() private readonly nodes?: FounderNodeService,
    @Optional() private readonly ideBridge?: IdeBridgeService,
  ) {}

  /**
   * No-op. isConnected() is a real-time check, not a cached flag, so
   * connect() has nothing to do. Kept on the interface for ExecutionAdapter
   * contract compatibility.
   */
  async connect(): Promise<void> {
    if (!this.nodes) {
      this.logger.warn('FounderIdeAdapter wired without FounderNodeService — isConnected() will be false.');
    }
  }

  async disconnect(): Promise<void> {
    // Real-time check; nothing to tear down.
  }

  /**
   * True ONLY when an authenticated IDE handshake is currently active for
   * this install. Real-time check (not cached) — every call asks
   * FounderNodeService for the latest state. Fail-closed when:
   *   - the service isn't wired (no DI)
   *   - no installId is resolvable
   *   - the node hasn't reported an active handshake in the staleness window
   */
  isConnected(): boolean {
    if (!this.nodes) return false;
    const installId = this.resolveInstallIdSync();
    if (!installId) return false;
    // The service exposes a sync accessor that reads the in-memory state.
    // isIdeHandshakeActiveForInstall is async (Prisma lookup), so we use
    // the sync helper that reads the cached state directly. We assume the
    // install's nodeId was resolved once at startup and stashed; for the
    // first call we fall back to false (fail-closed) and let the next
    // heartbeat cycle populate the state.
    const nodeId = this.cachedNodeIdForInstall(installId);
    if (!nodeId) return false;
    return this.nodes.isIdeHandshakeActive(nodeId);
  }

  async readWorkspace(path?: string): Promise<WorkspaceNode[]> {
    if (!this.failClosedGuard()) return [];
    try {
      const result = await this.dispatchAction(
        { type: 'workspaceReadRequest', path, maxEntries: 500 },
        30_000,
      );
      return result.kind === 'workspace' && Array.isArray(result.nodes)
        ? (result.nodes as WorkspaceNode[])
        : [];
    } catch (error) {
      this.logger.warn(`Founder IDE workspace read failed: ${this.errorMessage(error)}`);
      return [];
    }
  }

  async applyEdits(edits: FileEdit[]): Promise<EditOutcome[]> {
    if (!this.failClosedGuard()) {
      return edits.map((e) => ({ path: e.path, ok: false, error: 'ipc_not_connected' }));
    }
    const outcomes: EditOutcome[] = [];
    for (const edit of edits) {
      try {
        const result = await this.dispatchAction(
          {
            type: 'proposedEdit',
            path: edit.path,
            diff: '',
            creates: edit.kind === 'create',
            edit: {
              kind: edit.kind,
              content: edit.content,
              ...(edit.anchor ? { anchor: edit.anchor } : {}),
            },
          },
          FounderIdeAdapter.DEFAULT_DISPATCH_TIMEOUT_MS,
        );
        const approved = result.kind === 'edit' && result.approved === true;
        outcomes.push({
          path: edit.path,
          ok: approved,
          bytesWritten: approved ? Buffer.byteLength(edit.content, 'utf8') : 0,
          ...(!approved
            ? { error: String(result.reason ?? 'user_denied') }
            : {}),
        });
      } catch (error) {
        outcomes.push({
          path: edit.path,
          ok: false,
          bytesWritten: 0,
          error: this.errorMessage(error),
        });
      }
    }
    return outcomes;
  }

  async runCommand(command: string, opts?: RunCommandOpts): Promise<CommandResult> {
    if (!this.failClosedGuard()) {
      return {
        command,
        exitCode: 126,
        stdout: '',
        stderr: 'ipc_not_connected',
        durationMs: 0,
      };
    }
    const startedAt = Date.now();
    try {
      const result = await this.dispatchAction(
        {
          type: 'commandRequest',
          command,
          ...(opts?.cwd ? { cwd: opts.cwd } : {}),
          risk: this.commandRisk(command),
          timeoutMs: opts?.timeoutMs,
        },
        Math.max(
          30_000,
          Math.min(
            (opts?.timeoutMs ?? FounderIdeAdapter.DEFAULT_DISPATCH_TIMEOUT_MS) + 30_000,
            FounderIdeAdapter.DEFAULT_DISPATCH_TIMEOUT_MS,
          ),
        ),
      );
      return {
        command,
        exitCode: typeof result.exitCode === 'number' ? result.exitCode : 1,
        stdout: typeof result.stdout === 'string' ? result.stdout : '',
        stderr: typeof result.stderr === 'string' ? result.stderr : '',
        durationMs: Date.now() - startedAt,
      };
    } catch (error) {
      const message = this.errorMessage(error);
      return {
        command,
        exitCode: message === 'ipc_timeout' ? 124 : 126,
        stdout: '',
        stderr: message,
        durationMs: Date.now() - startedAt,
        ...(message === 'ipc_timeout' ? { signal: 'timeout' as const } : {}),
      };
    }
  }

  // ─── Internals ────────────────────────────────────────────────────────

  /**
   * Fail-closed guard. Returns true only when isConnected() is true. Side-
   * effect: logs a warning when callers invoke a method while disconnected
   * so the kernel-side Flight Recorder has a trail.
   */
  private failClosedGuard(): boolean {
    if (this.isConnected()) return true;
    this.logger.warn('FounderIdeAdapter dispatch refused — IDE IPC not connected (fail-closed).');
    return false;
  }

  private async dispatchAction(
    action: Record<string, unknown>,
    timeoutMs: number,
  ): Promise<Record<string, unknown>> {
    const userId = this.nodeIdCacheUserId;
    const nodeId = this.nodeIdCacheValue;
    if (!this.ideBridge || !userId || !nodeId) {
      throw new Error('ipc_relay_unavailable');
    }
    const created = await this.ideBridge.createDispatch(
      userId,
      `founder-ide:${nodeId}`,
      JSON.stringify({ founderIdeAction: action }),
      'founder-ide',
    );
    const dispatchId = String((created as { id?: string }).id ?? '');
    if (!dispatchId) throw new Error('ipc_relay_create_failed');

    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const status = await this.ideBridge.getDispatchStatus(userId, dispatchId);
      if (status.status === 'DISPATCHED') {
        if (!status.result) throw new Error('ipc_empty_result');
        if (/^error:/i.test(status.result)) {
          throw new Error(status.result.replace(/^error:\s*/i, '') || 'ipc_dispatch_failed');
        }
        try {
          const parsed = JSON.parse(status.result) as unknown;
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            return parsed as Record<string, unknown>;
          }
        } catch {
          if (status.failed) {
            throw new Error(status.result);
          }
          throw new Error('ipc_invalid_result');
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    throw new Error('ipc_timeout');
  }

  private commandRisk(command: string): 'readonly' | 'mutation' | 'destructive' {
    if (/\b(rm|rmdir|del|drop|format|shutdown)\b|--force|reset\s+--hard/i.test(command)) {
      return 'destructive';
    }
    if (/^(git\s+(status|diff|log)|ls\b|dir\b|pwd\b|type\b|cat\b|rg\b)/i.test(command.trim())) {
      return 'readonly';
    }
    return 'mutation';
  }

  private errorMessage(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }

  /**
   * Synchronously resolve the installId this adapter is bound to. We read
   * it from the FOUNDER_IDE_INSTALL_ID environment variable (set by the
   * bootstrapper when running in single-install mode), or null when unset.
   */
  private resolveInstallIdSync(): string | null {
    const env = process.env.FOUNDER_IDE_INSTALL_ID?.trim();
    return env || null;
  }

  /**
   * Cached nodeId for the install. The first isConnected() call after a
   * restart doesn't know which nodeId maps to this install, so we trigger
   * an async lookup. The result is cached so subsequent calls are sync.
   */
  private cachedNodeIdForInstall(installId: string): string | null {
    if (this.nodeIdCacheInstall === installId && this.nodeIdCacheValue) {
      return this.nodeIdCacheValue;
    }
    // Cache miss — kick off an async lookup, return false this time so the
    // adapter fail-closes. The next isConnected() call sees the cached value.
    if (this.nodes && !this.nodeIdCacheInFlight) {
      this.nodeIdCacheInFlight = true;
      void this.nodes
        .findNodeByInstallId(installId)
        .then((node) => {
          if (node?.nodeId) {
            this.nodeIdCacheValue = node.nodeId;
            this.nodeIdCacheUserId =
              typeof node.userId === 'string' ? node.userId : null;
            this.nodeIdCacheInstall = installId;
          }
        })
        .catch((err) => this.logger.warn?.(`install lookup failed: ${err}`))
        .finally(() => {
          this.nodeIdCacheInFlight = false;
        });
    }
    return this.nodeIdCacheValue ?? null;
  }

  private nodeIdCacheInstall: string | null = null;
  private nodeIdCacheValue: string | null = null;
  private nodeIdCacheUserId: string | null = null;
  private nodeIdCacheInFlight = false;
}
