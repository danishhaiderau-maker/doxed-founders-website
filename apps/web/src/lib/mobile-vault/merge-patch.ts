'use client';

import type { VaultFileManifest, VaultMergePatch } from '@dcf/utils';
import { FOUNDER_VAULT_FILES, type FounderVaultFileKey } from '@dcf/founder-vault/paths';
import { parseTasksJson } from '@dcf/founder-vault/schema';

const VAULT_DIR = 'FounderVault';
const FILE_KEYS: FounderVaultFileKey[] = [
  'projectContext',
  'roadmap',
  'tasks',
  'decisions',
  'privateNotes',
];

function vaultPath(key: FounderVaultFileKey): string {
  return `${VAULT_DIR}/${FOUNDER_VAULT_FILES[key]}`;
}

async function readFile(path: string): Promise<string | null> {
  const { Filesystem, Directory } = await import('@capacitor/filesystem');
  try {
    const read = await Filesystem.readFile({ path, directory: Directory.Data });
    const data = typeof read.data === 'string' ? read.data : null;
    return data;
  } catch {
    return null;
  }
}

async function fileMtime(path: string): Promise<string | null> {
  const { Filesystem, Directory } = await import('@capacitor/filesystem');
  try {
    const stat = await Filesystem.stat({ path, directory: Directory.Data });
    if (stat.mtime) return new Date(stat.mtime).toISOString();
    return new Date().toISOString();
  } catch {
    return null;
  }
}

export async function buildMobileVaultMergePatch(input: {
  nodeId: string;
  platform?: string;
  label?: string;
  vaultSyncVersion?: number;
}): Promise<VaultMergePatch> {
  const manifest: VaultFileManifest = {};
  for (const key of FILE_KEYS) {
    const m = await fileMtime(vaultPath(key));
    if (m) manifest[key] = m;
  }

  const tasksRaw = await readFile(vaultPath('tasks'));
  const projectContext = (await readFile(vaultPath('projectContext'))) ?? undefined;
  const roadmap = (await readFile(vaultPath('roadmap'))) ?? undefined;
  const decisions = (await readFile(vaultPath('decisions'))) ?? undefined;

  let tasksFile: VaultMergePatch['tasksFile'];
  let currentGoal: string | undefined;
  if (tasksRaw) {
    tasksFile = parseTasksJson(tasksRaw) ?? undefined;
    currentGoal = tasksFile?.currentGoal;
  }

  return {
    version: 1,
    sourceNodeId: input.nodeId,
    sourcePlatform: input.platform ?? 'android',
    sourceLabel: input.label ?? null,
    updatedAt: new Date().toISOString(),
    vaultSyncVersion: input.vaultSyncVersion ?? 0,
    currentGoal,
    tasksFile,
    projectContext,
    roadmap,
    decisions,
    fileManifest: manifest,
  };
}
