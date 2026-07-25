import { createHash } from 'node:crypto';

export const WORKSPACE_CONTEXT_INDEX_VERSION = 3 as const;
export const DEFAULT_WORKSPACE_CONTEXT_TOKEN_BUDGET = 4_000;

export interface WorkspaceContextSymbol {
  name: string;
  startLine: number;
  endLine: number;
  kind?: string;
}

export interface WorkspaceContextFile {
  path: string;
  languageId: string;
  size: number;
  mtimeMs: number;
  sha256: string;
  symbols: string[];
  symbolLocations?: WorkspaceContextSymbol[];
  imports: string[];
}

export interface WorkspaceDecisionRecord {
  id: string;
  title: string;
  status: 'accepted' | 'rejected' | 'reopened';
  rationale: string;
  sourcePath: string;
  sourceHash: string;
}

export interface WorkspaceContextIndexState {
  version: typeof WORKSPACE_CONTEXT_INDEX_VERSION;
  workspaceId: string;
  indexedAt: string;
  files: WorkspaceContextFile[];
  decisions: WorkspaceDecisionRecord[];
}

export interface WorkspaceContextFileStat {
  path: string;
  size: number;
  mtimeMs: number;
}

export interface WorkspaceCacheContext {
  workspaceId: string;
  contextHash: string;
  files: Array<{ path: string; sha256: string }>;
}

export interface WorkspaceContextRankOptions {
  limit?: number;
  activeFile?: string | null;
}

export interface WorkspaceContextFormatOptions extends WorkspaceContextRankOptions {
  maxEstimatedTokens?: number;
}

export interface WorkspaceContextTuple {
  file: string;
  startLine: number;
  endLine: number;
  score: number;
  reason: string;
}

interface RankedWorkspaceFile {
  file: WorkspaceContextFile;
  score: number;
  textScore: number;
  graphScore: number;
  reason: string;
}

const IMPORTANT_FILES = new Set([
  'agents.md',
  'package.json',
  'readme.md',
  'tsconfig.json',
  'pyproject.toml',
  'cargo.toml',
  'go.mod',
  'dockerfile',
]);
const FILE_SEARCH_CACHE = new WeakMap<
  WorkspaceContextFile,
  { normalizedPath: string; pathTerms: Set<string>; symbolTerms: Set<string> }
>();
const GRAPH_NEIGHBOR_CACHE = new WeakMap<WorkspaceContextFile[], number[][]>();
const REVERSE_DEPENDENCY_CACHE = new WeakMap<
  WorkspaceContextFile[],
  Map<string, string[]>
>();

function terms(value: string): string[] {
  return value
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .split(/\s+/)
    .filter((term) => term.length > 1);
}

export function parseWorkspaceContextIndex(
  value: unknown,
  expectedWorkspaceId: string,
): WorkspaceContextIndexState | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const candidate = value as Partial<WorkspaceContextIndexState>;
  if (
    candidate.version !== WORKSPACE_CONTEXT_INDEX_VERSION ||
    candidate.workspaceId !== expectedWorkspaceId ||
    typeof candidate.indexedAt !== 'string' ||
    !Array.isArray(candidate.files)
  ) {
    return null;
  }
  const files = candidate.files.filter((file): file is WorkspaceContextFile =>
    Boolean(
      file &&
      typeof file.path === 'string' &&
      typeof file.languageId === 'string' &&
      Number.isFinite(file.size) &&
      Number.isFinite(file.mtimeMs) &&
      typeof file.sha256 === 'string' &&
      Array.isArray(file.symbols) &&
      (!file.symbolLocations || (
        Array.isArray(file.symbolLocations) &&
        file.symbolLocations.every(isWorkspaceContextSymbol)
      )) &&
      Array.isArray(file.imports),
    ),
  );
  const decisions = Array.isArray(candidate.decisions)
    ? candidate.decisions.filter(isDecisionRecord)
    : [];
  return { ...candidate, files, decisions } as WorkspaceContextIndexState;
}

