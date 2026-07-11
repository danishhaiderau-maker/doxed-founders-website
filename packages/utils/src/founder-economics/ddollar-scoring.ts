/**
 * DDollar Scoring — defines what ECONOMIC activity earns DDollar.
 *
 * The separation between Economic and Speculative activity is a hard rule:
 *   - BUILDING, LAUNCHING, PROVIDING LIQUIDITY, CONTRIBUTING KNOWLEDGE,
 *     REACHING MILESTONES, GOVERNING → earns DDollar.
 *   - TRADING, SWAPPING, PAPER TRADING, COPY-TRADING → NEVER earns DDollar.
 *
 * This separation exists because DDollar drives token vesting distribution.
 * If trading earned DDollar, the distribution would become a self-feeding
 * speculative loop. Economic activity is the only durable signal.
 *
 * The amounts below are the on-chain-style reward ranges. The settlement job
 * (and the DdollarEngineService) maps an event into one of these activity
 * types and grants DDollar. Ranges let the engine scale by quality / proof.
 */

export type DDollarActivityType =
  | 'BUILD_POST_LINKED_TO_COMMIT'
  | 'PRODUCT_LAUNCH_VIA_RAISE_ROOM'
  | 'DURABLE_LIQUIDITY_LP_DAYS'
  | 'KNOWLEDGE_CONTRIBUTION'
  | 'KNOWLEDGE_REUSED_IMPACT'
  | 'COMPANY_MILESTONE_VERIFIED'
  | 'GOVERNANCE_PARTICIPATION'
  | 'FOUNDER_MEMORY_REUSED';

export type DDollarSpec = {
  activity: DDollarActivityType;
  label: string;
  description: string;
  min: number;
  max: number;
  /** Whether the same activity can be granted multiple times to one user. */
  repeatable: boolean;
  /** Whether the grant requires a ProofOfSuccess record. */
  requiresProof: boolean;
};

export const DDOLLAR_ACTIVITY_SPECS: Record<DDollarActivityType, DDollarSpec> = {
  BUILD_POST_LINKED_TO_COMMIT: {
    activity: 'BUILD_POST_LINKED_TO_COMMIT',
    label: 'Build post linked to real commit',
    description: 'A build-in-public post that references a real, verifiable git commit.',
    min: 50,
    max: 500,
    repeatable: true,
    requiresProof: false,
  },
  PRODUCT_LAUNCH_VIA_RAISE_ROOM: {
    activity: 'PRODUCT_LAUNCH_VIA_RAISE_ROOM',
    label: 'Launch a product via Raise Room',
    description: 'A project that graduates the Raise Room and goes LIVE.',
    min: 5_000,
    max: 5_000,
    repeatable: true,
    requiresProof: true,
  },
  DURABLE_LIQUIDITY_LP_DAYS: {
    activity: 'DURABLE_LIQUIDITY_LP_DAYS',
    label: 'Durable liquidity provided (LP-days)',
    description: 'Per-day reward for liquidity that stays deployed (anti-flash).',
    min: 10,
    max: 10,
    repeatable: true,
    requiresProof: false,
  },
  KNOWLEDGE_CONTRIBUTION: {
    activity: 'KNOWLEDGE_CONTRIBUTION',
    label: 'Contribute knowledge',
    description: 'Reusable knowledge node — playbook, research, pattern.',
    min: 100,
    max: 1_000,
    repeatable: true,
    requiresProof: false,
  },
  KNOWLEDGE_REUSED_IMPACT: {
    activity: 'KNOWLEDGE_REUSED_IMPACT',
    label: 'Knowledge reused by another founder (Impact)',
    description: 'Another founder built on your knowledge node — verified reuse.',
    min: 1_000,
    max: 10_000,
    repeatable: true,
    requiresProof: false,
  },
  COMPANY_MILESTONE_VERIFIED: {
    activity: 'COMPANY_MILESTONE_VERIFIED',
    label: 'Company milestone (verified ARR, paying users)',
    description: 'Real business milestone verified by Proof of Success.',
    min: 10_000,
    max: 100_000,
    repeatable: true,
    requiresProof: true,
  },
  GOVERNANCE_PARTICIPATION: {
    activity: 'GOVERNANCE_PARTICIPATION',
    label: 'Governance participation',
    description: 'Voted on a listing, prediction market, or distribution model change.',
    min: 10,
    max: 100,
    repeatable: true,
    requiresProof: false,
  },
  FOUNDER_MEMORY_REUSED: {
    activity: 'FOUNDER_MEMORY_REUSED',
    label: 'Founder Memory reused by another founder',
    description: 'Another founder reused a node from your Founder Memory Graph.',
    min: 500,
    max: 5_000,
    repeatable: true,
    requiresProof: false,
  },
};

/** Clamp an amount into the spec's [min, max] range. */
export function clampDdollarAmount(spec: DDollarSpec, amount: number): number {
  return Math.max(spec.min, Math.min(spec.max, Math.round(amount)));
}

/** Quick predicate — does this activity count as Economic (DDollar-eligible)? */
export function isEconomicActivity(activity: DDollarActivityType): boolean {
  return activity in DDOLLAR_ACTIVITY_SPECS;
}

/**
 * Trading / swapping / paper-trading NEVER earns DDollar.
 * This list documents the exclusion. The DdollarEngineService consults it
 * before granting — anything that matches is rejected.
 */
export const SPECULATIVE_ACTIONS_NEVER_EARN_DDOLLAR = [
  'PAPER_TRADE',
  'PAPER_TRADE_BUY',
  'PAPER_TRADE_SELL',
  'DEX_SWAP',
  'COPY_TRADE',
  'AGENT_COPY',
  'WATCHLIST_ADD',
  'WALL_SUMMARIZER_MONTHLY',
] as const;

export function isSpeculativeAction(actionKey: string): boolean {
  const base = actionKey.split(':')[0] ?? actionKey;
  return (SPECULATIVE_ACTIONS_NEVER_EARN_DDOLLAR as readonly string[]).includes(base);
}
