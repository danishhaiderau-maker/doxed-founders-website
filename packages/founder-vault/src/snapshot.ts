import type { DeviceMemoryPayload, DeviceMemoryMetadataPayload } from '@dcf/utils';
import { stripDeviceMemoryToMetadata } from '@dcf/utils';
import type { FounderVaultMeta, FounderVaultSnapshot } from './schema.js';
import { emptyTasksFile, parseTasksJson } from './schema.js';

export function buildVaultSnapshot(input: {
  meta: FounderVaultMeta;
  projectContext?: string;
  roadmap?: string;
  tasksRaw?: string;
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
  const sensitive = JSON.stringify({
    projectContext: snapshot.projectContext,
    roadmap: snapshot.roadmap,
    tasksFile: snapshot.tasksFile,
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
