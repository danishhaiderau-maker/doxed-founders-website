export const WORKSPACE_CONTEXT_INDEX_VERSION = 1 as const;

export interface WorkspaceContextFile {
  path: string;
  languageId: string;
  size: number;
  mtimeMs: number;
  sha256: string;
  symbols: string[];
}

export interface WorkspaceContextIndexState {
  version: typeof WORKSPACE_CONTEXT_INDEX_VERSION;
  workspaceId: string;
  indexedAt: string;
  files: WorkspaceContextFile[];
}

export interface WorkspaceContextFileStat {
  path: string;
  size: number;
  mtimeMs: number;
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
      Array.isArray(file.symbols),
    ),
  );
  return { ...candidate, files } as WorkspaceContextIndexState;
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
): WorkspaceContextIndexState {
  return {
    version: WORKSPACE_CONTEXT_INDEX_VERSION,
    workspaceId,
    indexedAt,
    files: [...files].sort((a, b) => a.path.localeCompare(b.path)),
  };
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
    return `- ${file.path} [${file.languageId}]${outline}`;
  });
  return [
    '## Founder local project map',
    `Indexed ${index.files.length} files and ${symbolCount} symbols. Source contents remain local.`,
    ...lines,
  ].join('\n');
}
