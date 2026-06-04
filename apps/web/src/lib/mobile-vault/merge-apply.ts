'use client';

import type { VaultMergePatch } from '@dcf/utils';
import { shouldApplyFile } from '@dcf/utils';
import { FOUNDER_VAULT_FILES, type FounderVaultFileKey } from '@dcf/founder-vault/paths';
import type { FounderVaultMeta } from '@dcf/founder-vault/schema';

const VAULT_DIR = 'FounderVault';

function vaultPath(key: FounderVaultFileKey): string {
  return `${VAULT_DIR}/${FOUNDER_VAULT_FILES[key]}`;
}

async function readFile(path: string): Promise<string | null> {
  const { Filesystem, Directory } = await import('@capacitor/filesystem');
  try {
    const res = await Filesystem.readFile({ path, directory: Directory.Data });
    return typeof res.data === 'string' ? res.data : null;
  } catch {
    return null;
  }
}

async function writeFile(path: string, data: string): Promise<void> {
  const { Filesystem, Directory, Encoding } = await import('@capacitor/filesystem');
  const dir = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
  if (dir) {
    try {
      await Filesystem.mkdir({ path: dir, directory: Directory.Data, recursive: true });
    } catch {
      /* exists */
    }
  }
  await Filesystem.writeFile({
    path,
    data,
    directory: Directory.Data,
    encoding: Encoding.UTF8,
  });
}

async function readManifest(): Promise<Record<string, string>> {
  const keys: FounderVaultFileKey[] = ['projectContext', 'roadmap', 'tasks', 'decisions', 'privateNotes'];
  const manifest: Record<string, string> = {};
  const { Filesystem, Directory } = await import('@capacitor/filesystem');
  for (const key of keys) {
    const path = vaultPath(key);
    try {
      const stat = await Filesystem.stat({ path, directory: Directory.Data });
      if (stat.mtime) manifest[key] = new Date(stat.mtime).toISOString();
    } catch {
      /* missing */
    }
  }
  return manifest;
}

export async function applyVaultMergePatchOnDevice(
  patch: VaultMergePatch,
  options?: { primaryPlatform?: 'desktop' | 'mobile' | null },
): Promise<{ applied: string[]; skipped: string[] }> {
  const localManifest = await readManifest();
  const primary = options?.primaryPlatform ?? null;
  const applied: string[] = [];
  const skipped: string[] = [];

  if (patch.tasksFile && shouldApplyFile('tasks', patch, localManifest, primary)) {
    await writeFile(vaultPath('tasks'), JSON.stringify(patch.tasksFile, null, 2));
    applied.push('tasks');
  } else if (patch.currentGoal && shouldApplyFile('tasks', patch, localManifest, primary)) {
    const tasksRaw = await readFile(vaultPath('tasks'));
    const existing = tasksRaw ? JSON.parse(tasksRaw) : { version: 1, tasks: [], updatedAt: new Date().toISOString() };
    existing.currentGoal = patch.currentGoal;
    existing.updatedAt = new Date().toISOString();
    await writeFile(vaultPath('tasks'), JSON.stringify(existing, null, 2));
    applied.push('tasks');
  } else if (patch.currentGoal || patch.tasksFile) {
    skipped.push('tasks');
  }

  if (patch.projectContext && shouldApplyFile('projectContext', patch, localManifest, primary)) {
    await writeFile(vaultPath('projectContext'), patch.projectContext);
    applied.push('projectContext');
  }

  if (patch.roadmap && shouldApplyFile('roadmap', patch, localManifest, primary)) {
    await writeFile(vaultPath('roadmap'), patch.roadmap);
    applied.push('roadmap');
  }

  if (patch.decisions && shouldApplyFile('decisions', patch, localManifest, primary)) {
    await writeFile(vaultPath('decisions'), patch.decisions);
    applied.push('decisions');
  }

  const metaRaw = await readFile(vaultPath('meta'));
  if (metaRaw) {
    const meta = JSON.parse(metaRaw) as FounderVaultMeta;
    meta.updatedAt = new Date().toISOString();
    await writeFile(vaultPath('meta'), JSON.stringify(meta, null, 2));
  }

  return { applied, skipped };
}
