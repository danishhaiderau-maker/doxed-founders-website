import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import {
  FOUNDER_VAULT_DIR_NAME,
  FOUNDER_VAULT_FILES,
  FOUNDER_VAULT_SCHEMA_VERSION,
  type FounderNodeConfig,
  type FounderVaultMeta,
  buildVaultSnapshot,
  defaultProjectContext,
  defaultRoadmap,
  emptyTasksFile,
  vaultFilePath,
} from '@dcf/founder-vault';

export function defaultVaultRoot(): string {
  return path.join(os.homedir(), FOUNDER_VAULT_DIR_NAME);
}

export function ensureVault(vaultRoot: string, nodeId: string): void {
  fs.mkdirSync(vaultRoot, { recursive: true });

  const metaPath = vaultFilePath(vaultRoot, 'meta');
  if (!fs.existsSync(metaPath)) {
    const now = new Date().toISOString();
    const meta: FounderVaultMeta = {
      version: FOUNDER_VAULT_SCHEMA_VERSION,
      createdAt: now,
      updatedAt: now,
      projectName: 'My Project',
      nodeId,
    };
    fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2), 'utf8');
  }

  const contextPath = vaultFilePath(vaultRoot, 'projectContext');
  if (!fs.existsSync(contextPath)) {
    fs.writeFileSync(
      contextPath,
      defaultProjectContext('My Project', 'Define your next milestone'),
      'utf8',
    );
  }

  const roadmapPath = vaultFilePath(vaultRoot, 'roadmap');
  if (!fs.existsSync(roadmapPath)) {
    fs.writeFileSync(roadmapPath, defaultRoadmap(), 'utf8');
  }

  const tasksPath = vaultFilePath(vaultRoot, 'tasks');
  if (!fs.existsSync(tasksPath)) {
    fs.writeFileSync(
      tasksPath,
      JSON.stringify(emptyTasksFile('Define your next milestone'), null, 2),
      'utf8',
    );
  }
}

export function loadOrCreateNodeId(vaultRoot: string): string {
  ensureVault(vaultRoot, 'pending');
  const metaPath = vaultFilePath(vaultRoot, 'meta');
  const meta = JSON.parse(fs.readFileSync(metaPath, 'utf8')) as FounderVaultMeta;
  if (meta.nodeId && meta.nodeId !== 'pending') return meta.nodeId;
  const nodeId = `node_${randomBytes(8).toString('hex')}`;
  meta.nodeId = nodeId;
  meta.updatedAt = new Date().toISOString();
  fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2), 'utf8');
  return nodeId;
}

export function readNodeConfig(vaultRoot: string): FounderNodeConfig | null {
  const configPath = vaultFilePath(vaultRoot, 'nodeConfig');
  if (!fs.existsSync(configPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(configPath, 'utf8')) as FounderNodeConfig;
  } catch {
    return null;
  }
}

export function writeNodeConfig(vaultRoot: string, config: FounderNodeConfig): void {
  fs.writeFileSync(vaultFilePath(vaultRoot, 'nodeConfig'), JSON.stringify(config, null, 2), 'utf8');
}

export function buildSnapshotFromVault(vaultRoot: string, label: string) {
  const meta = JSON.parse(
    fs.readFileSync(vaultFilePath(vaultRoot, 'meta'), 'utf8'),
  ) as FounderVaultMeta;

  const projectContext = fs.existsSync(vaultFilePath(vaultRoot, 'projectContext'))
    ? fs.readFileSync(vaultFilePath(vaultRoot, 'projectContext'), 'utf8')
    : undefined;
  const roadmap = fs.existsSync(vaultFilePath(vaultRoot, 'roadmap'))
    ? fs.readFileSync(vaultFilePath(vaultRoot, 'roadmap'), 'utf8')
    : undefined;
  const tasksRaw = fs.existsSync(vaultFilePath(vaultRoot, 'tasks'))
    ? fs.readFileSync(vaultFilePath(vaultRoot, 'tasks'), 'utf8')
    : undefined;

  return buildVaultSnapshot({
    meta,
    projectContext,
    roadmap,
    tasksRaw,
    vaultHealthy: true,
    deviceLabel: label,
  });
}

export function vaultDiskStats(vaultRoot: string): { storageGb?: number; storageFreeGb?: number } {
  try {
    if (process.platform === 'win32') {
      return {};
    }
    const stats = fs.statfsSync?.(vaultRoot);
    if (!stats) return {};
    const totalGb = Math.round((stats.blocks * stats.bsize) / 1e9);
    const freeGb = Math.round((stats.bfree * stats.bsize) / 1e9);
    return { storageGb: totalGb, storageFreeGb: freeGb };
  } catch {
    return {};
  }
}

export { FOUNDER_VAULT_FILES };
