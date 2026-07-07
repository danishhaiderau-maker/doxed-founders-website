import { Injectable, Logger } from '@nestjs/common';
import { spawn } from 'node:child_process';
import { platform } from 'node:os';
import { Readable } from 'node:stream';
import type {
  CommandResult,
  EditOutcome,
  ExecutionAdapter,
  FileEdit,
  RunCommandOpts,
  StreamChunk,
  WorkspaceNode,
} from '../execution-manager.types';

/**
 * LocalShellAdapter — the `terminal` execution target.
 *
 * The first real adapter. Shells out via child_process.spawn on the
 * kernel host. This is the only execution target that actually performs
 * work today; the others are stubs pending their transports
 * (Cursor via Founder Node IPC, OpenHands via container API, etc.).
 *
 * Commands run through the platform shell (`cmd.exe` on Windows,
 * `/bin/sh` elsewhere) so callers can use shell features (pipes, env
 * expansion). Output is buffered into stdout/stderr; long-running work
 * should use streamOutput() instead.
 *
 * Boundary note: the adapter does not import any application code.
 * It only depends on NestJS common + Node built-ins.
 */
@Injectable()
export class LocalShellAdapter implements ExecutionAdapter {
  readonly target = 'terminal' as const;
  private readonly logger = new Logger(LocalShellAdapter.name);
  private connected = true;

