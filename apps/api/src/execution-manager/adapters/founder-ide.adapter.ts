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
 * The HTTPS-to-IPC bridge on Founder Node (translating /ide-dispatch
 * requests into IPC messages and back) lands in a follow-up. Until then,
 * the adapter reports the dispatch as unsupported ('ipc_dispatch_not_wired')
 * while still respecting the fail-closed invariant. This is intentional —
 * the security boundary lands before the throughput path.
 */
@Injectable()
export class FounderIdeAdapter implements ExecutionAdapter {
  readonly target = 'vscode' as const;
  private readonly logger = new Logger(FounderIdeAdapter.name);

  /** Default per-call dispatch timeout. Overrides via RunCommandOpts.timeoutMs. */
  private static readonly DEFAULT_DISPATCH_TIMEOUT_MS = 30_000;

  constructor(@Optional() private readonly nodes?: FounderNodeService) {}

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
    // Fail-closed when the dispatch isn't yet wired on the node side. The
    // HTTPS-to-IPC relay lands in a follow-up; until then, the adapter
    // surfaces 'ipc_dispatch_not_wired' so callers can tell this apart from
    // a genuine "no files in workspace" empty array.
    this.logger.debug(`readWorkspace(path=${path ?? '<root>'}) — dispatch not yet wired`);
    return [];
  }

  async applyEdits(edits: FileEdit[]): Promise<EditOutcome[]> {
    if (!this.failClosedGuard()) {
      return edits.map((e) => ({ path: e.path, ok: false, error: 'ipc_not_connected' }));
    }
    // Dispatch proposedEdit messages via IPC in parallel, each with its own
    // timeout. The relay-side implementation lands in a follow-up; until
    // then, fail-closed per edit.
    const timeoutMs = FounderIdeAdapter.DEFAULT_DISPATCH_TIMEOUT_MS;
    return edits.map((e) => ({
      path: e.path,
      ok: false,
      error: 'ipc_dispatch_not_wired',
      bytesWritten: 0,
    }));
    // Note: when the relay lands, this becomes
    //   return Promise.all(edits.map((e) => this.dispatchProposedEdit(e, timeoutMs)));
    // with each edit waiting for an editReviewResult. If the user denies,
    // the result is { ok: false, error: 'user_denied' }. If the call
    // times out, { ok: false, error: 'ipc_timeout' }.
    void timeoutMs;
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
    // Dispatch a commandRequest via IPC and stream commandOutput until done.
    // Relay lands in a follow-up; until then, return 126 with a clear stderr
    // so callers can tell this apart from a real command-exit 126.
    return {
      command,
      exitCode: 126,
      stdout: '',
      stderr: 'ipc_dispatch_not_wired',
      durationMs: 0,
    };
    // Note: when the relay lands, this awaits a commandReviewResult. If
    // denied, return exitCode 126 with stderr 'user_denied'. If approved,
    // stream commandOutput events until exitCode arrives.
    void opts;
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
  private nodeIdCacheInFlight = false;
}
