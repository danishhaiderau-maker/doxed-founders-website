import { Injectable, NotImplementedException } from '@nestjs/common';
import type {
  CommandResult,
  EditOutcome,
  ExecutionAdapter,
  FileEdit,
  RunCommandOpts,
  WorkspaceNode,
} from '../execution-manager.types';

/**
 * CursorAdapter — STUB. The `cursor` execution target.
 *
 * Placeholder so the kernel can register a Cursor target, surface it
 * in /api/execution-manager/health, and route actions to it — while
 * making it loud and clear that the transport isn't wired yet. The
 * real implementation lands in Phase 5+, once Founder Node IPC is in
 * place (docs/KERNEL.md §10, "Execution Engine 🚧 Phase 3 → Cursor
 * adapter first" but Founder Node IPC is the gating dependency).
 *
 * connect() resolves immediately and flips a flag so health checks
 * report Cursor as a connected target. Every other method throws
 * NotImplementedException with a Phase 5+ pointer.
 */
@Injectable()
export class CursorAdapter implements ExecutionAdapter {
  readonly target = 'cursor' as const;
  private connected = false;

  async connect(): Promise<void> {
    // No transport to establish yet. We flip the flag so the kernel's
    // health endpoint can honestly say "cursor target registered".
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    this.connected = false;
  }

  isConnected(): boolean {
    return this.connected;
  }

  async readWorkspace(_path?: string): Promise<WorkspaceNode[]> {
    throw this.notImplemented('readWorkspace');
  }

  async applyEdits(_edits: FileEdit[]): Promise<EditOutcome[]> {
    throw this.notImplemented('applyEdits');
  }

  async runCommand(_command: string, _opts?: RunCommandOpts): Promise<CommandResult> {
    throw this.notImplemented('runCommand');
  }

  // streamOutput() intentionally omitted — it's optional on the
  // ExecutionAdapter contract. The real Cursor adapter will add it
  // when Founder Node IPC lands (Phase 5+).

  private notImplemented(op: string): NotImplementedException {
    return new NotImplementedException(
      `Cursor execution adapter.${op}() is Phase 5+ work — ` +
        `wire through Founder Node IPC then.`,
    );
  }
}