  // The terminal target is always "connected" — there's no transport
  // to establish. connect()/disconnect() exist to satisfy the adapter
  // contract and let the kernel track connected targets uniformly.
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
   * List the contents of a directory on the local filesystem. Used by
   * readWorkspace() callers who haven't pinned the `filesystem` target —
   * the local shell and the local filesystem share a view of the disk.
   */
  async readWorkspace(path?: string): Promise<WorkspaceNode[]> {
    const dir = path ?? process.cwd();
    const fs = await import('node:fs/promises');
    const entries = await fs.readdir(dir, { withFileTypes: true });
    const nodes: WorkspaceNode[] = [];
    for (const entry of entries) {
      const fullPath = `${dir}/${entry.name}`;
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
   * Apply file edits by writing through the local filesystem. Kept here
   * so the terminal adapter is self-sufficient for shell + edit combos
   * (e.g. git-commit workflows). The filesystem adapter is the canonical
   * implementation; this delegates to the same primitives.
   */
  async applyEdits(edits: FileEdit[]): Promise<EditOutcome[]> {
    const fs = await import('node:fs/promises');
    const pathMod = await import('node:path');
    const results: EditOutcome[] = [];
    for (const edit of edits) {
      try {
        const dir = pathMod.dirname(edit.path);
        await fs.mkdir(dir, { recursive: true });
        let bytesWritten = Buffer.byteLength(edit.content, 'utf8');
        if (edit.kind === 'append') {
          let existing = '';
          try {
            existing = await fs.readFile(edit.path, 'utf8');
          } catch {
            existing = '';
          }
          await fs.writeFile(edit.path, existing + edit.content, 'utf8');
        } else if (edit.kind === 'patch') {
          // Simple anchor-replace patch: if anchor provided, replace its
          // first occurrence; otherwise treat as overwrite.
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
        } else {
          // create / overwrite
          await fs.writeFile(edit.path, edit.content, 'utf8');
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
   * Spawn a command on the platform shell. Resolves with merged output
   * and the exit code. Times out (exitCode=-1, signal='timeout') if
   * opts.timeoutMs passes with no exit.
   */
  async runCommand(command: string, opts?: RunCommandOpts): Promise<CommandResult> {
    const isWindows = platform() === 'win32';
    const shell = isWindows ? 'cmd.exe' : '/bin/sh';
    const shellFlag = isWindows ? '/c' : '-c';
    const start = Date.now();

    return new Promise<CommandResult>((resolve) => {
      const child = spawn(shell, [shellFlag, command], {
        cwd: opts?.cwd,
        env: { ...process.env, ...(opts?.env ?? {}) },
        windowsHide: true,
      });

      let stdout = '';
      let stderr = '';
      let timedOut = false;
      let timer: NodeJS.Timeout | undefined;

      if (opts?.timeoutMs && opts.timeoutMs > 0) {
        timer = setTimeout(() => {
          timedOut = true;
          child.kill('SIGKILL');
        }, opts.timeoutMs);
      }

      child.stdout?.on('data', (chunk: Buffer | string) => {
        const text = typeof chunk === 'string' ? chunk : chunk.toString('utf8');
        if (opts?.mergeStderr) {
          stdout += text;
        } else {
          stdout += text;
        }
      });
      child.stderr?.on('data', (chunk: Buffer | string) => {
        const text = typeof chunk === 'string' ? chunk : chunk.toString('utf8');
        if (opts?.mergeStderr) {
          stdout += text;
        } else {
          stderr += text;
        }
      });

      child.on('error', (err) => {
        if (timer) clearTimeout(timer);
        stderr += `\n[spawn error] ${err.message}`;
        resolve({
          command,
          exitCode: -1,
          stdout,
          stderr,
          durationMs: Date.now() - start,
        });
      });

      child.on('close', (code, signal) => {
        if (timer) clearTimeout(timer);
        resolve({
          command,
          exitCode: typeof code === 'number' ? code : -1,
          stdout,
          stderr,
          durationMs: Date.now() - start,
          signal: timedOut ? 'timeout' : (signal ?? undefined) as CommandResult['signal'],
        });
      });
    });
  }

  /**
   * Stream output as it arrives. The kernel forwards these chunks to
   * the Founder OS shell's live output panel. Yields a final
   * { stream: 'done', exitCode } chunk so callers know the run ended.
   */
  async *streamOutput(
    command: string,
    opts?: RunCommandOpts,
  ): AsyncIterable<StreamChunk> {
    const isWindows = platform() === 'win32';
    const shell = isWindows ? 'cmd.exe' : '/bin/sh';
    const shellFlag = isWindows ? '/c' : '-c';

    const child = spawn(shell, [shellFlag, command], {
      cwd: opts?.cwd,
      env: { ...process.env, ...(opts?.env ?? {}) },
      windowsHide: true,
    });

    let timer: NodeJS.Timeout | undefined;
    let timedOut = false;
    if (opts?.timeoutMs && opts.timeoutMs > 0) {
      timer = setTimeout(() => {
        timedOut = true;
        child.kill('SIGKILL');
      }, opts.timeoutMs);
    }

    // Bridge Node streams to an async iterator. Each 'data' event yields
    // a StreamChunk; the iterator completes on 'close'.
    yield* iterateChunks(child.stdout, 'stdout', opts?.mergeStderr ?? false);
    if (!opts?.mergeStderr) {
      yield* iterateChunks(child.stderr, 'stderr', false);
    }

    const exitCode: number = await new Promise((resolve) => {
      child.on('close', (code) => {
        if (timer) clearTimeout(timer);
        resolve(typeof code === 'number' ? code : -1);
      });
      child.on('error', () => {
        if (timer) clearTimeout(timer);
        resolve(-1);
      });
    });

    if (timedOut) {
      this.logger.warn(`streamOutput timed out for command: ${command}`);
    }

    yield { stream: 'done', data: '', exitCode };
  }
}

/**
 * Convert a Node Readable into an AsyncIterable<StreamChunk>. Honours
 * `mergeIntoStdout` for stderr streams when the caller asked for merge.
 */
async function* iterateChunks(
  stream: Readable | null,
  which: 'stdout' | 'stderr',
  _mergeIntoStdout: boolean,
): AsyncIterable<StreamChunk> {
  if (!stream) return;
  for await (const chunk of stream) {
    const text =
      typeof chunk === 'string' ? chunk : (chunk as Buffer).toString('utf8');
    yield { stream: which, data: text } as StreamChunk;
  }
}
