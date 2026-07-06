import type { AiProvider } from '@prisma/client';
import type { FounderBrainTask } from '@dcf/utils';

/** Stable section slugs — align with `ai-routing.constants.ts`. */
export type AiRuntimeSection =
  | 'copilot'
  | 'quick_build'
  | 'founder_draft'
  | 'share_paraphrase'
  | 'wall_summarizer'
  | 'platform_brain';

export type AiRuntimeIntent =
  | 'simple_qa'
  | 'reasoning'
  | 'code'
  | 'social_draft'
  | 'summarize'
  | 'unknown';

export type AiRuntimeRequest = {
  userId: string;
  system: string;
  userPrompt: string;
  section: AiRuntimeSection;
  founderBrainTask?: FounderBrainTask;
  projectId?: string | null;
  /** Skip cache read/write (forced provider, BYOK overrides, streaming). */
  skipCache?: boolean;
};

/** Cache layer id — aligns with FOUNDER-BRAIN-AI-OS-SPEC § multi-level cache. */
export type AiRuntimeCacheLevel =
  | 'L0_tool'
  | 'L2_prompt_hash'
  | 'L5_semantic'
  | 'miss';

export type AiRuntimeResponse = {
  ok: boolean;
  text?: string;
  provider?: AiProvider | string;
  model?: string;
  intent?: AiRuntimeIntent;
  cacheHit?: boolean;
  cacheKey?: string;
  cacheLevel?: AiRuntimeCacheLevel;
  localToolUsed?: boolean;
  confidenceScore?: number;
  promptTokens?: number;
  completionTokens?: number;
};

export type ModelRoute = {
  intent: AiRuntimeIntent;
  providerKey: string;
  model: string;
  tier: 'fast' | 'reasoning' | 'code';
};
