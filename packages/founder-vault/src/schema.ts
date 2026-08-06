import type { BridgeSession, BridgeWorkspace, DesktopBridgeInput, DeviceMemoryPayload, FounderOsTasksFile } from '@dcf/utils';

export const FOUNDER_VAULT_SCHEMA_VERSION = 1 as const;
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
  /** Phase 5 — Founder Cloud local stack on this machine. */
  founderCloud?: FounderCloudMode;
};

export type FounderCloudMode = {
  enabled: boolean;
  repoPath?: string;
  stackRunning?: boolean;
  webUrl?: string;
  apiUrl?: string;
  lastStartedAt?: string;
  lastError?: string;
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
  /** Metadata-only IDE context (branch, file names, task label — no file contents). */
  desktopBridge?: DesktopBridgeInput;
  /** Phase 5 — local Founder Cloud stack status from tray. */
  founderCloud?: FounderCloudMode;
  /** Discovered IDE workspaces surfaced from the desktop node (Phase A). */
  workspaces?: BridgeWorkspace[];
  /** Recent IDE chat/agent sessions read from on-disk IDE storage (Phase A). */
  sessions?: BridgeSession[];
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

/**
 * Parse Founder Node credentials from an Authorization header.
 *
 * Accepted forms (docs + IDE clients use both):
 *   - `FounderNode {nodeId}:{nodeToken}`  (Node tray / sync clients)
 *   - `Bearer fos_{nodeId}:{nodeToken}`   (OpenAI-compat IDE / extension)
 */
export function parseFounderNodeAuthHeader(
  header: string | undefined,
): { nodeId: string; nodeToken: string } | null {
  if (!header) return null;
  const trimmed = header.trim();

  let creds: string | null = null;
  if (trimmed.startsWith('FounderNode ')) {
    creds = trimmed.slice('FounderNode '.length).trim();
  } else if (/^Bearer\s+/i.test(trimmed)) {
    const token = trimmed.replace(/^Bearer\s+/i, '').trim();
    if (token.startsWith('fos_')) {
      creds = token.slice('fos_'.length);
    }
  }

  if (!creds) return null;
  const colon = creds.indexOf(':');
  if (colon <= 0) return null;
  const nodeId = creds.slice(0, colon);
  const nodeToken = creds.slice(colon + 1);
  if (!nodeId || !nodeToken) return null;
  return { nodeId, nodeToken };
}
