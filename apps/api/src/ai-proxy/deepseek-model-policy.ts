import type { AiProxyTier } from '@dcf/utils';
import type { FounderPlanName } from '../founder-os/founder-plan-entitlements.service';

/**
 * Current model identifiers accepted by the DeepSeek platform key used by
 * Founder OS. Keep provider-specific translation at the API boundary so a
 * stale Capability row or cached routing decision cannot send a retired model
 * name upstream.
 */
export const DEEPSEEK_V4_FLASH_MODEL = 'deepseek-v4-flash';
export const DEEPSEEK_V4_PRO_MODEL = 'deepseek-v4-pro';

export type ForcedAliasIntent = 'simple_qa' | 'reasoning' | 'code';

export type SupportedProxyProvider = 'glm' | 'deepseek';

export function forcedIntentForAlias(alias: string): ForcedAliasIntent | null {
  if (alias === 'founder-os-code') return 'code';
  if (alias === 'founder-os-reasoning') return 'reasoning';
  if (alias === 'founder-os-fast') return 'simple_qa';
  return null;
}

export function tierForFounderAlias(alias: string, inferred: AiProxyTier): AiProxyTier {
  if (alias === 'founder-os-auto' || alias === 'founder-os-fast') return 'fast';
  if (alias === 'founder-os-code') return 'code';
  if (alias === 'founder-os-reasoning') return 'reasoning';
  return inferred;
}

export function tierForFounderPlan(
  plan: FounderPlanName,
  alias: string,
  inferred: AiProxyTier,
): { tier: AiProxyTier; policy: 'free_flash_only' | 'managed_auto' } {
  if (plan === 'free') {
    return { tier: 'fast', policy: 'free_flash_only' };
  }
  return { tier: tierForFounderAlias(alias, inferred), policy: 'managed_auto' };
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

/**
 * The OpenAI-compatible Founder gateway currently has upstream adapters for
 * GLM and DeepSeek only. Capability rows for research/execution providers must
 * never leak through to this money-independent chat boundary. A stale shared
 * cache or an older active Capability row is therefore normalized to the
 * supported DeepSeek route instead of being sent as (for example) `kimi-k2`
 * to the DeepSeek endpoint.
 */
export function normalizeProxyRoute(
  provider: string,
  model: string,
  tier: AiProxyTier,
): { providerKey: SupportedProxyProvider; model: string; wasUnsupported: boolean } {
  if (provider === 'glm') {
    return { providerKey: 'glm', model, wasUnsupported: false };
  }
  if (provider === 'deepseek') {
    return {
      providerKey: 'deepseek',
      model: normalizeProviderModel('deepseek', model, tier),
      wasUnsupported: false,
    };
  }
  return {
    providerKey: 'deepseek',
    model: normalizeProviderModel('deepseek', model, tier),
    wasUnsupported: true,
  };
}

/** Founder IDE aliases are a stable public contract, not raw provider names. */
export function normalizeFounderAliasRoute(
  alias: string,
  provider: string,
  model: string,
  tier: AiProxyTier,
): { providerKey: SupportedProxyProvider; model: string; wasUnsupported: boolean } {
  if (alias.startsWith('founder-os-')) {
    return {
      providerKey: 'deepseek',
      model: normalizeProviderModel('deepseek', model, tier),
      wasUnsupported: provider !== 'deepseek',
    };
  }
  return normalizeProxyRoute(provider, model, tier);
}
