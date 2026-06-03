/** Discover V4 — Project Universe scoring & visual mapping */

export type DiscoverUniverseStage = 'building' | 'validation' | 'live' | 'recently_listed';

export type DiscoverTimeframe = '1h' | '6h' | '24h' | '7d';

export const DISCOVER_UNIVERSE_COLORS: Record<
  DiscoverUniverseStage,
  { color: string; border: string; glow: string; label: string }
> = {
  building: { color: '#3b82f6', border: '#60a5fa', glow: '#3b82f655', label: 'Building' },
  validation: { color: '#f97316', border: '#fb923c', glow: '#f9731655', label: 'Validation' },
  live: { color: '#22c55e', border: '#4ade80', glow: '#22c55e55', label: 'Live' },
  recently_listed: {
    color: '#a855f7',
    border: '#c084fc',
    glow: '#a855f755',
    label: 'Recently Listed',
  },
};

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

export function resolveDiscoverUniverseStage(input: {
  stageBucket: string;
  createdAt: string | Date;
  isLiveToken?: boolean;
}): DiscoverUniverseStage {
  if (input.isLiveToken || input.stageBucket === 'LIVE_TOKEN') return 'live';
  const created = new Date(input.createdAt).getTime();
  const daysSince = (Date.now() - created) / 86_400_000;
  if (daysSince <= 14) return 'recently_listed';
  if (input.stageBucket === 'LAUNCH_READY') return 'validation';
  return 'building';
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

/** Golden-angle layout for stable bubble positions */
export function layoutBubblePositions(
  count: number,
  width: number,
  height: number,
): { x: number; y: number }[] {
  const cx = width / 2;
  const cy = height / 2;
  const golden = Math.PI * (3 - Math.sqrt(5));
  return Array.from({ length: count }, (_, i) => {
    const t = i + 0.5;
    const r = Math.min(width, height) * 0.08 * Math.sqrt(t);
    const angle = t * golden;
    return {
      x: cx + Math.cos(angle) * r,
      y: cy + Math.sin(angle) * r * 0.85,
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
