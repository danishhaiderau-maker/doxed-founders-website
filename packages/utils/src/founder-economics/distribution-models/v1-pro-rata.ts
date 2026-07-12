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
import { allocateTokenUnits } from './base-distribution-model';
import { buildMerkleTree } from '../merkle-tree';

export class V1ProRataDistributionModel implements DistributionModel {
  readonly version = 'v1-pro-rata';
  readonly description =
    'Pro-rata distribution to raw DDollar balance. Simplest baseline — no reputation weighting.';
  readonly governanceApproved = true;

  computeShares(epoch: Epoch, snapshot: DDollarSnapshot) {
    const leaves = allocateTokenUnits(epoch.tokensReleased, snapshot.founders
      .filter((f) => f.rawDdollar > 0 && f.walletAddress)
      .map((f) => ({ walletAddress: f.walletAddress, score: f.rawDdollar })));

    return buildMerkleTree(leaves);
  }
}
