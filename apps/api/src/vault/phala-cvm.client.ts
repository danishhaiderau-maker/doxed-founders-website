import { normalizePhalaBaseUrl } from '../builder/phala.client';

export type CvmVaultBackupRequest = {
  blobHash: string;
  relayUpdatedAt: string;
  memoryMode: string;
  deviceLabel: string | null;
  taskCount: number;
  workloadId?: string | null;
};

export type CvmVaultBackupResult = {
  ok: boolean;
  backupId: string | null;
  signingAddress: string | null;
  receipt: Record<string, unknown> | null;
  error: string | null;
};

export async function pushVaultBackupToCvm(input: {
  backupUrl: string;
  apiKey: string;
  payload: CvmVaultBackupRequest;
}): Promise<CvmVaultBackupResult> {
  const base = input.backupUrl.replace(/\/$/, '');
  const path = base.endsWith('/vault/backup') ? base : `${base}/vault/backup`;

  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${input.apiKey.trim()}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(input.payload),
      signal: AbortSignal.timeout(45_000),
    });

    const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;

    if (!res.ok) {
      const errText =
        typeof body?.error === 'string'
          ? body.error
          : typeof body?.message === 'string'
            ? body.message
            : `CVM backup HTTP ${res.status}`;
      return { ok: false, backupId: null, signingAddress: null, receipt: body, error: errText };
    }

    const backupId =
      typeof body?.backupId === 'string'
        ? body.backupId
        : typeof body?.id === 'string'
          ? body.id
          : null;
    const signingAddress =
      typeof body?.signing_address === 'string'
        ? body.signing_address
        : typeof body?.signingAddress === 'string'
          ? body.signingAddress
          : null;

    return {
      ok: true,
      backupId,
      signingAddress,
      receipt: body,
      error: null,
    };
  } catch (err) {
    return {
      ok: false,
      backupId: null,
      signingAddress: null,
      receipt: null,
      error: err instanceof Error ? err.message : 'CVM backup request failed',
    };
  }
}

export async function probeCvmBackupHealth(input: {
  backupUrl: string;
  apiKey: string;
}): Promise<{ ok: boolean; detail: string }> {
  const base = input.backupUrl.replace(/\/$/, '');
  const healthPath = base.endsWith('/vault/backup')
    ? base.replace(/\/vault\/backup$/, '/health')
    : `${base}/health`;

  try {
    const res = await fetch(healthPath, {
      headers: { Authorization: `Bearer ${input.apiKey.trim()}` },
      signal: AbortSignal.timeout(12_000),
    });
    if (res.ok) return { ok: true, detail: 'CVM workload reachable' };
    return { ok: false, detail: `CVM health HTTP ${res.status}` };
  } catch (err) {
    return {
      ok: false,
      detail: err instanceof Error ? err.message : 'CVM workload unreachable',
    };
  }
}

export function resolvePlatformCvmApiKey(env: NodeJS.ProcessEnv = process.env): string | null {
  return env.PHALA_CVM_API_KEY?.trim() || env.PHALA_API_KEY?.trim() || null;
}

export function resolvePlatformCvmBackupUrl(env: NodeJS.ProcessEnv = process.env): string | null {
  return env.PHALA_CVM_BACKUP_URL?.trim() || null;
}

export { normalizePhalaBaseUrl };
