/**
 * Verified trader metrics from platform-recorded paper trades (not self-reported).
 * Wins/losses come from realized PnL on SELL legs; optional TP/SL classify exit quality.
 */

export type VerifiedTradeOutcome =
  | 'WIN'
  | 'LOSS'
  | 'BREAKEVEN'
  | 'TARGET_HIT'
  | 'STOP_HIT';

export type ClosedVerifiedTrade = {
  id: string;
  closedAt: string;
  entryPriceUsd: number;
  exitPriceUsd: number;
  investedUsd: number;
  realizedPnlUsd: number;
  takeProfitUsd?: number | null;
  stopLossUsd?: number | null;
  peakPriceUsd?: number | null;
};

export type TraderVerifiedStats = {
  verifiedTrades: number;
  wins: number;
  losses: number;
  breakeven: number;
  winRatePct: number;
  profitFactor: number | null;
  averageRR: number | null;
  averageWinUsd: number;
  averageLossUsd: number;
  netPnlUsd: number;
  roiPct: number;
  roi30dPct: number | null;
  maxDrawdownPct: number;
  consistencyScore: number;
  traderScore: number;
  targetHitRatePct: number | null;
  stopHitRatePct: number | null;
};

export type TraderScoreWeights = {
  roi: number;
  profitFactor: number;
  consistency: number;
  verifiedTrades: number;
  winRate: number;
};

export const DEFAULT_TRADER_SCORE_WEIGHTS: TraderScoreWeights = {
  roi: 0.4,
  profitFactor: 0.25,
  consistency: 0.15,
  verifiedTrades: 0.1,
  winRate: 0.1,
};

const PNL_EPS = 0.5;

/** Default protective stop when user does not set one (8% below entry). */
export function defaultStopLossUsd(entryPriceUsd: number): number {
  return Math.max(entryPriceUsd * 0.0001, entryPriceUsd * 0.92);
}

/** Default take-profit when user sets target on trade (uses their target). */
export function resolveTakeProfitUsd(
  _entryPriceUsd: number,
  explicitTarget?: number | null,
): number | null {
  if (explicitTarget != null && explicitTarget > 0) return explicitTarget;
  return null;
}

/**
 * Classify a closed round-trip. Profit/loss is from platform math (realized PnL), not user claims.
 */
export function verifyClosedTrade(trade: ClosedVerifiedTrade): {
  outcome: VerifiedTradeOutcome;
  achievedRR: number | null;
  plannedRiskUsd: number;
  plannedRewardUsd: number | null;
} {
  const entry = Math.max(trade.entryPriceUsd, 1e-12);
  const exit = Math.max(trade.exitPriceUsd, 0);
  const stop = trade.stopLossUsd ?? defaultStopLossUsd(entry);
  const tp = trade.takeProfitUsd ?? null;

  const stopDist = Math.max(entry - stop, entry * 0.02);
  const qty = trade.investedUsd / entry;
  const plannedRiskUsd = Math.max(1, stopDist * qty);

  let plannedRewardUsd: number | null = null;
  if (tp != null && tp > entry) {
    plannedRewardUsd = Math.max(0, (tp - entry) * qty);
  }

  const achievedRR =
    plannedRiskUsd > 0 ? trade.realizedPnlUsd / plannedRiskUsd : null;

  let outcome: VerifiedTradeOutcome;
  if (trade.realizedPnlUsd > PNL_EPS) {
    if (tp != null && exit >= tp * 0.985) outcome = 'TARGET_HIT';
    else outcome = 'WIN';
  } else if (trade.realizedPnlUsd < -PNL_EPS) {
    if (stop > 0 && exit <= stop * 1.015) outcome = 'STOP_HIT';
    else outcome = 'LOSS';
  } else {
    outcome = 'BREAKEVEN';
  }

  return { outcome, achievedRR, plannedRiskUsd, plannedRewardUsd };
}

function profitFactorFromTrades(trades: ClosedVerifiedTrade[]): number | null {
  let grossWin = 0;
  let grossLoss = 0;
  for (const t of trades) {
    if (t.realizedPnlUsd > PNL_EPS) grossWin += t.realizedPnlUsd;
    else if (t.realizedPnlUsd < -PNL_EPS) grossLoss += Math.abs(t.realizedPnlUsd);
  }
  if (grossLoss <= 0) return grossWin > 0 ? 99 : null;
  return Math.round((grossWin / grossLoss) * 100) / 100;
}

function maxDrawdownPctFromEquityCurve(pnls: number[], startingEquity: number): number {
  if (pnls.length === 0) return 0;
  let peak = startingEquity;
  let equity = startingEquity;
  let maxDd = 0;
  for (const pnl of pnls) {
    equity += pnl;
    if (equity > peak) peak = equity;
    if (peak > 0) {
      const dd = ((peak - equity) / peak) * 100;
      if (dd > maxDd) maxDd = dd;
    }
  }
  return Math.round(maxDd * 10) / 10;
}

function consistencyScoreFromReturns(returnPcts: number[]): number {
  if (returnPcts.length < 3) return 50;
  const mean = returnPcts.reduce((a, b) => a + b, 0) / returnPcts.length;
  const variance =
    returnPcts.reduce((s, r) => s + (r - mean) ** 2, 0) / returnPcts.length;
  const std = Math.sqrt(variance);
  const cv = Math.abs(mean) > 0.1 ? std / Math.abs(mean) : std;
  return Math.max(0, Math.min(100, Math.round(100 - cv * 25)));
}

