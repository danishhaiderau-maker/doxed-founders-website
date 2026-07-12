/**
 * DexRouter — Phase 8 swap surface with a Jupiter-ready interface.
 *
 * DexStubService implements this today (fixed-price demo swaps). A future
 * JupiterDexRouter can implement the same contract against Jupiter quote/swap
 * APIs without changing TokenLaunchController call sites.
 *
 * Spec rule: no production Solana mainnet deploys from this interface —
 * implementations must stay on demos / stubs / explicit devnet until counsel.
 */
export type DexQuote = {
  launchId: string;
  priceUsd: number;
  feeBps: number;
  live: boolean;
  /** Router identity for UI badges. */
  router: 'stub' | 'jupiter';
  /** Human-readable note (e.g. "fixed price — Jupiter pending"). */
  note?: string;
};

export type DexSwapResult = {
  swapId: string;
  inputAmount: number;
  outputAmount: number;
  feeUsd: number;
  priceUsd: number;
  router: 'stub' | 'jupiter';
  /** Present when an on-chain / Jupiter tx id exists. */
  txSignature?: string;
};

export type DexVolume = {
  totalInputUsd: number;
  totalOutputTokens: number;
  totalFeeUsd: number;
  swapCount: number;
};

export interface DexRouter {
  readonly routerId: 'stub' | 'jupiter';
  getPrice(launchId: string): Promise<DexQuote>;
  swap(
    launchId: string,
    userId: string | null,
    inputAmount: number,
  ): Promise<DexSwapResult>;
  getVolume(launchId: string): Promise<DexVolume>;
}

/** Injection token for the active DexRouter. */
export const DEX_ROUTER = Symbol('DEX_ROUTER');
