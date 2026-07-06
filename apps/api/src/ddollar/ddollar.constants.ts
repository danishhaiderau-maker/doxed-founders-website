export function isDdollarRuntimeEnabled(): boolean {
  return process.env.DDOLLAR_RUNTIME_ENABLED === 'true';
}

/** Platform fee fraction retained in founder treasury on marketplace spends. */
export const MARKETPLACE_TREASURY_FEE_BPS = 1000; // 10%

export const DDOLLAR_ACTION_KEYS = {
  AI_SPEND: 'AI_SPEND',
  RAISE_BURN: 'RAISE_BURN',
  MARKETPLACE_PURCHASE: 'MARKETPLACE_PURCHASE',
  TREASURY_FEE: 'TREASURY_FEE',
  DAILY_EMISSION: 'DAILY_EMISSION',
} as const;

export type DdollarWalletSnapshot = {
  userId: string;
  spendableBalance: number;
  lifetimeContributionEarned: number;
  contributorLevel: number;
};

export type DdollarTreasuryAudit = {
  totalInflowDdollar: number;
  entryCount: number;
  recentEntries: {
    id: string;
    amountDdollar: number;
    actionKey: string;
    label: string;
    createdAt: string;
  }[];
};
