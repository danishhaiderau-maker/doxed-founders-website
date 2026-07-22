import * as vscode from 'vscode';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { spawn, type ChildProcess } from 'node:child_process';
import { founderPathLeases } from '../agent-path-leases';
import {
  generateNonce,
  type IpcCancel,
  type IpcChatPrompt,
  type IpcChatPromptResult,
  type IpcCommandOutput,
  type IpcCommandRequest,
  type IpcCommandReviewResult,
  type IpcEditReviewResult,
  type IpcMessage,
  type IpcProposedEdit,
  type IpcWorkspaceReadRequest,
  type IpcWorkspaceReadResult,
} from './protocol.js';

type SendMessage = (message: IpcMessage) => void;

const runningCommands = new Map<string, ChildProcess>();
const MAX_COMMAND_CHUNK = 16_000;
let commandOutput: vscode.OutputChannel | null = null;
let commandSlotHeld = false;

function envelope() {
  return { nonce: generateNonce(), ts: new Date().toISOString() };
}

function workspaceRoot(): string | null {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? null;
}

function isInside(root: string, candidate: string): boolean {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

function resolveWorkspacePath(input: string): { root: string; file: string } | null {
  const root = workspaceRoot();
  if (!root || !input?.trim()) return null;
  const file = path.isAbsolute(input) ? path.resolve(input) : path.resolve(root, input);
  return isInside(root, file) ? { root, file } : null;
}

function applyStructuredEdit(
  original: string,
  edit: NonNullable<IpcProposedEdit['edit']>,
): { content: string; error?: string } {
  switch (edit.kind) {
    case 'create':
    case 'overwrite':
      return { content: edit.content };
    case 'append':
      return { content: original + edit.content };
    case 'patch': {
      if (!edit.anchor) {
        return { content: original, error: 'Patch edit requires an exact anchor.' };
      }
      const first = original.indexOf(edit.anchor);
      if (first < 0) {
        return { content: original, error: 'Patch anchor was not found in the current file.' };
      }
      if (original.indexOf(edit.anchor, first + edit.anchor.length) >= 0) {
        return { content: original, error: 'Patch anchor is ambiguous in the current file.' };
      }
      return {
        content:
          original.slice(0, first) +
          edit.content +
          original.slice(first + edit.anchor.length),
      };
    }
  }
}

async function showDiff(
  requestId: string,
  target: string,
  original: string,
  proposed: string,
): Promise<void> {
  const dir = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'founder-ide-review-'));
  const ext = path.extname(target);
  const base = path.basename(target, ext);
  const before = path.join(dir, `${base}.before${ext}`);
  const after = path.join(dir, `${base}.proposed${ext}`);
  await Promise.all([
    fs.promises.writeFile(before, original, 'utf8'),
    fs.promises.writeFile(after, proposed, 'utf8'),
  ]);
  const cleanupTimer = setTimeout(() => {
    void fs.promises.rm(dir, { recursive: true, force: true });
  }, 60 * 60_000);
  cleanupTimer.unref();
  await vscode.commands.executeCommand(
    'vscode.diff',
    vscode.Uri.file(before),
    vscode.Uri.file(after),
    `Founder OS edit review: ${path.basename(target)} (${requestId.slice(0, 8)})`,
    { preview: true },
  );
}

