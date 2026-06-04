import { BUILDER_REWARDS_INACTIVITY_DAYS, daysSince } from './builder-rewards';

export { daysSince };

/** @deprecated Use BUILDER_REWARDS_* from builder-rewards.ts — kept for API compat. */
export const AIRDROP_INACTIVITY_WARN_DAYS = BUILDER_REWARDS_INACTIVITY_DAYS;
/** Legacy constant — Builder Rewards uses weekly score decay instead. */
export const AIRDROP_INACTIVITY_DECAY_DDOLLAR_PER_DAY = 0;
export const AIRDROP_ACTIVITY_WINDOW_DAYS = 14;

export type AirdropRunwayStatus =
  | 'active'
  | 'warming'
  | 'at_risk'
  | 'decaying';

export type AirdropXEligibility = 'eligible' | 'review' | 'not_connected';

export type AirdropRunwayInput = {
  reputationPoints: number;
  activityScore: number;
  lastActiveAt: string | null;
  twitterConnected: boolean;
  /** Future: set when X API confirms verified or historical blue */
  xVerifiedBadge?: boolean;
  /** Future: user ever had X Premium / blue */
  xPremiumHistory?: boolean;
};

export function computeAirdropRunwayStatus(input: {
  lastActiveAt: string | null;
  activityScore: number;
}): AirdropRunwayStatus {
  const idle = daysSince(input.lastActiveAt);
  if (idle == null || input.activityScore <= 0) return 'warming';
  if (idle >= AIRDROP_INACTIVITY_WARN_DAYS) return 'decaying';
  if (idle >= AIRDROP_INACTIVITY_WARN_DAYS - 7) return 'at_risk';
  if (input.activityScore >= 12) return 'active';
  return 'warming';
}

export function computeInactivityDecayDdollar(_idleDays: number | null): number {
  return 0;
}

/** 0–100 human-likelihood score from activity patterns (not identity verification). */
export function computeHumanLikelihoodScore(input: {
  activityScore: number;
  reputationPoints: number;
  actionKinds: number;
  accountAgeDays: number;
}): number {
  let score = 35;
  score += Math.min(40, input.activityScore * 2);
  score += Math.min(15, Math.log10(input.reputationPoints + 1) * 8);
  score += Math.min(10, input.actionKinds * 3);
  if (input.accountAgeDays >= 7) score += 5;
  if (input.accountAgeDays >= 30) score += 5;
  if (input.activityScore <= 2 && input.reputationPoints > 500) score -= 25;
  return Math.max(0, Math.min(100, Math.round(score)));
}

export function computeRunwayRankScore(input: {
  reputationPoints: number;
  activityScore: number;
  humanScore: number;
  status: AirdropRunwayStatus;
}): number {
  const statusMult =
    input.status === 'active'
      ? 1.15
      : input.status === 'warming'
        ? 1
        : input.status === 'at_risk'
          ? 0.75
          : 0.4;
  return (
    (input.reputationPoints + input.activityScore * 25) *
    (0.5 + input.humanScore / 200) *
    statusMult
  );
}

export function xEligibilityForUser(input: {
  twitterConnected: boolean;
  humanScore: number;
  status: AirdropRunwayStatus;
}): AirdropXEligibility {
  if (!input.twitterConnected) return 'not_connected';
  if (input.status === 'decaying' || input.humanScore < 25) return 'review';
  return 'eligible';
}

export function formatRunwayStatusLabel(status: AirdropRunwayStatus): string {
  switch (status) {
    case 'active':
      return 'On runway';
    case 'warming':
      return 'Warming up';
    case 'at_risk':
      return 'At risk';
    case 'decaying':
      return 'Inactive — decay';
    default:
      return status;
  }
}
