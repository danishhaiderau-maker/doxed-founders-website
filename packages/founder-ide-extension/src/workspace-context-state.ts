import { createHash } from 'node:crypto';

export const WORKSPACE_CONTEXT_INDEX_VERSION = 2 as const;

export interface WorkspaceContextFile {
  path: string;
  languageId: string;
  size: number;
  mtimeMs: number;
  sha256: string;
  symbols: string[];
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
    .slice(0, 12);
  return { imports: file.imports.slice(0, 12), importedBy };
}

function fileScore(file: WorkspaceContextFile, queryTerms: string[]): number {
  const pathTerms = new Set(terms(file.path));
  const symbolTerms = new Set(file.symbols.flatMap(terms));
  let score = IMPORTANT_FILES.has(file.path.toLowerCase().split('/').at(-1) ?? '') ? 1 : 0;
  for (const term of queryTerms) {
    if (pathTerms.has(term)) score += 5;
    if (symbolTerms.has(term)) score += 3;
    if (file.path.toLowerCase().includes(term)) score += 1;
  }
  return score;
}

export function rankWorkspaceContextFiles(
  index: WorkspaceContextIndexState,
  query: string,
  limit = 14,
): WorkspaceContextFile[] {
  const queryTerms = terms(query);
  return index.files
    .map((file) => ({ file, score: fileScore(file, queryTerms) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score || a.file.path.localeCompare(b.file.path))
    .slice(0, Math.max(1, limit))
    .map((entry) => entry.file);
}

export function workspaceCacheContext(
  index: WorkspaceContextIndexState | null,
  query: string,
  limit = 14,
): WorkspaceCacheContext | null {
  if (!index) return null;
  const ranked = rankWorkspaceContextFiles(index, query, limit);
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
  limit = 14,
): string {
  if (!index || index.files.length === 0) return '';
  const ranked = rankWorkspaceContextFiles(index, query, limit);
  if (ranked.length === 0) return '';
  const symbolCount = index.files.reduce((sum, file) => sum + file.symbols.length, 0);
  const lines = ranked.map((file) => {
    const outline = file.symbols.length > 0
      ? ` :: ${file.symbols.slice(0, 8).join(', ')}`
      : '';
    const impact = dependencyImpact(index, file.path);
    const dependency = impact.importedBy.length > 0
      ? ` | used by ${impact.importedBy.slice(0, 4).join(', ')}`
      : '';
    return `- ${file.path} [${file.languageId}]${outline}${dependency}`;
  });
  const queryTerms = terms(query);
  const decisions = index.decisions
    .map((decision) => ({ decision, score: decisionScore(decision, queryTerms) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score || a.decision.title.localeCompare(b.decision.title))
    .slice(0, 5)
    .map(({ decision }) => `- ${decision.status.toUpperCase()}: ${decision.title}${decision.rationale ? ` - ${decision.rationale}` : ''}`);
  const result = [
    '## Founder local project map',
    `Indexed ${index.files.length} files and ${symbolCount} symbols. Source contents remain local.`,
    ...lines,
  ];
  if (decisions.length > 0) {
    result.push(
      '## Relevant founder decisions',
      'Respect rejected decisions unless the founder explicitly reopens them.',
      ...decisions,
    );
  }
  return result.join('\n');
}

function decisionScore(decision: WorkspaceDecisionRecord, queryTerms: string[]): number {
  const haystack = new Set(terms(`${decision.title} ${decision.rationale}`));
  return queryTerms.reduce((score, term) => score + (haystack.has(term) ? 1 : 0), 0);
}

function resolvesTo(importerPath: string, specifier: string, targetPath: string): boolean {
  if (!specifier.startsWith('.')) return false;
  const base = normalizeModulePath(`${importerPath.slice(0, importerPath.lastIndexOf('/') + 1)}${specifier}`);
  const target = normalizeModulePath(targetPath);
  return target === base || target === `${base}/index`;
}

function normalizeModulePath(value: string): string {
  const parts: string[] = [];
  for (const part of value.replaceAll('\\', '/').split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') parts.pop(); else parts.push(part);
  }
  return parts.join('/').replace(/\.(?:d\.)?(?:ts|tsx|js|jsx|mjs|cjs|json)$/i, '');
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
