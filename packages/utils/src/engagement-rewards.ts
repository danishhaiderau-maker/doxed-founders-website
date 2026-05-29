/** Daily engagement lottery — paper cash for active community members */
export const ENGAGEMENT_LOTTERY_WINNER_RATE = 0.002; // 0.2% of active users per day
export const ENGAGEMENT_LOTTERY_MIN_PRIZE_USD = 500;
export const ENGAGEMENT_LOTTERY_MAX_PRIZE_USD = 2_000;
export const ENGAGEMENT_ACTIVITY_WINDOW_HOURS = 24;

/** Activity weights for lottery selection (higher = better odds) */
export const ACTIVITY_WEIGHTS = {
  PAPER_TRADE: 3,
  FEED_COMMENT: 5,
  COMMUNITY_COMMENT: 5,
  COMMUNITY_THREAD: 8,
  BUILD_POST: 10,
  DEMAND_POLL_VOTE: 4,
  LISTING_VOTE: 6,
  RAISE_ALLOCATE: 4,
  PROJECT_FOLLOW: 2,
} as const;

export function engagementLotteryWinnerCount(activeUsers: number): number {
  if (activeUsers <= 0) return 0;
  return Math.max(1, Math.ceil(activeUsers * ENGAGEMENT_LOTTERY_WINNER_RATE));
}

export function randomEngagementPrizeUsd(): number {
  const min = ENGAGEMENT_LOTTERY_MIN_PRIZE_USD;
  const max = ENGAGEMENT_LOTTERY_MAX_PRIZE_USD;
  return Math.floor(min + Math.random() * (max - min + 1));
}

export function pickWeightedWinners(
  entries: { userId: string; score: number }[],
  count: number,
): string[] {
  const pool = entries.filter((e) => e.score > 0);
  const winners: string[] = [];
  for (let i = 0; i < count && pool.length > 0; i++) {
    const total = pool.reduce((s, e) => s + e.score, 0);
    let r = Math.random() * total;
    let idx = 0;
    for (let j = 0; j < pool.length; j++) {
      r -= pool[j]!.score;
      if (r <= 0) {
        idx = j;
        break;
      }
    }
    winners.push(pool[idx]!.userId);
    pool.splice(idx, 1);
  }
  return winners;
}
