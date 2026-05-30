export type UnifiedFeedCategory = 'all' | 'founder' | 'trading' | 'community' | 'market';

export type UnifiedFeedTier = 1 | 2 | 3;

export type UnifiedFeedItem = {
  id: string;
  tier: UnifiedFeedTier;
  category: 'founder' | 'trading' | 'community' | 'market';
  eventType: string;
  headline: string;
  detail?: string;
  at: string;
  link?: string;
  emoji?: string;
  tradePostId?: string;
  projectSlug?: string;
  projectTicker?: string;
  founderSlug?: string;
  amountUsd?: number;
};

export type PlatformPulseItem = {
  id: string;
  emoji: string;
  headline: string;
  detail?: string;
  link?: string;
  tier: UnifiedFeedTier;
};

const TIER1 = new Set([
  'scout_vote_opened',
  'raise_opened',
  'token_launch',
  'hot_buy',
  'top_trader_buy',
]);

const TIER2 = new Set([
  'build_update',
  'founder_video',
  'github_milestone',
  'deployment',
  'demand_allocated',
  'conviction_posted',
]);

export function unifiedFeedTier(eventType: string): UnifiedFeedTier {
  if (TIER1.has(eventType)) return 1;
  if (TIER2.has(eventType)) return 2;
  return 3;
}

export function sortUnifiedFeedItems(items: UnifiedFeedItem[]): UnifiedFeedItem[] {
  return [...items].sort((a, b) => {
    if (a.tier !== b.tier) return a.tier - b.tier;
    return new Date(b.at).getTime() - new Date(a.at).getTime();
  });
}
