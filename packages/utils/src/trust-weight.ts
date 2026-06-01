/** Maximum vote weight — caps influence so blue ticks cannot dominate. */
export const MAX_TRUST_WEIGHT = 10;

export const TRUST_WEIGHT_CAP = MAX_TRUST_WEIGHT;

/** Listing passes when weighted yes% >= this and distinct voter count met. */
export const LISTING_MIN_APPROVAL_PERCENT = 70;

/** Investigation opens when weighted scam% >= this. */
export const INVESTIGATION_SCAM_THRESHOLD_PERCENT = 40;

export const INVESTIGATION_WINDOW_HOURS = 48;

export type TrustWeightInput = {
  /** Email or OAuth verified identity (+1). */
  verifiedAccount?: boolean;
  /** Platform contributor level 1–5 → scout reputation bonus 0–3. */
  contributorLevel?: number;
  /** Reputation points → community reputation bonus 0–3. */
  reputationPoints?: number;
  /** Account age in days → 0–2 bonus. */
  accountAgeDays?: number;
};

/**
 * Trust weight formula (earned, not purchased):
 * Base 1 + verified +1 + scout 0–3 + community 0–3 + age 0–2, capped at 10.
 */
export function computeTrustWeight(input: TrustWeightInput): number {
  let weight = 1;

  if (input.verifiedAccount) weight += 1;

  const level = Math.max(1, Math.min(5, input.contributorLevel ?? 1));
  const scoutBonus = level <= 1 ? 0 : level === 2 ? 1 : level === 3 ? 2 : 3;
  weight += scoutBonus;

  const pts = Math.max(0, input.reputationPoints ?? 0);
  const communityBonus =
    pts >= 5000 ? 3 : pts >= 2000 ? 2 : pts >= 800 ? 1 : 0;
  weight += communityBonus;

  const days = Math.max(0, input.accountAgeDays ?? 0);
  if (days >= 180) weight += 2;
  else if (days >= 30) weight += 1;

  return Math.min(MAX_TRUST_WEIGHT, weight);
}

export function trustWeightLabel(weight: number): string {
  if (weight >= 8) return 'Legendary scout';
  if (weight >= 5) return 'Trusted scout';
  if (weight >= 3) return 'Active scout';
  if (weight >= 2) return 'Verified';
  return 'Community member';
}

export type CommunityValidationCategory =
  | 'LOOKS_LEGIT'
  | 'BUILDING_CONSISTENTLY'
  | 'COMMUNITY_EXISTS'
  | 'NEEDS_MORE_PROOF'
  | 'SUSPICIOUS'
  | 'LIKELY_SCAM';

export const POSITIVE_VALIDATION: CommunityValidationCategory[] = [
  'LOOKS_LEGIT',
  'BUILDING_CONSISTENTLY',
  'COMMUNITY_EXISTS',
];

export const NEGATIVE_VALIDATION: CommunityValidationCategory[] = [
  'NEEDS_MORE_PROOF',
  'SUSPICIOUS',
  'LIKELY_SCAM',
];

export const VALIDATION_LABELS: Record<CommunityValidationCategory, string> = {
  LOOKS_LEGIT: 'Founder looks legit',
  BUILDING_CONSISTENTLY: 'Building consistently',
  COMMUNITY_EXISTS: 'Community exists',
  NEEDS_MORE_PROOF: 'Needs more proof',
  SUSPICIOUS: 'Suspicious',
  LIKELY_SCAM: 'Likely scam',
};

export function validationCategoryToVote(
  category: CommunityValidationCategory,
): 'YES' | 'NO' {
  return POSITIVE_VALIDATION.includes(category) ? 'YES' : 'NO';
}

export type WeightedVote = { vote: 'YES' | 'NO'; weight?: number };

export type WeightedVoteTally = {
  totalVoters: number;
  yesWeight: number;
  noWeight: number;
  totalWeight: number;
  yesPercent: number;
  scamPercent: number;
  requiredVoters: number;
  minYesPercent: number;
  passed: boolean;
  remainingVoters: number;
};

export function tallyWeightedVotes(
  votes: WeightedVote[],
  requiredVoters: number,
  minYesPercent: number = LISTING_MIN_APPROVAL_PERCENT,
): WeightedVoteTally {
  const yesWeight = votes
    .filter((v) => v.vote === 'YES')
    .reduce((sum, v) => sum + (v.weight ?? 1), 0);
  const noWeight = votes
    .filter((v) => v.vote === 'NO')
    .reduce((sum, v) => sum + (v.weight ?? 1), 0);
  const totalWeight = yesWeight + noWeight;
  const totalVoters = votes.length;
  const yesPercent = totalWeight > 0 ? Math.round((yesWeight / totalWeight) * 100) : 0;
  const scamPercent = totalWeight > 0 ? Math.round((noWeight / totalWeight) * 100) : 0;
  const passed = totalVoters >= requiredVoters && yesPercent >= minYesPercent;

  return {
    totalVoters,
    yesWeight,
    noWeight,
    totalWeight,
    yesPercent,
    scamPercent,
    requiredVoters,
    minYesPercent,
    passed,
    remainingVoters: Math.max(0, requiredVoters - totalVoters),
  };
}
