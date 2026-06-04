import {
  applyVaultMergePatchToDisk,
  buildVaultMergePatch,
  founderNodeAuthHeader,
} from '@dcf/founder-vault';
import type { VaultMergePatch } from '@dcf/utils';
import { readNodeConfig } from './vault-manager';
import { throwIfFounderNodeAuthResponse } from './sync-client';

function apiBase(apiBaseUrl: string, path: string): string {
  const base = apiBaseUrl.replace(/\/$/, '');
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

type VaultSyncPlan = {
  pulls: Array<{
    sourceNodeId: string;
    vaultSyncVersion: number;
  }>;
  vaultPrimaryPlatform?: 'desktop' | 'mobile' | null;
};

export function buildMergePatchForSync(vaultRoot: string, nodeId: string, label: string, platform: string) {
  return buildVaultMergePatch({
    nodeId,
    platform,
    label,
    vaultSyncVersion: 0,
    vaultRoot,
    primaryPlatform: null,
  });
}

export async function pullPendingVaultMerges(vaultRoot: string): Promise<number> {
  const config = readNodeConfig(vaultRoot);
  if (!config) return 0;

  const headers = {
    Authorization: founderNodeAuthHeader(config.nodeId, config.nodeToken),
  };

  const planRes = await fetch(apiBase(config.apiBaseUrl, '/api/founder-node/vault-sync/plan'), {
    headers,
  });
  if (!planRes.ok) {
    const text = await planRes.text().catch(() => '');
    throwIfFounderNodeAuthResponse(planRes.status, text);
    return 0;
  }

  const plan = (await planRes.json()) as VaultSyncPlan;
  let applied = 0;

  for (const pull of plan.pulls ?? []) {
    const mergeRes = await fetch(
      apiBase(
        config.apiBaseUrl,
        `/api/founder-node/vault-sync/merge/${encodeURIComponent(pull.sourceNodeId)}`,
      ),
      { headers },
    );
    if (!mergeRes.ok) continue;
    const body = (await mergeRes.json()) as {
      mergePatch?: VaultMergePatch | null;
      vaultSyncVersion?: number;
      vaultPrimaryPlatform?: 'desktop' | 'mobile' | null;
      alreadyApplied?: boolean;
    };
    if (body.alreadyApplied || !body.mergePatch) continue;

    applyVaultMergePatchToDisk(vaultRoot, body.mergePatch, {
      primaryPlatform: body.vaultPrimaryPlatform ?? plan.vaultPrimaryPlatform ?? 'desktop',
    });

    await fetch(apiBase(config.apiBaseUrl, '/api/founder-node/vault-sync/ack'), {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sourceNodeId: pull.sourceNodeId,
        vaultSyncVersion: body.vaultSyncVersion ?? pull.vaultSyncVersion,
      }),
    });
    applied += 1;
  }

  return applied;
}
