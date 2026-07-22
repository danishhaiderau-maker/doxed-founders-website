import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vscode from 'vscode';
import {
  buildWorkspaceContextIndex,
  extractImportSpecifiers,
  formatWorkspaceContextForPrompt,
  parseDecisionLedger,
  parseWorkspaceContextIndex,
  workspaceContextFileNeedsRefresh,
  type WorkspaceContextFile,
  type WorkspaceContextIndexState,
  type WorkspaceDecisionRecord,
} from './workspace-context-state';

const INDEXABLE_GLOB = '**/*.{ts,tsx,js,jsx,mjs,cjs,json,jsonc,md,mdx,py,go,rs,java,kt,swift,cs,cpp,c,h,hpp,css,scss,html,yml,yaml,toml,sql,prisma,sol,sh,ps1}';
const EXCLUDE_GLOB = '**/{.git,node_modules,dist,out,build,.next,.turbo,coverage,.cache,.venv,venv,__pycache__,artifacts}/**';
const MAX_FILES = 5_000;
const MAX_FILE_BYTES = 512_000;
const INITIAL_SYMBOL_BUDGET = 250;
const REFRESH_DEBOUNCE_MS = 1_500;

export interface FounderWorkspaceContextSummary {
  files: number;
  symbols: number;
  indexedAt: string | null;
  refreshing: boolean;
}

export class FounderWorkspaceContextIndex implements vscode.Disposable {
  private state: WorkspaceContextIndexState | null = null;
  private refreshPromise: Promise<void> | null = null;
  private refreshTimer: NodeJS.Timeout | undefined;
  private readonly watcher: vscode.FileSystemWatcher;
  private workspaceId: string | null;
  private indexFile: string | null;

  constructor(private readonly context: vscode.ExtensionContext) {
    this.workspaceId = currentWorkspaceId();
    this.indexFile = this.indexFileFor(this.workspaceId);
    this.state = this.readPersisted(this.workspaceId, this.indexFile);
    this.watcher = vscode.workspace.createFileSystemWatcher(INDEXABLE_GLOB);
    context.subscriptions.push(
      this.watcher,
      this.watcher.onDidCreate(() => this.scheduleRefresh()),
      this.watcher.onDidChange(() => this.scheduleRefresh()),
      this.watcher.onDidDelete(() => this.scheduleRefresh()),
      vscode.workspace.onDidChangeWorkspaceFolders(() => {
        this.switchWorkspace();
        this.scheduleRefresh(true);
      }),
    );
    void this.refresh();
  }

  contextFor(prompt: string): string {
    return formatWorkspaceContextForPrompt(this.state, prompt);
  }

  summary(): FounderWorkspaceContextSummary {
    return {
      files: this.state?.files.length ?? 0,
      symbols: this.state?.files.reduce((sum, file) => sum + file.symbols.length, 0) ?? 0,
      indexedAt: this.state?.indexedAt ?? null,
      refreshing: this.refreshPromise !== null,
    };
  }

  async refresh(force = false): Promise<void> {
    if (this.refreshPromise) return this.refreshPromise;
    this.refreshPromise = this.rebuild(force).finally(() => {
      this.refreshPromise = null;
    });
    return this.refreshPromise;
  }

