/**
 * Flight Recorder — types for the kernel's Routing Decision logger.
 * See docs/KERNEL.md §9.
 */

export type RecordInput = {
  requestId: string;
  userId: string;
  workspaceId?: string | null;
  intent: string;
  profile: string;
  candidates: Array<{ provider: string; model: string; score: number }>;
  chosenProvider: string;
  chosenModel: string;
  cacheLevel: 'hit' | 'partial' | 'miss';
  cacheKey?: string | null;
  promptHash: string;
  tokenCountPrompt?: number | null;
  tokenCountCompletion?: number | null;
  latencyMs?: number | null;
  costUsd?: number | null;
};

export type OutcomeUpdate = {
  accepted?: boolean;
  retried?: boolean;
  edited?: boolean;
  rating?: number;
};

/**
 * Local row type mirroring the Prisma `RoutingDecision` model.
 *
 * NOTE: `@prisma/client` has not yet been regenerated to include the
 * `RoutingDecision` model. The parent agent will run `prisma generate`
 * after this lands; the `any` casts in the service are intentional and
 * match this shape.
 */
export type RoutingDecisionRow = {
  id: string;
  requestId: string;
  userId: string;
  workspaceId: string | null;

  intent: string;
  profile: string;

  candidates: Array<{ provider: string; model: string; score: number }>;
  chosenProvider: string;
  chosenModel: string;

  cacheLevel: string;
  cacheKey: string | null;

  promptHash: string;
  tokenCountPrompt: number | null;
  tokenCountCompletion: number | null;

  latencyMs: number | null;
  costUsd: number | null;

  accepted: boolean | null;
  retried: boolean | null;
  edited: boolean | null;
  rating: number | null;

  createdAt: Date;
};
