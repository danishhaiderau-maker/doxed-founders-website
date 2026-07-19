/**
 * `founder.runCommand` — LanguageModelTool.
 *
 * Runs one reviewed command at a time and streams stdout/stderr into a visible
 * output channel while returning bounded output to the model.
 */
import * as vscode from 'vscode';
import * as path from 'node:path';
import * as fs from 'node:fs';
import { spawn, type ChildProcess } from 'node:child_process';

export interface RunCommandInput {
  command: string;
  /** Optional working directory (absolute or workspace-relative). */
  cwd?: string;
  /** Approximate max milliseconds to wait for the command. Default 30s. */
  timeoutMs?: number;
}

let commandOutput: vscode.OutputChannel | null = null;
let activeCommand: ChildProcess | null = null;

function resolveCwd(cwd: string | undefined): string | null {
  const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!root) return null;
  const candidate = !cwd
    ? path.resolve(root)
    : path.isAbsolute(cwd)
      ? path.resolve(cwd)
      : path.resolve(root, cwd);
  const relative = path.relative(path.resolve(root), candidate);
  if (relative.startsWith('..') || path.isAbsolute(relative)) return null;
  try {
    const realRoot = fs.realpathSync(root);
    const realCandidate = fs.realpathSync(candidate);
    const realRelative = path.relative(realRoot, realCandidate);
    return realRelative.startsWith('..') || path.isAbsolute(realRelative)
      ? null
      : realCandidate;
  } catch {
    return null;
  }
}

function terminateCommand(child: ChildProcess): void {
  if (process.platform === 'win32' && child.pid) {
    const killer = spawn(
      'taskkill',
      ['/pid', String(child.pid), '/t', '/f'],
      { windowsHide: true, stdio: 'ignore' },
    );
    killer.unref();
    return;
  }
  child.kill('SIGTERM');
}

export const runCommandTool: vscode.LanguageModelTool<RunCommandInput> = {
  async prepareInvocation(
    options: vscode.LanguageModelToolInvocationPrepareOptions<RunCommandInput>,
    _token: vscode.CancellationToken,
  ): Promise<vscode.PreparedToolInvocation> {
    const cmd = options.input.command;
    return {
      invocationMessage: `Running: ${cmd}`,
      confirmationMessages: {
        title: 'Run terminal command',
        message: `Allow Founder OS to run this command?\n\n\`${cmd}\``,
      },
    };
  },

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<RunCommandInput>,
    token: vscode.CancellationToken,
  ): Promise<vscode.LanguageModelToolResult> {
    const input = options.input;
    if (!input.command || typeof input.command !== 'string') {
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart('Error: no command provided.'),
      ]);
    }

    const cwd = resolveCwd(input.cwd);
    if (!cwd) {
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart(
          'Error: command working directory must be inside the open workspace.',
        ),
      ]);
    }
    if (activeCommand) {
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart(
          'Error: another Founder OS command is already running.',
        ),
      ]);
    }
    const timeoutMs = Math.max(1000, Math.min(300_000, input.timeoutMs ?? 30_000));
    commandOutput ??= vscode.window.createOutputChannel('Founder OS');
    commandOutput.show(true);
    commandOutput.appendLine(`\n> ${input.command}`);
    commandOutput.appendLine(`  cwd: ${cwd}`);

    const output: string[] = [];
    const append = (text: string) => {
      output.push(text);
      commandOutput?.append(text);
    };
    const completion = await new Promise<{ exitCode: number; suffix?: string }>((resolve) => {
      const child = spawn(input.command, {
        cwd,
        shell: true,
        windowsHide: true,
        env: process.env,
      });
      activeCommand = child;
      let settled = false;
      let timeout: NodeJS.Timeout | undefined;
      let cancellation: vscode.Disposable | undefined;
      const finish = (value: { exitCode: number; suffix?: string }) => {
        if (settled) return;
        settled = true;
        if (activeCommand === child) activeCommand = null;
        if (timeout) clearTimeout(timeout);
        cancellation?.dispose();
        resolve(value);
      };
      child.stdout?.on('data', (data: Buffer) => append(data.toString('utf8')));
      child.stderr?.on('data', (data: Buffer) => append(data.toString('utf8')));
      child.on('close', (code) => finish({ exitCode: code ?? 1 }));
      child.on('error', (error) =>
        finish({ exitCode: 1, suffix: `\n[Founder OS: ${error.message}]` }),
      );
      timeout = setTimeout(() => {
        terminateCommand(child);
        finish({
          exitCode: 124,
          suffix: `\n[Founder OS: command timed out after ${timeoutMs}ms]`,
        });
      }, timeoutMs);
      cancellation = token.onCancellationRequested(() => {
        terminateCommand(child);
        finish({ exitCode: 130, suffix: '\n[Founder OS: command cancelled]' });
      });
      if (token.isCancellationRequested) {
        terminateCommand(child);
        finish({ exitCode: 130, suffix: '\n[Founder OS: command cancelled]' });
      }
    });
    let body = `${output.join('')}${completion.suffix ?? ''}`;
    body += `\n[exit code ${completion.exitCode}]`;
    commandOutput.appendLine(`\n[exit code ${completion.exitCode}]`);

    // Truncate huge output so we don't blow the model's context window.
    const MAX = 20_000;
    if (body.length > MAX) {
      body = body.slice(-MAX);
      body = `…[truncated to last ${MAX} chars]\n${body}`;
    }

    return new vscode.LanguageModelToolResult([
      new vscode.LanguageModelTextPart(body || '(no output)'),
    ]);
  },
};
