import { execFile as execFileCallback } from 'node:child_process';
import { createHash } from 'node:crypto';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import { promisify } from 'node:util';

const execFile = promisify(execFileCallback);

export interface WorkspaceHousekeepingCandidate {
  id: string;
  path: string;
  workspaceFolder: string;
  sizeBytes: number;
  category:
    | 'generated'
    | 'cache'
    | 'duplicate'
    | 'obsolete_source'
    | 'stale_worktree'
    | 'archive';
  evidence: string[];
  referencedBy: string[];
  recommendedAction: 'keep' | 'archive' | 'delete';
  reversible: boolean;
  auditFingerprint: string;
  restorePlan: {
    kind: 'regenerate' | 'checkpoint' | 'manual_review';
    instructions: string;
  };
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
  minDuplicateBytes?: number;
  maxDuplicateHashBytes?: number;
}

type CandidatePolicy = Pick<
  WorkspaceHousekeepingCandidate,
  'category' | 'recommendedAction' | 'reversible' | 'restorePlan'
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
      restorePlan: {
        kind: 'regenerate',
        instructions: 'Restore dependencies from the project lockfile with the project package manager.',
      },
    },
  ],
  [
    '.turbo',
    {
      category: 'cache',
      recommendedAction: 'delete',
      reversible: true,
      reason: 'Turbo task cache is recreated by the next build.',
      restorePlan: {
        kind: 'regenerate',
        instructions: 'Run the affected build or test task to regenerate this cache.',
      },
    },
  ],
  [
    '.cache',
    {
      category: 'cache',
      recommendedAction: 'delete',
      reversible: true,
      reason: 'Recognized tool cache is recreated on demand.',
      restorePlan: {
        kind: 'regenerate',
        instructions: 'Run the owning tool again to regenerate this cache.',
      },
    },
  ],
  [
    '.parcel-cache',
    {
      category: 'cache',
      recommendedAction: 'delete',
      reversible: true,
      reason: 'Parcel cache is recreated by the next development build.',
      restorePlan: {
        kind: 'regenerate',
        instructions: 'Run the Parcel development build to regenerate this cache.',
      },
    },
  ],
  [
    '.vite',
    {
      category: 'cache',
      recommendedAction: 'delete',
      reversible: true,
      reason: 'Vite cache is recreated by the next development build.',
      restorePlan: {
        kind: 'regenerate',
        instructions: 'Run the Vite development build to regenerate this cache.',
      },
    },
  ],
  [
    '__pycache__',
    {
      category: 'cache',
      recommendedAction: 'delete',
      reversible: true,
      reason: 'Python bytecode cache is recreated by Python.',
      restorePlan: {
        kind: 'regenerate',
        instructions: 'Run the owning Python program or test suite to regenerate bytecode.',
      },
    },
  ],
  [
    'coverage',
    {
      category: 'generated',
      recommendedAction: 'delete',
      reversible: true,
      reason: 'Coverage output is recreated by the test suite.',
      restorePlan: {
        kind: 'regenerate',
        instructions: 'Run the project coverage task to regenerate this report.',
      },
    },
  ],
  [
    'dist',
    {
      category: 'generated',
      recommendedAction: 'archive',
      reversible: false,
      reason: 'Build output may contain a release artifact, so review it before removal.',
      restorePlan: {
        kind: 'manual_review',
        instructions: 'Confirm the exact release source and rebuild command before changing this output.',
      },
    },
  ],
  [
    'out',
    {
      category: 'generated',
      recommendedAction: 'archive',
      reversible: false,
      reason: 'Compiled output may contain an installed or release-tested build.',
      restorePlan: {
        kind: 'manual_review',
        instructions: 'Confirm this output is not the installed or release-tested build before changing it.',
      },
    },
  ],
  [
    'build',
    {
      category: 'generated',
      recommendedAction: 'archive',
      reversible: false,
      reason: 'Build output needs release-reference review before removal.',
      restorePlan: {
        kind: 'manual_review',
        instructions: 'Confirm this output is reproducible and not referenced by a release before changing it.',
      },
    },
  ],
]);

