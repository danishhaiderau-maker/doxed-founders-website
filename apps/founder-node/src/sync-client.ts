import os from 'node:os';
import {
  FOUNDER_NODE_APP_VERSION,
  founderNodeAuthHeader,
  type FounderNodeHeartbeat,
  type FounderNodePairRequest,
  type FounderNodePairResponse,
} from '@dcf/founder-vault';
import type { DeviceMemoryMetadataPayload } from '@dcf/utils';

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

export function defaultHeartbeat(label: string, vaultPath: string): FounderNodeHeartbeat {
  const ramGb = Math.round(os.totalmem() / 1e9);
  return {
    nodeId: '',
    label,
    platform: process.platform,
    appVersion: FOUNDER_NODE_APP_VERSION,
    ramGb,
    vaultHealthy: true,
    vaultPath,
  };
}
