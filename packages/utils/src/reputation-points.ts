export const POINTS = {
  REGISTER: 50,
  PAPER_TRADE: 10,
  FEED_COMMENT: 5,
  LISTING_SUBMIT: 25,
  WATCHLIST_ADD: 2,
} as const;

export function contributorLevelFromPoints(points: number): number {
  if (points >= 5000) return 5;
  if (points >= 2000) return 4;
  if (points >= 800) return 3;
  if (points >= 250) return 2;
  return 1;
}

export function contributorLevelLabel(level: number): string {
  const labels: Record<number, string> = {
    1: 'Newcomer',
    2: 'Contributor',
    3: 'Analyst',
    4: 'Signal',
    5: 'Believer',
  };
  return labels[level] ?? 'Newcomer';
}

export function extractTwitterHandle(url: string | null | undefined): string | null {
  if (!url?.trim()) return null;
  const match = url.trim().match(/(?:twitter\.com|x\.com)\/([A-Za-z0-9_]+)/i);
  return match?.[1] ?? null;
}