const NEVER_SCAN = new Set([
  '.git',
  '.hg',
  '.svn',
  'FounderVault',
]);

const ARCHIVE_EXTENSIONS = new Set([
  '.7z',
  '.gz',
  '.rar',
  '.tar',
  '.tgz',
  '.zip',
]);

const OBSOLETE_SUFFIXES = [
  '.bak',
  '.old',
  '.orig',
  '.tmp',
  '~',
];

type DuplicateFile = {
  absolute: string;
  relative: string;
  sizeBytes: number;
  modifiedMs: number;
};

export async function auditWorkspaceHousekeeping(
  workspaceRoot: string,
  options: WorkspaceHousekeepingAuditOptions = {},
): Promise<WorkspaceHousekeepingAudit> {
  const root = path.resolve(workspaceRoot);
  const maxCandidates = boundedInteger(options.maxCandidates, 1, 200, 100);
  const maxEntries = boundedInteger(options.maxEntries, 1, 200_000, 30_000);
  const maxDepth = boundedInteger(options.maxDepth, 1, 20, 8);
  const minDuplicateBytes = boundedInteger(
    options.minDuplicateBytes,
    1_024,
    1_024 ** 3,
    1_024 ** 2,
  );
  const maxDuplicateHashBytes = boundedInteger(
    options.maxDuplicateHashBytes,
    1_024,
    2 * 1_024 ** 3,
    256 * 1_024 ** 2,
  );
  const candidates: WorkspaceHousekeepingCandidate[] = [];
  const warnings: string[] = [];
  const duplicateFiles: DuplicateFile[] = [];
  const workspaceFolder = path.basename(root);
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
      const absolute = path.join(directory, entry.name);
      if (entry.isFile()) {
        let stat: Awaited<ReturnType<typeof fs.lstat>>;
        try {
          stat = await fs.lstat(absolute);
        } catch (error) {
          warnings.push(`${safeRelative(root, absolute)}: ${safeError(error)}`);
          continue;
        }
        if (!stat.isFile() || stat.isSymbolicLink()) continue;
        const relative = safeRelative(root, absolute);
        const lowerName = entry.name.toLowerCase();
        const extension = path.extname(lowerName);
        if (ARCHIVE_EXTENSIONS.has(extension)) {
          candidates.push(reviewOnlyFileCandidate({
            root,
            absolute,
            workspaceFolder,
            sizeBytes: stat.size,
            modifiedMs: stat.mtimeMs,
            category: 'archive',
            reason: 'Archive files may contain backups or release evidence and require manual review.',
          }));
        } else if (OBSOLETE_SUFFIXES.some((suffix) => lowerName.endsWith(suffix))) {
          candidates.push(reviewOnlyFileCandidate({
            root,
            absolute,
            workspaceFolder,
            sizeBytes: stat.size,
            modifiedMs: stat.mtimeMs,
            category: 'obsolete_source',
            reason: 'The filename suggests an old or temporary copy, but references and history are not yet proven.',
          }));
        }
        if (stat.size >= minDuplicateBytes) {
          duplicateFiles.push({
            absolute,
            relative,
            sizeBytes: stat.size,
            modifiedMs: stat.mtimeMs,
          });
        }
        continue;
      }
      if (!entry.isDirectory() || NEVER_SCAN.has(entry.name)) continue;
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
          workspaceFolder,
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
            `Restore plan: ${policy.restorePlan.instructions}`,
          ],
          referencedBy: [],
          recommendedAction: safeToRecommend
            ? policy.recommendedAction
            : 'keep',
          reversible: safeToRecommend && policy.reversible,
          auditFingerprint: candidateFingerprint({
            path: safeRelative(root, absolute),
            sizeBytes: measurement.sizeBytes,
            files: measurement.files,
            latestModifiedMs: measurement.latestModifiedMs,
          }),
          restorePlan: safeToRecommend
            ? policy.restorePlan
            : {
              kind: 'manual_review',
              instructions: 'Complete a bounded audit before changing this path.',
            },
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
  if (!truncated && candidates.length < maxCandidates) {
    const duplicates = await findDuplicateCandidates(
      root,
      workspaceFolder,
      duplicateFiles,
      maxDuplicateHashBytes,
      maxCandidates - candidates.length,
      warnings,
    );
    candidates.push(...duplicates);
  }
  if (candidates.length < maxCandidates) {
    candidates.push(...await findGitWorktreeCandidates(
      root,
      workspaceFolder,
      maxCandidates - candidates.length,
      warnings,
    ));
  }
  candidates.sort((left, right) =>
    left.path.localeCompare(right.path) || left.category.localeCompare(right.category));
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
  let latestModifiedMs = 0;
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
        latestModifiedMs = Math.max(latestModifiedMs, stat.mtimeMs);
      }
    }
  }
  return {
    sizeBytes,
    entries,
    files,
    linksSkipped,
    truncated,
    latestModifiedMs,
  };
}

