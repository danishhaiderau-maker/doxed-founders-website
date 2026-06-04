import type { FeedTerminalCardKind, FeedTerminalTab } from './feed-terminal';
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

  const unifiedFiltered = unified.filter((item) => {
    if (!includeTerminal) return true;
    if (item.tradePostId && terminalPostIds.has(item.tradePostId)) return false;
    return true;
  });

  const entries: FeedHubEntry<T>[] = [
    ...unifiedFiltered.map((item) => ({ kind: 'unified' as const, at: item.at, item })),
    ...(includeTerminal
      ? terminal.map((card) => ({ kind: 'terminal' as const, at: card.at, card }))
      : []),
  ];

  return entries
    .sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime())
    .slice(0, options.limit);
}

export const FEED_HUB_CATEGORIES: {
  id: UnifiedFeedCategory;
  label: string;
  subtitle: string;
}[] = [
  { id: 'all', label: 'All', subtitle: 'Platform-wide stream' },
  { id: 'trading', label: 'Trading', subtitle: 'Paper trades & conviction' },
  { id: 'founder', label: 'Founders', subtitle: 'Builds, deploys, GitHub' },
  { id: 'market', label: 'Market', subtitle: 'Hot buys & listings' },
  { id: 'community', label: 'Community', subtitle: 'Votes, scouts, chat' },
];
