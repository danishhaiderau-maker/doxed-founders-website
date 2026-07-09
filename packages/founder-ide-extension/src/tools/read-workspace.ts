/**
 * `founder.readWorkspace` — LanguageModelTool.
 *
 * Walks the current workspace file tree and returns a compact summary the model
 * can reason about: the tree (respecting common ignore patterns) plus optional
 * file contents for a small set of explicitly-requested files.
 *
 * See design report §4.3 / §8.4.
 */
import * as vscode from 'vscode';
import * as fs from 'node:fs';
import * as path from 'node:path';

export interface ReadWorkspaceInput {
  /** Optional sub-directory to walk (workspace-relative). Default = workspace root. */
  subdir?: string;
  /** Optional list of workspace-relative files whose full contents to include. */
  readFiles?: string[];
  /** Max number of file-tree entries to return. Default 400. */
  maxTreeEntries?: number;
}

const DEFAULT_IGNORE_DIRS = new Set([
  'node_modules',
  '.git',
  '.svn',
  '.hg',
  'out',
  'dist',
  'build',
  '.next',
  '.cache',
  '.turbo',
  'coverage',
  '__pycache__',
  '.venv',
  'venv',
  '.tmp',
  '.vscode-test',
]);

const DEFAULT_IGNORE_FILE_GLOBS = [
  /\.log$/i,
  /\.pyc$/i,
  /\.map$/i,
];

function shouldIgnoreName(name: string, isDir: boolean): boolean {
  if (isDir && DEFAULT_IGNORE_DIRS.has(name)) return true;
  if (!isDir && DEFAULT_IGNORE_FILE_GLOBS.some((re) => re.test(name))) return true;
  return false;
}

function walk(root: string, maxEntries: number): { entries: string[]; truncated: boolean } {
  const entries: string[] = [];
  let truncated = false;

  function recurse(dir: string, depth: number): void {
    if (truncated) return;
    if (depth > 6) return;
    let names: string[];
    try {
      names = fs.readdirSync(dir, { withFileTypes: true })
        .filter((d) => !shouldIgnoreName(d.name, d.isDirectory()))
        .map((d) => d.name);
    } catch {
      return;
    }
    names.sort();
    for (const name of names) {
      if (entries.length >= maxEntries) {
        truncated = true;
        return;
      }
      const full = path.join(dir, name);
      let stat: fs.Stats;
      try {
        stat = fs.statSync(full);
      } catch {
        continue;
      }
      const isDir = stat.isDirectory();
      const rel = path.relative(root, full).replace(/\\/g, '/');
      entries.push(isDir ? `${rel}/` : rel);
      if (isDir) recurse(full, depth + 1);
    }
  }

  recurse(root, 0);
  return { entries, truncated };
}

function resolveSubdir(subdir: string | undefined): string | null {
  const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!root) return null;
  if (!subdir) return root;
  if (path.isAbsolute(subdir)) return subdir;
  return path.join(root, subdir);
}

function readWorkspaceFile(root: string, rel: string): string | null {
  const full = path.isAbsolute(rel) ? rel : path.join(root, rel);
  try {
    const buf = fs.readFileSync(full, 'utf8');
    if (buf.length > 16_000) {
      return buf.slice(0, 16_000) + `\n…[truncated, ${buf.length - 16_000} more chars]`;
    }
    return buf;
  } catch {
    return null;
  }
}

export const readWorkspaceTool: vscode.LanguageModelTool<ReadWorkspaceInput> = {
  async prepareInvocation(
    options: vscode.LanguageModelToolInvocationPrepareOptions<ReadWorkspaceInput>,
    _token: vscode.CancellationToken,
  ): Promise<vscode.PreparedToolInvocation> {
    const sub = options.input.subdir ? ` under ${options.input.subdir}` : '';
    return {
      invocationMessage: `Reading workspace${sub}`,
    };
  },

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<ReadWorkspaceInput>,
    _token: vscode.CancellationToken,
  ): Promise<vscode.LanguageModelToolResult> {
    const input = options.input;
    const root = resolveSubdir(input.subdir);
    if (!root) {
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart('Error: no workspace folder is open.'),
      ]);
    }

    const maxEntries = Math.max(10, Math.min(5000, input.maxTreeEntries ?? 400));
    const { entries, truncated } = walk(root, maxEntries);
    const parts: string[] = [];
    parts.push(`Workspace root: ${root}`);
    parts.push(`File tree (${entries.length} entries${truncated ? ', truncated' : ''}):`);
    parts.push(entries.join('\n'));

    if (input.readFiles && input.readFiles.length > 0) {
      parts.push('\n--- File contents ---');
      for (const rel of input.readFiles.slice(0, 10)) {
        const content = readWorkspaceFile(root, rel);
        parts.push(`\n=== ${rel} ===`);
        parts.push(content ?? `(could not read ${rel})`);
      }
    }

    return new vscode.LanguageModelToolResult([
      new vscode.LanguageModelTextPart(parts.join('\n')),
    ]);
  },
};