function reviewOnlyFileCandidate(input: {
  root: string;
  absolute: string;
  workspaceFolder: string;
  sizeBytes: number;
  modifiedMs: number;
  category: 'obsolete_source' | 'archive';
  reason: string;
}): WorkspaceHousekeepingCandidate {
  const relative = safeRelative(input.root, input.absolute);
  return {
    id: `${stableCandidateId(input.root, input.absolute)}:${input.category}`,
    path: relative,
    workspaceFolder: input.workspaceFolder,
    sizeBytes: input.sizeBytes,
    category: input.category,
    evidence: [
      input.reason,
      'Founder will keep this path until source-control and reference evidence proves a safer action.',
    ],
    referencedBy: [],
    recommendedAction: 'keep',
    reversible: false,
    auditFingerprint: candidateFingerprint({
      path: relative,
      sizeBytes: input.sizeBytes,
      latestModifiedMs: input.modifiedMs,
    }),
    restorePlan: {
      kind: 'manual_review',
      instructions: 'Review source control, references, and release history before changing this file.',
    },
  };
}

async function findDuplicateCandidates(
  root: string,
  workspaceFolder: string,
  files: DuplicateFile[],
  maxHashBytes: number,
  limit: number,
  warnings: string[],
): Promise<WorkspaceHousekeepingCandidate[]> {
  if (limit <= 0) return [];
  const bySize = new Map<number, DuplicateFile[]>();
  for (const file of files) {
    const group = bySize.get(file.sizeBytes) ?? [];
    group.push(file);
    bySize.set(file.sizeBytes, group);
  }
  let hashedBytes = 0;
  const byDigest = new Map<string, DuplicateFile[]>();
  for (const group of bySize.values()) {
    if (group.length < 2) continue;
    for (const file of group) {
      if (hashedBytes + file.sizeBytes > maxHashBytes) {
        warnings.push('Duplicate hashing reached its byte budget; remaining large files were not compared.');
        return duplicateGroupsToCandidates(root, workspaceFolder, byDigest, limit);
      }
      try {
        const digest = await hashFile(file.absolute);
        hashedBytes += file.sizeBytes;
        const digestGroup = byDigest.get(digest) ?? [];
        digestGroup.push(file);
        byDigest.set(digest, digestGroup);
      } catch (error) {
        warnings.push(`${file.relative}: ${safeError(error)}`);
      }
    }
  }
  return duplicateGroupsToCandidates(root, workspaceFolder, byDigest, limit);
}

