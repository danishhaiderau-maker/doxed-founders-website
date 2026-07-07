/**
 * Capability Registry — shared types for the kernel's Capability service.
 * See docs/KERNEL.md §8 for the data backbone spec.
 */

export type AiRuntimeIntent = 'code' | 'reasoning' | 'simple_qa' | 'agent' | 'vision';

export type ExecutionProfile = 'turbo' | 'balanced' | 'architect' | 'autonomous';

export type CapabilityRequirement = {
  toolUse?: boolean;
  jsonMode?: boolean;
  largeContext?: boolean;
  vision?: boolean;
};

export type CapabilityScored = {
  provider: string;
  model: string;
  score: number; // 0..1, higher is better
  intentScore: number;
  costScore: number;
  latencyScore: number;
  reputation: number;
};

/**
 * Local row type mirroring the Prisma `Capability` model.
 *
 * NOTE: We declare this locally because `@prisma/client` has not yet been
 * regenerated to include the new `Capability` model. The parent agent will
 * run `prisma generate` after this lands; once it does, callers may switch
 * to importing `Capability` from `@prisma/client` directly.
 *
 * Field names match the Prisma schema exactly so the `any`-cast Prisma
 * accessors return data that is structurally compatible.
 */
export type CapabilityRow = {
  id: string;
  provider: string;
  model: string;
  displayName: string;
  isActive: boolean;

  toolUse: boolean;
  jsonMode: boolean;
  largeContext: boolean;
  largeContextWindow: number | null;
  vision: boolean;
  streaming: boolean;

  inputCostPer1M: number;
  outputCostPer1M: number;
  latencyP50Ms: number;

  codeScore: number;
  reasoningScore: number;
  simpleQaScore: number;
  agentScore: number;
  visionScore: number;

  successRate: number;
  retryRate: number;
  sampleCount: number;

  createdAt: Date;
  updatedAt: Date;
};

/**
 * Shape used by the seed script (scripts/seed-capabilities.ts). The seed
 * script reads these fields by name, so the property names here MUST match
 * the schema field names exactly.
 */
export type CapabilitySeed = {
  provider: string;
  model: string;
  displayName: string;
  isActive?: boolean;
  toolUse?: boolean;
  jsonMode?: boolean;
  largeContext?: boolean;
  largeContextWindow?: number | null;
  vision?: boolean;
  streaming?: boolean;
  inputCostPer1M: number;
  outputCostPer1M: number;
  latencyP50Ms: number;
  codeScore?: number;
  reasoningScore?: number;
  simpleQaScore?: number;
  agentScore?: number;
  visionScore?: number;
};
