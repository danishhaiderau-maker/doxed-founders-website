/** Discover V4 — Project Universe scoring & visual mapping */

/** Bubble ring color — always matches project lifecycle stage bucket. */
export type DiscoverUniverseStage = 'building' | 'validation' | 'live';

/** Map filter includes recently listed (by age), not a ring color. */
export type DiscoverUniverseStageFilter = DiscoverUniverseStage | 'all' | 'recently_listed';

export type DiscoverTimeframe = '1h' | '6h' | '24h' | '7d';

export const DISCOVER_RECENTLY_LISTED_DAYS = 14;

export const DISCOVER_UNIVERSE_COLORS: Record<
  DiscoverUniverseStage,
  { color: string; border: string; glow: string; label: string }
> = {
  building: { color: '#3b82f6', border: '#60a5fa', glow: '#3b82f655', label: 'Building' },
  validation: { color: '#f97316', border: '#fb923c', glow: '#f9731655', label: 'Validation' },
  live: { color: '#22c55e', border: '#4ade80', glow: '#22c55e55', label: 'Live' },
};

export const DISCOVER_RECENTLY_LISTED_FILTER_LABEL = 'Recently Listed';

export function timeframeToMs(tf: DiscoverTimeframe): number {
  switch (tf) {
    case '1h':
      return 60 * 60 * 1000;
    case '6h':
      return 6 * 60 * 60 * 1000;
    case '24h':
      return 24 * 60 * 60 * 1000;
    case '7d':
      return 7 * 24 * 60 * 60 * 1000;
  }
}

/** Ring color follows platform stage bucket — not listing age. */
export function resolveDiscoverUniverseStage(input: {
  stageBucket: string;
  isLiveToken?: boolean;
}): DiscoverUniverseStage {
  if (input.isLiveToken || input.stageBucket === 'LIVE_TOKEN') return 'live';
  if (input.stageBucket === 'LAUNCH_READY') return 'validation';
  return 'building';
}

/** Tab filter: projects listed on the platform within the last N days. */
export function isDiscoverRecentlyListed(
  createdAt: string | Date,
  maxDays = DISCOVER_RECENTLY_LISTED_DAYS,
): boolean {
  const days = (Date.now() - new Date(createdAt).getTime()) / 86_400_000;
  return days >= 0 && days <= maxDays;
}

export type DiscoverActivityInput = {
  buildPosts: number;
  githubEvents: number;
  tradesInflow: number;
  tradesVolume: number;
  followers: number;
  scoutStake: number;
  communitySignals: number;
  bubbleScore: number;
};

/** Composite activity score 0–100 */
export function computeDiscoverActivityScore(input: DiscoverActivityInput): number {
  const raw =
    Math.min(input.buildPosts * 8, 24) +
    Math.min(input.githubEvents * 6, 18) +
    Math.min(input.tradesInflow / 500, 20) +
    Math.min(input.tradesVolume / 2000, 10) +
    Math.min(input.followers * 4, 12) +
    Math.min(input.scoutStake / 1000, 8) +
    Math.min(input.communitySignals * 2, 8) +
    Math.min(input.bubbleScore / 50, 10);
  return Math.round(Math.min(100, Math.max(0, raw)));
}

export type DiscoverConvictionInput = {
  launchReadiness: number;
  demandPct: number;
  founderScore: number;
  followerCount: number;
  scoutPoolUsd: number;
};

/** Conviction score 0–100 */
export function computeDiscoverConvictionScore(input: DiscoverConvictionInput): number {
  const raw =
    input.launchReadiness * 0.35 +
    input.demandPct * 0.25 +
    Math.min(input.founderScore / 2, 50) * 0.2 +
    Math.min(input.followerCount / 20, 25) * 0.1 +
    Math.min(input.scoutPoolUsd / 5000, 20) * 0.1;
  return Math.round(Math.min(100, Math.max(0, raw)));
}

/** Bubble diameter in px from activity score */
export function bubbleRadiusFromActivityScore(score: number): number {
  if (score <= 25) return 56;
  if (score <= 50) return 72;
  if (score <= 75) return 96;
  return 120;
}

/** Golden-angle layout — spread bubbles across the canvas (not stacked at center). */
export function layoutBubblePositions(
  count: number,
  width: number,
  height: number,
): { x: number; y: number }[] {
  if (count <= 0) return [];
  const cx = width / 2;
  const cy = height / 2;
  const golden = Math.PI * (3 - Math.sqrt(5));
  const maxR = Math.min(width, height) * 0.4;
  const pad = 64;
  return Array.from({ length: count }, (_, i) => {
    const t = (i + 0.5) / count;
    const r = maxR * Math.sqrt(t);
    const angle = i * golden;
    const x = cx + Math.cos(angle) * r;
    const y = cy + Math.sin(angle) * r * 0.88;
    return {
      x: Math.min(width - pad, Math.max(pad, x)),
      y: Math.min(height - pad, Math.max(pad, y)),
    };
  });
}

export type DiscoverTrendDirection = 'up' | 'down' | 'flat';

export function computeTrendDirection(current: number, prior: number): {
  direction: DiscoverTrendDirection;
  pct: number;
} {
  if (prior <= 0 && current <= 0) return { direction: 'flat', pct: 0 };
  if (prior <= 0) return { direction: 'up', pct: 100 };
  const pct = Math.round(((current - prior) / prior) * 100);
  if (pct > 5) return { direction: 'up', pct };
  if (pct < -5) return { direction: 'down', pct };
  return { direction: 'flat', pct };
}
