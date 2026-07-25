import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  buildWorkspaceContextIndex,
  dependencyImpact,
  extractImportSpecifiers,
  formatWorkspaceContextForPrompt,
  isSensitiveWorkspacePath,
  parseDecisionLedger,
  parseWorkspaceContextIndex,
  rankWorkspaceContextTuples,
  type WorkspaceContextFile,
  type WorkspaceContextIndexState,
  type WorkspaceContextSymbol,
  type WorkspaceDecisionRecord,
} from 'founder-ide-extension/code-intelligence';

const MAX_FILES = 25_000;
const MAX_FILE_BYTES = 512_000;
const INDEXABLE_EXTENSIONS = new Set([
  '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.json', '.jsonc',
  '.md', '.mdx', '.py', '.go', '.rs', '.java', '.kt', '.swift', '.cs',
  '.cpp', '.c', '.h', '.hpp', '.css', '.scss', '.html', '.yml', '.yaml',
  '.toml', '.sql', '.prisma', '.sol', '.sh', '.ps1',
]);
const EXCLUDED_DIRECTORIES = new Set([
  '.git', 'node_modules', 'dist', 'out', 'build', '.next', '.turbo',
  'coverage', '.cache', '.venv', 'venv', '__pycache__', 'artifacts',
]);

export interface CodeIntelligenceRefreshResult {
  workspaceId: string;
  files: number;
  symbols: number;
  refreshedFiles: number;
  reusedFiles: number;
  indexedAt: string;
  persistedAt: string;
  persistence: 'disk' | 'memory';
}

export interface CodeIntelligenceQuery {
  query: string;
  activeFile?: string | null;
  limit?: number;
  maxEstimatedTokens?: number;
}

export interface CodeIntelligenceQueryResult extends CodeIntelligenceRefreshResult {
  promptMap: string;
  tuples: ReturnType<typeof rankWorkspaceContextTuples>;
}

export class FounderCodeIntelligenceIndex {
  private readonly workspaceRoot: string;
  private readonly workspaceId: string;
  private readonly persistedAt: string;
  private state: WorkspaceContextIndexState | null;
  private lastRefresh: CodeIntelligenceRefreshResult | null = null;
  private refreshPromise: Promise<CodeIntelligenceRefreshResult> | null = null;
  private readonly queryCache = new Map<string, CodeIntelligenceQueryResult>();

  constructor(workspaceRoot: string) {
    this.workspaceRoot = path.resolve(workspaceRoot);
    this.workspaceId = workspaceIndexId(this.workspaceRoot);
    this.persistedAt = sharedIndexFile(this.workspaceId);
    this.state = readPersistedIndex(this.persistedAt, this.workspaceId);
  }

  status(): CodeIntelligenceRefreshResult {
    const state = this.state;
    return this.lastRefresh ?? {
      workspaceId: this.workspaceId,
      files: state?.files.length ?? 0,
      symbols: state?.files.reduce((total, file) => total + file.symbols.length, 0) ?? 0,
      refreshedFiles: 0,
      reusedFiles: state?.files.length ?? 0,
      indexedAt: state?.indexedAt ?? '',
      persistedAt: this.persistedAt,
      persistence: state ? 'disk' : 'memory',
    };
  }

  async refresh(force = false): Promise<CodeIntelligenceRefreshResult> {
    if (this.refreshPromise) return this.refreshPromise;
    this.refreshPromise = this.rebuild(force).finally(() => {
      this.refreshPromise = null;
    });
    return this.refreshPromise;
  }

  async query(input: CodeIntelligenceQuery): Promise<CodeIntelligenceQueryResult> {
    const refresh = await this.ready();
    const state = this.state;
    if (!state) {
      return { ...refresh, promptMap: '', tuples: [] };
    }
    const options = {
      activeFile: input.activeFile ?? null,
      limit: Math.max(1, Math.min(100, input.limit ?? 18)),
      maxEstimatedTokens: Math.max(
        256,
        Math.min(4_000, input.maxEstimatedTokens ?? 4_000),
      ),
    };
    const cacheKey = JSON.stringify({
      indexedAt: state.indexedAt,
      query: input.query.trim(),
      activeFile: options.activeFile,
      limit: options.limit,
      maxEstimatedTokens: options.maxEstimatedTokens,
    });
    const cached = this.queryCache.get(cacheKey);
    if (cached) {
      this.queryCache.delete(cacheKey);
      this.queryCache.set(cacheKey, cached);
      return cached;
    }
    const result = {
      ...refresh,
      promptMap: formatWorkspaceContextForPrompt(state, input.query, options),
      tuples: rankWorkspaceContextTuples(state, input.query, options),
    };
    this.queryCache.set(cacheKey, result);
    while (this.queryCache.size > 64) {
      const oldest = this.queryCache.keys().next().value;
      if (typeof oldest !== 'string') break;
      this.queryCache.delete(oldest);
    }
    return result;
  }

