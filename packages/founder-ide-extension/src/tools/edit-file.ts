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

export interface EditFileInput {
  filePath: string;
  oldText: string;
  newText: string;
  /** When true, create the file if it doesn't exist (insert at end). */
  createIfMissing?: boolean;
}

function resolveUri(filePath: string): vscode.Uri | null {
  if (!filePath || typeof filePath !== 'string') return null;
  // Accept absolute paths, workspace-relative paths, and `file://` URIs.
  if (filePath.startsWith('file://')) {
    try {
      return vscode.Uri.parse(filePath);
    } catch {
      return null;
    }
  }
  if (vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders.length > 0) {
    const root = vscode.workspace.workspaceFolders[0].uri;
    const joined = vscode.Uri.joinPath(root, filePath);
    // If the path is absolute on disk, joinPath still produces a file URI
    // rooted at the workspace — normalize by checking for a drive letter / leading slash.
    if (/^[A-Za-z]:[\\/]/.test(filePath) || filePath.startsWith('/')) {
      return vscode.Uri.file(filePath);
    }
    return joined;
  }
  return vscode.Uri.file(filePath);
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

    let exists = await fileExists(uri);
    if (!exists && !input.createIfMissing) {
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart(
          `Error: file not found (${input.filePath}). Set createIfMissing=true to create it.`,
        ),
      ]);
    }

    // Resolve the edit range. If oldText is empty / file is missing, append.
    let range: vscode.Range;
    if (!exists || input.oldText.length === 0) {
      // Append to end of file (or create empty).
      let endLine = 0;
      if (exists) {
        const existing = await readFullFile(uri);
        const lines = existing.split('\n');
        endLine = Math.max(0, lines.length - 1);
      }
      range = new vscode.Range(endLine, Number.MAX_SAFE_INTEGER, endLine, Number.MAX_SAFE_INTEGER);
    } else {
      const existing = await readFullFile(uri);
      const idx = existing.indexOf(input.oldText);
      if (idx === -1) {
        return new vscode.LanguageModelToolResult([
          new vscode.LanguageModelTextPart(
            `Error: oldText not found in ${input.filePath}. The file has changed since the model last read it.`,
          ),
        ]);
      }
      const start = offsetToPosition(existing, idx);
      const end = offsetToPosition(existing, idx + input.oldText.length);
      range = new vscode.Range(start, end);
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
