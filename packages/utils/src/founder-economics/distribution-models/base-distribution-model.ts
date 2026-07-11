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

import type { MerkleTree } from '../merkle-tree';

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
 * Helper — normalize raw integer DDollar to a whole-token amount for an epoch.
 * Rounds down to whole tokens (no fractional claims on-chain).
 */
export function tokensForFounder(
  shareRatio: number,
  tokensReleased: number,
): number {
  if (tokensReleased <= 0 || shareRatio <= 0) return 0;
  return Math.floor(shareRatio * tokensReleased);
}
