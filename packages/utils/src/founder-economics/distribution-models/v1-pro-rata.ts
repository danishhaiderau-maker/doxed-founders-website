/**
 * V1 Distribution Model — pro-rata to raw DDollar.
 *
 * Simplest possible model: each founder gets a share of the epoch's released
 * tokens proportional to their raw DDollar balance.
 *
 *   founderTokens = (founderDdollar / totalDdollar) * tokensReleased
 *
 * This is the baseline. v2 (reputation-weighted) builds on this by applying
 * the reputation multiplier before normalizing.
 */

import type {
  DistributionModel,
  Epoch,
  DDollarSnapshot,
} from './base-distribution-model';
import { tokensForFounder } from './base-distribution-model';
import { buildMerkleTree } from '../merkle-tree';

export class V1ProRataDistributionModel implements DistributionModel {
  readonly version = 'v1-pro-rata';
  readonly description =
    'Pro-rata distribution to raw DDollar balance. Simplest baseline — no reputation weighting.';
  readonly governanceApproved = true;

  computeShares(epoch: Epoch, snapshot: DDollarSnapshot) {
    const totalDdollar = snapshot.founders.reduce(
      (sum, f) => sum + Math.max(0, f.rawDdollar),
      0,
    );

    const leaves = snapshot.founders
      .filter((f) => f.rawDdollar > 0 && f.walletAddress)
      .map((f) => {
        const ratio = totalDdollar > 0 ? f.rawDdollar / totalDdollar : 0;
        const amount = tokensForFounder(ratio, epoch.tokensReleased);
        return { walletAddress: f.walletAddress, amount };
      })
      .filter((leaf) => leaf.amount > 0);

    return buildMerkleTree(leaves);
  }
}
