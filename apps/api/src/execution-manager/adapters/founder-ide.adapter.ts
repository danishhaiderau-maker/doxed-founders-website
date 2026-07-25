import { Injectable, Logger } from '@nestjs/common';
import type {
  CommandResult,
  EditOutcome,
  ExecutionAdapter,
  FileEdit,
  RunCommandOpts,
  WorkspaceNode,
} from '../execution-manager.types';

/**
 * Process-global registration for the Founder IDE execution target.
 *
 * A remote IDE action must be scoped to an authenticated founder and the
 * exact paired computer that advertised the selected session. That context
 * is available only through IdeBridgeService's durable queue, not through
 * this global adapter contract. The adapter therefore remains visibly
 * disconnected and fails closed instead of executing on the API host.
 */
@Injectable()
export class FounderIdeAdapter implements ExecutionAdapter {
  readonly target = 'vscode' as const;
  private readonly logger = new Logger(FounderIdeAdapter.name);

  async connect(): Promise<void> {
    this.logger.log(
      'Founder IDE remote execution uses the authenticated user-scoped IDE bridge.',
    );
  }

  async disconnect(): Promise<void> {
    // Stateless. The user-scoped relay owns its own lifecycle.
  }

  isConnected(): boolean {
    return false;
  }

  async readWorkspace(_path?: string): Promise<WorkspaceNode[]> {
    this.refuseProcessGlobalDispatch();
    return [];
  }

  async applyEdits(edits: FileEdit[]): Promise<EditOutcome[]> {
    this.refuseProcessGlobalDispatch();
    return edits.map((edit) => ({
      path: edit.path,
      ok: false,
      error: 'user_scoped_ide_bridge_required',
    }));
  }

  async runCommand(
    command: string,
    _opts?: RunCommandOpts,
  ): Promise<CommandResult> {
    this.refuseProcessGlobalDispatch();
    return {
      command,
      exitCode: 126,
      stdout: '',
      stderr: 'user_scoped_ide_bridge_required',
      durationMs: 0,
    };
  }

  private refuseProcessGlobalDispatch(): void {
    this.logger.warn(
      'Refused process-global Founder IDE action; use the authenticated user-scoped IDE bridge.',
    );
  }
}
