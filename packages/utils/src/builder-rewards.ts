/**
 * Builder Rewards — proof-of-contribution scoring (not an airdrop farm page).
 */

export const BUILDER_REWARDS_INACTIVITY_DAYS = 21;
/** Weekly contribution score decay after inactivity threshold (not DDollar destruction). */
export const BUILDER_REWARDS_WEEKLY_DECAY_PERCENT = 1;
export const BUILDER_REWARDS_ACTIVITY_WINDOW_DAYS = 14;
export const BUILDER_REWARDS_X_VERIFIED_BONUS_PERCENT = 5;

/** Future snapshot weight guidance (not enforced on-chain yet). */
export const BUILDER_REWARDS_SNAPSHOT_WEIGHTS = {
  ddollar: 0.3,
  builderActivity: 0.25,
  reputation: 0.2,
  scoutAccuracy: 0.15,
  communityActivity: 0.1,
} as const;

export type BuilderRewardStatus = 'active' | 'warming' | 'at_risk' | 'decaying';

export type BuilderTier =
  | 'bronze'
  | 'silver'
  | 'gold'
  | 'platinum'
  | 'legend'
  | 'genesis';

export type BuilderXTrust = 'eligible' | 'review' | 'not_connected';

export type BuilderSubScores = {
  contributionScore: number;
  reputationScore: number;
  activityScore: number;
  builderActivityScore: number;
  scoutScore: number;
  tradingScore: number;
  humanityScore: number;
};

export type BuilderRewardsInput = {
  reputationPoints: number;
  activityEventCount: number;
  actionKinds: number;
  builderPosts: number;
  buildStreakDays: number;
  scoutStakes: number;
  trades: number;
  tradesWithConviction: number;
  communityActions: number;
  ddollarEarnedApprox: number;
  accountAgeDays: number;
  lastActiveAt: string | null;
  twitterConnected: boolean;
  xVerifiedBadge?: boolean;
  xPremiumHistory?: boolean;
};

export function daysSince(iso: string | null | undefined, nowMs = Date.now()): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return Math.floor((nowMs - t) / (24 * 60 * 60 * 1000));
}

export function computeBuilderRewardStatus(input: {
  lastActiveAt: string | null;
  activityEventCount: number;
}): BuilderRewardStatus {
  const idle = daysSince(input.lastActiveAt);
  if (idle == null || input.activityEventCount <= 0) return 'warming';
  if (idle >= BUILDER_REWARDS_INACTIVITY_DAYS) return 'decaying';
  if (idle >= BUILDER_REWARDS_INACTIVITY_DAYS - 7) return 'at_risk';
  if (input.activityEventCount >= 12) return 'active';
  return 'warming';
}

/** Multiplier applied to Builder Score after 21+ days idle (1% per week, floor 50%). */
export function computeContributionDecayMultiplier(idleDays: number | null): number {
  if (idleDays == null || idleDays < BUILDER_REWARDS_INACTIVITY_DAYS) return 1;
  const weeks = Math.floor((idleDays - BUILDER_REWARDS_INACTIVITY_DAYS) / 7) + 1;
  const decay = (weeks * BUILDER_REWARDS_WEEKLY_DECAY_PERCENT) / 100;
  return Math.max(0.5, 1 - decay);
}

export function computeWeeklyDecayPercent(idleDays: number | null): number {
  if (idleDays == null || idleDays < BUILDER_REWARDS_INACTIVITY_DAYS) return 0;
  const weeks = Math.floor((idleDays - BUILDER_REWARDS_INACTIVITY_DAYS) / 7) + 1;
  return Math.min(50, weeks * BUILDER_REWARDS_WEEKLY_DECAY_PERCENT);
}

/** Internal humanity signal (0–100) — not shown as primary rank driver. */
export function computeHumanityScore(input: {
  activityEventCount: number;
  reputationPoints: number;
  actionKinds: number;
  accountAgeDays: number;
}): number {
  let score = 35;
  const activity = Math.min(50, input.activityEventCount);
  score += Math.min(40, activity * 2);
  score += Math.min(15, Math.log10(input.reputationPoints + 1) * 8);
  score += Math.min(10, input.actionKinds * 3);
  if (input.accountAgeDays >= 7) score += 5;
  if (input.accountAgeDays >= 30) score += 5;
  if (activity <= 2 && input.reputationPoints > 500) score -= 25;
  return Math.max(0, Math.min(100, Math.round(score)));
}

