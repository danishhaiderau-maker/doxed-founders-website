import type { DeviceMemoryPayload, DeviceMemoryMetadataPayload } from '@dcf/utils';
import { stripDeviceMemoryToMetadata } from '@dcf/utils';
import type { FounderVaultMeta, FounderVaultSnapshot } from './schema.js';
import { emptyTasksFile, parseTasksJson } from './schema.js';

export function buildVaultSnapshot(input: {
  meta: FounderVaultMeta;
  projectContext?: string;
  roadmap?: string;
  tasksRaw?: string;
  decisions?: string;
  privateNotes?: string;
  vaultHealthy?: boolean;
  deviceLabel?: string;
}): FounderVaultSnapshot {
  const tasksFile = input.tasksRaw ? parseTasksJson(input.tasksRaw) : null;
  const currentGoal =
    tasksFile?.currentGoal?.trim() ||
    extractGoalFromContext(input.projectContext) ||
    'Define your next milestone';

  const payload: DeviceMemoryPayload = {
    version: 1,
    updatedAt: new Date().toISOString(),
    projectName: input.meta.projectName,
    currentGoal,
    projectContext: input.projectContext,
    roadmap: input.roadmap,
    tasksFile: tasksFile ?? emptyTasksFile(currentGoal),
    deviceLabel: input.deviceLabel ?? 'Founder Node',
  };

  const openTasks = (payload.tasksFile?.tasks ?? []).filter((t) => !t.done);

  return {
    ...payload,
    vaultHealthy: input.vaultHealthy ?? true,
    tasksRemaining: openTasks.length,
  };
}

/** Metadata-only payload for cloud relay — no plaintext vault contents. */
export function buildVaultMetadataSyncPayload(
  snapshot: FounderVaultSnapshot,
  encryptedVaultBlob?: string,
): DeviceMemoryMetadataPayload {
  const base = stripDeviceMemoryToMetadata(snapshot);
  if (encryptedVaultBlob) {
    return { ...base, encryptedVaultBlob };
  }
  return base;
}

export function buildVaultEncryptedBlob(
  snapshot: FounderVaultSnapshot,
  encrypt: (json: string) => string,
): DeviceMemoryMetadataPayload {
  const extended = snapshot as FounderVaultSnapshot & {
    decisions?: string;
    privateNotes?: string;
  };
  const sensitive = JSON.stringify({
    projectContext: snapshot.projectContext,
    roadmap: snapshot.roadmap,
    tasksFile: snapshot.tasksFile,
    decisions: extended.decisions,
    privateNotes: extended.privateNotes,
  });
  return buildVaultMetadataSyncPayload(snapshot, encrypt(sensitive));
}

function extractGoalFromContext(markdown?: string): string | null {
  if (!markdown?.trim()) return null;
  const match = markdown.match(/## Current Goal\s*\n+\s*(.+)/i);
  return match?.[1]?.trim() ?? null;
}

export function defaultProjectContext(projectName: string, currentGoal: string): string {
  return [
    `# ${projectName} — Project Context`,
    '',
    '> Stored locally in Founder Node vault. Founder OS reads metadata only.',
    '',
    '## Current Goal',
    '',
    currentGoal,
    '',
  ].join('\n');
}

export function defaultRoadmap(): string {
  return [
    '# Roadmap',
    '',
    '> Edit in Founder Node or sync from Founder OS.',
    '',
    '- [ ] Define MVP',
    '- [ ] Ship beta',
    '',
  ].join('\n');
}

export function defaultPrivateNotes(): string {
  return [
    '# Private notes',
    '',
    '> Stored only in your Founder Vault on this machine. Encrypted before any cloud relay.',
    '',
    '## Investor conversations',
    '',
    '_Add notes here — not visible to Founder OS servers._',
    '',
    '## Product roadmap (confidential)',
    '',
    '',
  ].join('\n');
}

export function defaultDecisionsLog(): string {
  return [
    '# Decisions log',
    '',
    '> Key product and technical decisions. Synced encrypted when Founder Node relays to cloud.',
    '',
  ].join('\n');
}

export function defaultBuildHistoryLine(): string {
  return JSON.stringify({
    at: new Date().toISOString(),
    event: 'vault_initialized',
    note: 'Founder Vault created',
  });
}
