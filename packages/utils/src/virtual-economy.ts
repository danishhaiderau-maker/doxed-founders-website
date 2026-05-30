/** Virtual economy constants — all paper dollars are accounted for in the ecosystem. */
export const STARTING_CASH_USD = 10_000;
export const RESTRICTED_CASH_THRESHOLD_USD = 1_000;
export const TOP_UP_FEE_USD = 25;
export const TOP_UP_INTENT_TTL_MS = 30 * 60 * 1000;

/** Solana mainnet USDC mint (SPL). */
export const SOLANA_USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v';
export const SOLANA_USDC_DECIMALS = 6;

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

/** High-level buckets for Discover and journey UX */
export type StageBucket = 'IDEA_STAGE' | 'BUILDING' | 'LAUNCH_READY' | 'LIVE_TOKEN';

export const STAGE_BUCKETS: { key: StageBucket; label: string; color: string; border: string }[] = [
  { key: 'IDEA_STAGE', label: 'Building', color: '#3b82f6', border: '#60a5fa' },
  { key: 'BUILDING', label: 'Building', color: '#3b82f6', border: '#60a5fa' },
  { key: 'LAUNCH_READY', label: 'Validation', color: '#eab308', border: '#facc15' },
  { key: 'LIVE_TOKEN', label: 'Live', color: '#a855f7', border: '#c084fc' },
];

/** Scan-friendly stage colors: blue = building, yellow = validation, green = launch ready, purple = live */
export type StageColorTheme = 'building' | 'validation' | 'launch_ready' | 'live';

export function getStageColorTheme(stage: string, isLiveToken?: boolean): StageColorTheme {
  if (isLiveToken || stage === 'TOKEN_LAUNCH' || stage === 'LIVE_TRADING') return 'live';
  if (stage === 'LAUNCH_READY') return 'launch_ready';
  if (stage === 'DEMAND_VALIDATION' || stage === 'SIMULATED_RAISE') return 'validation';
  return 'building';
}

export const STAGE_COLOR_CLASSES: Record<
  StageColorTheme,
  { badge: string; text: string; dot: string }
> = {
  building: {
    badge: 'border-blue-500/30 bg-blue-500/10',
    text: 'text-blue-300',
    dot: 'bg-blue-400',
  },
  validation: {
    badge: 'border-amber-500/30 bg-amber-500/10',
    text: 'text-amber-300',
    dot: 'bg-amber-400',
  },
  launch_ready: {
    badge: 'border-emerald-500/30 bg-emerald-500/10',
    text: 'text-emerald-300',
    dot: 'bg-emerald-400',
  },
  live: {
    badge: 'border-purple-500/30 bg-purple-500/10',
    text: 'text-purple-300',
    dot: 'bg-purple-400',
  },
};

export function getStageColorLabel(theme: StageColorTheme): string {
  const labels: Record<StageColorTheme, string> = {
    building: 'Building',
    validation: 'Validation',
    launch_ready: 'Launch ready',
    live: 'Live',
  };
  return labels[theme];
}

const STAGE_INDEX: Record<string, number> = Object.fromEntries(
  LIFECYCLE_STAGES.map((s, i) => [s.key, i]),
);

export function getStageBucket(stage: string, isLiveToken?: boolean): StageBucket {
  if (isLiveToken || stage === 'TOKEN_LAUNCH' || stage === 'LIVE_TRADING') {
    return 'LIVE_TOKEN';
  }
  if (['LAUNCH_READY', 'SIMULATED_RAISE', 'DEMAND_VALIDATION'].includes(stage)) {
    return 'LAUNCH_READY';
  }
  if (['MVP', 'BETA'].includes(stage)) return 'BUILDING';
  return 'IDEA_STAGE';
}

export function getStageBucketMeta(bucket: StageBucket) {
  return STAGE_BUCKETS.find((b) => b.key === bucket) ?? STAGE_BUCKETS[0];
}

export function computeJourneyProgress(stage: string): number {
  const idx = STAGE_INDEX[stage] ?? 0;
  return Math.round(((idx + 1) / LIFECYCLE_STAGES.length) * 100);
}

/** Infer lifecycle for curated/live projects still marked IDEA in DB */
export function inferProjectLifecycleStage(input: {
  lifecycleStage: string;
  isLiveToken: boolean;
  dexscreenerUrl?: string | null;
  contractAddress?: string | null;
  marketCap?: number | null;
}): string {
  if (input.isLiveToken) return 'LIVE_TRADING';
  const hasMarket =
    Boolean(input.dexscreenerUrl) &&
    (input.marketCap != null && input.marketCap > 0 || Boolean(input.contractAddress));
  if (hasMarket) return 'LIVE_TRADING';
  return input.lifecycleStage;
}

/** verified = scout/admin listing · founder_os = Founder OS · paper_track = DexScreener paper trade only */
export type ProjectListingKind = 'verified' | 'founder_os' | 'paper_track';

export function resolveProjectListingKind(input: {
  source: string;
  founderId?: string | null;
}): ProjectListingKind {
  if (input.source === 'CURATED') return 'verified';
  if (input.founderId) return 'founder_os';
  return 'paper_track';
}

export function resolveEffectiveLifecycleStage(input: {
  source: string;
  founderId?: string | null;
  lifecycleStage: string;
  isLiveToken: boolean;
  dexscreenerUrl?: string | null;
  contractAddress?: string | null;
  marketCap?: number | null;
}): string {
  const inferred = inferProjectLifecycleStage({
    lifecycleStage: input.lifecycleStage,
    isLiveToken: input.isLiveToken,
    dexscreenerUrl: input.dexscreenerUrl,
    contractAddress: input.contractAddress,
    marketCap: input.marketCap,
  });
  const kind = resolveProjectListingKind({ source: input.source, founderId: input.founderId });
  if (kind === 'verified' && inferred === 'IDEA') return 'LAUNCH_READY';
  if (kind === 'founder_os') return inferred === 'LIVE_TRADING' ? inferred : input.lifecycleStage;
  return inferred;
}

export function formatStageBucketLabel(bucket: StageBucket): string {
  return getStageBucketMeta(bucket).label;
}

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
