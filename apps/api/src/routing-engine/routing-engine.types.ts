/**
 * Routing Engine v2 — types for the 3-layer pipeline.
 * See docs/KERNEL.md §6 for the pipeline and §7 for Execution Profiles.
 */
import type {
  AiRuntimeIntent,
  CapabilityRequirement,
  ExecutionProfile,
} from '../capability-registry/capability-registry.types';

export type RoutingRequest = {
  userId: string;
  workspaceId?: string | null;
  intent: AiRuntimeIntent;
  prompt: string;
  requirements?: CapabilityRequirement[];
  profile?: ExecutionProfile;
  requestId: string;
};

export type RoutingDecision = {
  requestId: string;
  chosenProvider: string;
  chosenModel: string;
  score: number;
  cacheLevel: 'hit' | 'partial' | 'miss';
  cacheKey?: string | null;
  candidates: Array<{ provider: string; model: string; score: number }>;
};

/**
 * Per-profile weights applied during Layer 3 scoring. The four numbers must
 * sum to 1.0 for a profile to be a valid distribution over the four scoring
 * axes (intent / cost / latency / reputation).
 *
 * Phase 4 (Learning Engine) raised the `reputation` weight so that observed
 * retry/edit signals actually move the needle on selection. The intent: when
 * a model's successRate degrades to ~0.85 from repeated retries, a model at
 * ~0.96 should start winning more often even if its raw intent score is
 * slightly lower. Keeping reputation at ~25% on the day-to-day profiles
 * (turbo / balanced) lets the Learning Engine's EMA overpower a 5-10%
 * intent-score gap once enough samples accumulate, while architect /
 * autonomous stay reputation-heavy because they're the profiles where a bad
 * answer is most expensive to recover from.
 */
export const PROFILE_WEIGHTS: Record<
  ExecutionProfile,
  { intent: number; cost: number; latency: number; reputation: number }
> = {
  turbo: { intent: 0.3, cost: 0.25, latency: 0.2, reputation: 0.25 },
  balanced: { intent: 0.4, cost: 0.15, latency: 0.2, reputation: 0.25 },
  architect: { intent: 0.55, cost: 0.05, latency: 0.05, reputation: 0.35 },
  autonomous: { intent: 0.45, cost: 0.1, latency: 0.1, reputation: 0.35 },
};