  dispose(): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    this.watcher.dispose();
  }

  private scheduleRefresh(force = false): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    this.refreshTimer = setTimeout(() => void this.refresh(force), REFRESH_DEBOUNCE_MS);
    this.refreshTimer.unref?.();
  }

  private indexFileFor(workspaceId: string | null): string | null {
    return workspaceId
      ? path.join(this.context.globalStorageUri.fsPath, 'workspace-context', `${workspaceId}.json`)
      : null;
  }

  private switchWorkspace(): void {
    const workspaceId = currentWorkspaceId();
    if (workspaceId === this.workspaceId) return;
    this.workspaceId = workspaceId;
    this.indexFile = this.indexFileFor(workspaceId);
    this.state = this.readPersisted(workspaceId, this.indexFile);
  }

  private readPersisted(
    workspaceId: string | null,
    indexFile: string | null,
  ): WorkspaceContextIndexState | null {
    if (!workspaceId || !indexFile) return null;
    try {
      return parseWorkspaceContextIndex(
        JSON.parse(fs.readFileSync(indexFile, 'utf8')),
        workspaceId,
      );
    } catch {
      return null;
    }
  }

  private async rebuild(force: boolean): Promise<void> {
    this.switchWorkspace();
    const workspaceId = currentWorkspaceId();
    const indexFile = this.indexFile;
    if (!workspaceId || !indexFile) return;
    const uris = await vscode.workspace.findFiles(INDEXABLE_GLOB, EXCLUDE_GLOB, MAX_FILES);
    const previous = new Map(this.state?.files.map((file) => [file.path, file]) ?? []);
    const next: WorkspaceContextFile[] = [];
    const decisions: WorkspaceDecisionRecord[] = [];
    const symbolQueue: Array<{ uri: vscode.Uri; file: WorkspaceContextFile }> = [];
    let symbolBudget = INITIAL_SYMBOL_BUDGET;

    for (const uri of uris) {
      const relativePath = relativeWorkspacePath(uri);
      if (!relativePath) continue;
      let stat: vscode.FileStat;
      try {
        stat = await vscode.workspace.fs.stat(uri);
      } catch {
        continue;
      }
      if (stat.size > MAX_FILE_BYTES) continue;
      const old = previous.get(relativePath);
      const isDecisionLedger = relativePath.toLowerCase() === '.github/founder-os/decisions.md';
      if (!force && !isDecisionLedger && !workspaceContextFileNeedsRefresh(old, {
        path: relativePath,
        size: stat.size,
        mtimeMs: stat.mtime,
      })) {
        next.push(old!);
        continue;
      }
      let bytes: Uint8Array;
      try {
        bytes = await vscode.workspace.fs.readFile(uri);
      } catch {
        continue;
      }
      const sha256 = createHash('sha256').update(bytes).digest('hex');
      const source = Buffer.from(bytes).toString('utf8');
      const file: WorkspaceContextFile = {
        path: relativePath,
        languageId: languageIdForPath(relativePath),
        size: stat.size,
        mtimeMs: stat.mtime,
        sha256,
        symbols: [],
        imports: extractImportSpecifiers(source, languageIdForPath(relativePath)),
      };
      next.push(file);
      if (isDecisionLedger) {
        decisions.push(...parseDecisionLedger(source, relativePath, sha256));
      }
      if (symbolBudget > 0 && supportsSymbols(relativePath)) {
        symbolQueue.push({ uri, file });
        symbolBudget -= 1;
      }
    }

    for (let offset = 0; offset < symbolQueue.length; offset += 8) {
      const batch = symbolQueue.slice(offset, offset + 8);
      await Promise.all(batch.map(async ({ uri, file }) => {
        file.symbols = await documentSymbols(uri);
      }));
    }

    if (workspaceId !== currentWorkspaceId() || indexFile !== this.indexFile) {
      this.scheduleRefresh(true);
      return;
    }
    this.state = buildWorkspaceContextIndex(
      workspaceId,
      next,
      new Date().toISOString(),
      decisions,
    );
    this.writePersisted(this.state, indexFile);
  }

  private writePersisted(state: WorkspaceContextIndexState, indexFile: string): void {
    try {
      fs.mkdirSync(path.dirname(indexFile), { recursive: true });
      const temp = `${indexFile}.${process.pid}.tmp`;
      fs.writeFileSync(temp, JSON.stringify(state), 'utf8');
      fs.renameSync(temp, indexFile);
    } catch {
      // Project context is an optimization and must not block chat.
    }
  }
}

function currentWorkspaceId(): string | null {
  const roots = vscode.workspace.workspaceFolders
    ?.map((folder) => path.resolve(folder.uri.fsPath).toLowerCase())
    .sort();
  if (!roots || roots.length === 0) return null;
  return createHash('sha256').update(roots.join('\n')).digest('hex').slice(0, 24);
}

function relativeWorkspacePath(uri: vscode.Uri): string | null {
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    const relative = path.relative(folder.uri.fsPath, uri.fsPath);
    if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
      continue;
    }
    const normalized = relative.replaceAll('\\', '/');
    return (vscode.workspace.workspaceFolders?.length ?? 0) > 1
      ? `${folder.name}/${normalized}`
      : normalized;
  }
  return null;
}

function languageIdForPath(file: string): string {
  const name = path.basename(file).toLowerCase();
  if (name === 'dockerfile') return 'dockerfile';
  const extension = path.extname(name).slice(1);
  return extension || 'text';
}

function supportsSymbols(file: string): boolean {
  return /\.(?:ts|tsx|js|jsx|mjs|cjs|py|go|rs|java|kt|swift|cs|cpp|c|h|hpp|sol)$/i.test(file);
}

async function documentSymbols(uri: vscode.Uri): Promise<string[]> {
  try {
    const symbols = await vscode.commands.executeCommand<
      Array<vscode.DocumentSymbol | vscode.SymbolInformation>
    >('vscode.executeDocumentSymbolProvider', uri);
    if (!symbols) return [];
    const names: string[] = [];
    const visit = (symbol: vscode.DocumentSymbol | vscode.SymbolInformation): void => {
      if (names.length >= 40) return;
      if (symbol.name && !names.includes(symbol.name)) names.push(symbol.name);
      if ('children' in symbol) {
        for (const child of symbol.children.slice(0, 12)) visit(child);
      }
    };
    for (const symbol of symbols) visit(symbol);
    return names;
  } catch {
    return [];
  }
}

export const __testHooks = {
  languageIdForPath,
  supportsSymbols,
};
