/** Total token supply at launch (1B tokens, $1B FDV at $1/token). */
export const TOKEN_SUPPLY = 1_000_000_000;

/** Share of total supply reserved for community airdrop. */
export const AIRDROP_SUPPLY_PERCENT = 10;

/** Tokens in the community airdrop pool (10% of 1B). */
export const AIRDROP_TOKEN_POOL = (TOKEN_SUPPLY * AIRDROP_SUPPLY_PERCENT) / 100;

/** Implied FDV at launch ($1 per token). */
export const LAUNCH_FDV_USD = TOKEN_SUPPLY;

/** USD value of the airdrop pool at launch. */
export const AIRDROP_POOL_USD = (LAUNCH_FDV_USD * AIRDROP_SUPPLY_PERCENT) / 100;

export type AirdropAllocation = {
  reputationPoints: number;
  /** Share of the 100M-token airdrop pool (0–100). */
  airdropPoolPercent: number;
  /** Share of total 1B supply (0–10). */
  supplyPercent: number;
  estimatedTokens: number;
  estimatedUsd: number;
};

export function computeAirdropAllocation(
  userPoints: number,
  totalPoints: number,
): AirdropAllocation {
  if (userPoints <= 0 || totalPoints <= 0) {
    return {
      reputationPoints: userPoints,
      airdropPoolPercent: 0,
      supplyPercent: 0,
      estimatedTokens: 0,
      estimatedUsd: 0,
    };
  }

  const share = userPoints / totalPoints;
  const estimatedTokens = share * AIRDROP_TOKEN_POOL;
  const tokenPriceUsd = LAUNCH_FDV_USD / TOKEN_SUPPLY;

  return {
    reputationPoints: userPoints,
    airdropPoolPercent: share * 100,
    supplyPercent: share * AIRDROP_SUPPLY_PERCENT,
    estimatedTokens,
    estimatedUsd: estimatedTokens * tokenPriceUsd,
  };
}

export function formatTokenAmount(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${(tokens / 1_000_000).toFixed(2)}M`;
  }
  if (tokens >= 1_000) {
    return `${(tokens / 1_000).toFixed(1)}K`;
  }
  return tokens.toLocaleString(undefined, { maximumFractionDigits: 0 });
}