export function workspaceContextFileNeedsRefresh(
  previous: WorkspaceContextFile | undefined,
  next: WorkspaceContextFileStat,
): boolean {
  return !previous || previous.size !== next.size || previous.mtimeMs !== next.mtimeMs;
}

export function buildWorkspaceContextIndex(
  workspaceId: string,
  files: WorkspaceContextFile[],
  indexedAt = new Date().toISOString(),
  decisions: WorkspaceDecisionRecord[] = [],
): WorkspaceContextIndexState {
  return {
    version: WORKSPACE_CONTEXT_INDEX_VERSION,
    workspaceId,
    indexedAt,
    files: [...files].sort((a, b) => a.path.localeCompare(b.path)),
    decisions: [...decisions].sort((a, b) => a.title.localeCompare(b.title)),
  };
}

export function extractImportSpecifiers(source: string, languageId: string): string[] {
  const imports = new Set<string>();
  const patterns = /^(?:typescript|typescriptreact|javascript|javascriptreact|ts|tsx|js|jsx|mjs|cjs)$/.test(languageId)
    ? [
        /\b(?:import|export)\s+(?:[^'"\n]+?\s+from\s+)?['"]([^'"]+)['"]/g,
        /\brequire\(\s*['"]([^'"]+)['"]\s*\)/g,
        /\bimport\(\s*['"]([^'"]+)['"]\s*\)/g,
      ]
    : languageId === 'python' || languageId === 'py'
      ? [/^\s*from\s+([\w.]+)\s+import\s+/gm, /^\s*import\s+([\w.]+)/gm]
      : [];
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      const value = match[1]?.trim();
      if (value) imports.add(value);
      if (imports.size >= 80) break;
    }
  }
  return [...imports].sort();
}

