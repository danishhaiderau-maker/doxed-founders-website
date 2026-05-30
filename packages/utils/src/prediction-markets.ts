export const PREDICTION_MARKET_HOURS = 48;
export const PREDICTION_MARKET_MIN_STAKE_USD = 10;

export type PredictionMarketSource = 'AI' | 'USER' | 'DEFAULT';

export type PredictionQuestionInput = {
  projectName: string;
  ticker: string;
  founderName?: string | null;
  priceChange24h?: number | null;
  marketCap?: number | null;
  liquidityUsd?: number | null;
  volume24h?: number | null;
  verificationScore?: number;
  isNewListing?: boolean;
  isLiveToken?: boolean;
};

export function computePredictionHeatScore(totalPoolUsd: number, participantCount: number): number {
  return Math.round(totalPoolUsd * 2 + participantCount * 75);
}

export function predictionHeatLabel(
  totalPoolUsd: number,
  participantCount: number,
): 'Blazing' | 'Heating up' | null {
  const score = computePredictionHeatScore(totalPoolUsd, participantCount);
  if (totalPoolUsd >= 100 || score >= 200) return 'Blazing';
  if (totalPoolUsd >= 25 || participantCount >= 2 || score >= 75) return 'Heating up';
  return null;
}

export function sortPredictionMarketsByHeat<
  T extends { totalPoolUsd: number; participantCount: number; createdAt?: string | null },
>(markets: T[]): T[] {
  return [...markets].sort((a, b) => {
    const heatA = computePredictionHeatScore(a.totalPoolUsd, a.participantCount);
    const heatB = computePredictionHeatScore(b.totalPoolUsd, b.participantCount);
    if (heatB !== heatA) return heatB - heatA;
    const atA = a.createdAt ? new Date(a.createdAt).getTime() : 0;
    const atB = b.createdAt ? new Date(b.createdAt).getTime() : 0;
    return atB - atA;
  });
}

/** Rule-based “AI” questions from DexScreener-style market data — no external API required. */
export function generatePredictionQuestions(input: PredictionQuestionInput): string[] {
  const { projectName, ticker } = input;
  const pct = input.priceChange24h ?? 0;
  const mcap = input.marketCap ?? 0;
  const live = input.isLiveToken ?? false;
  const questions: string[] = [];

  if (live) {
    if (pct >= 15) {
      questions.push(`Will ${ticker} pull back 30%+ after this ${Math.round(pct)}% 24h run?`);
    } else if (pct <= -15) {
      questions.push(`Will ${ticker} bounce 50%+ after this ${Math.round(Math.abs(pct))}% dip?`);
    } else {
      questions.push(`Will ${ticker} move 25%+ in either direction within 48 hours?`);
    }

    if (mcap >= 1_000_000) {
      questions.push(`Will ${ticker} hold above $1M market cap through this 48h window?`);
    } else if (mcap > 0 && mcap < 500_000) {
      questions.push(`Will ${projectName} (${ticker}) cross $1M market cap this week?`);
    }

    if (input.liquidityUsd != null && input.liquidityUsd > 0) {
      const floor = Math.max(25_000, Math.round(input.liquidityUsd * 0.7));
      questions.push(`Will ${ticker} liquidity stay above $${Math.round(floor / 1000)}k through this window?`);
    }
  } else if (input.isNewListing) {
    questions.push(
      `New listing: will ${ticker} gain another 100% from here within 48 hours?`,
      `Will ${ticker} see a 50%+ correction from the listing pump in the next 48 hours?`,
    );
  } else if (pct >= 15) {
    questions.push(`Will ${ticker} pull back 30%+ after this ${Math.round(pct)}% 24h run?`);
  } else if (pct <= -15) {
    questions.push(`Will ${ticker} bounce 50%+ after this ${Math.round(Math.abs(pct))}% dip?`);
  } else {
    questions.push(`Will ${ticker} move 25%+ in either direction within 48 hours?`);
  }

  if (!live) {
    if (mcap > 0 && mcap < 500_000) {
      questions.push(`Will ${projectName} (${ticker}) cross $1M market cap this week?`);
    } else if (mcap >= 500_000) {
      questions.push(`Will ${ticker} double its market cap within 7 days?`);
    }
  }

  if (input.founderName?.trim()) {
    const founder = input.founderName.trim();
    questions.push(
      `Will founder ${founder} ship a public build update in the next 48 hours?`,
      `Is ${founder} doxxed and credible enough to drive mass adoption for ${projectName}?`,
    );
  }

  questions.push(`Is ${projectName} a real product — not just a token narrative?`);

  if (!live && input.liquidityUsd != null && input.liquidityUsd < 75_000) {
    questions.push(`Will ${ticker} liquidity stay above $50k through this window?`);
  }

  const unique = [...new Set(questions.map((q) => q.trim()))];
  return unique.slice(0, 5);
}

export function generateDefaultScoutQuestions(isLiveToken: boolean): string[] {
  const base = [
    'Will this project reach launch ready?',
    'Will the Raise Room fill before deadline?',
  ];
  if (isLiveToken) {
    return [...base, 'Will trading volume stay strong through the next 48 hours?'];
  }
  return [...base, 'Will this project ship a token in the next 90 days?'];
}

export function computeParimutuelPayout(
  stakeUsd: number,
  winningSidePoolUsd: number,
  totalPoolUsd: number,
): number {
  if (winningSidePoolUsd <= 0 || totalPoolUsd <= 0 || stakeUsd <= 0) return 0;
  return Math.round((stakeUsd / winningSidePoolUsd) * totalPoolUsd * 100) / 100;
}

export function predictionMarketOutcome(yesPoolUsd: number, noPoolUsd: number): boolean {
  if (yesPoolUsd === noPoolUsd) return yesPoolUsd >= noPoolUsd;
  return yesPoolUsd > noPoolUsd;
}

export function hoursUntilResolve(resolvesAt: string | Date | null): number | null {
  if (!resolvesAt) return null;
  const ms = new Date(resolvesAt).getTime() - Date.now();
  if (ms <= 0) return 0;
  return Math.ceil(ms / (60 * 60 * 1000));
}
