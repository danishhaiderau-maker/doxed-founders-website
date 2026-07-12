import { Injectable, Logger, Optional } from '@nestjs/common';
import type {
  CommandResult,
  EditOutcome,
  ExecutionAdapter,
  FileEdit,
  RunCommandOpts,
  WorkspaceNode,
} from '../execution-manager.types';
import { FilesystemAdapter } from './filesystem.adapter';
import { LocalShellAdapter } from './local-shell.adapter';

/**
 * CursorAdapter — IDE execution target with a real local fallback path.
 *
 * Full Cursor / Founder Node IPC (remote agent dispatch into a live Cursor
 * session) is still Phase 5+ transport work. Until that lands, this adapter
 * does **not** throw NotImplementedException on every call — it delegates
 * to the LocalShell + Filesystem adapters that already work on the kernel
 * host (and on Founder Node when the API runs co-located with the vault).
 *
 * Health therefore reports `cursor` as connected when the local path is
 * live, and actions succeed for shell / file-read / file-write graphs.
 * Callers that need true Cursor Agent IPC should check
 * `usesRemoteIpc()` once that flag flips true.
 */
@Injectable()
export class CursorAdapter implements ExecutionAdapter {
  readonly target = 'cursor' as const;
  private readonly logger = new Logger(CursorAdapter.name);
  private connected = false;

  constructor(
    @Optional() private readonly shell?: LocalShellAdapter,
    @Optional() private readonly filesystem?: FilesystemAdapter,
  ) {}

  /** True only when real Cursor/Founder-Node IPC is wired (not yet). */
  usesRemoteIpc(): boolean {
    return false;
  }

  async connect(): Promise<void> {
    this.connected = Boolean(this.shell || this.filesystem);
    if (!this.connected) {
      this.logger.warn(
        'CursorAdapter connected without local shell/filesystem delegates — actions will fail until adapters are wired.',
      );
    } else {
      this.logger.log(
        'CursorAdapter using local shell/filesystem path (Founder Node IPC pending).',
      );
    }
  }

  async disconnect(): Promise<void> {
    this.connected = false;
  }

  isConnected(): boolean {
    return this.connected;
  }

  async readWorkspace(path?: string): Promise<WorkspaceNode[]> {
    if (this.filesystem) return this.filesystem.readWorkspace(path);
    if (this.shell) return this.shell.readWorkspace(path);
    return this.fail('readWorkspace');
  }

  async applyEdits(edits: FileEdit[]): Promise<EditOutcome[]> {
    if (this.filesystem) return this.filesystem.applyEdits(edits);
    if (this.shell) return this.shell.applyEdits(edits);
    return edits.map((e) => ({
      path: e.path,
      ok: false,
      error: 'CursorAdapter has no filesystem delegate',
    }));
  }

  async runCommand(command: string, opts?: RunCommandOpts): Promise<CommandResult> {
    if (this.shell) return this.shell.runCommand(command, opts);
    return {
      command,
      exitCode: 126,
      stdout: '',
      stderr:
        'CursorAdapter has no shell delegate — register LocalShellAdapter or wait for Founder Node IPC.',
      durationMs: 0,
    };
  }

  private fail(op: string): never {
    throw new Error(
      `CursorAdapter.${op}() unavailable — no local shell/filesystem delegate registered.`,
    );
  }
}
