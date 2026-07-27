import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import type {
  FounderGoalUiDecision,
  FounderHousekeepingUiCandidate,
} from './founder-goal-state';
import {
  auditWorkspaceHousekeeping,
  type WorkspaceHousekeepingAuditOptions,
} from './workspace-housekeeping-audit';

export type HousekeepingWorkspace = {
  name: string;
  path: string;
};

export type HousekeepingExecutionReceipt = {
  decisionId: string;
  checkpointPath: string;
  deletedPaths: string[];
  reclaimedBytes: number;
  completedAt: string;
};

type HousekeepingCheckpoint = {
  version: 1;
  decisionId: string;
  createdAt: string;
  completedAt?: string;
  status: 'planned' | 'complete' | 'partial';
  entries: Array<{
    workspaceFolder: string;
    path: string;
    sizeBytes: number;
    auditFingerprint: string;
    restoreInstructions: string;
    deletedAt?: string;
    error?: string;
  }>;
};

export async function applyApprovedHousekeeping(input: {
  decision: FounderGoalUiDecision;
  workspaces: HousekeepingWorkspace[];
  checkpointDirectory: string;
  now?: Date;
  auditOptions?: WorkspaceHousekeepingAuditOptions;
}): Promise<HousekeepingExecutionReceipt> {
  const { decision } = input;
  if (
    decision.kind !== 'housekeeping'
    || decision.status !== 'resolved'
    || decision.selectedOptionId !== 'approve_selected'
    || !decision.resolvedAt
  ) {
    throw new Error('Housekeeping requires an explicit resolved founder approval.');
  }
  const selectedIds = new Set(decision.selectedCandidateIds ?? []);
  if (selectedIds.size === 0) {
    throw new Error('Housekeeping approval contains no selected paths.');
  }
  const approved = (decision.housekeepingCandidates ?? []).filter(
    (candidate) => selectedIds.has(candidate.id),
  );
  if (approved.length !== selectedIds.size) {
    throw new Error('Housekeeping approval references an unknown candidate.');
  }
  for (const candidate of approved) assertExecutableCandidate(candidate);

  const workspaceByName = uniqueWorkspaces(input.workspaces);
  const freshCandidates = new Map<string, FounderHousekeepingUiCandidate>();
  for (const workspaceName of new Set(
    approved.map((candidate) => candidate.workspaceFolder),
  )) {
    const workspace = workspaceByName.get(workspaceName);
    if (!workspace) {
      throw new Error(`Approved workspace "${workspaceName}" is not open.`);
    }
    const audit = await auditWorkspaceHousekeeping(
      workspace.path,
      input.auditOptions,
    );
    for (const candidate of audit.candidates) {
      const scopedId = `${workspace.name}:${candidate.id}`;
      freshCandidates.set(scopedId, {
        ...candidate,
        id: scopedId,
      });
    }
  }
  for (const approvedCandidate of approved) {
    const fresh = freshCandidates.get(approvedCandidate.id);
    if (!fresh) {
      throw new Error(
        `Approved path "${approvedCandidate.path}" no longer matches the audit.`,
      );
    }
    assertExecutableCandidate(fresh);
    if (fresh.auditFingerprint !== approvedCandidate.auditFingerprint) {
      throw new Error(
        `Approved path "${approvedCandidate.path}" changed after review. Run housekeeping again.`,
      );
    }
  }

  const checkpointRoot = path.resolve(input.checkpointDirectory);
  await fs.mkdir(checkpointRoot, { recursive: true });
  const checkpointPath = path.join(
    checkpointRoot,
    `${safeFileName(decision.id)}.json`,
  );
  const checkpoint: HousekeepingCheckpoint = {
    version: 1,
    decisionId: decision.id,
    createdAt: (input.now ?? new Date()).toISOString(),
    status: 'planned',
    entries: approved.map((candidate) => ({
      workspaceFolder: candidate.workspaceFolder,
      path: candidate.path,
      sizeBytes: candidate.sizeBytes,
      auditFingerprint: candidate.auditFingerprint,
      restoreInstructions: candidate.restorePlan.instructions,
    })),
  };
  await writeCheckpoint(checkpointPath, checkpoint);

  let reclaimedBytes = 0;
  const deletedPaths: string[] = [];
  try {
    for (const entry of checkpoint.entries) {
      const workspace = workspaceByName.get(entry.workspaceFolder)!;
      const absolute = await resolveOwnedDirectory(workspace.path, entry.path);
      try {
        await fs.rm(absolute, { recursive: true, force: false });
        entry.deletedAt = new Date().toISOString();
        reclaimedBytes += entry.sizeBytes;
        deletedPaths.push(`${entry.workspaceFolder}/${entry.path}`);
        await writeCheckpoint(checkpointPath, checkpoint);
      } catch (error) {
        entry.error = safeError(error);
        checkpoint.status = 'partial';
        await writeCheckpoint(checkpointPath, checkpoint);
        throw error;
      }
    }
    checkpoint.status = 'complete';
    checkpoint.completedAt = (input.now ?? new Date()).toISOString();
    await writeCheckpoint(checkpointPath, checkpoint);
  } catch (error) {
    throw new Error(
      `Housekeeping stopped after ${deletedPaths.length} path(s): ${safeError(error)}`,
    );
  }
  return {
    decisionId: decision.id,
    checkpointPath,
    deletedPaths,
    reclaimedBytes,
    completedAt: checkpoint.completedAt!,
  };
}

