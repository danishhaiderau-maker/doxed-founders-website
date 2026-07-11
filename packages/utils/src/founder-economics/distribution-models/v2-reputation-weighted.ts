/**
 * V2 Distribution Model — reputation-weighted pro-rata.
 *
 * Each founder's DDollar is multiplied by their reputation multiplier
 * (Trust × Longevity × Verification, see `reputation-multiplier.ts`) before
 * normalizing into shares.
 *
 *   weightedDdollar = rawDdollar * reputationMultiplier
 *   founderTokens = (weightedDdollar / sum(weightedDdollar)) * tokensReleased
 *
 * This rewards founders who have earned trust and verification — not just raw
 * activity volume. The multiplier is the same `trustWeight` reused across the
 * platform (see `packages/utils/src/trust-weight.ts`), so this model is
 * consistent with the rest of the anti-sybil machinery.
 */

import type {
  DistributionModel,
  Epoch,
  DDollarSnapshot,
  FounderShare,
} from './base-distribution-model';
import { tokensForFounder } from './base-distribution-model';
import { buildMerkleTree } from '../merkle-tree';

export class V2ReputationWeightedDistributionModel implements DistributionModel {
  readonly version = 'v2-reputation-weighted';
  readonly description =
    'Pro-rata to reputation-weighted DDollar — Trust × Longevity × Verification. Rewards earned trust, not just raw activity.';
  readonly governanceApproved = false;

  /**
   * Compute weighted DDollar for a single founder.
   * Exposed so the settlement job can show the breakdown in the dashboard
   * without recomputing.
   */
  static weightedDdollar(f: FounderShare): number {
    const multiplier = Math.max(0, f.reputationMultiplier);
    return Math.max(0, f.rawDdollar) * multiplier;
  }

  computeShares(epoch: Epoch, snapshot: DDollarSnapshot) {
    const weighted = snapshot.founders
      .filter((f) => f.rawDdollar > 0 && f.walletAddress)
      .map((f) => ({
        founder: f,
        weighted: V2ReputationWeightedDistributionModel.weightedDdollar(f),
      }));

    const totalWeighted = weighted.reduce((sum, w) => sum + w.weighted, 0);

    const leaves = weighted
      .map((w) => {
        const ratio = totalWeighted > 0 ? w.weighted / totalWeighted : 0;
        const amount = tokensForFounder(ratio, epoch.tokensReleased);
        return { walletAddress: w.founder.walletAddress, amount };
      })
      .filter((leaf) => leaf.amount > 0);

    return buildMerkleTree(leaves);
  }
}
