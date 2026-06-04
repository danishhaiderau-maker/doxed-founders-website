'use client';

import { FOUNDER_VAULT_FILES, type FounderVaultFileKey } from '@dcf/founder-vault/paths';
import {
  FOUNDER_VAULT_SCHEMA_VERSION,
  emptyTasksFile,
  type FounderVaultMeta,
} from '@dcf/founder-vault/schema';
import {
  buildVaultMetadataSyncPayload,
  buildVaultSnapshot,
  defaultProjectContext,
  defaultRoadmap,
  defaultPrivateNotes,
  defaultDecisionsLog,
  defaultBuildHistoryLine,
} from '@dcf/founder-vault/snapshot';
import { deriveVaultKey, encryptVaultJson } from './crypto-web';

const VAULT_DIR = 'FounderVault';

function vaultPath(key: FounderVaultFileKey): string {
  return `${VAULT_DIR}/${FOUNDER_VAULT_FILES[key]}`;
}

async function readFile(path: string): Promise<string | null> {
  const { Filesystem, Directory } = await import('@capacitor/filesystem');
  try {
    const res = await Filesystem.readFile({ path, directory: Directory.Data });
    if (typeof res.data === 'string') return res.data;
    return null;
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

export async function ensureMobileVault(nodeId: string): Promise<void> {
  const metaRaw = await readFile(vaultPath('meta'));
  if (!metaRaw) {
    const now = new Date().toISOString();
    const meta: FounderVaultMeta = {
      version: FOUNDER_VAULT_SCHEMA_VERSION,
      createdAt: now,
      updatedAt: now,
      projectName: 'My Project',
      nodeId,
    };
    await writeFile(vaultPath('meta'), JSON.stringify(meta, null, 2));
    await writeFile(
      vaultPath('projectContext'),
      defaultProjectContext('My Project', 'Define your next milestone'),
    );
    await writeFile(vaultPath('roadmap'), defaultRoadmap());
    await writeFile(
      vaultPath('tasks'),
      JSON.stringify(emptyTasksFile('Define your next milestone'), null, 2),
    );
    await writeFile(vaultPath('decisions'), defaultDecisionsLog());
    await writeFile(vaultPath('privateNotes'), defaultPrivateNotes());
    await writeFile(vaultPath('buildHistory'), `${defaultBuildHistoryLine()}\n`);
  } else {
    const meta = JSON.parse(metaRaw) as FounderVaultMeta;
    if (!meta.nodeId || meta.nodeId === 'pending') {
      meta.nodeId = nodeId;
      meta.updatedAt = new Date().toISOString();
      await writeFile(vaultPath('meta'), JSON.stringify(meta, null, 2));
    }
  }
}

export async function buildMobileVaultSnapshot(label: string): Promise<{
  snapshot: ReturnType<typeof buildVaultSnapshot>;
}> {
  const meta = JSON.parse((await readFile(vaultPath('meta'))) ?? '{}') as FounderVaultMeta;
  const projectContext = (await readFile(vaultPath('projectContext'))) ?? undefined;
  const roadmap = (await readFile(vaultPath('roadmap'))) ?? undefined;
  const tasksRaw = (await readFile(vaultPath('tasks'))) ?? undefined;
  const decisions = (await readFile(vaultPath('decisions'))) ?? undefined;
  const privateNotes = (await readFile(vaultPath('privateNotes'))) ?? undefined;

  const snapshot = buildVaultSnapshot({
    meta,
    projectContext,
    roadmap,
    tasksRaw,
    decisions,
    privateNotes,
    deviceLabel: label,
    vaultHealthy: true,
  });

  return { snapshot };
}

export async function buildEncryptedSyncPayloadAsync(
  nodeToken: string,
  nodeId: string,
  label: string,
): Promise<import('@dcf/utils').DeviceMemoryMetadataPayload> {
  const { snapshot } = await buildMobileVaultSnapshot(label);
  const key = await deriveVaultKey(nodeToken, nodeId);
  const extended = snapshot as typeof snapshot & { decisions?: string; privateNotes?: string };
  const sensitive = JSON.stringify({
    projectContext: snapshot.projectContext,
    roadmap: snapshot.roadmap,
    tasksFile: snapshot.tasksFile,
    decisions: extended.decisions,
    privateNotes: extended.privateNotes,
  });
  const encryptedVaultBlob = await encryptVaultJson(sensitive, key);
  return buildVaultMetadataSyncPayload(snapshot, encryptedVaultBlob);
}

export async function applyEncryptedVaultToDevice(
  blobBase64: string,
  nodeToken: string,
  nodeId: string,
): Promise<void> {
  const key = await deriveVaultKey(nodeToken, nodeId);
  const { decryptVaultJson } = await import('./crypto-web');
  const plain = await decryptVaultJson(blobBase64, key);
  const sensitive = JSON.parse(plain) as {
    projectContext?: string;
    roadmap?: string;
    tasksFile?: import('@dcf/utils').FounderOsTasksFile;
    decisions?: string;
    privateNotes?: string;
  };
  if (sensitive.projectContext) await writeFile(vaultPath('projectContext'), sensitive.projectContext);
  if (sensitive.roadmap) await writeFile(vaultPath('roadmap'), sensitive.roadmap);
  if (sensitive.tasksFile) {
    await writeFile(vaultPath('tasks'), JSON.stringify(sensitive.tasksFile, null, 2));
  }
  if (sensitive.decisions) await writeFile(vaultPath('decisions'), sensitive.decisions);
  if (sensitive.privateNotes) await writeFile(vaultPath('privateNotes'), sensitive.privateNotes);
  const metaRaw = await readFile(vaultPath('meta'));
  if (metaRaw) {
    const meta = JSON.parse(metaRaw) as FounderVaultMeta;
    meta.updatedAt = new Date().toISOString();
    await writeFile(vaultPath('meta'), JSON.stringify(meta, null, 2));
  }
}
