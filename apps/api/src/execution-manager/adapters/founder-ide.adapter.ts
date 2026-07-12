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
 * FounderIdeAdapter — the `vscode` execution target (Founder IDE is a
 * IDE target. The API process deliberately does not impersonate an IDE by
 * falling back to its own shell or filesystem. Founder Node / the IDE bridge
 * must report a verified IPC session before this target can execute work.
 *
 * Until that bridge exists, health truthfully reports this adapter offline and
 * every execution request receives a precise unavailable result.
 */
@Injectable()
export class FounderIdeAdapter implements ExecutionAdapter {
  readonly target = 'vscode' as const;
  private readonly logger = new Logger(FounderIdeAdapter.name);
  private connected = false;

  async connect(): Promise<void> {
    this.connected = false;
    this.logger.warn('Founder IDE IPC is not available; target remains offline.');
  }

  async disconnect(): Promise<void> {
    this.connected = false;
  }

  isConnected(): boolean {
    return this.connected;
  }

  async readWorkspace(path?: string): Promise<WorkspaceNode[]> {
    void path;
    return [];
  }

  async applyEdits(edits: FileEdit[]): Promise<EditOutcome[]> {
    return edits.map((e) => ({
      path: e.path,
      ok: false,
      error: 'Founder IDE is offline: no verified IPC bridge is connected',
    }));
  }

  async runCommand(command: string, opts?: RunCommandOpts): Promise<CommandResult> {
    void opts;
    return {
      command,
      exitCode: 69,
      stdout: '',
      stderr: 'Founder IDE is offline: no verified IPC bridge is connected',
      durationMs: 0,
    };
  }
}
