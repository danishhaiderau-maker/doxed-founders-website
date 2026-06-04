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