  async impact(file: string): Promise<ReturnType<typeof dependencyImpact>> {
    await this.ready();
    return this.state
      ? dependencyImpact(this.state, normalizeRelativePath(file))
      : { imports: [], importedBy: [] };
  }

  private async ready(): Promise<CodeIntelligenceRefreshResult> {
    return this.lastRefresh ?? this.refresh(false);
  }

  private async rebuild(force: boolean): Promise<CodeIntelligenceRefreshResult> {
    this.queryCache.clear();
    const relativeFiles = discoverWorkspaceFiles(this.workspaceRoot);
    const previous = new Map(this.state?.files.map((file) => [file.path, file]) ?? []);
    const next: WorkspaceContextFile[] = [];
    let decisions: WorkspaceDecisionRecord[] = [];
    let refreshedFiles = 0;
    let reusedFiles = 0;

    for (const relativeFile of relativeFiles) {
      const absoluteFile = path.join(this.workspaceRoot, relativeFile);
      let stat: fs.Stats;
      try {
        stat = fs.statSync(absoluteFile);
      } catch {
        continue;
      }
      if (!stat.isFile() || stat.size > MAX_FILE_BYTES) continue;
      const previousFile = previous.get(relativeFile);
      const mtimeMs = Math.trunc(stat.mtimeMs);
      if (
        !force &&
        previousFile &&
        previousFile.size === stat.size &&
        Math.trunc(previousFile.mtimeMs) === mtimeMs
      ) {
        next.push(previousFile);
        reusedFiles += 1;
        if (relativeFile.toLowerCase() === '.github/founder-os/decisions.md') {
          decisions = this.state?.decisions ?? [];
        }
        continue;
      }

      let bytes: Buffer;
      try {
        bytes = fs.readFileSync(absoluteFile);
      } catch {
        continue;
      }
      const source = bytes.toString('utf8');
      const languageId = languageIdForPath(relativeFile);
      const symbolLocations = lightweightSymbols(source, languageId);
      const sha256 = createHash('sha256').update(bytes).digest('hex');
      next.push({
        path: relativeFile,
        languageId,
        size: stat.size,
        mtimeMs,
        sha256,
        symbols: symbolLocations.map((symbol) => symbol.name),
        symbolLocations,
        imports: extractImportSpecifiers(source, languageId),
      });
      refreshedFiles += 1;
      if (relativeFile.toLowerCase() === '.github/founder-os/decisions.md') {
        decisions = parseDecisionLedger(source, relativeFile, sha256);
      }
    }

    this.state = buildWorkspaceContextIndex(
      this.workspaceId,
      next,
      new Date().toISOString(),
      decisions,
    );
    const persisted = writePersistedIndex(this.persistedAt, this.state);
    this.lastRefresh = {
      workspaceId: this.workspaceId,
      files: this.state.files.length,
      symbols: this.state.files.reduce((total, file) => total + file.symbols.length, 0),
      refreshedFiles,
      reusedFiles,
      indexedAt: this.state.indexedAt,
      persistedAt: this.persistedAt,
      persistence: persisted ? 'disk' : 'memory',
    };
    return this.lastRefresh;
  }
}

