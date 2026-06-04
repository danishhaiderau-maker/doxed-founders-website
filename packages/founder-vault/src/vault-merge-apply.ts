import fs from 'node:fs';
import type { VaultFileManifest, VaultMergePatch } from '@dcf/utils';
import { shouldApplyFile } from '@dcf/utils';
import { vaultFilePath, type FounderVaultFileKey } from './paths.js';
import type { FounderVaultMeta } from './schema.js';
import { applyPushGoal } from './vault-apply.js';

const FILE_KEYS: FounderVaultFileKey[] = [
  'projectContext',
  'roadmap',
  'tasks',
  'decisions',
  'privateNotes',
];

function readManifest(vaultRoot: string): VaultFileManifest {
  const manifest: VaultFileManifest = {};
  for (const key of FILE_KEYS) {
    const path = vaultFilePath(vaultRoot, key);
    if (fs.existsSync(path)) {
      manifest[key] = fs.statSync(path).mtime.toISOString();
    }
  }
  const metaPath = vaultFilePath(vaultRoot, 'meta');
  if (fs.existsSync(metaPath)) {
    manifest.meta = fs.statSync(metaPath).mtime.toISOString();
  }
  return manifest;
}

export function buildVaultMergePatch(input: {
  nodeId: string;
  platform?: string | null;
  label?: string | null;
  vaultSyncVersion: number;
  vaultRoot: string;
  primaryPlatform?: 'desktop' | 'mobile' | null;
}): VaultMergePatch {
  const manifest = readManifest(input.vaultRoot);
  const patch: VaultMergePatch = {
    version: 1,
    sourceNodeId: input.nodeId,
    sourcePlatform: input.platform ?? null,
    sourceLabel: input.label ?? null,
    updatedAt: new Date().toISOString(),
    vaultSyncVersion: input.vaultSyncVersion,
    fileManifest: manifest,
  };

  const read = (key: FounderVaultFileKey) => {
    const path = vaultFilePath(input.vaultRoot, key);
    return fs.existsSync(path) ? fs.readFileSync(path, 'utf8') : undefined;
  };

  const tasksRaw = read('tasks');
  if (tasksRaw) {
    try {
      patch.tasksFile = JSON.parse(tasksRaw) as VaultMergePatch['tasksFile'];
      patch.currentGoal = patch.tasksFile?.currentGoal;
    } catch {
      /* ignore */
    }
  }
  patch.projectContext = read('projectContext');
  patch.roadmap = read('roadmap');
  patch.decisions = read('decisions');

  return patch;
}

export function applyVaultMergePatchToDisk(
  vaultRoot: string,
  patch: VaultMergePatch,
  options?: { primaryPlatform?: 'desktop' | 'mobile' | null },
): { applied: string[]; skipped: string[] } {
  const localManifest = readManifest(vaultRoot);
  const applied: string[] = [];
  const skipped: string[] = [];
  const primary = options?.primaryPlatform ?? null;

  const applyTasks =
    patch.tasksFile &&
    shouldApplyFile('tasks', patch, localManifest, primary);
  if (applyTasks && patch.tasksFile) {
    fs.writeFileSync(
      vaultFilePath(vaultRoot, 'tasks'),
      JSON.stringify(patch.tasksFile, null, 2),
      'utf8',
    );
    applied.push('tasks');
  } else if (patch.currentGoal && shouldApplyFile('tasks', patch, localManifest, primary)) {
    applyPushGoal(vaultRoot, patch.currentGoal);
    applied.push('tasks');
  } else if (patch.currentGoal || patch.tasksFile) {
    skipped.push('tasks');
  }

  if (patch.projectContext && shouldApplyFile('projectContext', patch, localManifest, primary)) {
    fs.writeFileSync(vaultFilePath(vaultRoot, 'projectContext'), patch.projectContext, 'utf8');
    applied.push('projectContext');
  }

  if (patch.roadmap && shouldApplyFile('roadmap', patch, localManifest, primary)) {
    fs.writeFileSync(vaultFilePath(vaultRoot, 'roadmap'), patch.roadmap, 'utf8');
    applied.push('roadmap');
  }

  if (patch.decisions && shouldApplyFile('decisions', patch, localManifest, primary)) {
    fs.writeFileSync(vaultFilePath(vaultRoot, 'decisions'), patch.decisions, 'utf8');
    applied.push('decisions');
  }

  const metaPath = vaultFilePath(vaultRoot, 'meta');
  if (fs.existsSync(metaPath)) {
    const meta = JSON.parse(fs.readFileSync(metaPath, 'utf8')) as FounderVaultMeta;
    meta.updatedAt = new Date().toISOString();
    fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2), 'utf8');
  }

  return { applied, skipped };
}