export function computeBuilderSubScores(input: BuilderRewardsInput): BuilderSubScores {
  const activity = Math.min(50, input.activityEventCount);
  const reputationScore = Math.min(500, Math.log10(input.reputationPoints + 1) * 120);
  const activityScore = activity * 4;
  const builderActivityScore = Math.min(400, input.builderPosts * 40 + input.buildStreakDays * 8);
  const scoutScore = Math.min(200, input.scoutStakes * 25);
  const tradingScore = Math.min(
    250,
    input.trades * 8 + input.tradesWithConviction * 15,
  );
  const communityScore = Math.min(150, input.communityActions * 6);
  const contributionScore = Math.round(
    builderActivityScore * 0.45 +
      scoutScore * 0.2 +
      communityScore * 0.2 +
      tradingScore * 0.15,
  );

  const humanityScore = computeHumanityScore({
    activityEventCount: input.activityEventCount,
    reputationPoints: input.reputationPoints,
    actionKinds: input.actionKinds,
    accountAgeDays: input.accountAgeDays,
  });

  return {
    contributionScore,
    reputationScore: Math.round(reputationScore),
    activityScore: Math.round(activityScore + communityScore * 0.3),
    builderActivityScore: Math.round(builderActivityScore),
    scoutScore: Math.round(scoutScore),
    tradingScore: Math.round(tradingScore),
    humanityScore,
  };
}

export function computeRawBuilderScore(
  sub: BuilderSubScores,
  ddollarEarnedApprox: number,
): number {
  const ddollarSignal = Math.min(120, Math.log10(ddollarEarnedApprox + 1) * 35);
  return (
    sub.contributionScore +
    sub.reputationScore +
    sub.activityScore +
    sub.builderActivityScore * 0.15 +
    sub.scoutScore * 0.1 +
    sub.tradingScore * 0.1 +
    ddollarSignal
  );
}

export function applyBuilderScoreModifiers(input: {
  rawScore: number;
  status: BuilderRewardStatus;
  humanityScore: number;
  xVerifiedBadge?: boolean;
  xPremiumHistory?: boolean;
  idleDays: number | null;
}): number {
  const decayMult = computeContributionDecayMultiplier(input.idleDays);
  const statusMult =
    input.status === 'active'
      ? 1.12
      : input.status === 'warming'
        ? 1
        : input.status === 'at_risk'
          ? 0.88
          : 0.65;
  const humanMult = 0.55 + input.humanityScore / 200;
  let score = input.rawScore * statusMult * humanMult * decayMult;
  if (input.xVerifiedBadge) score *= 1 + BUILDER_REWARDS_X_VERIFIED_BONUS_PERCENT / 100;
  else if (input.xPremiumHistory) score *= 1.03;
  return Math.round(score);
}

export function tierFromBuilderScore(score: number): BuilderTier {
  if (score >= 5000) return 'genesis';
  if (score >= 2500) return 'legend';
  if (score >= 1200) return 'platinum';
  if (score >= 600) return 'gold';
  if (score >= 250) return 'silver';
  return 'bronze';
}

export function formatBuilderTier(tier: BuilderTier): string {
  const labels: Record<BuilderTier, string> = {
    bronze: 'Bronze Builder',
    silver: 'Silver Builder',
    gold: 'Gold Builder',
    platinum: 'Platinum Builder',
    legend: 'Legend Builder',
    genesis: 'Genesis Builder',
  };
  return labels[tier];
}

export function formatBuilderStatusLabel(status: BuilderRewardStatus): string {
  switch (status) {
    case 'active':
      return 'Active';
    case 'warming':
      return 'Warming up';
    case 'at_risk':
      return 'At risk';
    case 'decaying':
      return 'Score decaying';
    default:
      return status;
  }
}

export function xTrustForUser(input: {
  twitterConnected: boolean;
  humanityScore: number;
  status: BuilderRewardStatus;
}): BuilderXTrust {
  if (!input.twitterConnected) return 'not_connected';
  if (input.status === 'decaying' || input.humanityScore < 25) return 'review';
  return 'eligible';
}

export function computeRewardSharePercent(
  builderScore: number,
  totalBuilderScore: number,
): number {
  if (builderScore <= 0 || totalBuilderScore <= 0) return 0;
  return (builderScore / totalBuilderScore) * 100;
}

export type ActivityStreakBadge = {
  id: string;
  label: string;
  earned: boolean;
};

export function computeActivityStreakBadges(activeDaysEstimate: number): ActivityStreakBadge[] {
  const thresholds = [
    { id: '7d', days: 7, label: '7 Day Streak' },
    { id: '30d', days: 30, label: '30 Day Streak' },
    { id: '90d', days: 90, label: '90 Day Streak' },
    { id: '180d', days: 180, label: '180 Day Streak' },
  ];
  return thresholds.map((t) => ({
    id: t.id,
    label: t.label,
    earned: activeDaysEstimate >= t.days,
  }));
}

export function estimateActiveDaysFromEvents(eventCount: number, accountAgeDays: number): number {
  if (eventCount <= 0) return 0;
  return Math.min(accountAgeDays, Math.max(7, Math.floor(eventCount * 1.5)));
}
