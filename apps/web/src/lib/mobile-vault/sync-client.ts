'use client';

import { founderNodeAuthHeader, type FounderNodeHeartbeat } from '@dcf/founder-vault/schema';
import type { DeviceMemoryMetadataPayload } from '@dcf/utils';
import { apiUrl } from '@/lib/api-base';

function apiBase(path: string): string {
  return apiUrl(path);
}

export async function pairMobileNode(input: {
  code: string;
  nodeId: string;
  label: string;
  appVersion: string;
}): Promise<{ nodeToken: string; nodeId: string; userId: string }> {
  const res = await fetch(apiBase('/founder-node/pair'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...input,
      platform: 'android',
    }),
  });
  const body = (await res.json().catch(() => ({}))) as {
    nodeToken?: string;
    nodeId?: string;
    userId?: string;
    message?: string | string[];
  };
  if (!res.ok) {
    const msg = Array.isArray(body.message) ? body.message.join(', ') : body.message;
    throw new Error(msg ?? `Pairing failed (${res.status})`);
  }
  if (!body.nodeToken || !body.nodeId) throw new Error('Invalid pairing response');
  return { nodeToken: body.nodeToken, nodeId: body.nodeId, userId: body.userId! };
}

export async function sendMobileHeartbeat(
  nodeId: string,
  nodeToken: string,
  heartbeat: FounderNodeHeartbeat,
): Promise<void> {
  const res = await fetch(apiBase('/founder-node/heartbeat'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: founderNodeAuthHeader(nodeId, nodeToken),
    },
    body: JSON.stringify(heartbeat),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Heartbeat failed (${res.status}): ${text.slice(0, 120)}`);
  }
}

export async function syncMobileVault(
  nodeId: string,
  nodeToken: string,
  payload: DeviceMemoryMetadataPayload,
): Promise<void> {
  const res = await fetch(apiBase('/founder-node/sync'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: founderNodeAuthHeader(nodeId, nodeToken),
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Vault sync failed (${res.status}): ${text.slice(0, 120)}`);
  }
}

export async function fetchVaultSyncPlan(nodeId: string, nodeToken: string) {
  const res = await fetch(apiBase('/founder-node/vault-sync/plan'), {
    headers: { Authorization: founderNodeAuthHeader(nodeId, nodeToken) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Vault sync plan failed (${res.status}): ${text.slice(0, 120)}`);
  }
  return res.json() as Promise<{
    pulls: Array<{
      sourceNodeId: string;
      sourceLabel: string | null;
      vaultSyncVersion: number;
      updatedAt: string;
    }>;
    vaultPrimaryPlatform?: 'desktop' | 'mobile' | null;
  }>;
}

export async function fetchVaultMergePatch(
  nodeId: string,
  nodeToken: string,
  sourceNodeId: string,
) {
  const res = await fetch(
    apiBase(`/founder-node/vault-sync/merge/${encodeURIComponent(sourceNodeId)}`),
    { headers: { Authorization: founderNodeAuthHeader(nodeId, nodeToken) } },
  );
  if (!res.ok) throw new Error(`Merge fetch failed (${res.status})`);
  return res.json() as Promise<{
    mergePatch: import('@dcf/utils').VaultMergePatch | null;
    vaultSyncVersion: number;
    vaultPrimaryPlatform?: 'desktop' | 'mobile' | null;
    alreadyApplied?: boolean;
  }>;
}

export async function ackVaultMerge(
  nodeId: string,
  nodeToken: string,
  sourceNodeId: string,
  vaultSyncVersion: number,
): Promise<void> {
  const res = await fetch(apiBase('/founder-node/vault-sync/ack'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: founderNodeAuthHeader(nodeId, nodeToken),
    },
    body: JSON.stringify({ sourceNodeId, vaultSyncVersion }),
  });
  if (!res.ok) throw new Error(`Vault merge ack failed (${res.status})`);
}

export async function fetchVaultRelayForNode(
  nodeId: string,
  accessToken: string,
): Promise<{ encryptedVaultBlob: string; updatedAt: string; label: string | null }> {
  const res = await fetch(apiUrl(`/founder-node/vault-relays/${encodeURIComponent(nodeId)}`), {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const msg =
      body && typeof body === 'object' && 'message' in body
        ? String((body as { message: unknown }).message)
        : `Relay fetch failed (${res.status})`;
    throw new Error(msg);
  }
  return res.json() as Promise<{
    encryptedVaultBlob: string;
    updatedAt: string;
    label: string | null;
  }>;
}