async function handleProposedEdit(
  message: IpcProposedEdit,
  send: SendMessage,
): Promise<void> {
  const resolved = resolveWorkspacePath(message.path);
  if (!resolved || !message.edit) {
    send({
      type: 'editReviewResult',
      requestId: message.requestId,
      approved: false,
      reason: !resolved
        ? 'Edit target is outside the active workspace.'
        : 'Structured edit payload is missing.',
      ...envelope(),
    } satisfies IpcEditReviewResult);
    return;
  }

  let original = '';
  const exists = fs.existsSync(resolved.file);
  try {
    const boundaryPath = exists ? resolved.file : path.dirname(resolved.file);
    if (exists && (await fs.promises.lstat(resolved.file)).isSymbolicLink()) {
      throw new Error('Edit target is a symbolic link.');
    }
    const realRoot = await fs.promises.realpath(resolved.root);
    const realBoundary = await fs.promises.realpath(boundaryPath);
    if (!isInside(realRoot, realBoundary)) {
      throw new Error('Edit target resolves outside the active workspace.');
    }
  } catch (error) {
    send({
      type: 'editReviewResult',
      requestId: message.requestId,
      approved: false,
      reason:
        error instanceof Error
          ? error.message
          : 'Edit target could not be verified.',
      ...envelope(),
    } satisfies IpcEditReviewResult);
    return;
  }
  if (exists) {
    original = await fs.promises.readFile(resolved.file, 'utf8');
  } else if (message.edit.kind !== 'create') {
    send({
      type: 'editReviewResult',
      requestId: message.requestId,
      approved: false,
      reason: 'Target file does not exist.',
      ...envelope(),
    } satisfies IpcEditReviewResult);
    return;
  }

  const proposed = applyStructuredEdit(original, message.edit);
  if (proposed.error) {
    send({
      type: 'editReviewResult',
      requestId: message.requestId,
      approved: false,
      reason: proposed.error,
      ...envelope(),
    } satisfies IpcEditReviewResult);
    return;
  }

  const remoteTaskId = `remote-edit-${message.requestId}`;
  const claim = founderPathLeases.claim(resolved.root, resolved.file, remoteTaskId);
  if (!claim.ok) {
    send({
      type: 'editReviewResult',
      requestId: message.requestId,
      approved: false,
      reason: `${claim.reason} Coordinate with the active task before retrying.`,
      ...envelope(),
    } satisfies IpcEditReviewResult);
    return;
  }

  try {
    await showDiff(message.requestId, resolved.file, original, proposed.content);
    const choice = await vscode.window.showWarningMessage(
      `Founder OS wants to ${message.edit.kind} ${path.relative(resolved.root, resolved.file)}.`,
      { modal: true, detail: 'Review the open diff, then approve or reject this change.' },
      'Apply edit',
      'Reject',
    );
    if (choice !== 'Apply edit') {
      send({
        type: 'editReviewResult',
        requestId: message.requestId,
        approved: false,
        reason: 'user_denied',
        ...envelope(),
      } satisfies IpcEditReviewResult);
      return;
    }

    let changedDuringReview = false;
    try {
      changedDuringReview = exists
        ? (await fs.promises.readFile(resolved.file, 'utf8')) !== original
        : fs.existsSync(resolved.file);
    } catch {
      changedDuringReview = true;
    }
    if (changedDuringReview) {
      send({
        type: 'editReviewResult',
        requestId: message.requestId,
        approved: false,
        reason: 'File changed while the edit was being reviewed. Review a fresh proposal.',
        ...envelope(),
      } satisfies IpcEditReviewResult);
      return;
    }

    const validation = founderPathLeases.validate(claim.lease);
    if (!validation.ok) {
      send({
        type: 'editReviewResult',
        requestId: message.requestId,
        approved: false,
        reason: validation.reason,
        ...envelope(),
      } satisfies IpcEditReviewResult);
      return;
    }

    const uri = vscode.Uri.file(resolved.file);
    const workspaceEdit = new vscode.WorkspaceEdit();
    if (!exists) {
      workspaceEdit.createFile(uri, { ignoreIfExists: false });
      workspaceEdit.insert(uri, new vscode.Position(0, 0), proposed.content);
    } else {
      const document = await vscode.workspace.openTextDocument(uri);
      const fullRange = new vscode.Range(
        document.positionAt(0),
        document.positionAt(document.getText().length),
      );
      workspaceEdit.replace(uri, fullRange, proposed.content);
    }
    const applied = await vscode.workspace.applyEdit(workspaceEdit);
    if (applied) {
      const document = await vscode.workspace.openTextDocument(uri);
      await vscode.window.showTextDocument(document, { preview: false });
      await document.save();
    }
    send({
      type: 'editReviewResult',
      requestId: message.requestId,
      approved: applied,
      reason: applied ? undefined : 'workspace_edit_rejected',
      ...envelope(),
    } satisfies IpcEditReviewResult);
  } finally {
    founderPathLeases.releaseTask(remoteTaskId);
  }
}