export function parseDecisionLedger(
  markdown: string,
  sourcePath: string,
  sourceHash: string,
): WorkspaceDecisionRecord[] {
  const sections = markdown.split(/^##\s+/m).slice(1);
  const records: WorkspaceDecisionRecord[] = [];
  for (const section of sections) {
    const [heading = '', ...bodyLines] = section.split(/\r?\n/);
    const body = bodyLines.join('\n').trim();
    const marker = heading.match(/^\s*\[(accepted|rejected|reopened)\]\s*(.+)$/i);
    const statusLine = body.match(/^\s*(?:status|decision)\s*:\s*(accepted|rejected|reopened)\s*$/im);
    const status = (marker?.[1] ?? statusLine?.[1])?.toLowerCase() as WorkspaceDecisionRecord['status'] | undefined;
    const title = (marker?.[2] ?? heading).trim();
    if (!status || !title) continue;
    records.push({
      id: `${sourceHash.slice(0, 12)}:${records.length + 1}`,
      title: title.slice(0, 240),
      status,
      rationale: body.replace(/^\s*(?:status|decision)\s*:\s*(?:accepted|rejected|reopened)\s*$/im, '').trim().slice(0, 1_200),
      sourcePath,
      sourceHash,
    });
    if (records.length >= 100) break;
  }
  return records;
}

export function dependencyImpact(
  index: WorkspaceContextIndexState,
  filePath: string,
): { imports: string[]; importedBy: string[] } {
  const file = index.files.find((candidate) => candidate.path === filePath);
  if (!file) return { imports: [], importedBy: [] };
  const importedBy = index.files
    .filter((candidate) => candidate.path !== filePath)
    .filter((candidate) => candidate.imports.some((specifier) => resolvesTo(candidate.path, specifier, filePath)))
    .map((candidate) => candidate.path)
    .sort()
    .slice(0, 12);
  return { imports: file.imports.slice(0, 12), importedBy };
}

function fileScore(file: WorkspaceContextFile, queryTerms: string[]): number {
  const { normalizedPath, pathTerms, symbolTerms } = fileSearchTerms(file);
  let score = IMPORTANT_FILES.has(normalizedPath.split('/').at(-1) ?? '') ? 1 : 0;
  for (const term of queryTerms) {
    if (pathTerms.has(term)) score += 5;
    if (symbolTerms.has(term)) score += 3;
    if (normalizedPath.includes(term)) score += 1;
  }
  return score;
}

export function rankWorkspaceContextFiles(
  index: WorkspaceContextIndexState,
  query: string,
  limitOrOptions: number | WorkspaceContextRankOptions = 14,
): WorkspaceContextFile[] {
  return rankWorkspaceFiles(index, query, normalizeRankOptions(limitOrOptions))
    .map((entry) => entry.file);
}

export function rankWorkspaceContextTuples(
  index: WorkspaceContextIndexState,
  query: string,
  options: WorkspaceContextRankOptions = {},
): WorkspaceContextTuple[] {
  const ranked = rankWorkspaceFiles(index, query, normalizeRankOptions(options));
  const queryTerms = terms(query);
  return ranked.map((entry) => {
    const location = bestSymbolLocation(entry.file, queryTerms);
    return {
      file: entry.file.path,
      startLine: location?.startLine ?? 1,
      endLine: location?.endLine ?? 80,
      score: Number(entry.score.toFixed(6)),
      reason: entry.reason,
    };
  });
}

export function workspaceCacheContext(
  index: WorkspaceContextIndexState | null,
  query: string,
  limitOrOptions: number | WorkspaceContextRankOptions = 14,
): WorkspaceCacheContext | null {
  if (!index) return null;
  const ranked = rankWorkspaceContextFiles(index, query, limitOrOptions);
  if (ranked.length === 0) return null;
  const files = ranked.map((file) => ({ path: file.path, sha256: file.sha256 }));
  const relevantDecisionHashes = index.decisions
    .filter((decision) => decisionScore(decision, terms(query)) > 0)
    .map((decision) => `${decision.id}:${decision.sourceHash}`)
    .sort();
  const contextHash = createHash('sha256')
    .update(JSON.stringify({ files, relevantDecisionHashes }))
    .digest('hex');
  return { workspaceId: index.workspaceId, contextHash, files };
}

export function formatWorkspaceContextForPrompt(
  index: WorkspaceContextIndexState | null,
  query: string,
  limitOrOptions: number | WorkspaceContextFormatOptions = 14,
): string {
  if (!index || index.files.length === 0) return '';
  const options = normalizeFormatOptions(limitOrOptions);
  const ranked = rankWorkspaceFiles(index, query, options);
  if (ranked.length === 0) return '';
  const symbolCount = index.files.reduce((sum, file) => sum + file.symbols.length, 0);
  const result = [
    '## Founder local project map',
    `Indexed ${index.files.length} files and ${symbolCount} symbols. Source contents remain local.`,
  ];
  const queryTerms = terms(query);
  const importedBy = reverseDependencyMap(index.files);
  for (const entry of ranked) {
    const file = entry.file;
    const location = bestSymbolLocation(file, queryTerms);
    const range = location ? `:${location.startLine}-${location.endLine}` : ':1-80';
    const outline = file.symbols.length > 0
      ? ` :: ${file.symbols.slice(0, 8).join(', ')}`
      : '';
    const dependents = importedBy.get(file.path) ?? [];
    const dependency = dependents.length > 0
      ? ` | used by ${dependents.slice(0, 4).join(', ')}`
      : '';
    if (!appendWithinBudget(
      result,
      `- ${file.path}${range} [${file.languageId}] score=${entry.score.toFixed(3)} (${entry.reason})${outline}${dependency}`,
      options.maxEstimatedTokens,
    )) break;
  }

  const decisions = index.decisions
    .map((decision) => ({ decision, score: decisionScore(decision, queryTerms) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score || a.decision.title.localeCompare(b.decision.title))
    .slice(0, 5);
  if (decisions.length > 0) {
    appendWithinBudget(result, '## Relevant founder decisions', options.maxEstimatedTokens);
    appendWithinBudget(
      result,
      'Respect rejected decisions unless the founder explicitly reopens them.',
      options.maxEstimatedTokens,
    );
    for (const { decision } of decisions) {
      appendWithinBudget(
        result,
        `- ${decision.status.toUpperCase()}: ${decision.title}${decision.rationale ? ` - ${decision.rationale}` : ''}`,
        options.maxEstimatedTokens,
      );
    }
  }
  return result.join('\n');
}

export function estimateWorkspaceContextTokens(value: string): number {
  return Math.ceil(value.length / 4);
}

export function isSensitiveWorkspacePath(file: string): boolean {
  const normalized = file.replaceAll('\\', '/').toLowerCase();
  const name = normalized.split('/').at(-1) ?? '';
  if (name === '.env' || name.startsWith('.env.')) return true;
  if (/^(?:id_rsa|id_ed25519|credentials\.json|service-account(?:\.[^.]+)?\.json|node-config\.json|install\.json|\.npmrc|\.pypirc)$/.test(name)) {
    return true;
  }
  return /\.(?:pem|key|p12|pfx|jks|keystore|kdbx)$/i.test(name);
}

export function symbolCandidateScore(file: string): number {
  const normalized = file.replaceAll('\\', '/').toLowerCase();
  const segments = normalized.split('/');
  const name = segments.at(-1) ?? normalized;
  let score = Math.max(0, 40 - segments.length * 4);
  if (segments.includes('src')) score += 20;
  if (segments.includes('apps') || segments.includes('packages')) score += 8;
  if (/^(?:index|main|app|server|extension|router|routes)\./.test(name)) score += 30;
  if (/(?:^|[.-])(?:spec|test)\./.test(name) || segments.some((part) => part === 'test' || part === 'tests')) {
    score -= 30;
  }
  return score;
}

function rankWorkspaceFiles(
  index: WorkspaceContextIndexState,
  query: string,
  options: Required<WorkspaceContextRankOptions>,
): RankedWorkspaceFile[] {
  const queryTerms = terms(query);
  const activeFile = normalizeWorkspacePath(options.activeFile ?? '');
  const textScores = index.files.map((file) => fileScore(file, queryTerms));
  const graphScores = personalizedPageRank(index.files, textScores, activeFile);
  return index.files
    .map((file, indexPosition) => {
      const textScore = textScores[indexPosition] ?? 0;
      const graphScore = graphScores[indexPosition] ?? 0;
      const activeBoost = activeFile && normalizeWorkspacePath(file.path) === activeFile ? 25 : 0;
      const score = textScore * 10 + graphScore * 100 + activeBoost;
      return {
        file,
        textScore,
        graphScore,
        score,
        reason: rankReason(file, queryTerms, textScore, graphScore, activeBoost > 0),
      };
    })
    .filter((entry) => entry.textScore > 0 || entry.graphScore > 0.00001 || entry.score > 0)
    .sort((a, b) => b.score - a.score || a.file.path.localeCompare(b.file.path))
    .slice(0, options.limit);
}

function personalizedPageRank(
  files: WorkspaceContextFile[],
  textScores: number[],
  activeFile: string,
): number[] {
  if (files.length === 0) return [];
  const neighbors = graphNeighbors(files);

  const seeds = textScores.map((score) => Math.max(0, score));
  const activeIndex = files.findIndex((file) => normalizeWorkspacePath(file.path) === activeFile);
  if (activeIndex >= 0) {
    seeds[activeIndex] = (seeds[activeIndex] ?? 0) + Math.max(10, sum(seeds));
  }
  let seedTotal = sum(seeds);
  if (seedTotal === 0) {
    for (let index = 0; index < files.length; index += 1) {
      const name = files[index]!.path.toLowerCase().split('/').at(-1) ?? '';
      if (IMPORTANT_FILES.has(name)) seeds[index] = 1;
    }
    seedTotal = sum(seeds);
  }
  if (seedTotal === 0) return files.map(() => 0);
  const teleport = seeds.map((score) => score / seedTotal);
  let rank = [...teleport];
  const damping = 0.85;
  for (let iteration = 0; iteration < 12; iteration += 1) {
    const next = teleport.map((weight) => (1 - damping) * weight);
    let dangling = 0;
    for (let from = 0; from < files.length; from += 1) {
      const adjacent = neighbors[from]!;
      if (adjacent.length === 0) {
        dangling += rank[from] ?? 0;
        continue;
      }
      const share = damping * (rank[from] ?? 0) / adjacent.length;
      for (const to of adjacent) next[to] = (next[to] ?? 0) + share;
    }
    if (dangling > 0) {
      for (let index = 0; index < next.length; index += 1) {
        next[index] = (next[index] ?? 0) + damping * dangling * (teleport[index] ?? 0);
      }
    }
    rank = next;
  }
  return rank;
}

function bestSymbolLocation(
  file: WorkspaceContextFile,
  queryTerms: string[],
): WorkspaceContextSymbol | undefined {
  const locations = file.symbolLocations ?? [];
  return locations
    .map((location) => ({
      location,
      score: terms(location.name).reduce(
        (total, term) => total + (queryTerms.includes(term) ? 1 : 0),
        0,
      ),
    }))
    .sort((a, b) => b.score - a.score || a.location.startLine - b.location.startLine)[0]?.location;
}

function rankReason(
  file: WorkspaceContextFile,
  queryTerms: string[],
  textScore: number,
  graphScore: number,
  active: boolean,
): string {
  if (active) return 'active file';
  const { symbolTerms, pathTerms } = fileSearchTerms(file);
  if (queryTerms.some((term) => symbolTerms.has(term))) return 'symbol match';
  if (queryTerms.some((term) => pathTerms.has(term))) return 'path match';
  if (textScore > 0) return 'project entry point';
  if (graphScore > 0) return 'dependency neighbor';
  return 'repository map';
}

function normalizeRankOptions(
  value: number | WorkspaceContextRankOptions,
): Required<WorkspaceContextRankOptions> {
  if (typeof value === 'number') {
    return { limit: Math.max(1, value), activeFile: null };
  }
  return {
    limit: Math.max(1, value.limit ?? 14),
    activeFile: value.activeFile ?? null,
  };
}

function normalizeFormatOptions(
  value: number | WorkspaceContextFormatOptions,
): Required<WorkspaceContextFormatOptions> {
  if (typeof value === 'number') {
    return {
      limit: Math.max(1, value),
      activeFile: null,
      maxEstimatedTokens: DEFAULT_WORKSPACE_CONTEXT_TOKEN_BUDGET,
    };
  }
  return {
    limit: Math.max(1, value.limit ?? 14),
    activeFile: value.activeFile ?? null,
    maxEstimatedTokens: Math.max(256, value.maxEstimatedTokens ?? DEFAULT_WORKSPACE_CONTEXT_TOKEN_BUDGET),
  };
}

function appendWithinBudget(lines: string[], value: string, maxTokens: number): boolean {
  const prefix = lines.length > 0 ? `${lines.join('\n')}\n` : '';
  const remainingCharacters = maxTokens * 4 - prefix.length;
  if (remainingCharacters <= 1) return false;
  lines.push(value.length <= remainingCharacters
    ? value
    : remainingCharacters <= 3
      ? value.slice(0, remainingCharacters)
      : `${value.slice(0, remainingCharacters - 3)}...`);
  return value.length <= remainingCharacters;
}

function decisionScore(decision: WorkspaceDecisionRecord, queryTerms: string[]): number {
  const haystack = new Set(terms(`${decision.title} ${decision.rationale}`));
  return queryTerms.reduce((score, term) => score + (haystack.has(term) ? 1 : 0), 0);
}

function resolvesTo(importerPath: string, specifier: string, targetPath: string): boolean {
  if (!specifier.startsWith('.')) return false;
  const base = resolveImportKey(importerPath, specifier);
  const target = normalizeModulePath(targetPath);
  return target === base || target === `${base}/index`;
}

function resolveImportKey(importerPath: string, specifier: string): string {
  return normalizeModulePath(
    `${importerPath.slice(0, importerPath.lastIndexOf('/') + 1)}${specifier}`,
  );
}

function reverseDependencyMap(
  files: WorkspaceContextFile[],
): Map<string, string[]> {
  const cached = REVERSE_DEPENDENCY_CACHE.get(files);
  if (cached) return cached;
  const pathByModule = new Map<string, string>();
  for (const file of files) {
    pathByModule.set(normalizeModulePath(file.path), file.path);
  }
  const reverse = new Map<string, string[]>();
  for (const importer of files) {
    for (const specifier of importer.imports) {
      if (!specifier.startsWith('.')) continue;
      const resolved = resolveImportKey(importer.path, specifier);
      const target = pathByModule.get(resolved) ?? pathByModule.get(`${resolved}/index`);
      if (!target || target === importer.path) continue;
      const dependents = reverse.get(target) ?? [];
      if (!dependents.includes(importer.path)) dependents.push(importer.path);
      reverse.set(target, dependents);
    }
  }
  for (const dependents of reverse.values()) dependents.sort();
  REVERSE_DEPENDENCY_CACHE.set(files, reverse);
  return reverse;
}

function fileSearchTerms(
  file: WorkspaceContextFile,
): { normalizedPath: string; pathTerms: Set<string>; symbolTerms: Set<string> } {
  const cached = FILE_SEARCH_CACHE.get(file);
  if (cached) return cached;
  const value = {
    normalizedPath: file.path.toLowerCase(),
    pathTerms: new Set(terms(file.path)),
    symbolTerms: new Set(file.symbols.flatMap(terms)),
  };
  FILE_SEARCH_CACHE.set(file, value);
  return value;
}

function graphNeighbors(files: WorkspaceContextFile[]): number[][] {
  const cached = GRAPH_NEIGHBOR_CACHE.get(files);
  if (cached) return cached;
  const pathToIndex = new Map<string, number>();
  for (let index = 0; index < files.length; index += 1) {
    pathToIndex.set(normalizeModulePath(files[index]!.path), index);
  }
  const neighborSets = files.map(() => new Set<number>());
  for (let index = 0; index < files.length; index += 1) {
    const importer = files[index]!;
    for (const specifier of importer.imports) {
      if (!specifier.startsWith('.')) continue;
      const resolved = resolveImportKey(importer.path, specifier);
      const target = pathToIndex.get(resolved) ?? pathToIndex.get(`${resolved}/index`);
      if (target === undefined || target === index) continue;
      neighborSets[index]!.add(target);
      neighborSets[target]!.add(index);
    }
  }
  const neighbors = neighborSets.map((entries) => [...entries]);
  GRAPH_NEIGHBOR_CACHE.set(files, neighbors);
  return neighbors;
}

function normalizeModulePath(value: string): string {
  const parts: string[] = [];
  for (const part of value.replaceAll('\\', '/').split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') parts.pop(); else parts.push(part);
  }
  return parts.join('/').replace(/\.(?:d\.)?(?:ts|tsx|js|jsx|mjs|cjs|json)$/i, '');
}

function normalizeWorkspacePath(value: string): string {
  return value.replaceAll('\\', '/').replace(/^\.\//, '').toLowerCase();
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

function isWorkspaceContextSymbol(value: unknown): value is WorkspaceContextSymbol {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const symbol = value as Partial<WorkspaceContextSymbol>;
  return typeof symbol.name === 'string' &&
    Number.isInteger(symbol.startLine) &&
    Number.isInteger(symbol.endLine) &&
    (symbol.kind === undefined || typeof symbol.kind === 'string');
}

function isDecisionRecord(value: unknown): value is WorkspaceDecisionRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const record = value as Partial<WorkspaceDecisionRecord>;
  return typeof record.id === 'string' &&
    typeof record.title === 'string' &&
    (record.status === 'accepted' || record.status === 'rejected' || record.status === 'reopened') &&
    typeof record.rationale === 'string' &&
    typeof record.sourcePath === 'string' &&
    typeof record.sourceHash === 'string';
}
