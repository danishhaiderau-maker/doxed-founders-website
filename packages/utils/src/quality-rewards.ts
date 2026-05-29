import { ENGAGEMENT_LOTTERY_WINNER_RATE } from './engagement-rewards';

/** Quality weights — validated usefulness, not raw spam */
export const QUALITY_WEIGHTS = {
  HELPFUL_MARK: 50,
  BOUNTY_AWARDED: 100,
  CONVICTION_WITH_THESIS: 30,
  EARLY_SCOUT: 40,
  BUILD_POST: 15,
  FOUNDER_VIDEO: 12,
  DEMAND_POLL_VOTE: 8,
  LISTING_VOTE: 10,
  RAISE_ALLOCATE: 6,
  /** Raw comments without helpful mark — eligibility only, near-zero lottery weight */
  RAW_COMMENT: 1,
} as const;

/** Top 0.2% of quality scorers enter the daily reward pool */
export function qualityTierPoolSize(rankedUsers: number): number {
  if (rankedUsers <= 0) return 0;
  return Math.max(1, Math.ceil(rankedUsers * ENGAGEMENT_LOTTERY_WINNER_RATE));
}

export function takeTopQualityTier<T extends { score: number }>(
  entries: T[],
  tierSize: number,
): T[] {
  const sorted = [...entries].filter((e) => e.score > 0).sort((a, b) => b.score - a.score);
  return sorted.slice(0, tierSize);
}

/** Simple spam heuristic — short / repetitive comments score 0 for quality */
export function isLikelySpamComment(body: string): boolean {
  const t = body.trim().toLowerCase().replace(/\s+/g, ' ');
  if (t.length < 12) return true;
  const spamPatterns = [
    /^(bullish|lfg|moon|gm|wagmi|nice|great project|amazing founder)[!.?\s]*$/,
    /^[\W\d\s]+$/,
  ];
  return spamPatterns.some((p) => p.test(t));
}
