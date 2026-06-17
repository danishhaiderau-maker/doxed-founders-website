/** Phase 5 — migrate/import wizard (cloud → vault). */

export type ImportWizardStepId =
  | 'validate_sources'
  | 'mirror_repo_memory'
  | 'env_manifest'
  | 'vault_seed'
  | 'complete';

export type ImportWizardStepStatus = 'pending' | 'running' | 'done' | 'error' | 'skipped';

export type ImportWizardStep = {
  id: ImportWizardStepId;
  label: string;
  status: ImportWizardStepStatus;
  detail?: string;
  at?: string;
};

export type FounderImportJob = {
  jobId: string;
  status: 'idle' | 'running' | 'complete' | 'failed';
  steps: ImportWizardStep[];
  startedAt?: string;
  completedAt?: string;
  summary?: string;
  repoFullName?: string | null;
  providersMirrored?: string[];
};

export const IMPORT_WIZARD_STEP_DEFS: { id: ImportWizardStepId; label: string }[] = [
  { id: 'validate_sources', label: 'Validate GitHub + host credentials' },
  { id: 'mirror_repo_memory', label: 'Mirror repo memory (context, roadmap, tasks)' },
  { id: 'env_manifest', label: 'Build env manifest (provider names only)' },
  { id: 'vault_seed', label: 'Seed Founder Vault via Founder Node' },
  { id: 'complete', label: 'Import complete' },
];

export function createImportJob(jobId: string, repoFullName?: string | null): FounderImportJob {
  return {
    jobId,
    status: 'idle',
    repoFullName: repoFullName ?? null,
    steps: IMPORT_WIZARD_STEP_DEFS.map((s) => ({ id: s.id, label: s.label, status: 'pending' })),
  };
}

export function parseFounderImportJob(raw: unknown): FounderImportJob | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  if (typeof o.jobId !== 'string' || !Array.isArray(o.steps)) return null;
  return {
    jobId: o.jobId,
    status: (o.status as FounderImportJob['status']) ?? 'idle',
    steps: o.steps as ImportWizardStep[],
    startedAt: typeof o.startedAt === 'string' ? o.startedAt : undefined,
    completedAt: typeof o.completedAt === 'string' ? o.completedAt : undefined,
    summary: typeof o.summary === 'string' ? o.summary : undefined,
    repoFullName: typeof o.repoFullName === 'string' ? o.repoFullName : null,
    providersMirrored: Array.isArray(o.providersMirrored)
      ? (o.providersMirrored as string[])
      : undefined,
  };
}

export function parseFounderCloudState(raw: unknown): FounderCloudState | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  return {
    import: o.import ? parseFounderImportJob(o.import) : null,
    localStack: o.localStack && typeof o.localStack === 'object' ? (o.localStack as LocalStackState) : null,
  };
}

export type LocalStackState = {
  enabled?: boolean;
  running?: boolean;
  webUrl?: string;
  apiUrl?: string;
  repoPath?: string;
  nodeLabel?: string;
  updatedAt?: string;
};

export type FounderCloudState = {
  import: FounderImportJob | null;
  localStack: LocalStackState | null;
};

export function importJobComplete(job: FounderImportJob | null): boolean {
  return job?.status === 'complete';
}

export function formatImportSummary(job: FounderImportJob): string {
  const done = job.steps.filter((s) => s.status === 'done').length;
  const parts = [`${done}/${job.steps.length} steps complete`];
  if (job.repoFullName) parts.push(`repo ${job.repoFullName}`);
  if (job.providersMirrored?.length) parts.push(`providers: ${job.providersMirrored.join(', ')}`);
  return parts.join(' · ');
}
