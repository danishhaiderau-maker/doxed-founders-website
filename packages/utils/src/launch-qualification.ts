/**
 * Launch Qualification Score — weighted 0–100 composite (Architecture Review v2 #2).
 * Constants only; API/service wiring in Phase 1.5 (RR-012).
 */

/** Minimum composite score to pass launch gates (maps to gate G6). */
export const LAUNCH_QUALIFICATION_MIN_SCORE = 70;

/** Tier thresholds for discovery ranking and progressive unlock copy. */
export const LAUNCH_QUALIFICATION_TIER_ELITE = 90;
export const LAUNCH_QUALIFICATION_TIER_STRONG = 80;
export const LAUNCH_QUALIFICATION_TIER_MINIMUM = 70;

export type LaunchQualificationTier = 'ELITE' | 'STRONG' | 'MINIMUM' | 'BELOW';

export function getLaunchQualificationTier(
  score: number,
): LaunchQualificationTier {
  if (score >= LAUNCH_QUALIFICATION_TIER_ELITE) return 'ELITE';
  if (score >= LAUNCH_QUALIFICATION_TIER_STRONG) return 'STRONG';
  if (score >= LAUNCH_QUALIFICATION_TIER_MINIMUM) return 'MINIMUM';
  return 'BELOW';
}

/** Component weights — must sum to 1.0 */
export const LAUNCH_QUALIFICATION_WEIGHTS = {
  /** Trust Center + Raise Room weighted validation (anti-sybil adjusted). */
  communityTrust: 0.25,
  /** Paper conviction fill ratio vs goal + effectivePaper (trust-weighted). */
  paperConviction: 0.2,
  /** Founder Launch Score composite (identity, GitHub, transparency, AI pass). */
  founderLaunchScore: 0.2,
  /** Founder Integrity — separate from Builder; investigations, identity, delivery. */
  founderIntegrity: 0.15,
  /** Build proof — Startup Genome, build posts, video gates. */
  buildDelivery: 0.1,
  /** Regulatory Engine classification cleared (Community / Utility / Governance). */
  regulatoryClearance: 0.1,
} as const;

export type LaunchQualificationComponents = {
  communityTrust: number;
  paperConviction: number;
  founderLaunchScore: number;
  founderIntegrity: number;
  buildDelivery: number;
  regulatoryClearance: number;
};

/**
 * Compute Launch Qualification Score (0–100) from normalized component scores (each 0–100).
 */
export function computeLaunchQualificationScore(
  components: LaunchQualificationComponents,
): number {
  const w = LAUNCH_QUALIFICATION_WEIGHTS;
  const raw =
    components.communityTrust * w.communityTrust +
    components.paperConviction * w.paperConviction +
    components.founderLaunchScore * w.founderLaunchScore +
    components.founderIntegrity * w.founderIntegrity +
    components.buildDelivery * w.buildDelivery +
    components.regulatoryClearance * w.regulatoryClearance;
  return Math.round(Math.min(100, Math.max(0, raw)));
}

export function passesLaunchQualification(score: number): boolean {
  return score >= LAUNCH_QUALIFICATION_MIN_SCORE;
}
