/**
 * Model alias definitions for the Founder OS chat provider.
 *
 * These mirror `apps/api/src/ai-proxy/ai-proxy.constants.ts` and
 * `packages/utils/src/ai-proxy.ts` so the IDE's model picker maps 1:1 to the
 * gateway's routing tiers. The gateway's `decideRoute` ultimately picks the
 * concrete provider+model; the alias here is just the user-facing handle.
 */

export const FOUNDER_OS_VENDOR = 'founder-os' as const;

export type FounderOsModelAliasId =
  | 'founder-os-auto'
  | 'founder-os-code'
  | 'founder-os-reasoning'
  | 'founder-os-fast';

export interface FounderOsModelAlias {
  id: FounderOsModelAliasId;
  name: string;
  family: string;
  tooltip: string;
  detail: string;
  /** Execution-profile header value sent as `X-Execution-Profile` to the gateway. */
  executionProfile: 'auto' | 'turbo' | 'architect';
  isDefault: boolean;
  maxInputTokens: number;
  maxOutputTokens: number;
}

export const FOUNDER_OS_MODELS: readonly FounderOsModelAlias[] = [
  {
    id: 'founder-os-auto',
    name: 'Founder OS Auto',
    family: 'founder-os',
    tooltip: 'Balanced — let the Routing Engine pick the right provider/model.',
    detail: 'Autonomous routing. The gateway decides between Turbo / Balanced / Architect based on the prompt.',
    executionProfile: 'auto',
    isDefault: true,
    maxInputTokens: 64_000,
    maxOutputTokens: 8_192,
  },
  {
    id: 'founder-os-code',
    name: 'Founder OS Code',
    family: 'founder-os',
    tooltip: 'Turbo — speed-optimized coding (GLM 5.2).',
    detail: 'Coding profile. Routes to the Turbo tier for fast, code-focused responses.',
    executionProfile: 'turbo',
    isDefault: false,
    maxInputTokens: 64_000,
    maxOutputTokens: 8_192,
  },
  {
    id: 'founder-os-reasoning',
    name: 'Founder OS Reasoning',
    family: 'founder-os',
    tooltip: 'Architect — deep reasoning (DeepSeek).',
    detail: 'Reasoning profile. Routes to the Architect tier for deep, deliberate answers.',
    executionProfile: 'architect',
    isDefault: false,
    maxInputTokens: 64_000,
    maxOutputTokens: 8_192,
  },
  {
    id: 'founder-os-fast',
    name: 'Founder OS Fast',
    family: 'founder-os',
    tooltip: 'Turbo — quick Q&A, cheapest DDollar cost.',
    detail: 'Fast profile. Routes to the Turbo tier for quick Q&A and low DDollar spend.',
    executionProfile: 'turbo',
    isDefault: false,
    maxInputTokens: 32_000,
    maxOutputTokens: 4_096,
  },
];

export const DEFAULT_FOUNDER_OS_MODEL_ID: FounderOsModelAliasId = 'founder-os-auto';

export function findModelAlias(id: string): FounderOsModelAlias | undefined {
  return FOUNDER_OS_MODELS.find((m) => m.id === id);
}
