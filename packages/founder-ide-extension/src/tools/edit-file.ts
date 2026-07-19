/**
 * `founder.editFile` — LanguageModelTool.
 *
 * The model emits a `LanguageModelToolCallPart` with `{ filePath, oldText, newText }`.
 * VS Code routes it to our `invoke`, which applies the change via
 * `vscode.workspace.applyEdit` (a `WorkspaceEdit` with a single replace on the
 * target file). The result is returned to the model as a
 * `LanguageModelToolResult` so the agent loop can continue.
 *
 * See `docs/FOUNDER-IDE-FORK-PLAN.md` §4.3 / §8.4.
 */
import * as vscode from 'vscode';
import * as path from 'node:path';
import * as fs from 'node:fs';

export interface EditFileInput {
  filePath: string;
  oldText: string;
  newText: string;
  /** When true, create the file if it doesn't exist (insert at end). */
  createIfMissing?: boolean;
}

function resolveUri(filePath: string): vscode.Uri | null {
  if (!filePath || typeof filePath !== 'string') return null;
  const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!root) return null;
  let candidate: string;
  try {
    candidate = filePath.startsWith('file://')
      ? vscode.Uri.parse(filePath).fsPath
      : path.isAbsolute(filePath)
        ? filePath
        : path.join(root, filePath);
  } catch {
    return null;
  }
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  if (relative.startsWith('..') || path.isAbsolute(relative)) return null;
  return vscode.Uri.file(candidate);
}

async function fileExists(uri: vscode.Uri): Promise<boolean> {
  try {
    await vscode.workspace.fs.stat(uri);
    return true;
  } catch {
    return false;
  }
}

async function readFullFile(uri: vscode.Uri): Promise<string> {
  const buf = await vscode.workspace.fs.readFile(uri);
  return new TextDecoder('utf8').decode(buf);
}

async function isSafeWorkspaceTarget(
  uri: vscode.Uri,
  exists: boolean,
): Promise<boolean> {
  const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!root) return false;
  try {
    if (exists && (await fs.promises.lstat(uri.fsPath)).isSymbolicLink()) {
      return false;
    }
    const realRoot = await fs.promises.realpath(root);
    const boundary = exists ? uri.fsPath : path.dirname(uri.fsPath);
    const realBoundary = await fs.promises.realpath(boundary);
    const relative = path.relative(realRoot, realBoundary);
    return !relative.startsWith('..') && !path.isAbsolute(relative);
  } catch {
    return false;
  }
}

export const editFileTool: vscode.LanguageModelTool<EditFileInput> = {
  async prepareInvocation(
    options: vscode.LanguageModelToolInvocationPrepareOptions<EditFileInput>,
    _token: vscode.CancellationToken,
  ): Promise<vscode.PreparedToolInvocation> {
    const input = options.input;
    const verb = input.createIfMissing ? 'create / edit' : 'edit';
    return {
      invocationMessage: `Editing ${input.filePath}`,
      confirmationMessages: {
        title: `${verb} ${input.filePath}`,
        message: `Allow Founder OS to ${verb} \`${input.filePath}\`?`,
      },
    };
  },

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<EditFileInput>,
    _token: vscode.CancellationToken,
  ): Promise<vscode.LanguageModelToolResult> {
    const input = options.input;
    const uri = resolveUri(input.filePath);
    if (!uri) {
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart(`Error: invalid filePath "${input.filePath}".`),
      ]);
    }

    const exists = await fileExists(uri);
    if (!(await isSafeWorkspaceTarget(uri, exists))) {
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart(
          `Error: ${input.filePath} resolves outside the open workspace or through a symbolic link.`,
        ),
      ]);
    }
    if (!exists && !input.createIfMissing) {
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart(
          `Error: file not found (${input.filePath}). Set createIfMissing=true to create it.`,
        ),
      ]);
    }

    const existing = exists ? await readFullFile(uri) : '';

    // Resolve the edit range. If oldText is empty / file is missing, append.
    let range: vscode.Range;
    if (!exists) {
      range = new vscode.Range(0, 0, 0, 0);
    } else if (input.oldText.length === 0) {
      const end = offsetToPosition(existing, existing.length);
      range = new vscode.Range(end, end);
    } else {
      const idx = existing.indexOf(input.oldText);
      if (idx === -1) {
        return new vscode.LanguageModelToolResult([
          new vscode.LanguageModelTextPart(
            `Error: oldText not found in ${input.filePath}. The file has changed since the model last read it.`,
          ),
        ]);
      }
      if (existing.indexOf(input.oldText, idx + input.oldText.length) !== -1) {
        return new vscode.LanguageModelToolResult([
          new vscode.LanguageModelTextPart(
            `Error: oldText is ambiguous in ${input.filePath}. Include more surrounding text so it matches exactly once.`,
          ),
        ]);
      }
      const start = offsetToPosition(existing, idx);
      const end = offsetToPosition(existing, idx + input.oldText.length);
      range = new vscode.Range(start, end);
    }

    const changedBeforeApply = exists
      ? (await readFullFile(uri).catch(() => null)) !== existing
      : await fileExists(uri);
    if (changedBeforeApply) {
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart(
          `Error: ${input.filePath} changed while the edit was being prepared. Read it again before editing.`,
        ),
      ]);
    }

    const we = new vscode.WorkspaceEdit();
    if (!exists && input.createIfMissing) {
      // Create the file with the new content, then we still record a replace
      // for the (empty) range so undo restores the prior (nonexistent) state.
      we.createFile(uri, { ignoreIfExists: false, overwrite: false });
    }
    we.replace(uri, range, input.newText);
    const ok = await vscode.workspace.applyEdit(we);
    if (!ok) {
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart(`Error: applyEdit was rejected for ${input.filePath}.`),
      ]);
    }

    // Persist so the editor shows it.
    try {
      await vscode.workspace.save(uri);
    } catch {
      /* save is best-effort */
    }

    return new vscode.LanguageModelToolResult([
      new vscode.LanguageModelTextPart(
        `Edited ${input.filePath} — replaced ${input.oldText.length} chars with ${input.newText.length} chars.`,
      ),
    ]);
  },
};

/** Convert an absolute offset within `text` to a {line, character} Position. */
function offsetToPosition(text: string, offset: number): vscode.Position {
  let line = 0;
  let lastLineStart = 0;
  for (let i = 0; i < offset && i < text.length; i++) {
    if (text.charCodeAt(i) === 10 /* \n */) {
      line++;
      lastLineStart = i + 1;
    }
  }
  const character = Math.max(0, offset - lastLineStart);
  return new vscode.Position(line, character);
}
