import type { AiProxyTier } from '@dcf/utils';

/**
 * Current model identifiers accepted by the DeepSeek platform key used by
 * Founder OS. Keep provider-specific translation at the API boundary so a
 * stale Capability row or cached routing decision cannot send a retired model
 * name upstream.
 */
export const DEEPSEEK_V4_FLASH_MODEL = 'deepseek-v4-flash';
export const DEEPSEEK_V4_PRO_MODEL = 'deepseek-v4-pro';

export type ForcedAliasIntent = 'simple_qa' | 'reasoning' | 'code';

export function forcedIntentForAlias(alias: string): ForcedAliasIntent | null {
  if (alias === 'founder-os-code') return 'code';
  if (alias === 'founder-os-reasoning') return 'reasoning';
  if (alias === 'founder-os-fast') return 'simple_qa';
  return null;
}

export function normalizeProviderModel(
  provider: string,
  model: string,
  tier: AiProxyTier,
): string {
  if (provider !== 'deepseek') return model;
  // The Founder alias contract wins over a stale/cached routing decision.
  // In particular, founder-os-fast must never preserve a cached v4-pro model.
  return tier === 'fast' ? DEEPSEEK_V4_FLASH_MODEL : DEEPSEEK_V4_PRO_MODEL;
}
