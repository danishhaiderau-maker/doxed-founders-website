import type { FeedTerminalCardKind, FeedTerminalTab } from './feed-terminal';
import {
  computeMoneyFeedItemScore,
  filterMoneyFeedUnifiedItems,
  isMoneyFeedTerminalKind,
} from './money-feed.js';
import type { UnifiedFeedCategory, UnifiedFeedItem } from './unified-feed';

export type FeedHubUnifiedEntry = {
  kind: 'unified';
  at: string;
  item: UnifiedFeedItem;
};

export type FeedHubTerminalCardInput = {
  id: string;
  kind: FeedTerminalCardKind;
  at: string;
  feedPostId?: string;
};

export type FeedHubTerminalEntry<T extends FeedHubTerminalCardInput = FeedHubTerminalCardInput> = {
  kind: 'terminal';
  at: string;
  card: T;
};

export type FeedHubEntry<T extends FeedHubTerminalCardInput = FeedHubTerminalCardInput> =
  | FeedHubUnifiedEntry
  | FeedHubTerminalEntry<T>;

export function mergeFeedHubEntries<T extends FeedHubTerminalCardInput>(
  unified: UnifiedFeedItem[],
  terminal: T[],
  options: {
    category: UnifiedFeedCategory;
    terminalTab: FeedTerminalTab;
    limit: number;
  },
): FeedHubEntry<T>[] {
  const includeTerminal = options.category === 'all' || options.category === 'trading';
  const terminalPostIds = new Set(
    terminal.map((c) => c.feedPostId).filter((id): id is string => Boolean(id)),
  );

  const moneyUnified =
    options.category === 'founder'
      ? unified
      : filterMoneyFeedUnifiedItems(unified);

  const unifiedFiltered = moneyUnified.filter((item) => {
    if (!includeTerminal) return true;
    if (item.tradePostId && terminalPostIds.has(item.tradePostId)) return false;
    return true;
  });

  const terminalFiltered = includeTerminal
    ? terminal.filter((c) => isMoneyFeedTerminalKind(c.kind))
    : [];

  const entries: FeedHubEntry<T>[] = [
    ...unifiedFiltered.map((item) => ({ kind: 'unified' as const, at: item.at, item })),
    ...terminalFiltered.map((card) => ({ kind: 'terminal' as const, at: card.at, card })),
  ];

  return entries
    .sort((a, b) => {
      const scoreA =
        a.kind === 'unified'
          ? computeMoneyFeedItemScore({
              eventType: a.item.eventType,
              amountUsd: a.item.amountUsd,
              tier: a.item.tier,
              at: a.at,
            })
          : terminalMoneyScore(a.card.kind);
      const scoreB =
        b.kind === 'unified'
          ? computeMoneyFeedItemScore({
              eventType: b.item.eventType,
              amountUsd: b.item.amountUsd,
              tier: b.item.tier,
              at: b.at,
            })
          : terminalMoneyScore(b.card.kind);
      if (scoreB !== scoreA) return scoreB - scoreA;
      return new Date(b.at).getTime() - new Date(a.at).getTime();
    })
    .slice(0, options.limit);
}

function terminalMoneyScore(kind: FeedTerminalCardKind): number {
  const map: Partial<Record<FeedTerminalCardKind, number>> = {
    LISTING: 95,
    HOT_BUY: 88,
    THESIS: 85,
    BUY: 75,
    ADD: 72,
    SELL: 70,
    SMART_EXIT: 68,
    MISSED_ALPHA: 65,
    REDUCE: 60,
    LOSS: 55,
    FOLLOWER_SPIKE: 50,
  };
  return map[kind] ?? 40;
}

export const FEED_HUB_CATEGORIES: {
  id: UnifiedFeedCategory;
  label: string;
  subtitle: string;
}[] = [
  { id: 'all', label: 'Money Feed', subtitle: 'Trades · conviction · listings · markets' },
  { id: 'trading', label: 'Trading Tape', subtitle: 'Buys · sells · adds · thesis' },
  { id: 'market', label: 'Markets', subtitle: 'Predictions · hot buys · listings' },
  { id: 'founder', label: 'Milestones', subtitle: 'Ships & verification only — no commits' },
  { id: 'community', label: 'Scout', subtitle: 'Listing votes & scout markets' },
];
