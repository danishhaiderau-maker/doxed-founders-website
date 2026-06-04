/**
 * Prediction Market Constitution — governance constants and Oracle Rank math.
 * Public conviction markets tied to founders, projects, and execution — not generic gambling.
 */

export const PREDICTION_MARKET_CREATE_COST_DD = 10;
export const PREDICTION_MARKET_MIN_STAKE_DD = 10;

export type PredictionConvictionLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'EXTREME';

export const PREDICTION_CONVICTION_MULTIPLIERS: Record<PredictionConvictionLevel, number> = {
  LOW: 0.75,
  MEDIUM: 1,
  HIGH: 1.35,
  EXTREME: 1.75,
};

export type PredictionMarketCategory =
  | 'FOUNDER'
  | 'PROJECT'
  | 'BUILDER'
  | 'LAUNCH'
  | 'TRADING_CONVICTION';

export type PredictionResolutionType =
  | 'AUTOMATIC_PRICE'
  | 'AUTOMATIC_MCAP'
  | 'AUTOMATIC_GITHUB'
  | 'COMMUNITY_SCOUT'
  | 'ADMIN_DISPUTE';

/** Implied probability of the side that actually won (0–1). Lower = harder call. */
export function predictionDifficultyWeight(impliedWinProbability: number): number {
  const p = Math.min(0.99, Math.max(0.01, impliedWinProbability));
  return Math.round((1 - p) * 100) / 100;
}

/**
 * Oracle Rank score component for one resolved market position.
 * Not raw win rate — rewards correct calls on low-implied outcomes and conviction alignment.
 */
export function computeOraclePositionPoints(input: {
  won: boolean;
  stakeUsd: number;
  impliedWinProbability: number;
  conviction?: PredictionConvictionLevel;
}): number {
  const difficulty = predictionDifficultyWeight(input.impliedWinProbability);
  const stakeFactor = Math.min(3, Math.max(0.5, input.stakeUsd / 100));
  const conviction =
    PREDICTION_CONVICTION_MULTIPLIERS[input.conviction ?? 'MEDIUM'];
  const base = difficulty * stakeFactor * conviction * 100;
  if (input.won) return Math.round(base * 10) / 10;
  return -Math.round(base * 5) / 10;
}

export type OracleLeaderboardRow = {
  userId: string;
  displayName: string;
  marketsWon: number;
  marketsLost: number;
  marketsPlayed: number;
  accuracyPct: number;
  oracleScore: number;
  netDdollarUsd: number;
  avgDifficulty: number;
};

export function buildOracleLeaderboard(
  rows: Array<{
    userId: string;
    displayName: string;
    won: boolean;
    stakeUsd: number;
    impliedWinProbability: number;
    payoutUsd?: number;
    conviction?: PredictionConvictionLevel;
  }>,
): OracleLeaderboardRow[] {
  const byUser = new Map<
    string,
    {
      displayName: string;
      wins: number;
      losses: number;
      score: number;
      net: number;
      difficulties: number[];
    }
  >();

  for (const r of rows) {
    let bucket = byUser.get(r.userId);
    if (!bucket) {
      bucket = {
        displayName: r.displayName,
        wins: 0,
        losses: 0,
        score: 0,
        net: 0,
        difficulties: [],
      };
      byUser.set(r.userId, bucket);
    }
    const pts = computeOraclePositionPoints({
      won: r.won,
      stakeUsd: r.stakeUsd,
      impliedWinProbability: r.impliedWinProbability,
      conviction: r.conviction,
    });
    bucket.score += pts;
    bucket.difficulties.push(predictionDifficultyWeight(r.impliedWinProbability));
    if (r.won) {
      bucket.wins += 1;
      bucket.net += (r.payoutUsd ?? 0) - r.stakeUsd;
    } else {
      bucket.losses += 1;
      bucket.net -= r.stakeUsd;
    }
  }

  return [...byUser.entries()]
    .map(([userId, b]) => {
      const played = b.wins + b.losses;
      return {
        userId,
        displayName: b.displayName,
        marketsWon: b.wins,
        marketsLost: b.losses,
        marketsPlayed: played,
        accuracyPct:
          played > 0 ? Math.round((b.wins / played) * 1000) / 10 : 0,
        oracleScore: Math.round(b.score),
        netDdollarUsd: Math.round(b.net * 100) / 100,
        avgDifficulty:
          b.difficulties.length > 0
            ? Math.round(
                (b.difficulties.reduce((a, c) => a + c, 0) / b.difficulties.length) *
                  100,
              ) / 100
            : 0,
      };
    })
    .filter((r) => r.marketsPlayed > 0)
    .sort((a, b) => b.oracleScore - a.oracleScore || b.accuracyPct - a.accuracyPct);
}

/** Composite trader score weights (separate leaderboard — paper trading). */
export const TRADER_SCORE_FORMULA_NOTE =
  'ROI 40% · Profit factor 25% · Consistency 15% · Verified trades 10% · Win rate 10%';

export const ORACLE_SCORE_FORMULA_NOTE =
  'Per market: (1 − implied win probability) × stake weight × conviction multiplier. Wins add points; losses subtract at half weight. Easy 99% calls score almost nothing.';
