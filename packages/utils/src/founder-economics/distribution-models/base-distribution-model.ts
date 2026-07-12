/**
 * Base DistributionModel interface — the swappable brain of Founder Economics.
 *
 * Architectural rule (Founder Economics MVP):
 *   - The on-chain EpochDistributor has NO idea how a Merkle root was computed.
 *     It only verifies proofs and pays.
 *   - All upgradeable distribution logic implements this interface.
 *   - Swapping models (v1 pro-rata → v2 reputation-weighted → ...) requires
 *     zero contract changes — only the settlement job picks a different model.
 *
 * A model is a pure function: (epoch metadata, ddollar snapshot) → Merkle tree.
 * It must be deterministic so the settlement job can recompute the same root
 * and so governance can audit what produced a given root.
 */

import { TOKEN_UNIT, type MerkleLeaf, type MerkleTree } from '../merkle-tree';

/** Epoch metadata available to every DistributionModel. */
export interface Epoch {
  epochNumber: number;
  startTime: string;
  endTime: string;
  /** Tokens released from the VestingVault for this epoch (raw integer). */
  tokensReleased: number;
  /** Model version that produced the current root (set by settlement job). */
  distributionModelVersion?: string;
}

/**
 * Per-founder snapshot handed to a DistributionModel.
 * - `rawDdollar` is the literal balance (User.reputationPoints at snapshot).
 * - `reputationMultiplier` is the Trust × Longevity × Verification multiplier
 *   (see `reputation-multiplier.ts`) — used by v2+ models, ignored by v1.
 * - `walletAddress` is the leaf key used on-chain (keccak256(account, amount)).
 */
export interface DDollarSnapshot {
  epochNumber: number;
  snapshotAt: string;
  founders: FounderShare[];
}

export interface FounderShare {
  userId: string;
  walletAddress: string;
  rawDdollar: number;
  reputationMultiplier: number;
  /** Optional: builder score 0–100 — used by v2+. */
  builderScore?: number;
  /** Optional: contributor level 1–5 — used by v2+. */
  contributorLevel?: number;
}

/**
 * The contract every distribution model fulfils.
 *
 * `governanceApproved` is a soft flag — the settlement job should refuse to
 * publish a root from a model that hasn't been approved by governance vote.
 */
export interface DistributionModel {
  readonly version: string;
  readonly description: string;
  readonly governanceApproved: boolean;

  /**
   * Compute Merkle shares for an epoch.
   *
   * Returns a Merkle tree whose leaves are keccak256(walletAddress, amount).
   * The settlement job will publish `tree.root` on-chain and the proof data
   * (per-founder leaf + proof path) to IPFS so founders can claim.
   */
  computeShares(epoch: Epoch, snapshot: DDollarSnapshot): MerkleTree;
}

/**
 * Deterministically allocate every raw token unit in an epoch.  We deliberately
 * work in `uint256` units rather than JavaScript numbers so the sum of the
 * Merkle leaves is always exactly equal to the amount funded on-chain.
 *
 * Scores are normalised to 1e9 precision before the bigint calculation. This
 * makes decimal reputation multipliers reproducible across jobs. Any division
 * dust is assigned one raw unit at a time in stable address order.
 */
export function allocateTokenUnits(
  totalWholeTokens: number,
  scoredFounders: Array<{ walletAddress: string; score: number }>,
): MerkleLeaf[] {
  if (!Number.isSafeInteger(totalWholeTokens) || totalWholeTokens <= 0) {
    throw new Error('Epoch token release must be a positive safe whole-token integer');
  }

  const scoreToFixedUnits = (score: number): bigint => {
    if (!Number.isFinite(score) || score <= 0 || score >= 1e21) return 0n;
    // Avoid `BigInt(Math.round(score * SCORE_SCALE))`: the intermediate can
    // exceed Number's safe range for legitimate high-DDollar founders.
    return BigInt(score.toFixed(9).replace('.', ''));
  };
  const candidates = scoredFounders
    .map((founder) => ({
      walletAddress: founder.walletAddress,
      score: scoreToFixedUnits(founder.score),
    }))
    .filter((founder) => founder.walletAddress && founder.score > 0n)
    .sort((a, b) => a.walletAddress.toLowerCase().localeCompare(b.walletAddress.toLowerCase()));

  if (candidates.length === 0) {
    throw new Error('Cannot allocate an epoch without eligible claimant wallets');
  }

  const totalScore = candidates.reduce((sum, founder) => sum + founder.score, 0n);
  if (totalScore === 0n) throw new Error('Epoch claimant scores must be positive');

  const totalUnits = BigInt(totalWholeTokens) * TOKEN_UNIT;
  const leaves = candidates.map((founder) => ({
    walletAddress: founder.walletAddress,
    amount: (totalUnits * founder.score) / totalScore,
  }));
  let remainder = totalUnits - leaves.reduce((sum, leaf) => sum + leaf.amount, 0n);
  for (let index = 0; remainder > 0n; index = (index + 1) % leaves.length) {
    leaves[index]!.amount += 1n;
    remainder -= 1n;
  }
  return leaves;
}
