import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import * as vscode from 'vscode';
import {
  buildWorkspaceContextIndex,
  extractImportSpecifiers,
  formatWorkspaceContextForPrompt,
  isSensitiveWorkspacePath,
  parseDecisionLedger,
  parseWorkspaceContextIndex,
  symbolCandidateScore,
  workspaceContextFileNeedsRefresh,
  workspaceCacheContext,
  type WorkspaceContextFile,
  type WorkspaceContextIndexState,
  type WorkspaceContextSymbol,
  type WorkspaceDecisionRecord,
  type WorkspaceCacheContext,
} from './workspace-context-state';
import type { VerifiedSolutionFile } from './verified-solution-memory';

const INDEXABLE_GLOB = '**/*.{ts,tsx,js,jsx,mjs,cjs,json,jsonc,md,mdx,py,go,rs,java,kt,swift,cs,cpp,c,h,hpp,css,scss,html,yml,yaml,toml,sql,prisma,sol,sh,ps1}';
const EXCLUDE_GLOB = '**/{.git,node_modules,dist,out,build,.next,.turbo,coverage,.cache,.venv,venv,__pycache__,artifacts}/**';
const MAX_FILES = 25_000;
const MAX_FILE_BYTES = 512_000;
const INITIAL_SYMBOL_BUDGET = 96;
const SYMBOL_BATCH_SIZE = 8;
const REFRESH_DEBOUNCE_MS = 1_500;
const WORKSPACE_IDENTITY_CHECK_MS = 5_000;

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
  private readonly dirtyPaths = new Set<string>();
  private lastWorkspaceIdentityCheckAt = 0;
  private readonly invalidationEmitter = new vscode.EventEmitter<{ workspaceId: string; path?: string }>();
  readonly onDidInvalidate = this.invalidationEmitter.event;

  constructor(private readonly context: vscode.ExtensionContext) {
    this.workspaceId = currentWorkspaceId();
    this.indexFile = this.indexFileFor(this.workspaceId);
    this.state = this.readPersisted(this.workspaceId, this.indexFile);
    this.watcher = vscode.workspace.createFileSystemWatcher(INDEXABLE_GLOB);
    context.subscriptions.push(
      this.watcher,
      this.watcher.onDidCreate((uri) => this.invalidateAndSchedule(uri)),
      this.watcher.onDidChange((uri) => this.invalidateAndSchedule(uri)),
      this.watcher.onDidDelete((uri) => this.invalidateAndSchedule(uri)),
      vscode.workspace.onDidChangeWorkspaceFolders(() => {
        this.switchWorkspace();
        this.scheduleRefresh(true);
      }),
    );
    void this.refresh();
  }

  contextFor(prompt: string): string {
    this.ensureWorkspaceIdentity();
    return formatWorkspaceContextForPrompt(this.state, prompt, {
      activeFile: activeWorkspaceFile(),
      maxEstimatedTokens: 4_000,
    });
  }

  cacheContextFor(prompt: string): WorkspaceCacheContext | null {
    this.ensureWorkspaceIdentity();
    return workspaceCacheContext(this.state, prompt, {
      activeFile: activeWorkspaceFile(),
    });
  }

  workspaceIdValue(): string | null {
    return this.state?.workspaceId ?? this.workspaceId;
  }

  allFileHashes(): VerifiedSolutionFile[] {
    return this.state?.files.map((file) => ({
      path: file.path,
      sha256: file.sha256,
    })) ?? [];
  }

  fileHashes(paths: string[]): VerifiedSolutionFile[] {
    const requested = new Set(
      paths.map((file) => this.normalizeWorkspaceFile(file)).filter(Boolean),
    );
    return this.allFileHashes().filter((file) => requested.has(file.path));
  }

  headCommit(): string | null {
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!root) return null;
    try {
      const value = execFileSync('git', ['rev-parse', 'HEAD'], {
        cwd: root,
        encoding: 'utf8',
        timeout: 2_000,
        windowsHide: true,
      }).trim();
      return /^[a-f0-9]{40,64}$/i.test(value) ? value : null;
    } catch {
      return null;
    }
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
    this.invalidationEmitter.dispose();
    this.watcher.dispose();
  }

  private invalidateAndSchedule(uri?: vscode.Uri): void {
    const workspaceId = this.workspaceId;
    const invalidatedPath = uri ? relativeWorkspacePath(uri) : null;
    if (invalidatedPath) this.dirtyPaths.add(invalidatedPath);
    if (workspaceId) {
      this.invalidationEmitter.fire({
        workspaceId,
        ...(invalidatedPath ? { path: invalidatedPath } : {}),
      });
    }
    this.scheduleRefresh(false);
  }

  private scheduleRefresh(force = false): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    this.refreshTimer = setTimeout(() => void this.refresh(force), REFRESH_DEBOUNCE_MS);
    this.refreshTimer.unref?.();
  }

  private indexFileFor(workspaceId: string | null): string | null {
    return workspaceId
      ? path.join(
          process.env.FOUNDER_CODE_INTELLIGENCE_DIR
            ?? path.join(os.homedir(), '.founder-ide', 'code-intelligence'),
          `${workspaceId}.json`,
        )
      : null;
  }

  private normalizeWorkspaceFile(file: string): string {
    if (!path.isAbsolute(file)) {
      return file.replaceAll('\\', '/').replace(/^\.\//, '');
    }
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      const relative = path.relative(folder.uri.fsPath, file);
      if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
        continue;
      }
      const normalized = relative.replaceAll('\\', '/');
      return (vscode.workspace.workspaceFolders?.length ?? 0) > 1
        ? `${folder.name}/${normalized}`
        : normalized;
    }
    return '';
  }

  private switchWorkspace(): void {
    const workspaceId = currentWorkspaceId();
    if (workspaceId === this.workspaceId) return;
    this.workspaceId = workspaceId;
    this.indexFile = this.indexFileFor(workspaceId);
    this.state = this.readPersisted(workspaceId, this.indexFile);
    this.dirtyPaths.clear();
  }

  private ensureWorkspaceIdentity(): void {
    if (Date.now() - this.lastWorkspaceIdentityCheckAt < WORKSPACE_IDENTITY_CHECK_MS) {
      return;
    }
    this.lastWorkspaceIdentityCheckAt = Date.now();
    const previous = this.workspaceId;
    this.switchWorkspace();
    if (this.workspaceId !== previous) this.scheduleRefresh(false);
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
    const discovered = await vscode.workspace.findFiles(INDEXABLE_GLOB, EXCLUDE_GLOB, MAX_FILES);
    const uris = filterIndexableUris(discovered);
    const previous = new Map(this.state?.files.map((file) => [file.path, file]) ?? []);
    const next: WorkspaceContextFile[] = [];
    let decisions: WorkspaceDecisionRecord[] = this.state?.decisions ?? [];
    let decisionLedgerSeen = false;
    const symbolQueue: Array<{ uri: vscode.Uri; file: WorkspaceContextFile }> = [];

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
      if (isDecisionLedger) decisionLedgerSeen = true;
      if (!force && !this.dirtyPaths.has(relativePath) && !workspaceContextFileNeedsRefresh(old, {
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
        symbolLocations: [],
        imports: extractImportSpecifiers(source, languageIdForPath(relativePath)),
      };
      next.push(file);
      if (isDecisionLedger) {
        decisions = parseDecisionLedger(source, relativePath, sha256);
      }
      if (supportsSymbols(relativePath)) {
        symbolQueue.push({ uri, file });
      }
    }

    symbolQueue.sort((left, right) =>
      symbolCandidateScore(right.file.path) - symbolCandidateScore(left.file.path)
      || left.file.path.localeCompare(right.file.path),
    );
    const selectedSymbolFiles = symbolQueue.slice(0, INITIAL_SYMBOL_BUDGET);
    for (let offset = 0; offset < selectedSymbolFiles.length; offset += SYMBOL_BATCH_SIZE) {
      const batch = selectedSymbolFiles.slice(offset, offset + SYMBOL_BATCH_SIZE);
      await Promise.all(batch.map(async ({ uri, file }) => {
        file.symbolLocations = await documentSymbols(uri);
        file.symbols = file.symbolLocations.map((symbol) => symbol.name);
      }));
    }

    if (!decisionLedgerSeen) decisions = [];
    if (workspaceId !== currentWorkspaceId() || indexFile !== this.indexFile) {
      this.scheduleRefresh(false);
      return;
    }
    this.state = buildWorkspaceContextIndex(
      workspaceId,
      next,
      new Date().toISOString(),
      decisions,
    );
    this.writePersisted(this.state, indexFile);
    this.dirtyPaths.clear();
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
    ?.map((folder) => workspaceIdentity(folder.uri.fsPath))
    .sort();
  if (!roots || roots.length === 0) return null;
  return createHash('sha256').update(roots.join('\n')).digest('hex').slice(0, 24);
}

function workspaceIdentity(root: string): string {
  const resolved = path.resolve(root).toLowerCase();
  try {
    const gitDir = execFileSync('git', ['rev-parse', '--absolute-git-dir'], {
      cwd: root,
      encoding: 'utf8',
      timeout: 2_000,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim().toLowerCase();
    const branch = execFileSync('git', ['rev-parse', '--abbrev-ref', 'HEAD'], {
      cwd: root,
      encoding: 'utf8',
      timeout: 2_000,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim().toLowerCase();
    return `${resolved}\n${gitDir}\n${branch}`;
  } catch {
    return resolved;
  }
}

function activeWorkspaceFile(): string | null {
  const uri = vscode.window.activeTextEditor?.document.uri;
  return uri ? relativeWorkspacePath(uri) : null;
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

async function documentSymbols(uri: vscode.Uri): Promise<WorkspaceContextSymbol[]> {
  try {
    const symbols = await vscode.commands.executeCommand<
      Array<vscode.DocumentSymbol | vscode.SymbolInformation>
    >('vscode.executeDocumentSymbolProvider', uri);
    if (!symbols) return [];
    const locations: WorkspaceContextSymbol[] = [];
    const visit = (symbol: vscode.DocumentSymbol | vscode.SymbolInformation): void => {
      if (locations.length >= 40) return;
      const range = 'location' in symbol ? symbol.location.range : symbol.range;
      if (symbol.name && !locations.some((candidate) => candidate.name === symbol.name)) {
        locations.push({
          name: symbol.name,
          startLine: range.start.line + 1,
          endLine: Math.max(range.start.line + 1, range.end.line + 1),
          kind: vscode.SymbolKind[symbol.kind],
        });
      }
      if ('children' in symbol) {
        for (const child of symbol.children.slice(0, 12)) visit(child);
      }
    };
    for (const symbol of symbols) visit(symbol);
    return locations;
  } catch {
    return [];
  }
}

function filterIndexableUris(uris: vscode.Uri[]): vscode.Uri[] {
  const safe = uris.filter((uri) => !isSensitiveWorkspacePath(uri.fsPath));
  const ignored = new Set<string>();
  const grouped = new Map<string, Array<{ uri: vscode.Uri; relative: string }>>();
  for (const uri of safe) {
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (!folder) continue;
    const relative = path.relative(folder.uri.fsPath, uri.fsPath).replaceAll('\\', '/');
    const entries = grouped.get(folder.uri.fsPath) ?? [];
    entries.push({ uri, relative });
    grouped.set(folder.uri.fsPath, entries);
  }
  for (const [root, entries] of grouped) {
    const result = spawnSync(
      'git',
      ['check-ignore', '--stdin', '-z'],
      {
        cwd: root,
        encoding: 'utf8',
        input: `${entries.map((entry) => entry.relative).join('\0')}\0`,
        timeout: 5_000,
        maxBuffer: 5 * 1024 * 1024,
        windowsHide: true,
      },
    );
    if (result.status !== 0 && result.status !== 1) continue;
    for (const relative of String(result.stdout ?? '').split('\0').filter(Boolean)) {
      ignored.add(`${root.toLowerCase()}\0${relative.replaceAll('\\', '/').toLowerCase()}`);
    }
  }
  return safe.filter((uri) => {
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (!folder) return false;
    const relative = path.relative(folder.uri.fsPath, uri.fsPath).replaceAll('\\', '/').toLowerCase();
    return !ignored.has(`${folder.uri.fsPath.toLowerCase()}\0${relative}`);
  });
}

export const __testHooks = {
  languageIdForPath,
  supportsSymbols,
  symbolCandidateScore,
  isSensitiveWorkspacePath,
};
