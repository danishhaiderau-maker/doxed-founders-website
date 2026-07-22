/**
 * Provider-neutral quota units for Founder-managed inference.
 *
 * These units are a fairness budget, not a provider invoice and not a public
 * savings claim. Actual USD cost is recorded separately from provider usage.
 */
export const FOUNDER_MANAGED_TOKEN_WEIGHTS = {
  input: 1,
  cachedInput: 0.25,
  output: 3,
  reasoning: 3,
} as const;

export const FOUNDER_FREE_WEEKLY_WEIGHTED_UNITS = 200_000;
export const FOUNDER_FREE_WINDOW_DAYS = 7;
export const FOUNDER_MANAGED_RESERVATION_TTL_MINUTES = 10;
export const FOUNDER_DEFAULT_MAX_OUTPUT_TOKENS = 4_096;

export type FounderUsageBillingSource =
  | 'platform_managed'
  | 'personal_byok'
  | 'local';

export interface FounderTokenUsage {
  /** Uncached prompt tokens. */
  inputTokens: number;
  /** Prompt tokens served from a provider or Founder context cache. */
  cachedInputTokens?: number;
  /** Visible completion tokens, excluding separately reported reasoning. */
  outputTokens: number;
  /** Hidden chain-of-thought/reasoning tokens when the provider reports them. */
  reasoningTokens?: number;
  billingSource: FounderUsageBillingSource;
}

export interface FounderUsageMeasurement {
  weightedUnits: number;
  rawTokens: number;
  managed: boolean;
  weightsVersion: 'founder-wtu-v1';
}

export interface FounderQuotaWindow {
  startsAt: Date;
  resetsAt: Date;
}

function nonNegativeInteger(value: number | undefined, field: string): number {
  const normalized = value ?? 0;
  if (!Number.isFinite(normalized) || normalized < 0 || !Number.isInteger(normalized)) {
    throw new Error(`${field} must be a non-negative integer`);
  }
  return normalized;
}

/**
 * Calculate Founder weighted token units (WTU).
 *
 * Personal keys and local inference remain visible in raw telemetry but do
 * not consume the platform-managed allowance.
 */
export function measureFounderTokenUsage(
  usage: FounderTokenUsage,
): FounderUsageMeasurement {
  const input = nonNegativeInteger(usage.inputTokens, 'inputTokens');
  const cachedInput = nonNegativeInteger(
    usage.cachedInputTokens,
    'cachedInputTokens',
  );
  const output = nonNegativeInteger(usage.outputTokens, 'outputTokens');
  const reasoning = nonNegativeInteger(
    usage.reasoningTokens,
    'reasoningTokens',
  );
  const rawTokens = input + cachedInput + output + reasoning;
  const managed = usage.billingSource === 'platform_managed';
  const weightedUnits = managed
    ? input * FOUNDER_MANAGED_TOKEN_WEIGHTS.input +
      cachedInput * FOUNDER_MANAGED_TOKEN_WEIGHTS.cachedInput +
      output * FOUNDER_MANAGED_TOKEN_WEIGHTS.output +
      reasoning * FOUNDER_MANAGED_TOKEN_WEIGHTS.reasoning
    : 0;

  return {
    weightedUnits,
    rawTokens,
    managed,
    weightsVersion: 'founder-wtu-v1',
  };
}

export function founderQuotaPercentUsed(
  usedWeightedUnits: number,
  capWeightedUnits: number,
): number {
  if (!Number.isFinite(usedWeightedUnits) || usedWeightedUnits < 0) {
    throw new Error('usedWeightedUnits must be a non-negative number');
  }
  if (!Number.isFinite(capWeightedUnits) || capWeightedUnits <= 0) {
    throw new Error('capWeightedUnits must be greater than zero');
  }
  return Math.min(100, (usedWeightedUnits / capWeightedUnits) * 100);
}

/** Return the recurring allowance window anchored to the account creation time. */
export function founderQuotaWindow(
  registeredAt: Date,
  now = new Date(),
  windowDays = FOUNDER_FREE_WINDOW_DAYS,
): FounderQuotaWindow {
  if (!Number.isFinite(registeredAt.getTime()) || !Number.isFinite(now.getTime())) {
    throw new Error('registeredAt and now must be valid dates');
  }
  if (!Number.isInteger(windowDays) || windowDays <= 0) {
    throw new Error('windowDays must be a positive integer');
  }
  const windowMs = windowDays * 24 * 60 * 60 * 1_000;
  const elapsed = Math.max(0, now.getTime() - registeredAt.getTime());
  const windowIndex = Math.floor(elapsed / windowMs);
  const startsAt = new Date(registeredAt.getTime() + windowIndex * windowMs);
  return { startsAt, resetsAt: new Date(startsAt.getTime() + windowMs) };
}

/**
 * Reserve conservatively: prompt tokens are treated as uncached and the full
 * requested output budget is charged at the output weight. Reconciliation
 * returns unused capacity after the provider reports real usage.
 */
export function estimateFounderManagedReservation(input: {
  inputTokens: number;
  maxOutputTokens?: number;
}): number {
  const inputTokens = nonNegativeInteger(input.inputTokens, 'inputTokens');
  const maxOutputTokens = nonNegativeInteger(
    input.maxOutputTokens ?? FOUNDER_DEFAULT_MAX_OUTPUT_TOKENS,
    'maxOutputTokens',
  );
  return Math.ceil(
    inputTokens * FOUNDER_MANAGED_TOKEN_WEIGHTS.input +
      maxOutputTokens * FOUNDER_MANAGED_TOKEN_WEIGHTS.output,
  );
}
