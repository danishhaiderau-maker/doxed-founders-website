export type RiskInputs = {
  isDoxxedCurated?: boolean;
  userBelievesFounderDoxxed?: boolean;
  liquidityUsd?: number | null;
  marketCap?: number | null;
  volume24h?: number | null;
  hasWebsite?: boolean;
  hasTwitter?: boolean;
};

export type RiskAssessment = {
  score: number;
  label: 'Lower risk' | 'Medium risk' | 'High risk';
  factors: string[];
  userAdjusted: boolean;
};

export function computeTokenRiskScore(input: RiskInputs): RiskAssessment {
  let score = 5;
  const factors: string[] = [];

  if (input.isDoxxedCurated) {
    score -= 2;
    factors.push('Platform-verified doxxed founder (−2)');
  } else {
    score += 2;
    factors.push('Not a verified doxxed-founder project (+2)');
  }

  const liq = input.liquidityUsd ?? 0;
  if (liq < 50_000) {
    score += 2;
    factors.push('Low liquidity under $50K (+2)');
  } else if (liq < 200_000) {
    score += 1;
    factors.push('Moderate liquidity under $200K (+1)');
  }

  const mc = input.marketCap ?? 0;
  if (mc > 0 && mc < 500_000) {
    score += 1;
    factors.push('Small market cap under $500K (+1)');
  }

  if (!input.hasWebsite) {
    score += 0.5;
    factors.push('No website listed (+0.5)');
  }
  if (!input.hasTwitter) {
    score += 0.5;
    factors.push('No X / Twitter listed (+0.5)');
  }

  let userAdjusted = false;
  if (input.userBelievesFounderDoxxed && !input.isDoxxedCurated) {
    score -= 1;
    userAdjusted = true;
    factors.push('Your opinion: founder appears doxxed (−1, not platform verified)');
  }

  score = Math.max(1, Math.min(10, Math.round(score)));

  const label =
    score <= 3 ? 'Lower risk' : score <= 6 ? 'Medium risk' : 'High risk';

  return { score, label, factors, userAdjusted };
}

export function riskScoreColor(score: number): 'green' | 'amber' | 'red' {
  if (score <= 3) return 'green';
  if (score <= 6) return 'amber';
  return 'red';
}
