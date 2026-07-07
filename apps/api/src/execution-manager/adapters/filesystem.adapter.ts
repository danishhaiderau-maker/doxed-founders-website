import { Injectable } from '@nestjs/common';
import * as fs from 'node:fs/promises';
import * as pathMod from 'node:path';
import type {
  CommandResult,
  EditOutcome,
  ExecutionAdapter,
  FileEdit,
  RunCommandOpts,
  WorkspaceNode,
} from '../execution-manager.types';

/**
 * FilesystemAdapter — the `filesystem` execution target.
 *
 * The second real adapter. Handles file-read and file-write actions
 * against the kernel host's local filesystem via fs/promises. Pairs
 * with the terminal adapter for shell + edit workflows.
 *
 * runCommand() is intentionally a no-op that returns a failing result —
 * filesystems don't execute commands. Callers who want to run something
 * should target `terminal`, not `filesystem`. We don't throw here so a
 * misrouted action degrades gracefully instead of crashing the kernel.
 */
@Injectable()
export class FilesystemAdapter implements ExecutionAdapter {
  readonly target = 'filesystem' as const;
  private connected = true;

  async connect(): Promise<void> {
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    this.connected = false;
  }

  isConnected(): boolean {
    return this.connected;
  }

  /**
   * Enumerate one level of a directory. Recursive listing is the
   * caller's job (so callers can cap depth / respect ignore files).
   */
  async readWorkspace(path?: string): Promise<WorkspaceNode[]> {
    const dir = path ?? process.cwd();
    const entries = await fs.readdir(dir, { withFileTypes: true });
    const nodes: WorkspaceNode[] = [];
    for (const entry of entries) {
      const fullPath = pathMod.join(dir, entry.name);
      let stat;
      try {
        stat = await fs.stat(fullPath);
      } catch {
        stat = undefined;
      }
      nodes.push({
        path: fullPath,
        name: entry.name,
        type: entry.isDirectory() ? 'directory' : 'file',
        sizeBytes: stat?.size,
        modifiedAt: stat?.mtime.toISOString(),
      });
    }
    return nodes;
  }

  /**
   * Apply a batch of FileEdits. Honours create/overwrite/append/patch.
   * Patch mode uses `anchor` as a literal first-occurrence replace
   * target; if the anchor is missing the edit degrades to overwrite so
   * callers don't silently lose writes.
   */
  async applyEdits(edits: FileEdit[]): Promise<EditOutcome[]> {
    const results: EditOutcome[] = [];
    for (const edit of edits) {
      try {
        const dir = pathMod.dirname(edit.path);
        await fs.mkdir(dir, { recursive: true });

        let bytesWritten = Buffer.byteLength(edit.content, 'utf8');
        switch (edit.kind) {
          case 'append': {
            let existing = '';
            try {
              existing = await fs.readFile(edit.path, 'utf8');
            } catch {
              existing = '';
            }
            await fs.writeFile(edit.path, existing + edit.content, 'utf8');
            break;
          }
          case 'patch': {
            let existing = '';
            try {
              existing = await fs.readFile(edit.path, 'utf8');
            } catch {
              existing = '';
            }
            if (edit.anchor && existing.includes(edit.anchor)) {
              await fs.writeFile(
                edit.path,
                existing.replace(edit.anchor, edit.content),
                'utf8',
              );
            } else {
              await fs.writeFile(edit.path, edit.content, 'utf8');
            }
            break;
          }
          case 'create':
          case 'overwrite':
          default: {
            await fs.writeFile(edit.path, edit.content, 'utf8');
            break;
          }
        }
        results.push({ path: edit.path, ok: true, bytesWritten });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        results.push({ path: edit.path, ok: false, error: message });
      }
    }
    return results;
  }

  /**
   * Filesystems don't run commands. Degrade gracefully so a misrouted
   * action surfaces in the result rather than crashing the orchestrator.
   */
  async runCommand(_command: string, _opts?: RunCommandOpts): Promise<CommandResult> {
    return {
      command: _command,
      exitCode: 126, // "command not executable" — close enough
      stdout: '',
      stderr: 'filesystem adapter does not execute commands; route to terminal',
      durationMs: 0,
    };
  }
}
