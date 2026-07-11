/**
 * Negative DDollar — penalties + governance.
 *
 * Just as Economic activity earns DDollar, anti-economic behaviour loses it.
 * This protects the distribution model from gaming. Negative grants subtract
 * from `User.reputationPoints` (the DDollar ledger) and write a
 * `FounderTreasuryLedgerEntry` row with a negative amount for audit.
 *
 * All penalties require a governance-validated reason. The settlement job
 * refuses to apply a negative grant that doesn't match a spec here.
 */

export type NegativeDDollarReason =
  | 'SPAM_ABUSE'
  | 'PLAGIARISM'
  | 'FAKE_PROOF'
  | 'MILESTONE_REVERTED'
  | 'HARASSMENT'
  | 'GOVERNANCE_VIOLATION';

export type NegativeDDollarSpec = {
  reason: NegativeDDollarReason;
  label: string;
  description: string;
  /** Penalty range — clamp the actual deduction into [min, max]. */
  min: number;
  max: number;
  /** Whether a governance vote is required before applying. */
  requiresGovernanceVote: boolean;
};

export const NEGATIVE_DDOLLAR_SPECS: Record<NegativeDDollarReason, NegativeDDollarSpec> = {
  SPAM_ABUSE: {
    reason: 'SPAM_ABUSE',
    label: 'Spam / abuse',
    description: 'Flooding the feed or knowledge graph with low-quality entries.',
    min: -1_000,
    max: -100,
    requiresGovernanceVote: false,
  },
  PLAGIARISM: {
    reason: 'PLAGIARISM',
    label: 'Plagiarism',
    description: 'Claimed another founder\'s knowledge node as original.',
    min: -10_000,
    max: -2_000,
    requiresGovernanceVote: true,
  },
  FAKE_PROOF: {
    reason: 'FAKE_PROOF',
    label: 'Fake proof of success',
    description: 'Submitted a forged Stripe / GitHub / Vercel milestone.',
    min: -50_000,
    max: -10_000,
    requiresGovernanceVote: true,
  },
  MILESTONE_REVERTED: {
    reason: 'MILESTONE_REVERTED',
    label: 'Milestone reverted',
    description: 'A previously verified milestone was found to be reverted (e.g. refunds).',
    min: -100_000,
    max: -10_000,
    requiresGovernanceVote: true,
  },
  HARASSMENT: {
    reason: 'HARASSMENT',
    label: 'Harassment',
    description: 'Targeted harassment of other founders or community members.',
    min: -10_000,
    max: -1_000,
    requiresGovernanceVote: false,
  },
  GOVERNANCE_VIOLATION: {
    reason: 'GOVERNANCE_VIOLATION',
    label: 'Governance violation',
    description: 'Violated a distribution model rule after governance warning.',
    min: -25_000,
    max: -5_000,
    requiresGovernanceVote: true,
  },
};

/** Clamp a negative grant into the spec's [min, max] range (both negative). */
export function clampNegativeDdollar(spec: NegativeDDollarSpec, amount: number): number {
  // min and max are both negative; min is more negative.
  const clampedLow = Math.max(spec.min, amount);
  return Math.min(spec.max, clampedLow);
}

/** Does applying this penalty require a governance vote first? */
export function requiresGovernanceVote(reason: NegativeDDollarReason): boolean {
  return NEGATIVE_DDOLLAR_SPECS[reason]?.requiresGovernanceVote ?? false;
}
