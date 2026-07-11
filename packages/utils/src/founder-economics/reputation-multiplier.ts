/**
 * Reputation Multiplier — Trust × Longevity × Verification.
 *
 * This is the multiplier v2+ distribution models apply to raw DDollar before
 * normalizing into epoch shares. It is NOT a new scoring system — it composes
 * existing platform signals:
 *
 *   - Trust:           reuse `computeTrustWeight` from `trust-weight.ts`
 *   - Longevity:       account age in days → 0–2 bonus
 *   - Verification:    Proof of Success verified milestones → 0–3 bonus
 *
 * The result is a multiplier in [1, 16] (1 = baseline, 16 = max). It feeds
 * directly into the v2 DistributionModel as `FounderShare.reputationMultiplier`.
 */

import { computeTrustWeight, MAX_TRUST_WEIGHT } from '../trust-weight';

export type ReputationMultiplierInput = {
  verifiedAccount?: boolean;
  contributorLevel?: number;
  reputationPoints?: number;
  accountAgeDays?: number;
  /** Count of verified ProofOfSuccess milestones (e.g. ARR, paying users). */
  verifiedMilestoneCount?: number;
  /** Builder score 0–100 — small bump so shipping founders climb faster. */
  builderScore?: number;
};

export const MAX_REPUTATION_MULTIPLIER = MAX_TRUST_WEIGHT + 6; // 16

/** Verification bonus — each verified milestone adds 1, capped at 3. */
export function verificationBonus(verifiedMilestoneCount: number): number {
  return Math.min(3, Math.max(0, Math.floor(verifiedMilestoneCount)));
}

/** Builder-score bump — 0–1 extra point for builders above 70. */
export function builderScoreBonus(builderScore: number): number {
  if (builderScore >= 90) return 1;
  return 0;
}

/**
 * Compute the reputation multiplier.
 *
 *   multiplier = trustWeight + verificationBonus + builderScoreBonus
 *
 * Capped at MAX_REPUTATION_MULTIPLIER (16). Floor is 1 — every founder gets
 * at least the baseline multiplier so v2 never zeroes out a raw-DDollar holder.
 */
export function computeReputationMultiplier(
  input: ReputationMultiplierInput,
): number {
  const trust = computeTrustWeight({
    verifiedAccount: input.verifiedAccount,
    contributorLevel: input.contributorLevel,
    reputationPoints: input.reputationPoints,
    accountAgeDays: input.accountAgeDays,
  });
  const verify = verificationBonus(input.verifiedMilestoneCount ?? 0);
  const builder = builderScoreBonus(input.builderScore ?? 0);
  const multiplier = trust + verify + builder;
  return Math.max(1, Math.min(MAX_REPUTATION_MULTIPLIER, multiplier));
}

export function reputationMultiplierLabel(multiplier: number): string {
  if (multiplier >= 12) return 'Proven founder';
  if (multiplier >= 8) return 'Trusted builder';
  if (multiplier >= 4) return 'Verified builder';
  if (multiplier >= 2) return 'Active builder';
  return 'Community member';
}