export function workspaceIndexId(workspaceRoot: string): string {
  const root = path.resolve(workspaceRoot);
  const identity = [root.toLowerCase()];
  try {
    identity.push(
      execFileSync('git', ['rev-parse', '--absolute-git-dir'], {
        cwd: root,
        encoding: 'utf8',
        timeout: 2_000,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'ignore'],
      }).trim().toLowerCase(),
    );
    identity.push(
      execFileSync('git', ['rev-parse', '--abbrev-ref', 'HEAD'], {
        cwd: root,
        encoding: 'utf8',
        timeout: 2_000,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'ignore'],
      }).trim().toLowerCase(),
    );
  } catch {
    // Non-git folders still get a stable, root-scoped index.
  }
  return createHash('sha256').update(identity.join('\n')).digest('hex').slice(0, 24);
}

export function sharedIndexFile(workspaceId: string): string {
  const root = process.env.FOUNDER_CODE_INTELLIGENCE_DIR
    ?? path.join(os.homedir(), '.founder-ide', 'code-intelligence');
  return path.join(root, `${workspaceId}.json`);
}

export function discoverWorkspaceFiles(workspaceRoot: string): string[] {
  const fromGit = spawnSync(
    'git',
    ['ls-files', '--cached', '--others', '--exclude-standard', '-z'],
    {
      cwd: workspaceRoot,
      encoding: 'utf8',
      timeout: 10_000,
      maxBuffer: 10 * 1024 * 1024,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'ignore'],
    },
  );
  const files = fromGit.status === 0
    ? String(fromGit.stdout ?? '').split('\0').filter(Boolean)
    : walkWorkspace(workspaceRoot);
  return files
    .map(normalizeRelativePath)
    .filter((file) => isIndexablePath(file))
    .sort()
    .slice(0, MAX_FILES);
}

function walkWorkspace(root: string): string[] {
  const files: string[] = [];
  const visit = (directory: string): void => {
    if (files.length >= MAX_FILES) return;
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(directory, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      if (files.length >= MAX_FILES) return;
      const absolute = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) {
        if (!isExcludedDirectory(entry.name)) visit(absolute);
      } else if (entry.isFile()) {
        files.push(path.relative(root, absolute));
      }
    }
  };
  visit(root);
  return files;
}

function isIndexablePath(file: string): boolean {
  if (!file || path.isAbsolute(file) || file.startsWith('../')) return false;
  if (isSensitiveWorkspacePath(file)) return false;
  const parts = file.toLowerCase().split('/');
  if (parts.some(isExcludedDirectory)) return false;
  const name = parts.at(-1) ?? '';
  return name === 'dockerfile' || INDEXABLE_EXTENSIONS.has(path.extname(name));
}

function isExcludedDirectory(name: string): boolean {
  const normalized = name.toLowerCase();
  return EXCLUDED_DIRECTORIES.has(normalized)
    || normalized.startsWith('node_modules-')
    || normalized.startsWith('node_modules.');
}

function lightweightSymbols(
  source: string,
  languageId: string,
): WorkspaceContextSymbol[] {
  const patterns = /^(?:ts|tsx|js|jsx|mjs|cjs)$/.test(languageId)
    ? [
        /^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)/gm,
        /^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)/gm,
      ]
    : languageId === 'py'
      ? [/^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][\w]*)/gm]
      : [];
  const locations: WorkspaceContextSymbol[] = [];
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      const name = match[1];
      if (!name || locations.some((symbol) => symbol.name === name)) continue;
      const startLine = 1 + source.slice(0, match.index ?? 0).split('\n').length - 1;
      locations.push({ name, startLine, endLine: startLine });
      if (locations.length >= 40) return locations;
    }
  }
  return locations.sort((a, b) => a.startLine - b.startLine);
}

function languageIdForPath(file: string): string {
  const name = path.basename(file).toLowerCase();
  if (name === 'dockerfile') return 'dockerfile';
  return path.extname(name).slice(1) || 'text';
}

function normalizeRelativePath(file: string): string {
  return file.replaceAll('\\', '/').replace(/^\.\//, '');
}

function readPersistedIndex(
  file: string,
  workspaceId: string,
): WorkspaceContextIndexState | null {
  try {
    return parseWorkspaceContextIndex(
      JSON.parse(fs.readFileSync(file, 'utf8')),
      workspaceId,
    );
  } catch {
    return null;
  }
}

function writePersistedIndex(
  file: string,
  state: WorkspaceContextIndexState,
): boolean {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const temporary = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(state), 'utf8');
    fs.renameSync(temporary, file);
    return true;
  } catch {
    return false;
  }
}
