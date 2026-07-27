import * as fs from 'node:fs/promises';
import * as path from 'node:path';

export interface WorkspaceHousekeepingCandidate {
  id: string;
  path: string;
  sizeBytes: number;
  category:
    | 'generated'
    | 'cache'
    | 'duplicate'
    | 'obsolete_source'
    | 'stale_worktree'
    | 'archive';
  evidence: string[];
  recommendedAction: 'keep' | 'archive' | 'delete';
  reversible: boolean;
}

export interface WorkspaceHousekeepingAudit {
  candidates: WorkspaceHousekeepingCandidate[];
  scannedEntries: number;
  truncated: boolean;
  warnings: string[];
}

export interface WorkspaceHousekeepingAuditOptions {
  maxCandidates?: number;
  maxEntries?: number;
  maxDepth?: number;
}

type CandidatePolicy = Pick<
  WorkspaceHousekeepingCandidate,
  'category' | 'recommendedAction' | 'reversible'
> & {
  reason: string;
};

const DIRECTORY_POLICIES = new Map<string, CandidatePolicy>([
  [
    'node_modules',
    {
      category: 'cache',
      recommendedAction: 'delete',
      reversible: true,
      reason: 'Dependency cache can be restored from the project lockfile.',
    },
  ],
  [
    '.turbo',
    {
      category: 'cache',
      recommendedAction: 'delete',
      reversible: true,
      reason: 'Turbo task cache is recreated by the next build.',
    },
  ],
  [
    '.cache',
    {
      category: 'cache',
      recommendedAction: 'delete',
      reversible: true,
      reason: 'Recognized tool cache is recreated on demand.',
    },
  ],
  [
    '.parcel-cache',
    {
      category: 'cache',
      recommendedAction: 'delete',
      reversible: true,
      reason: 'Parcel cache is recreated by the next development build.',
    },
  ],
  [
    '.vite',
    {
      category: 'cache',
      recommendedAction: 'delete',
      reversible: true,
      reason: 'Vite cache is recreated by the next development build.',
    },
  ],
  [
    '__pycache__',
    {
      category: 'cache',
      recommendedAction: 'delete',
      reversible: true,
      reason: 'Python bytecode cache is recreated by Python.',
    },
  ],
  [
    'coverage',
    {
      category: 'generated',
      recommendedAction: 'delete',
      reversible: true,
      reason: 'Coverage output is recreated by the test suite.',
    },
  ],
  [
    'dist',
    {
      category: 'generated',
      recommendedAction: 'archive',
      reversible: false,
      reason: 'Build output may contain a release artifact, so review it before removal.',
    },
  ],
  [
    'out',
    {
      category: 'generated',
      recommendedAction: 'archive',
      reversible: false,
      reason: 'Compiled output may contain an installed or release-tested build.',
    },
  ],
  [
    'build',
    {
      category: 'generated',
      recommendedAction: 'archive',
      reversible: false,
      reason: 'Build output needs release-reference review before removal.',
    },
  ],
]);

const NEVER_SCAN = new Set([
  '.git',
  '.hg',
  '.svn',
  'FounderVault',
]);

