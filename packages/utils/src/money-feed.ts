import type { FeedTerminalCardKind } from './feed-terminal.js';
import type { UnifiedFeedItem } from './unified-feed.js';

/** Founder OS = Build · Discover = Research · Feed = Money */
export const MONEY_FEED_TAGLINE = 'Feed = Money';

/** Unified event types allowed on the main Money Feed (category all/trading/market). */
export const MONEY_FEED_UNIFIED_EVENT_TYPES = new Set([
  'listing_live',
  'hot_buy',
  'top_trader_buy',
  'scout_vote_opened',
  'hot_prediction',
  'prediction_staked',
  'prediction_resolved',
  'watchlist_surge',
  'conviction_posted',
  'position_opened',
  'position_closed',
  'position_added',
  'position_reduced',
  'founder_verified',
  'project_shipped',
  'raise_opened',
  'token_launch',
  'demand_allocated',
]);

const BLOCKED_UNIFIED = new Set([
  'github_milestone',
  'build_update',
  'deployment',
  'founder_video',
  'founder_x_update',
]);

const MONEY_TERMINAL_KINDS = new Set<FeedTerminalCardKind>([
  'BUY',
  'SELL',
  'ADD',
  'REDUCE',
  'THESIS',
  'NEW_THESIS',
  'MISSED_ALPHA',
  'SMART_EXIT',
  'LOSS',
  'HOT_BUY',
  'LISTING',
  'FOLLOWER_SPIKE',
]);

const FOUNDER_MILESTONE_UNIFIED = new Set([
  'founder_verified',
  'project_shipped',
  'listing_live',
]);

const MAJOR_SHIP_KEYWORDS =
  /\b(mainnet|testnet launch|public launch|product launch|shipped to production|live on chain|token launch|launched)\b/i;

export function isMajorShipHeadline(text: string): boolean {
  return MAJOR_SHIP_KEYWORDS.test(text);
}

export function isMoneyFeedUnifiedEvent(eventType: string): boolean {
  if (BLOCKED_UNIFIED.has(eventType)) return false;
  return MONEY_FEED_UNIFIED_EVENT_TYPES.has(eventType);
}

export function isFounderMilestoneUnifiedEvent(eventType: string): boolean {
  return FOUNDER_MILESTONE_UNIFIED.has(eventType) || eventType === 'project_shipped';
}

export function isMoneyFeedTerminalKind(kind: FeedTerminalCardKind): boolean {
  return MONEY_TERMINAL_KINDS.has(kind);
}

export function filterMoneyFeedUnifiedItems(items: UnifiedFeedItem[]): UnifiedFeedItem[] {
  return items.filter((i) => isMoneyFeedUnifiedEvent(i.eventType));
}

export function filterFounderMilestoneItems(items: UnifiedFeedItem[]): UnifiedFeedItem[] {
  return items.filter((i) => isFounderMilestoneUnifiedEvent(i.eventType));
}

/** Feed bubble score — trading & conviction weighted (not raw GitHub). */
export type FeedBubbleActivityInput = {
  tradesVolume: number;
  tradesInflow: number;
  comments: number;
  predictionVotes: number;
  watchlists: number;
  shares: number;
  mentions: number;
  founderMilestones: number;
};

export function computeFeedBubbleActivityScore(input: FeedBubbleActivityInput): number {
  const raw =
    Math.min(input.tradesVolume / 800, 40) +
    Math.min(input.watchlists * 3, 20) +
    Math.min(input.predictionVotes * 2, 15) +
    Math.min(input.founderMilestones * 10, 10) +
    Math.min(input.comments * 2, 10) +
    Math.min(input.shares * 3, 5) +
    Math.min(input.mentions, 5) +
    Math.min(input.tradesInflow / 600, 15);
  return Math.round(Math.min(100, Math.max(0, raw)));
}

/** Sort key: Feed Score = 40% trading … (see product spec). */
export function computeMoneyFeedItemScore(input: {
  eventType: string;
  amountUsd?: number;
  tier: number;
  at: string;
}): number {
  const ageH = (Date.now() - new Date(input.at).getTime()) / 3_600_000;
  const recency = Math.max(0, 48 - ageH) / 48;

  let base = 10;
  if (
    ['hot_buy', 'top_trader_buy', 'listing_live', 'conviction_posted'].includes(input.eventType)
  ) {
    base = 90;
  } else if (
    ['position_opened', 'position_closed', 'position_added', 'position_reduced'].includes(
      input.eventType,
    )
  ) {
    base = 70 + Math.min(20, (input.amountUsd ?? 0) / 500);
  } else if (['hot_prediction', 'prediction_staked', 'prediction_resolved'].includes(input.eventType)) {
    base = 55;
  } else if (['watchlist_surge', 'scout_vote_opened'].includes(input.eventType)) {
    base = 45;
  } else if (input.eventType === 'founder_verified' || input.eventType === 'project_shipped') {
    base = 35;
  } else {
    base = 20 - input.tier * 3;
  }

  return base + recency * 25;
}
