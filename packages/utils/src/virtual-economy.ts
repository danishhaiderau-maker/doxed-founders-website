/** Virtual economy constants — all paper dollars are accounted for in the ecosystem. */
export const STARTING_CASH_USD = 10_000;
export const RESTRICTED_CASH_THRESHOLD_USD = 1_000;
export const TOP_UP_FEE_USD = 25;

export function isCashRestricted(cashBalance: number): boolean {
  return cashBalance < RESTRICTED_CASH_THRESHOLD_USD;
}

export function formatTierLabel(tier: string): string {
  const labels: Record<string, string> = {
    EXPLORER: 'Explorer',
    TRADER: 'Trader',
    COMMUNITY_CONTRIBUTOR: 'Community Contributor',
    FOUNDER_IDEA: 'Founder · Working on an idea',
    FOUNDER_BUILDING: 'Founder · Building in public',
    LAUNCH_CANDIDATE: 'Launch Candidate',
    PROVEN_FOUNDER: 'Proven Founder',
  };
  return labels[tier] ?? tier.replace(/_/g, ' ');
}

export const LIFECYCLE_STAGES = [
  { key: 'IDEA', label: 'Idea', emoji: '💡' },
  { key: 'BRAINSTORMING', label: 'Brainstorming', emoji: '🧠' },
  { key: 'PROTOTYPE', label: 'Prototype', emoji: '🛠' },
  { key: 'MVP', label: 'MVP', emoji: '🚀' },
  { key: 'BETA', label: 'Beta users', emoji: '👥' },
  { key: 'DEMAND_VALIDATION', label: 'Demand validation', emoji: '📈' },
  { key: 'SIMULATED_RAISE', label: 'Simulated raise', emoji: '💰' },
  { key: 'LAUNCH_READY', label: 'Launch ready', emoji: '🎯' },
  { key: 'TOKEN_LAUNCH', label: 'Token launch', emoji: '🪙' },
  { key: 'LIVE_TRADING', label: 'Live trading', emoji: '📊' },
] as const;

export type StartupGenome = {
  execution: number;
  demand: number;
  community: number;
  transparency: number;
  launchReady: number;
  overall: number;
};

export function computeStartupGenome(input: {
  buildPostCount: number;
  githubConnected: boolean;
  simulatedDemandUsd: number;
  followerCount: number;
  videoCount: number;
  pollVoteCount: number;
  launchReadiness: number;
}): StartupGenome {
  const execution = Math.min(
    100,
    (input.githubConnected ? 30 : 0) + input.buildPostCount * 8,
  );
  const demand = Math.min(
    100,
    Math.round((input.simulatedDemandUsd / 50_000) * 10) + input.pollVoteCount * 2,
  );
  const community = Math.min(100, input.followerCount * 2 + input.pollVoteCount * 3);
  const transparency = Math.min(100, input.videoCount * 25 + input.buildPostCount * 5);
  const launchReady = input.launchReadiness;
  const overall = Math.round(
    (execution + demand + community + transparency + launchReady) / 5,
  );
  return { execution, demand, community, transparency, launchReady, overall };
}

export function computeLaunchReadiness(input: {
  videoCount: number;
  buildPostCount: number;
  followerCount: number;
  simulatedDemandUsd: number;
  goalUsd: number;
  githubConnected: boolean;
  hasActiveRaise: boolean;
}): number {
  let score = 0;
  if (input.videoCount >= 1) score += 20;
  if (input.buildPostCount >= 3) score += 15;
  if (input.githubConnected) score += 10;
  if (input.followerCount >= 10) score += 15;
  if (input.hasActiveRaise) score += 10;
  if (input.goalUsd > 0) {
    const pct = Math.min(1, input.simulatedDemandUsd / input.goalUsd);
    score += Math.round(pct * 30);
  }
  return Math.min(100, score);
}