function assertExecutableCandidate(
  candidate: FounderHousekeepingUiCandidate,
): void {
  if (
    candidate.recommendedAction !== 'delete'
    || !candidate.reversible
    || candidate.restorePlan.kind !== 'regenerate'
    || !candidate.auditFingerprint
    || !candidate.workspaceFolder
  ) {
    throw new Error(
      `Housekeeping path "${candidate.path}" is review-only and cannot be deleted automatically.`,
    );
  }
}

function uniqueWorkspaces(
  workspaces: HousekeepingWorkspace[],
): Map<string, HousekeepingWorkspace> {
  const result = new Map<string, HousekeepingWorkspace>();
  const duplicates = new Set<string>();
  for (const workspace of workspaces) {
    if (result.has(workspace.name)) duplicates.add(workspace.name);
    result.set(workspace.name, {
      name: workspace.name,
      path: path.resolve(workspace.path),
    });
  }
  for (const duplicate of duplicates) result.delete(duplicate);
  return result;
}

async function resolveOwnedDirectory(
  workspaceRoot: string,
  relativePath: string,
): Promise<string> {
  const root = path.resolve(workspaceRoot);
  const absolute = path.resolve(root, relativePath);
  const relative = path.relative(root, absolute);
  if (
    !relative
    || relative.startsWith('..')
    || path.isAbsolute(relative)
  ) {
    throw new Error('Housekeeping path is outside its workspace.');
  }
  const stat = await fs.lstat(absolute);
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new Error('Housekeeping path is no longer an owned directory.');
  }
  const resolved = await fs.realpath(absolute);
  const resolvedRelative = path.relative(root, resolved);
  if (
    resolvedRelative.startsWith('..')
    || path.isAbsolute(resolvedRelative)
  ) {
    throw new Error('Housekeeping path resolves outside its workspace.');
  }
  return absolute;
}

async function writeCheckpoint(
  checkpointPath: string,
  checkpoint: HousekeepingCheckpoint,
): Promise<void> {
  const temporary = `${checkpointPath}.tmp`;
  await fs.writeFile(
    temporary,
    `${JSON.stringify(checkpoint, null, 2)}\n`,
    { encoding: 'utf8', mode: 0o600 },
  );
  await fs.rename(temporary, checkpointPath);
}

function safeFileName(value: string): string {
  return value.replace(/[^a-z0-9._-]+/gi, '-').slice(0, 120) || 'housekeeping';
}

function safeError(error: unknown): string {
  return error instanceof Error
    ? error.message.replace(/\s+/g, ' ').slice(0, 300)
    : 'Unknown housekeeping failure.';
}