export async function auditWorkspaceHousekeeping(
  workspaceRoot: string,
  options: WorkspaceHousekeepingAuditOptions = {},
): Promise<WorkspaceHousekeepingAudit> {
  const root = path.resolve(workspaceRoot);
  const maxCandidates = boundedInteger(options.maxCandidates, 1, 200, 100);
  const maxEntries = boundedInteger(options.maxEntries, 1, 200_000, 30_000);
  const maxDepth = boundedInteger(options.maxDepth, 1, 20, 8);
  const candidates: WorkspaceHousekeepingCandidate[] = [];
  const warnings: string[] = [];
  let scannedEntries = 0;
  let truncated = false;

  async function walk(directory: string, depth: number): Promise<void> {
    if (
      truncated
      || candidates.length >= maxCandidates
      || scannedEntries >= maxEntries
      || depth > maxDepth
    ) {
      truncated = true;
      return;
    }
    let entries: import('node:fs').Dirent[];
    try {
      entries = await fs.readdir(directory, { withFileTypes: true });
    } catch (error) {
      warnings.push(`${safeRelative(root, directory)}: ${safeError(error)}`);
      return;
    }
    entries.sort((left, right) => left.name.localeCompare(right.name));
    for (const entry of entries) {
      if (scannedEntries >= maxEntries || candidates.length >= maxCandidates) {
        truncated = true;
        return;
      }
      scannedEntries += 1;
      if (!entry.isDirectory() || NEVER_SCAN.has(entry.name)) continue;
      const absolute = path.join(directory, entry.name);
      if (!(await isOwnedDirectory(root, absolute))) continue;
      const policy = DIRECTORY_POLICIES.get(entry.name);
      if (policy) {
        const measurement = await measureDirectory(root, absolute, {
          remainingEntries: maxEntries - scannedEntries,
        });
        scannedEntries += measurement.entries;
        const safeToRecommend =
          !measurement.truncated && measurement.linksSkipped === 0;
        candidates.push({
          id: stableCandidateId(root, absolute),
          path: safeRelative(root, absolute),
          sizeBytes: measurement.sizeBytes,
          category: policy.category,
          evidence: [
            policy.reason,
            `${measurement.files} files measured without reading file contents.`,
            ...(measurement.linksSkipped > 0
              ? [`Skipped ${measurement.linksSkipped} linked path(s); deletion is not recommended.`]
              : []),
            ...(measurement.truncated
              ? ['Size measurement hit the audit bound; deletion is not recommended.']
              : ['Size measurement completed within the audit boundary.']),
          ],
          recommendedAction: safeToRecommend
            ? policy.recommendedAction
            : 'keep',
          reversible: safeToRecommend && policy.reversible,
        });
        if (measurement.truncated) truncated = true;
        continue;
      }
      await walk(absolute, depth + 1);
    }
  }

  if (!(await isOwnedDirectory(root, root))) {
    throw new Error('Workspace housekeeping requires a real local workspace directory.');
  }
  await walk(root, 0);
  return {
    candidates,
    scannedEntries,
    truncated,
    warnings: warnings.slice(0, 20),
  };
}

async function measureDirectory(
  root: string,
  directory: string,
  options: { remainingEntries: number },
) {
  let sizeBytes = 0;
  let entries = 0;
  let files = 0;
  let linksSkipped = 0;
  let truncated = false;
  const pending = [directory];
  while (pending.length > 0) {
    if (entries >= options.remainingEntries) {
      truncated = true;
      break;
    }
    const current = pending.pop()!;
    let children: import('node:fs').Dirent[];
    try {
      children = await fs.readdir(current, { withFileTypes: true });
    } catch {
      truncated = true;
      continue;
    }
    for (const child of children) {
      if (entries >= options.remainingEntries) {
        truncated = true;
        break;
      }
      entries += 1;
      const absolute = path.join(current, child.name);
      let stat: Awaited<ReturnType<typeof fs.lstat>>;
      try {
        stat = await fs.lstat(absolute);
      } catch {
        truncated = true;
        continue;
      }
      if (stat.isSymbolicLink()) {
        linksSkipped += 1;
        continue;
      }
      if (stat.isDirectory()) {
        if (await isOwnedDirectory(root, absolute)) pending.push(absolute);
        else linksSkipped += 1;
        continue;
      }
      if (stat.isFile()) {
        files += 1;
        sizeBytes += stat.size;
      }
    }
  }
  return { sizeBytes, entries, files, linksSkipped, truncated };
}

async function isOwnedDirectory(root: string, candidate: string) {
  try {
    const stat = await fs.lstat(candidate);
    if (!stat.isDirectory() || stat.isSymbolicLink()) return false;
    const resolved = await fs.realpath(candidate);
    return isInside(root, resolved);
  } catch {
    return false;
  }
}

function isInside(root: string, candidate: string) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

function safeRelative(root: string, absolute: string) {
  const relative = path.relative(root, absolute);
  return relative && isInside(root, absolute) ? relative.replaceAll('\\', '/') : '.';
}

function stableCandidateId(root: string, absolute: string) {
  return `workspace:${safeRelative(root, absolute).toLowerCase()}`;
}

function boundedInteger(
  value: number | undefined,
  minimum: number,
  maximum: number,
  fallback: number,
) {
  return Number.isInteger(value)
    ? Math.max(minimum, Math.min(maximum, Number(value)))
    : fallback;
}

function safeError(error: unknown) {
  return error instanceof Error
    ? error.message.replace(/\s+/g, ' ').slice(0, 180)
    : 'Unable to inspect this path.';
}
