export type ClientPromptEfficiencyEstimate = {
  measurement: 'estimated';
  baselineTokens: number;
  sentTokens: number;
  avoidedTokens: number;
  savingsPercent: number;
  compactedToolResults: number;
  removedStaleCoordinationBlocks: number;
  techniques: string[];
};

function boundedInteger(value: unknown, max = 10_000_000): number | null {
  return typeof value === 'number'
    && Number.isFinite(value)
    && value >= 0
    && value <= max
    ? Math.round(value)
    : null;
}

export function clientIncludedFounderMemory(metadata: unknown): boolean {
  return Boolean(
    metadata
    && typeof metadata === 'object'
    && !Array.isArray(metadata)
    && (metadata as Record<string, unknown>).founder_memory_included === true,
  );
}

export function parseClientPromptEfficiency(
  metadata: unknown,
): ClientPromptEfficiencyEstimate | null {
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) return null;
  const raw = (metadata as Record<string, unknown>).prompt_efficiency;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const input = raw as Record<string, unknown>;
  if (input.measurement !== 'estimated') return null;
  const baselineTokens = boundedInteger(input.baselineTokens);
  const sentTokens = boundedInteger(input.sentTokens);
  const avoidedTokens = boundedInteger(input.avoidedTokens);
  const compactedToolResults = boundedInteger(input.compactedToolResults, 1_000);
  const removedStaleCoordinationBlocks = boundedInteger(
    input.removedStaleCoordinationBlocks,
    1_000,
  );
  if (
    baselineTokens === null
    || sentTokens === null
    || avoidedTokens === null
    || compactedToolResults === null
    || removedStaleCoordinationBlocks === null
    || sentTokens > baselineTokens
    || avoidedTokens !== baselineTokens - sentTokens
  ) return null;
  const techniques = Array.isArray(input.techniques)
    ? input.techniques
      .filter((item): item is string => typeof item === 'string')
      .map((item) => item.trim().slice(0, 48))
      .filter(Boolean)
      .slice(0, 8)
    : [];
  return {
    measurement: 'estimated',
    baselineTokens,
    sentTokens,
    avoidedTokens,
    savingsPercent: baselineTokens > 0
      ? Math.round((avoidedTokens / baselineTokens) * 10_000) / 100
      : 0,
    compactedToolResults,
    removedStaleCoordinationBlocks,
    techniques,
  };
}
