/** Phala P1 — CVM sealed vault sync/backup (shared API + web). */

export const PHALA_CVM_VAULT_ATTESTATION_KIND = 'PHALA_CVM_VAULT_BACKUP';

export type PhalaCvmVaultMode = 'cvm_enabled' | 'local_relay_only' | 'unconfigured';

export type PhalaCvmVaultBackupState =
  | 'idle'
  | 'pending'
  | 'recorded'
  | 'verified'
  | 'failed'
  | 'unavailable';

export type PhalaCvmVaultCheck = {
  name: string;
  ok: boolean;
  detail: string;
};

export type PhalaCvmPlatformConfig = {
  configured: boolean;
  backupUrlSet: boolean;
  workloadId: string | null;
  inferenceUrl: string | null;
};

export type PhalaCvmVaultStatusPayload = {
  version: 1;
  mode: PhalaCvmVaultMode;
  platformCvmAvailable: boolean;
  userPhalaConnected: boolean;
  backupState: PhalaCvmVaultBackupState;
  canRequestBackup: boolean;
  checks: PhalaCvmVaultCheck[];
  relay: {
    hasEncryptedBlob: boolean;
    lastSyncedAt: string | null;
    deviceLabel: string | null;
    blobHashPrefix: string | null;
  };
  lastBackup: {
    id: string;
    status: string;
    verified: boolean;
    summary: string | null;
    signingAddress: string | null;
    createdAt: string;
  } | null;
  docsUrl: string;
};

export type PhalaCvmCapabilitiesPayload = {
  version: 1;
  platformCvmConfigured: boolean;
  backupUrlSet: boolean;
  workloadId: string | null;
  docsUrl: string;
};

const DOCS_URL = 'https://docs.phala.com/phala-cloud/confidential-ai';

export function phalaCvmVaultDocsUrl(): string {
  return DOCS_URL;
}

/** Server-side env probe (never expose API keys). */
export function readPhalaCvmPlatformConfig(env: NodeJS.ProcessEnv = process.env): PhalaCvmPlatformConfig {
  const backupUrl = env.PHALA_CVM_BACKUP_URL?.trim() || '';
  const workloadId = env.PHALA_CVM_WORKLOAD_ID?.trim() || null;
  const inferenceUrl = env.PHALA_INFERENCE_URL?.trim() || null;
  const hasKey = Boolean(
    env.PHALA_CVM_API_KEY?.trim() || env.PHALA_API_KEY?.trim(),
  );
  const backupUrlSet = backupUrl.length > 0;
  return {
    configured: backupUrlSet && hasKey,
    backupUrlSet,
    workloadId,
    inferenceUrl,
  };
}

export function assessCvmVaultReadiness(input: {
  platformConfigured: boolean;
  userPhalaConnected: boolean;
  hasEncryptedBlob: boolean;
  founderNodeOnline: boolean;
  memoryModeFounderVault: boolean;
}): { checks: PhalaCvmVaultCheck[]; canRequestBackup: boolean; mode: PhalaCvmVaultMode } {
  const checks: PhalaCvmVaultCheck[] = [
    {
      name: 'encrypted_relay',
      ok: input.hasEncryptedBlob,
      detail: input.hasEncryptedBlob
        ? 'Encrypted vault blob on relay — server cannot decrypt'
        : 'No encrypted relay yet — keep Founder Node open to sync',
    },
    {
      name: 'founder_node_online',
      ok: input.founderNodeOnline,
      detail: input.founderNodeOnline
        ? 'Founder Node online'
        : 'Founder Node offline — open tray app before CVM backup',
    },
    {
      name: 'vault_memory_mode',
      ok: input.memoryModeFounderVault,
      detail: input.memoryModeFounderVault
        ? 'Founder Vault memory mode'
        : 'Enable Founder Vault (Founder Node) memory mode',
    },
    {
      name: 'phala_connected',
      ok: input.userPhalaConnected,
      detail: input.userPhalaConnected
        ? 'Phala Private AI connected or platform credits enabled'
        : 'Connect Phala in Builder settings for CVM attestation receipts',
    },
    {
      name: 'platform_cvm',
      ok: input.platformConfigured,
      detail: input.platformConfigured
        ? 'Phala CVM backup workload configured on API'
        : 'CVM backup URL not set — local relay only (set PHALA_CVM_BACKUP_URL on Railway)',
    },
  ];

  const relayReady = input.hasEncryptedBlob && input.founderNodeOnline && input.memoryModeFounderVault;
  const canRequestBackup = relayReady && input.userPhalaConnected;

  let mode: PhalaCvmVaultMode = 'unconfigured';
  if (input.platformConfigured && relayReady) mode = 'cvm_enabled';
  else if (relayReady) mode = 'local_relay_only';

  return { checks, canRequestBackup, mode };
}

export function resolveCvmBackupState(input: {
  platformConfigured: boolean;
  lastLog: { verified: boolean; status: string } | null;
  pendingRequest: boolean;
}): PhalaCvmVaultBackupState {
  if (input.pendingRequest) return 'pending';
  if (!input.lastLog) {
    return input.platformConfigured ? 'idle' : 'unavailable';
  }
  if (input.lastLog.verified) return 'verified';
  if (input.lastLog.status === 'failed') return 'failed';
  if (input.lastLog.status === 'verified') return 'verified';
  if (input.lastLog.status === 'recorded' || input.lastLog.status === 'pending') return 'recorded';
  return 'idle';
}