function duplicateGroupsToCandidates(
  root: string,
  workspaceFolder: string,
  groups: Map<string, DuplicateFile[]>,
  limit: number,
): WorkspaceHousekeepingCandidate[] {
  const result: WorkspaceHousekeepingCandidate[] = [];
  for (const [digest, files] of groups) {
    if (files.length < 2) continue;
    for (const file of files) {
      if (result.length >= limit) return result;
      const referencedBy = files
        .filter((item) => item.absolute !== file.absolute)
        .map((item) => item.relative)
        .slice(0, 20);
      result.push({
        id: `${stableCandidateId(root, file.absolute)}:duplicate`,
        path: file.relative,
        workspaceFolder,
        sizeBytes: file.sizeBytes,
        category: 'duplicate',
        evidence: [
          `Content hash matches ${referencedBy.length} other audited file(s).`,
          'Founder does not infer which copy is authoritative, so deletion is not recommended.',
        ],
        referencedBy,
        recommendedAction: 'keep',
        reversible: false,
        auditFingerprint: candidateFingerprint({
          path: file.relative,
          sizeBytes: file.sizeBytes,
          latestModifiedMs: file.modifiedMs,
          contentDigest: digest,
        }),
        restorePlan: {
          kind: 'manual_review',
          instructions: 'Choose an authoritative copy and verify every reference before removing a duplicate.',
        },
      });
    }
  }
  return result;
}

async function findGitWorktreeCandidates(
  root: string,
  workspaceFolder: string,
  limit: number,
  warnings: string[],
): Promise<WorkspaceHousekeepingCandidate[]> {
  if (limit <= 0) return [];
  try {
    const { stdout } = await execFile(
      'git',
      ['-C', root, 'worktree', 'list', '--porcelain'],
      {
        timeout: 3_000,
        windowsHide: true,
        maxBuffer: 256 * 1_024,
      },
    );
    const current = await fs.realpath(root);
    const records = String(stdout).split(/\r?\n\r?\n/);
    const result: WorkspaceHousekeepingCandidate[] = [];
    for (const record of records) {
      if (result.length >= limit) break;
      const worktreeLine = record.split(/\r?\n/)
        .find((line) => line.startsWith('worktree '));
      if (!worktreeLine) continue;
      const worktreePath = worktreeLine.slice('worktree '.length).trim();
      let resolved: string;
      try {
        resolved = await fs.realpath(worktreePath);
      } catch {
        continue;
      }
      if (path.resolve(resolved) === path.resolve(current)) continue;
      const branch = record.split(/\r?\n/)
        .find((line) => line.startsWith('branch '))
        ?.slice('branch '.length)
        .replace(/^refs\/heads\//, '')
        .trim();
      const prunable = /(?:^|\n)prunable(?:\s|$)/.test(record);
      const display = path.basename(resolved);
      result.push({
        id: `worktree:${createHash('sha256').update(resolved).digest('hex')}`,
        path: `Worktree: ${display}`,
        workspaceFolder,
        sizeBytes: 0,
        category: 'stale_worktree',
        evidence: [
          `Registered Git worktree${branch ? ` on branch ${branch}` : ''}.`,
          prunable
            ? 'Git reports this worktree as prunable, but Founder still requires a clean-state and merge review.'
            : 'Staleness, clean state, and merge status are not yet proven.',
        ],
        referencedBy: branch ? [branch] : [],
        recommendedAction: 'keep',
        reversible: false,
        auditFingerprint: candidateFingerprint({
          path: display,
          branch,
          prunable,
        }),
        restorePlan: {
          kind: 'manual_review',
          instructions: 'Verify the worktree is clean, merged, unowned, and no longer needed before removal.',
        },
      });
    }
    return result;
  } catch (error) {
    if (!/not a git repository/i.test(safeError(error))) {
      warnings.push(`Git worktree audit: ${safeError(error)}`);
    }
    return [];
  }
}

async function hashFile(absolute: string): Promise<string> {
  const handle = await fs.open(absolute, 'r');
  try {
    const hash = createHash('sha256');
    const buffer = Buffer.allocUnsafe(256 * 1_024);
    let position = 0;
    while (true) {
      const { bytesRead } = await handle.read(
        buffer,
        0,
        buffer.length,
        position,
      );
      if (bytesRead === 0) break;
      hash.update(buffer.subarray(0, bytesRead));
      position += bytesRead;
    }
    return hash.digest('hex');
  } finally {
    await handle.close();
  }
}

function candidateFingerprint(value: Record<string, unknown>): string {
  return createHash('sha256')
    .update(JSON.stringify(value))
    .digest('hex');
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
