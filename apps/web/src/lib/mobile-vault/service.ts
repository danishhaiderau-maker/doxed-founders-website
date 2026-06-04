'use client';

import type { FounderNodeConfig } from '@dcf/founder-vault/schema';
import { MOBILE_VAULT_APP_VERSION, whenCapacitorReady } from './capacitor';
import {
  clearMobileNodeConfig,
  loadOrCreateMobileNodeId,
  readMobileNodeConfig,
  writeMobileNodeConfig,
} from './storage';
import { applyEncryptedVaultToDevice, buildEncryptedSyncPayloadAsync, ensureMobileVault } from './vault-files';
import { fetchVaultRelayForNode, pairMobileNode, sendMobileHeartbeat, syncMobileVault } from './sync-client';

const SYNC_INTERVAL_MS = 45_000;
const API_BASE = 'https://doxxedcrypto.digital';

export type MobileVaultStatus = {
  paired: boolean;
  label?: string;
  nodeId?: string;
  lastSyncAt?: string | null;
  lastError?: string | null;
  syncing?: boolean;
};

let intervalId: ReturnType<typeof setInterval> | null = null;
let listeners: Array<(s: MobileVaultStatus) => void> = [];
let status: MobileVaultStatus = { paired: false };

function emit(next: Partial<MobileVaultStatus>) {
  status = { ...status, ...next };
  listeners.forEach((fn) => fn(status));
}

export function subscribeMobileVaultStatus(fn: (s: MobileVaultStatus) => void): () => void {
  listeners.push(fn);
  fn(status);
  return () => {
    listeners = listeners.filter((l) => l !== fn);
  };
}

export function getMobileVaultStatus(): MobileVaultStatus {
  return status;
}

async function runSyncCycle(): Promise<void> {
  const config = await readMobileNodeConfig();
  if (!config) return;
  emit({ syncing: true, lastError: null });
  try {
    await ensureMobileVault(config.nodeId);
    const payload = await buildEncryptedSyncPayloadAsync(
      config.nodeToken,
      config.nodeId,
      config.label,
    );
    await sendMobileHeartbeat(config.nodeId, config.nodeToken, {
      nodeId: config.nodeId,
      label: config.label,
      platform: 'android',
      appVersion: MOBILE_VAULT_APP_VERSION,
      vaultHealthy: true,
      vaultPath: 'FounderVault',
    });
    await syncMobileVault(config.nodeId, config.nodeToken, payload);
    emit({ lastSyncAt: new Date().toISOString(), syncing: false });
  } catch (e) {
    emit({
      syncing: false,
      lastError: e instanceof Error ? e.message : 'Sync failed',
    });
  }
}

function startSyncLoop() {
  if (intervalId) clearInterval(intervalId);
  void runSyncCycle();
  intervalId = setInterval(() => void runSyncCycle(), SYNC_INTERVAL_MS);
}

export async function pairMobileVaultWithCode(code: string, label = 'Android vault'): Promise<void> {
  if (!(await whenCapacitorReady())) {
    throw new Error('Vault pairing requires the Doxxed Crypto Android app');
  }
  const nodeId = await loadOrCreateMobileNodeId();
  await ensureMobileVault(nodeId);
  const result = await pairMobileNode({
    code: code.trim().toUpperCase(),
    nodeId,
    label,
    appVersion: MOBILE_VAULT_APP_VERSION,
  });
  const config: FounderNodeConfig = {
    version: 1,
    apiBaseUrl: API_BASE,
    nodeId: result.nodeId,
    nodeToken: result.nodeToken,
    label,
    pairedAt: new Date().toISOString(),
  };
  await writeMobileNodeConfig(config);
  emit({ paired: true, label, nodeId: result.nodeId, lastError: null });
  startSyncLoop();
}

export async function pullVaultFromRelayNode(
  sourceNodeId: string,
  accessToken: string,
): Promise<void> {
  const config = await readMobileNodeConfig();
  if (!config) throw new Error('Pair this phone first');
  const relay = await fetchVaultRelayForNode(sourceNodeId, accessToken);
  await applyEncryptedVaultToDevice(relay.encryptedVaultBlob, config.nodeToken, config.nodeId);
  await runSyncCycle();
}

export async function unpairMobileVault(): Promise<void> {
  if (intervalId) {
    clearInterval(intervalId);
    intervalId = null;
  }
  await clearMobileNodeConfig();
  emit({ paired: false, label: undefined, nodeId: undefined, lastSyncAt: null, lastError: null });
}

export async function initMobileVaultService(): Promise<void> {
  if (!(await whenCapacitorReady())) return;
  const config = await readMobileNodeConfig();
  if (config) {
    emit({ paired: true, label: config.label, nodeId: config.nodeId });
    startSyncLoop();
  } else {
    const nodeId = await loadOrCreateMobileNodeId();
    await ensureMobileVault(nodeId);
    emit({ paired: false });
  }
}
