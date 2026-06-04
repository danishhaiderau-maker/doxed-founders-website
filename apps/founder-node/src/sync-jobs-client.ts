import {
  executeSyncJobOnVault,
  founderNodeAuthHeader,
  rebuildVaultVectorIndex,
} from '@dcf/founder-vault';
import { readNodeConfig } from './vault-manager';
import { throwIfFounderNodeAuthResponse } from './sync-client';

const MAX_JOBS_PER_CYCLE = 5;

function apiBase(apiBaseUrl: string, path: string): string {
  const base = apiBaseUrl.replace(/\/$/, '');
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

export async function fetchPendingSyncJob(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
): Promise<{ id: string; kind: string; payload: Record<string, unknown> } | null> {
  const res = await fetch(apiBase(apiBaseUrl, '/api/founder-node/sync-jobs/pending'), {
    headers: { Authorization: founderNodeAuthHeader(nodeId, nodeToken) },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throwIfFounderNodeAuthResponse(res.status, text);
    console.warn(`Sync job poll failed (${res.status}): ${text.slice(0, 200)}`);
    return null;
  }
  const body = (await res.json()) as {
    id?: string;
    kind?: string;
    payload?: Record<string, unknown>;
  } | null;
  if (!body?.id || !body.kind) return null;
  return { id: body.id, kind: body.kind, payload: body.payload ?? {} };
}

export async function completeSyncJob(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
  jobId: string,
  input: { result?: Record<string, unknown>; error?: string },
): Promise<void> {
  const res = await fetch(apiBase(apiBaseUrl, `/api/founder-node/sync-jobs/${jobId}/complete`), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: founderNodeAuthHeader(nodeId, nodeToken),
    },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Sync job complete failed (${res.status}): ${text}`);
  }
}

async function processOneSyncJob(vaultRoot: string): Promise<boolean> {
  const config = readNodeConfig(vaultRoot);
  if (!config) return false;

  const job = await fetchPendingSyncJob(config.apiBaseUrl, config.nodeId, config.nodeToken);
  if (!job) return false;

  try {
    const result = executeSyncJobOnVault(vaultRoot, job.kind, job.payload);
    await completeSyncJob(config.apiBaseUrl, config.nodeId, config.nodeToken, job.id, { result });
  } catch (err) {
    await completeSyncJob(config.apiBaseUrl, config.nodeId, config.nodeToken, job.id, {
      error: err instanceof Error ? err.message : String(err),
    });
  }

  return true;
}

export async function processPendingSyncJobs(vaultRoot: string): Promise<void> {
  for (let i = 0; i < MAX_JOBS_PER_CYCLE; i += 1) {
    const handled = await processOneSyncJob(vaultRoot);
    if (!handled) break;
  }
}

export function maybeRebuildVectorIndex(vaultRoot: string): number {
  const index = rebuildVaultVectorIndex(vaultRoot);
  return index.chunks.length;
}