function sendOutput(
  send: SendMessage,
  requestId: string,
  stream: IpcCommandOutput['stream'],
  chunk: string,
  exitCode?: number | null,
): void {
  for (let i = 0; i < Math.max(1, chunk.length); i += MAX_COMMAND_CHUNK) {
    const part = chunk.slice(i, i + MAX_COMMAND_CHUNK);
    send({
      type: 'commandOutput',
      requestId,
      stream,
      chunk: part,
      ...(i + MAX_COMMAND_CHUNK >= chunk.length && exitCode !== undefined
        ? { exitCode }
        : {}),
      ...envelope(),
    });
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

async function handleCommandRequest(
  message: IpcCommandRequest,
  send: SendMessage,
): Promise<void> {
  const root = workspaceRoot();
  const cwd = message.cwd
    ? path.isAbsolute(message.cwd)
      ? path.resolve(message.cwd)
      : root
        ? path.resolve(root, message.cwd)
        : null
    : root;
  if (!root || !cwd || !isInside(root, cwd)) {
    send({
      type: 'commandReviewResult',
      requestId: message.requestId,
      approved: false,
      reason: 'Command working directory is outside the active workspace.',
      ...envelope(),
    } satisfies IpcCommandReviewResult);
    return;
  }
  let verifiedCwd: string;
  try {
    const [realRoot, realCwd] = await Promise.all([
      fs.promises.realpath(root),
      fs.promises.realpath(cwd),
    ]);
    if (!isInside(realRoot, realCwd)) {
      throw new Error('Command working directory resolves outside the active workspace.');
    }
    verifiedCwd = realCwd;
  } catch (error) {
    send({
      type: 'commandReviewResult',
      requestId: message.requestId,
      approved: false,
      reason:
        error instanceof Error
          ? error.message
          : 'Command working directory could not be verified.',
      ...envelope(),
    } satisfies IpcCommandReviewResult);
    return;
  }
  if (commandSlotHeld || runningCommands.size > 0) {
    send({
      type: 'commandReviewResult',
      requestId: message.requestId,
      approved: false,
      reason: 'Another Founder OS command is already running.',
      ...envelope(),
    } satisfies IpcCommandReviewResult);
    return;
  }
  commandSlotHeld = true;

  let choice: string | undefined;
  try {
    choice = await vscode.window.showWarningMessage(
      `Founder OS wants to run a ${message.risk} command.`,
      {
        modal: true,
        detail: `${message.command}\n\nWorking directory: ${verifiedCwd}`,
      },
      'Run command',
      'Reject',
    );
  } catch (error) {
    commandSlotHeld = false;
    throw error;
  }
  const approved = choice === 'Run command';
  send({
    type: 'commandReviewResult',
    requestId: message.requestId,
    approved,
    reason: approved ? undefined : 'user_denied',
    ...envelope(),
  } satisfies IpcCommandReviewResult);
  if (!approved) {
    commandSlotHeld = false;
    return;
  }

  commandOutput ??= vscode.window.createOutputChannel('Founder OS Remote');
  commandOutput.show(true);
  commandOutput.appendLine(`\n> ${message.command}`);
  commandOutput.appendLine(`  cwd: ${verifiedCwd}`);

  const child = spawn(message.command, {
    cwd: verifiedCwd,
    shell: true,
    windowsHide: true,
    env: process.env,
  });
  runningCommands.set(message.requestId, child);
  child.stdout?.on('data', (data: Buffer) => {
    const text = data.toString('utf8');
    commandOutput?.append(text);
    sendOutput(send, message.requestId, 'stdout', text);
  });
  child.stderr?.on('data', (data: Buffer) => {
    const text = data.toString('utf8');
    commandOutput?.append(text);
    sendOutput(send, message.requestId, 'stderr', text);
  });

  const timeout = setTimeout(() => {
    terminateCommand(child);
    sendOutput(send, message.requestId, 'stderr', 'Command timed out.', 124);
  }, Math.max(1_000, Math.min(message.timeoutMs ?? 30_000, 300_000)));
  child.on('close', (code) => {
    clearTimeout(timeout);
    runningCommands.delete(message.requestId);
    commandSlotHeld = false;
    commandOutput?.appendLine(`\n[exited ${code ?? 1}]`);
    sendOutput(send, message.requestId, 'stdout', '', code ?? 1);
  });
  child.on('error', (error) => {
    clearTimeout(timeout);
    runningCommands.delete(message.requestId);
    commandSlotHeld = false;
    commandOutput?.appendLine(`\n[failed: ${error.message}]`);
    sendOutput(send, message.requestId, 'stderr', error.message, 1);
  });
}

async function handleChatPrompt(
  message: IpcChatPrompt,
  send: SendMessage,
): Promise<void> {
  try {
    const prompt = message.prompt.trim();
    if (!prompt) throw new Error('Prompt is empty.');
    await vscode.commands.executeCommand('workbench.action.chat.open', {
      query: `@FounderOS ${prompt}`,
      isPartialQuery: false,
    });
    send({
      type: 'chatPromptResult',
      requestId: message.requestId,
      delivered: true,
      ...envelope(),
    } satisfies IpcChatPromptResult);
  } catch (error) {
    send({
      type: 'chatPromptResult',
      requestId: message.requestId,
      delivered: false,
      error: error instanceof Error ? error.message : String(error),
      ...envelope(),
    } satisfies IpcChatPromptResult);
  }
}

async function handleWorkspaceRead(
  message: IpcWorkspaceReadRequest,
  send: SendMessage,
): Promise<void> {
  const root = workspaceRoot();
  const resolved = resolveWorkspacePath(message.path?.trim() || '.');
  if (!root || !resolved) {
    send({
      type: 'workspaceReadResult',
      requestId: message.requestId,
      nodes: [],
      error: 'Requested path is outside the active workspace.',
      ...envelope(),
    } satisfies IpcWorkspaceReadResult);
    return;
  }
  try {
    const [realRoot, realRequestedPath] = await Promise.all([
      fs.promises.realpath(root),
      fs.promises.realpath(resolved.file),
    ]);
    if (!isInside(realRoot, realRequestedPath)) {
      throw new Error('Requested path resolves outside the active workspace.');
    }
  } catch (error) {
    send({
      type: 'workspaceReadResult',
      requestId: message.requestId,
      nodes: [],
      error:
        error instanceof Error
          ? error.message
          : 'Requested path could not be verified.',
      ...envelope(),
    } satisfies IpcWorkspaceReadResult);
    return;
  }

  const nodes: IpcWorkspaceReadResult['nodes'] = [];
  const maxEntries = Math.max(1, Math.min(message.maxEntries ?? 500, 2_000));
  const add = async (candidate: string): Promise<void> => {
    if (nodes.length >= maxEntries) return;
    let stat: fs.Stats;
    try {
      stat = await fs.promises.lstat(candidate);
    } catch {
      return;
    }
    if (stat.isSymbolicLink()) return;
    nodes.push({
      path: path.relative(root, candidate).replace(/\\/g, '/') || '.',
      name: path.basename(candidate),
      type: stat.isDirectory() ? 'directory' : 'file',
      ...(stat.isFile() ? { sizeBytes: stat.size } : {}),
      modifiedAt: stat.mtime.toISOString(),
    });
    if (!stat.isDirectory()) return;
    let children: fs.Dirent[];
    try {
      children = await fs.promises.readdir(candidate, { withFileTypes: true });
    } catch {
      return;
    }
    children.sort((a, b) => a.name.localeCompare(b.name));
    for (const child of children) {
      if (nodes.length >= maxEntries) break;
      if (
        child.name === '.git' ||
        child.name === 'node_modules' ||
        child.name === '.next' ||
        child.name === 'dist' ||
        child.name === 'out'
      ) {
        continue;
      }
      await add(path.join(candidate, child.name));
    }
  };

  await add(resolved.file);
  send({
    type: 'workspaceReadResult',
    requestId: message.requestId,
    nodes,
    ...envelope(),
  } satisfies IpcWorkspaceReadResult);
}

function handleCancel(message: IpcCancel): void {
  if (!message.requestId) return;
  const child = runningCommands.get(message.requestId);
  if (child) terminateCommand(child);
}

async function handleCompanionAction(
  action: Extract<IpcMessage, { type: 'companionAction' }>['action'],
): Promise<void> {
  switch (action) {
    case 'openTask':
      await vscode.commands.executeCommand('founderOs.openAgents');
      break;
    case 'openUsage':
    case 'openSettings':
      await vscode.commands.executeCommand('founderOs.openSettings');
      break;
    case 'hide':
      await vscode.workspace.getConfiguration('founderOs').update(
        'companion.enabled',
        false,
        vscode.ConfigurationTarget.Global,
      );
      break;
    case 'toggleReducedMotion': {
      const config = vscode.workspace.getConfiguration('founderOs');
      const reduced = config.get<boolean>('companion.reducedMotion', false);
      await config.update(
        'companion.reducedMotion',
        !reduced,
        vscode.ConfigurationTarget.Global,
      );
      break;
    }
    case 'signOut':
      await vscode.commands.executeCommand('founderOs.signOut');
      break;
  }
}

export async function handleAuthenticatedAction(
  message: IpcMessage,
  send: SendMessage,
): Promise<void> {
  switch (message.type) {
    case 'chatPrompt':
      await handleChatPrompt(message, send);
      break;
    case 'workspaceReadRequest':
      await handleWorkspaceRead(message, send);
      break;
    case 'proposedEdit':
      await handleProposedEdit(message, send);
      break;
    case 'commandRequest':
      await handleCommandRequest(message, send);
      break;
    case 'cancel':
      handleCancel(message);
      break;
    case 'companionAction':
      await handleCompanionAction(message.action);
      break;
    default:
      break;
  }
}
