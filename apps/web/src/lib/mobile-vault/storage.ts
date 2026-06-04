'use client';

import type { FounderNodeConfig } from '@dcf/founder-vault';

const CONFIG_KEY = 'dcf_mobile_founder_node_config';
const NODE_ID_KEY = 'dcf_mobile_vault_node_id';

export async function readMobileNodeConfig(): Promise<FounderNodeConfig | null> {
  const { Preferences } = await import('@capacitor/preferences');
  const { value } = await Preferences.get({ key: CONFIG_KEY });
  if (!value) return null;
  try {
    return JSON.parse(value) as FounderNodeConfig;
  } catch {
    return null;
  }
}

export async function writeMobileNodeConfig(config: FounderNodeConfig): Promise<void> {
  const { Preferences } = await import('@capacitor/preferences');
  await Preferences.set({ key: CONFIG_KEY, value: JSON.stringify(config) });
}

export async function clearMobileNodeConfig(): Promise<void> {
  const { Preferences } = await import('@capacitor/preferences');
  await Preferences.remove({ key: CONFIG_KEY });
}

export async function loadOrCreateMobileNodeId(): Promise<string> {
  const { Preferences } = await import('@capacitor/preferences');
  const existing = await Preferences.get({ key: NODE_ID_KEY });
  if (existing.value) return existing.value;
  const nodeId = `android_${crypto.randomUUID().replace(/-/g, '').slice(0, 16)}`;
  await Preferences.set({ key: NODE_ID_KEY, value: nodeId });
  return nodeId;
}
