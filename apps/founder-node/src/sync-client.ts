import fs from 'node:fs';
import os from 'node:os';
import {
  FOUNDER_NODE_APP_VERSION,
  founderNodeAuthHeader,
  parseTasksJson,
  vaultFilePath,
  type FounderNodeHeartbeat,
  type FounderNodePairRequest,
  type FounderNodePairResponse,
} from '@dcf/founder-vault';
import type { DesktopBridgeInput, DeviceMemoryMetadataPayload } from '@dcf/utils';

function apiBase(apiBaseUrl: string, path: string): string {
  const base = apiBaseUrl.replace(/\/$/, '');
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

export async function pairNode(
  apiBaseUrl: string,
  input: FounderNodePairRequest,
): Promise<FounderNodePairResponse> {
  const res = await fetch(apiBase(apiBaseUrl, '/api/founder-node/pair'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  const body = (await res.json().catch(() => null)) as FounderNodePairResponse & {
    message?: string | string[];
  };
  if (!res.ok) {
    const msg = Array.isArray(body?.message) ? body.message.join(', ') : body?.message;
    throw new Error(msg ?? `Pairing failed (${res.status})`);
  }
  return body;
}

export async function sendHeartbeat(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
  heartbeat: FounderNodeHeartbeat,
): Promise<void> {
  const res = await fetch(apiBase(apiBaseUrl, '/api/founder-node/heartbeat'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: founderNodeAuthHeader(nodeId, nodeToken),
    },
    body: JSON.stringify(heartbeat),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Heartbeat failed (${res.status}): ${text}`);
  }
}

export async function syncVaultMetadata(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
  payload: DeviceMemoryMetadataPayload,
): Promise<void> {
  const res = await fetch(apiBase(apiBaseUrl, '/api/founder-node/sync'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: founderNodeAuthHeader(nodeId, nodeToken),
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Sync failed (${res.status}): ${text}`);
  }
}

export function isFounderNodeAuthError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /\((401|403)\)/.test(msg) || /unauthorized/i.test(msg);
}

export function throwIfFounderNodeAuthResponse(status: number, text: string): void {
  if (status === 401 || status === 403) {
    throw new Error(`Request failed (${status}): ${text.slice(0, 200)}`);
  }
}

export function buildDesktopBridgeFromVault(vaultRoot: string): DesktopBridgeInput | undefined {
  try {
    const tasksPath = vaultFilePath(vaultRoot, 'tasks');
    const raw = fs.existsSync(tasksPath) ? fs.readFileSync(tasksPath, 'utf8') : '';
    const tasks = parseTasksJson(raw);
    const openTask = tasks?.tasks.find((t) => t.status !== 'done' && t.status !== 'DONE');
    const fileCount = fs.existsSync(vaultRoot)
      ? fs.readdirSync(vaultRoot).filter((f) => f.endsWith('.md') || f.endsWith('.json')).length
      : 0;
    return {
      taskLabel: openTask?.title ?? tasks?.currentGoal ?? undefined,
      editSummary: fileCount > 0 ? `${fileCount} vault files tracked` : undefined,
      agentStatus: 'founder_node_online',
    };
  } catch {
    return undefined;
  }
}

export function defaultHeartbeat(label: string, vaultPath: string): FounderNodeHeartbeat {
  const ramGb = Math.round(os.totalmem() / 1e9);
  const desktopBridge = buildDesktopBridgeFromVault(vaultPath);
  return {
    nodeId: '',
    label,
    platform: process.platform,
    appVersion: FOUNDER_NODE_APP_VERSION,
    ramGb,
    vaultHealthy: true,
    vaultPath,
    ...(desktopBridge ? { desktopBridge } : {}),
  };
}
