import type { ProviderTokenUsage } from '../founder-os/founder-managed-quota';
import {
  DEEPSEEK_V4_FLASH_MODEL,
  DEEPSEEK_V4_PRO_MODEL,
} from './deepseek-model-policy';

export const DEEPSEEK_PRICE_VERSION = 'deepseek-usd-2026-07-22';

export const DEEPSEEK_PRICES_PER_MILLION = {
  [DEEPSEEK_V4_FLASH_MODEL]: {
    cachedInput: 0.0028,
    uncachedInput: 0.14,
    output: 0.28,
  },
  [DEEPSEEK_V4_PRO_MODEL]: {
    cachedInput: 0.003625,
    uncachedInput: 0.435,
    output: 0.87,
  },
} as const;

function tokens(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.floor(value)
    : 0;
}

export function calculateDeepseekCostUsd(
  model: string,
  usage: ProviderTokenUsage | null | undefined,
): {
  costUsd: number;
  priceVersion: typeof DEEPSEEK_PRICE_VERSION;
  cachedInputTokens: number;
  uncachedInputTokens: number;
  outputTokens: number;
} | null {
  const prices = DEEPSEEK_PRICES_PER_MILLION[
    model as keyof typeof DEEPSEEK_PRICES_PER_MILLION
  ];
  if (!prices || !usage) return null;
  const promptTokens = tokens(usage.prompt_tokens);
  const cachedInputTokens = Math.min(
    promptTokens,
    tokens(usage.prompt_cache_hit_tokens),
  );
  const reportedMiss = tokens(usage.prompt_cache_miss_tokens);
  const uncachedInputTokens = reportedMiss > 0
    ? Math.min(promptTokens, reportedMiss)
    : Math.max(0, promptTokens - cachedInputTokens);
  const outputTokens = tokens(usage.completion_tokens);
  const costUsd =
    (cachedInputTokens / 1_000_000) * prices.cachedInput +
    (uncachedInputTokens / 1_000_000) * prices.uncachedInput +
    (outputTokens / 1_000_000) * prices.output;
  return {
    costUsd,
    priceVersion: DEEPSEEK_PRICE_VERSION,
    cachedInputTokens,
    uncachedInputTokens,
    outputTokens,
  };
}
