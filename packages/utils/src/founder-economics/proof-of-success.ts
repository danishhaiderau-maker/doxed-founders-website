/**
 * Proof of Success — verifier interface for real-world company milestones.
 *
 * The DDollar scoring rules award 10,000–100,000 DDollar for verified company
 * milestones (ARR, paying users, app store downloads). That grant is too large
 * to take on faith — it has to be backed by an external proof.
 *
 * This module defines the verifier interface and a registry of known
 * verifiers. Each verifier implementation lives in the backend service
 * (`proof-of-success.service.ts`) and talks to the real API (Stripe, GitHub,
 * Vercel, App Store Connect, etc.). Here we declare the contract and the
 * common result types so the utils layer stays free of API SDK dependencies.
 */

export type ProofType =
  | 'STRIPE_ARR'
  | 'STRIPE_PAYING_USERS'
  | 'GITHUB_REPO_STARS'
  | 'GITHUB_COMMITS'
  | 'VERCEL_DEPLOYMENTS'
  | 'APP_STORE_DOWNLOADS'
  | 'GOOGLE_ANALYTICS_USERS'
  | 'MANUAL_AUDIT';

export type ProofOfSuccessRecord = {
  id: string;
  userId: string;
  proofType: ProofType;
  /** External id (Stripe account, GitHub repo, Vercel project, ...). */
  externalId: string;
  /** Verified raw metric extracted from the external API. */
  verifiedMetric: number;
  /** Human label — e.g. "ARR (USD)", "Paying users", "Monthly active users". */
  metricLabel: string;
  /** Multiplier applied to the base DDollar grant for this milestone (1.0 = base). */
  multiplier: number;
  verifiedAt: string;
  /** Raw verified data blob (serialised API response) — for audit. */
  verifiedData: unknown;
};

export type ProofOfSuccessResult = {
  verified: boolean;
  proofType: ProofType;
  externalId: string;
  verifiedMetric: number;
  metricLabel: string;
  /** Suggested DDollar grant based on the milestone tier. */
  suggestedDdollarGrant: number;
  /** Raw payload from the external API — kept for audit. */
  rawPayload?: unknown;
  /** Reason the proof failed (if not verified). */
  failureReason?: string;
};

export type ProofOfSuccessVerifier = {
  readonly proofType: ProofType;
  readonly label: string;
  /**
   * Verify a single external id. Implementations should call the real API,
   * extract the canonical metric, and return a suggested DDollar grant
   * based on the milestone tier table below.
   */
  verify(externalId: string, credentials?: Record<string, string>): Promise<ProofOfSuccessResult>;
};

/**
 * Milestone tiers — the suggested DDollar grant for a verified metric value.
 * The settlement job applies the spec's [min, max] clamp before granting.
 */
export const MILESTONE_TIERS: {
  proofType: ProofType;
  metricLabel: string;
  tiers: { minMetric: number; ddollar: number; multiplier: number }[];
}[] = [
  {
    proofType: 'STRIPE_ARR',
    metricLabel: 'ARR (USD)',
    tiers: [
      { minMetric: 1_000, ddollar: 10_000, multiplier: 1.0 },
      { minMetric: 10_000, ddollar: 25_000, multiplier: 1.25 },
      { minMetric: 100_000, ddollar: 50_000, multiplier: 1.5 },
      { minMetric: 1_000_000, ddollar: 100_000, multiplier: 2.0 },
    ],
  },
  {
    proofType: 'STRIPE_PAYING_USERS',
    metricLabel: 'Paying users',
    tiers: [
      { minMetric: 10, ddollar: 10_000, multiplier: 1.0 },
      { minMetric: 100, ddollar: 25_000, multiplier: 1.25 },
      { minMetric: 1_000, ddollar: 50_000, multiplier: 1.5 },
      { minMetric: 10_000, ddollar: 100_000, multiplier: 2.0 },
    ],
  },
  {
    proofType: 'GITHUB_REPO_STARS',
    metricLabel: 'GitHub repo stars',
    tiers: [
      { minMetric: 100, ddollar: 10_000, multiplier: 1.0 },
      { minMetric: 500, ddollar: 20_000, multiplier: 1.1 },
      { minMetric: 2_000, ddollar: 40_000, multiplier: 1.25 },
      { minMetric: 10_000, ddollar: 80_000, multiplier: 1.5 },
    ],
  },
  {
    proofType: 'GITHUB_COMMITS',
    metricLabel: 'GitHub commits (recent page)',
    tiers: [
      { minMetric: 10, ddollar: 10_000, multiplier: 1.0 },
      { minMetric: 50, ddollar: 20_000, multiplier: 1.1 },
      { minMetric: 100, ddollar: 40_000, multiplier: 1.25 },
    ],
  },
  {
    proofType: 'VERCEL_DEPLOYMENTS',
    metricLabel: 'Production deployments',
    tiers: [
      { minMetric: 10, ddollar: 10_000, multiplier: 1.0 },
      { minMetric: 50, ddollar: 20_000, multiplier: 1.1 },
      { minMetric: 200, ddollar: 40_000, multiplier: 1.25 },
    ],
  },
  {
    proofType: 'APP_STORE_DOWNLOADS',
    metricLabel: 'App Store downloads',
    tiers: [
      { minMetric: 1_000, ddollar: 10_000, multiplier: 1.0 },
      { minMetric: 10_000, ddollar: 25_000, multiplier: 1.25 },
      { minMetric: 100_000, ddollar: 50_000, multiplier: 1.5 },
      { minMetric: 1_000_000, ddollar: 100_000, multiplier: 2.0 },
    ],
  },
  {
    proofType: 'GOOGLE_ANALYTICS_USERS',
    metricLabel: 'Monthly active users',
    tiers: [
      { minMetric: 1_000, ddollar: 10_000, multiplier: 1.0 },
      { minMetric: 10_000, ddollar: 25_000, multiplier: 1.25 },
      { minMetric: 100_000, ddollar: 50_000, multiplier: 1.5 },
    ],
  },
  {
    proofType: 'MANUAL_AUDIT',
    metricLabel: 'Manual audit score',
    tiers: [
      { minMetric: 1, ddollar: 10_000, multiplier: 1.0 },
      { minMetric: 50, ddollar: 25_000, multiplier: 1.25 },
      { minMetric: 100, ddollar: 50_000, multiplier: 1.5 },
    ],
  },
];

/** Look up the suggested DDollar grant for a verified metric. */
export function suggestMilestoneGrant(
  proofType: ProofType,
  verifiedMetric: number,
): { ddollar: number; multiplier: number; metricLabel: string } | null {
  const table = MILESTONE_TIERS.find((t) => t.proofType === proofType);
  if (!table) return null;
  let best: { minMetric: number; ddollar: number; multiplier: number } | null = null;
  for (const tier of table.tiers) {
    if (verifiedMetric >= tier.minMetric) {
      if (!best || tier.minMetric > best.minMetric) best = tier;
    }
  }
  if (!best) return null;
  return { ddollar: best.ddollar, multiplier: best.multiplier, metricLabel: table.metricLabel };
}
