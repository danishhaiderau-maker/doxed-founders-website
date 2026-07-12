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
 * FounderIdeAdapter — the `vscode` execution target (Founder IDE is a
 * VS Code fork). Same local-shell / filesystem path as CursorAdapter until
 * Founder Node IPC can dispatch into a live IDE session.
 *
 * Registered so `/api/execution-manager/health` lists Founder IDE / VS Code
 * as a first-class connected target alongside terminal + filesystem.
 */
@Injectable()
export class FounderIdeAdapter implements ExecutionAdapter {
  readonly target = 'vscode' as const;
  private readonly logger = new Logger(FounderIdeAdapter.name);
  private connected = false;

  constructor(
    @Optional() private readonly shell?: LocalShellAdapter,
    @Optional() private readonly filesystem?: FilesystemAdapter,
  ) {}

  async connect(): Promise<void> {
    this.connected = Boolean(this.shell || this.filesystem);
    if (this.connected) {
      this.logger.log(
        'FounderIdeAdapter using local shell/filesystem path (IDE IPC pending).',
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
    return [];
  }

  async applyEdits(edits: FileEdit[]): Promise<EditOutcome[]> {
    if (this.filesystem) return this.filesystem.applyEdits(edits);
    if (this.shell) return this.shell.applyEdits(edits);
    return edits.map((e) => ({
      path: e.path,
      ok: false,
      error: 'FounderIdeAdapter has no filesystem delegate',
    }));
  }

  async runCommand(command: string, opts?: RunCommandOpts): Promise<CommandResult> {
    if (this.shell) return this.shell.runCommand(command, opts);
    return {
      command,
      exitCode: 126,
      stdout: '',
      stderr: 'FounderIdeAdapter has no shell delegate',
      durationMs: 0,
    };
  }
}
