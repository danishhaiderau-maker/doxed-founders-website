import type { DeviceMemoryPayload, FounderOsTasksFile } from '@dcf/utils';

export const FOUNDER_VAULT_SCHEMA_VERSION = 1 as const;
export const FOUNDER_NODE_APP_VERSION = '0.3.0';

export type FounderVaultMeta = {
  version: typeof FOUNDER_VAULT_SCHEMA_VERSION;
  createdAt: string;
  updatedAt: string;
  projectName?: string;
  nodeId?: string;
  pairedAt?: string | null;
};

export type FounderNodeConfig = {
  version: 1;
  apiBaseUrl: string;
  nodeId: string;
  nodeToken: string;
  label: string;
  pairedAt: string;
  ollama?: FounderNodeOllamaConfig;
};

export type FounderNodeHeartbeat = {
  nodeId: string;
  label: string;
  platform: string;
  appVersion: string;
  ramGb?: number;
  storageGb?: number;
  storageFreeGb?: number;
  vaultHealthy: boolean;
  vaultPath?: string;
  ollamaEnabled?: boolean;
  ollamaBaseUrl?: string;
  ollamaModel?: string;
};

export type FounderNodeOllamaConfig = {
  enabled: boolean;
  baseUrl: string;
  model: string;
};

export type FounderNodePairRequest = {
  code: string;
  nodeId: string;
  label: string;
  platform?: string;
  appVersion?: string;
};

export type FounderNodePairResponse = {
  nodeToken: string;
  nodeId: string;
  userId: string;
};

export type FounderNodeStatusRow = {
  id: string;
  nodeId: string;
  label: string;
  status: 'online' | 'offline';
  lastSeenAt: string | null;
  ramGb: number | null;
  storageGb: number | null;
  storageFreeGb: number | null;
  vaultHealthy: boolean;
  platform: string | null;
  appVersion: string | null;
};

export type FounderNodePairingCodeResponse = {
  code: string;
  expiresAt: string;
};

export type FounderVaultSnapshot = DeviceMemoryPayload & {
  vaultHealthy: boolean;
  tasksRemaining?: number;
};

export function emptyTasksFile(currentGoal: string): FounderOsTasksFile {
  return {
    version: 1,
    updatedAt: new Date().toISOString(),
    currentGoal,
    tasks: [],
  };
}

export function parseTasksJson(raw: string): FounderOsTasksFile | null {
  try {
    const parsed = JSON.parse(raw) as FounderOsTasksFile;
    if (parsed?.version !== 1 || !Array.isArray(parsed.tasks)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function founderNodeAuthHeader(nodeId: string, nodeToken: string): string {
  return `FounderNode ${nodeId}:${nodeToken}`;
}

export function parseFounderNodeAuthHeader(
  header: string | undefined,
): { nodeId: string; nodeToken: string } | null {
  if (!header?.startsWith('FounderNode ')) return null;
  const creds = header.slice('FounderNode '.length).trim();
  const colon = creds.indexOf(':');
  if (colon <= 0) return null;
  const nodeId = creds.slice(0, colon);
  const nodeToken = creds.slice(colon + 1);
  if (!nodeId || !nodeToken) return null;
  return { nodeId, nodeToken };
}
