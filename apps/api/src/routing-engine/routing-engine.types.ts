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
 */
export const PROFILE_WEIGHTS: Record<
  ExecutionProfile,
  { intent: number; cost: number; latency: number; reputation: number }
> = {
  turbo: { intent: 0.3, cost: 0.3, latency: 0.3, reputation: 0.1 },
  balanced: { intent: 0.4, cost: 0.2, latency: 0.2, reputation: 0.2 },
  architect: { intent: 0.6, cost: 0.05, latency: 0.05, reputation: 0.3 },
  autonomous: { intent: 0.5, cost: 0.1, latency: 0.1, reputation: 0.3 },
};
