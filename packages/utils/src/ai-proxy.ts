/**
 * Founder OS AI Proxy — shared types and helpers.
 *
 * The proxy exposes an OpenAI-compatible endpoint that any IDE can call.
 * These types are shared between the API, Founder Node, and the web
 * dashboard so the model aliases and routing tiers stay in sync.
 */

export const FOUNDER_OS_AUTO_MODEL = 'founder-os-auto';

export const FOUNDER_OS_MODEL_ALIASES = [
  FOUNDER_OS_AUTO_MODEL,
  'founder-os-code',
  'founder-os-reasoning',
  'founder-os-fast',
] as const;

export type FounderOsModelAlias = (typeof FOUNDER_OS_MODEL_ALIASES)[number];

export type AiProxyTier = 'fast' | 'reasoning' | 'code';

export type AiProxyRouteDecision = {
  providerKey: string;
  model: string;
  tier: AiProxyTier;
  intent: string;
};

/**
 * Per-tier DDollar cost. These are tiny by design — the goal is to make
 * metering visible in the dashboard, not to gate usage. The real caps live
 * in SpendingEngine.enforceTierCap (parasite vs builder daily token caps).
 */
export const AI_PROXY_DDOLLAR_COST: Record<AiProxyTier, number> = {
  fast: 1,
  reasoning: 3,
  code: 2,
};

/** Helper for the web dashboard: human-readable tier label. */
export function describeProxyTier(tier: AiProxyTier): string {
  switch (tier) {
    case 'code':
      return 'Coding (GLM 5.2)';
    case 'reasoning':
      return 'Reasoning (DeepSeek)';
    case 'fast':
      return 'Fast (cheap model)';
  }
}

/** Helper for the dashboard: which alias to set per use case. */
export function recommendAliasForUseCase(useCase: 'code' | 'reasoning' | 'auto'): string {
  if (useCase === 'code') return 'founder-os-code';
  if (useCase === 'reasoning') return 'founder-os-reasoning';
  return FOUNDER_OS_AUTO_MODEL;
}
