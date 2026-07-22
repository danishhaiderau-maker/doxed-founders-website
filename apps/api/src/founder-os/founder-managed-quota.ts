import { measureFounderTokenUsage } from '@dcf/utils';

export type ManagedReservationCharge = {
  status: 'RESERVED' | 'RECONCILED' | 'RELEASED' | 'UNCERTAIN';
  reservedWeightedUnits: number;
  actualWeightedUnits: number | null;
};

export type ProviderTokenUsage = {
  prompt_tokens?: unknown;
  completion_tokens?: unknown;
  prompt_cache_hit_tokens?: unknown;
  prompt_cache_miss_tokens?: unknown;
  completion_tokens_details?: { reasoning_tokens?: unknown } | null;
};

export type ReconciledManagedUsage = {
  inputTokens: number;
  cachedInputTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  weightedUnits: number;
  providerMeasured: boolean;
};

function tokenCount(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.floor(value)
    : 0;
}

export function chargeForManagedReservation(row: ManagedReservationCharge): number {
  if (row.status === 'RELEASED') return 0;
  if (row.status === 'RECONCILED') {
    return Math.max(0, row.actualWeightedUnits ?? row.reservedWeightedUnits);
  }
  return Math.max(0, row.reservedWeightedUnits);
}

export function reconcileProviderUsage(
  usage: ProviderTokenUsage | null | undefined,
  reservedWeightedUnits: number,
): ReconciledManagedUsage {
  const prompt = tokenCount(usage?.prompt_tokens);
  const cacheHit = Math.min(prompt, tokenCount(usage?.prompt_cache_hit_tokens));
  const cacheMissReported = tokenCount(usage?.prompt_cache_miss_tokens);
  const inputTokens = cacheMissReported > 0
    ? Math.min(prompt, cacheMissReported)
    : Math.max(0, prompt - cacheHit);
  const completion = tokenCount(usage?.completion_tokens);
  const reasoningTokens = Math.min(
    completion,
    tokenCount(usage?.completion_tokens_details?.reasoning_tokens),
  );
  const outputTokens = Math.max(0, completion - reasoningTokens);
  const providerMeasured = prompt > 0 || completion > 0;
  const measurement = measureFounderTokenUsage({
    inputTokens,
    cachedInputTokens: cacheHit,
    outputTokens,
    reasoningTokens,
    billingSource: 'platform_managed',
  });

  return {
    inputTokens,
    cachedInputTokens: cacheHit,
    outputTokens,
    reasoningTokens,
    weightedUnits: providerMeasured
      ? Math.ceil(measurement.weightedUnits)
      : Math.max(0, Math.ceil(reservedWeightedUnits)),
    providerMeasured,
  };
}
