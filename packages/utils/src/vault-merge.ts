import type { FounderOsTasksFile } from './founder-os-memory-files.js';

/** Per vault file key → ISO updatedAt (LWW merge). */
export type VaultFileManifest = Record<string, string>;

export type VaultMergePatch = {
  version: 1;
  sourceNodeId: string;
  sourcePlatform?: string | null;
  sourceLabel?: string | null;
  updatedAt: string;
  vaultSyncVersion: number;
  currentGoal?: string;
  tasksFile?: FounderOsTasksFile;
  projectContext?: string;
  roadmap?: string;
  decisions?: string;
  fileManifest: VaultFileManifest;
};

export function shouldApplyFile(
  fileKey: string,
  patch: VaultMergePatch,
  localManifest: VaultFileManifest,
  primaryPlatform?: 'desktop' | 'mobile' | null,
): boolean {
  const patchAt = patch.fileManifest[fileKey];
  const localAt = localManifest[fileKey];
  if (!patchAt) return false;
  if (!localAt) return true;
  const patchMs = Date.parse(patchAt);
  const localMs = Date.parse(localAt);
  if (Number.isNaN(patchMs)) return true;
  if (Number.isNaN(localMs)) return true;
  if (patchMs > localMs) return true;
  if (patchMs < localMs) return false;
  if (primaryPlatform && patch.sourcePlatform) {
    return patch.sourcePlatform === primaryPlatform;
  }
  return false;
}
