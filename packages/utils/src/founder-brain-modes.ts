/** User-facing Founder Brain modes — never expose vendor names in default UI. */
export const FOUNDER_BRAIN_MODES = ['automatic', 'fast', 'balanced', 'deep'] as const;

export type FounderBrainMode = (typeof FOUNDER_BRAIN_MODES)[number];

export const FOUNDER_BRAIN_MODE_LABELS: Record<FounderBrainMode, string> = {
  automatic: 'Automatic',
  fast: 'Fast',
  balanced: 'Balanced',
  deep: 'Deep Thinking',
};

export const FOUNDER_BRAIN_MODE_HINTS: Record<FounderBrainMode, string> = {
  automatic: 'Routes automatically by task complexity',
  fast: 'Quick answers and drafts',
  balanced: 'Default quality and cost balance',
  deep: 'Strategy, analysis, and complex reasoning',
};

export function isFounderBrainMode(value: string): value is FounderBrainMode {
  return (FOUNDER_BRAIN_MODES as readonly string[]).includes(value);
}

export function parseFounderBrainMode(value: string | null | undefined): FounderBrainMode {
  if (value && isFounderBrainMode(value)) return value;
  return 'automatic';
}
