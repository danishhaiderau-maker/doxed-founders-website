/** Social Conviction Terminal — tab filters & card kinds */

export type FeedTerminalTab = 'all' | 'trades' | 'conviction' | 'movers' | 'regret' | 'activity';

export type FeedTerminalCardKind =
  | 'BUY'
  | 'SELL'
  | 'ADD'
  | 'REDUCE'
  | 'THESIS'
  | 'NEW_THESIS'
  | 'MISSED_ALPHA'
  | 'SMART_EXIT'
  | 'LOSS'
  | 'FOLLOWER_SPIKE'
  | 'LISTING'
  | 'VALIDATION'
  | 'MAJOR_UPDATE'
  | 'HOT_BUY';

const TRADES = new Set<FeedTerminalCardKind>(['BUY', 'SELL', 'ADD', 'REDUCE']);
const CONVICTION = new Set<FeedTerminalCardKind>(['THESIS', 'NEW_THESIS', 'HOT_BUY']);
const MOVERS = new Set<FeedTerminalCardKind>(['HOT_BUY', 'FOLLOWER_SPIKE']);
const REGRET = new Set<FeedTerminalCardKind>(['MISSED_ALPHA', 'SMART_EXIT', 'LOSS']);
const ACTIVITY = new Set<FeedTerminalCardKind>(['LISTING', 'VALIDATION', 'MAJOR_UPDATE']);

export function feedCardMatchesTab(kind: FeedTerminalCardKind, tab: FeedTerminalTab): boolean {
  if (tab === 'all') return true;
  if (tab === 'trades') return TRADES.has(kind);
  if (tab === 'conviction') return CONVICTION.has(kind);
  if (tab === 'movers') return MOVERS.has(kind);
  if (tab === 'regret') return REGRET.has(kind);
  if (tab === 'activity') return ACTIVITY.has(kind);
  return true;
}

export const FEED_TERMINAL_TABS: {
  id: FeedTerminalTab;
  label: string;
  icon: string;
  subtitle: string;
}[] = [
  { id: 'all', label: 'All Activity', icon: '🔥', subtitle: 'Everything' },
  { id: 'trades', label: 'Trades', icon: '💰', subtitle: 'Buys & Sells' },
  { id: 'conviction', label: 'Conviction', icon: '🧠', subtitle: 'Thesis & Calls' },
  { id: 'movers', label: 'Movers', icon: '📈', subtitle: 'Top Trending' },
  { id: 'regret', label: 'Regret', icon: '😅', subtitle: 'Missed Alpha' },
  { id: 'activity', label: 'Activity', icon: '⚡', subtitle: 'Updates' },
];

export function feedCardKindLabel(kind: FeedTerminalCardKind): string {
  const labels: Record<FeedTerminalCardKind, string> = {
    BUY: 'BUY',
    SELL: 'SELL',
    ADD: 'ADD',
    REDUCE: 'REDUCE',
    THESIS: 'NEW THESIS',
    NEW_THESIS: 'NEW THESIS',
    MISSED_ALPHA: 'SOLD TOO EARLY',
    SMART_EXIT: 'SMART EXIT',
    LOSS: 'LOSS',
    FOLLOWER_SPIKE: 'FOLLOWERS',
    LISTING: 'LISTING',
    VALIDATION: 'VALIDATION',
    MAJOR_UPDATE: 'MILESTONE',
    HOT_BUY: 'HOT BUY',
  };
  return labels[kind] ?? kind;
}

export function feedCardKindAccent(kind: FeedTerminalCardKind): string {
  if (['BUY', 'ADD', 'SMART_EXIT', 'HOT_BUY'].includes(kind)) return 'emerald';
  if (['SELL', 'REDUCE'].includes(kind)) return 'orange';
  if (['MISSED_ALPHA', 'LOSS'].includes(kind)) return 'red';
  if (['THESIS', 'NEW_THESIS'].includes(kind)) return 'violet';
  if (['FOLLOWER_SPIKE'].includes(kind)) return 'sky';
  return 'zinc';
}