function normalizeScore(value: number, cap: number): number {
  if (cap <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((value / cap) * 100)));
}

/**
 * Composite 0–100 trader score (higher = better verified track record).
 */
export function computeTraderScore(
  stats: Pick<
    TraderVerifiedStats,
    | 'roiPct'
    | 'profitFactor'
    | 'consistencyScore'
    | 'verifiedTrades'
    | 'winRatePct'
  >,
  weights: TraderScoreWeights = DEFAULT_TRADER_SCORE_WEIGHTS,
): number {
  const roiComponent = normalizeScore(Math.max(0, stats.roiPct), 150);
  const pf =
    stats.profitFactor == null
      ? 0
      : normalizeScore(Math.min(stats.profitFactor, 4), 4) * 100;
  const pfComponent = stats.profitFactor == null ? 0 : Math.min(100, pf);
  const consistencyComponent = stats.consistencyScore;
  const volumeComponent = normalizeScore(stats.verifiedTrades, 80);
  const winRateComponent = normalizeScore(stats.winRatePct, 70);

  const score =
    weights.roi * roiComponent +
    weights.profitFactor * pfComponent +
    weights.consistency * consistencyComponent +
    weights.verifiedTrades * volumeComponent +
    weights.winRate * winRateComponent;

  return Math.max(0, Math.min(100, Math.round(score)));
}

export function computeTraderVerifiedStats(input: {
  closedTrades: ClosedVerifiedTrade[];
  startingCashUsd: number;
  currentRoiPct: number;
  roi30dPct?: number | null;
}): TraderVerifiedStats {
  const trades = [...input.closedTrades].sort(
    (a, b) => new Date(a.closedAt).getTime() - new Date(b.closedAt).getTime(),
  );

  if (trades.length === 0) {
    return {
      verifiedTrades: 0,
      wins: 0,
      losses: 0,
      breakeven: 0,
      winRatePct: 0,
      profitFactor: null,
      averageRR: null,
      averageWinUsd: 0,
      averageLossUsd: 0,
      netPnlUsd: 0,
      roiPct: input.currentRoiPct,
      roi30dPct: input.roi30dPct ?? null,
      maxDrawdownPct: 0,
      consistencyScore: 50,
      traderScore: computeTraderScore({
        roiPct: Math.max(0, input.currentRoiPct),
        profitFactor: null,
        consistencyScore: 50,
        verifiedTrades: 0,
        winRatePct: 0,
      }),
      targetHitRatePct: null,
      stopHitRatePct: null,
    };
  }

  let wins = 0;
  let losses = 0;
  let breakeven = 0;
  let targetHits = 0;
  let stopHits = 0;
  const rrSamples: number[] = [];
  const winAmounts: number[] = [];
  const lossAmounts: number[] = [];
  const returnPcts: number[] = [];
  let netPnl = 0;

  for (const t of trades) {
    const v = verifyClosedTrade(t);
    netPnl += t.realizedPnlUsd;
    const retPct =
      t.investedUsd > 0 ? (t.realizedPnlUsd / t.investedUsd) * 100 : 0;
    returnPcts.push(retPct);

    if (v.achievedRR != null && Number.isFinite(v.achievedRR)) {
      rrSamples.push(v.achievedRR);
    }

    switch (v.outcome) {
      case 'WIN':
      case 'TARGET_HIT':
        wins += 1;
        winAmounts.push(t.realizedPnlUsd);
        if (v.outcome === 'TARGET_HIT') targetHits += 1;
        break;
      case 'LOSS':
      case 'STOP_HIT':
        losses += 1;
        lossAmounts.push(t.realizedPnlUsd);
        if (v.outcome === 'STOP_HIT') stopHits += 1;
        break;
      default:
        breakeven += 1;
    }
  }

  const verifiedTrades = trades.length;
  const winRatePct =
    verifiedTrades > 0 ? Math.round((wins / verifiedTrades) * 1000) / 10 : 0;
  const profitFactor = profitFactorFromTrades(trades);
  const averageRR =
    rrSamples.length > 0
      ? Math.round((rrSamples.reduce((a, b) => a + b, 0) / rrSamples.length) * 100) / 100
      : null;
  const averageWinUsd =
    winAmounts.length > 0
      ? Math.round((winAmounts.reduce((a, b) => a + b, 0) / winAmounts.length) * 100) / 100
      : 0;
  const averageLossUsd =
    lossAmounts.length > 0
      ? Math.round((lossAmounts.reduce((a, b) => a + b, 0) / lossAmounts.length) * 100) / 100
      : 0;

  const maxDrawdownPct = maxDrawdownPctFromEquityCurve(
    trades.map((t) => t.realizedPnlUsd),
    input.startingCashUsd,
  );
  const consistencyScore = consistencyScoreFromReturns(returnPcts);

  const base = {
    verifiedTrades,
    wins,
    losses,
    breakeven,
    winRatePct,
    profitFactor,
    averageRR,
    averageWinUsd,
    averageLossUsd,
    netPnlUsd: Math.round(netPnl * 100) / 100,
    roiPct: input.currentRoiPct,
    roi30dPct: input.roi30dPct ?? null,
    maxDrawdownPct,
    consistencyScore,
    targetHitRatePct:
      wins > 0 ? Math.round((targetHits / wins) * 1000) / 10 : null,
    stopHitRatePct:
      losses > 0 ? Math.round((stopHits / losses) * 1000) / 10 : null,
    traderScore: 0,
  };

  base.traderScore = computeTraderScore(base);
  return base;
}
