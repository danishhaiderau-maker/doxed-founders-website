/**
 * Epoch Settlement Job — the automated brain of Founder Economics.
 *
 * Runs on a schedule (cron) each epoch. For testing: every 5 minutes.
 * For production: every 90 days (matching VestingVault.epochSeconds).
 *
 * Pipeline:
 *   1. Ensure the current Epoch row exists (create from schedule).
 *   2. If the epoch has ended → mark SETTLING.
 *   3. Export the DDollar snapshot from DdollarEngineService.
 *   4. Run the active DistributionModel → compute shares → build Merkle tree.
 *   5. Persist an EpochClaim row per founder (with proof path).
 *   6. Publish the Merkle root via MerkleRootPublisher (on-chain when env
 *      credentials exist; otherwise an offline receipt — see publishers.ts).
 *   7. Publish proof data via ProofDataPublisher (IPFS when configured).
 *   8. Mark the Epoch PUBLISHED and bump `distributionModelVersion`.
 *   9. Open the next Epoch row.
 *
 * The active DistributionModel is selected by env `FOUNDER_ECONOMICS_MODEL`
 * (default: `v1-pro-rata`). Swapping models requires zero contract changes —
 * only this env var changes, and the next settlement publishes a root from
 * the new model.
 */

import { Injectable, Logger } from '@nestjs/common';
import { Cron, CronExpression } from '@nestjs/schedule';
import { PrismaService } from '../prisma/prisma.service';
import { DdollarEngineService } from './ddollar-engine.service';
import {
  createDefaultPublishers,
  type MerkleRootPublisher,
  type ProofDataPublisher,
} from './publishers';
import {
  V1ProRataDistributionModel,
  V2ReputationWeightedDistributionModel,
  computeReputationMultiplier,
  findProof,
  type DistributionModel,
  type Epoch as EpochMeta,
} from '@dcf/utils';

/** Tokens released per epoch — matches VestingVault.releasePerEpoch (raw integer). */
const DEFAULT_TOKENS_PER_EPOCH = 20_000_000;
/** Epoch length for the cron schedule (production = 90 days). */
const PRODUCTION_EPOCH_SECONDS = 90 * 24 * 60 * 60;
/** Test epoch length — every 5 minutes when FOUNDER_ECONOMICS_TEST_EPOCH=1. */
const TEST_EPOCH_SECONDS = 5 * 60;

function isTestEpochMode(): boolean {
  return process.env.FOUNDER_ECONOMICS_TEST_EPOCH === '1';
}

function epochSeconds(): number {
  return isTestEpochMode() ? TEST_EPOCH_SECONDS : PRODUCTION_EPOCH_SECONDS;
}

function tokensPerEpoch(): number {
  const env = Number(process.env.FOUNDER_ECONOMICS_TOKENS_PER_EPOCH);
  return Number.isFinite(env) && env > 0 ? env : DEFAULT_TOKENS_PER_EPOCH;
}

@Injectable()
export class EpochSettlementService {
  private readonly logger = new Logger(EpochSettlementService.name);
  private readonly models: Record<string, DistributionModel>;
  private readonly merklePublisher: MerkleRootPublisher;
  private readonly proofDataPublisher: ProofDataPublisher;

  constructor(
    private readonly prisma: PrismaService,
    private readonly ddollarEngine: DdollarEngineService,
  ) {
    this.models = {
      'v1-pro-rata': new V1ProRataDistributionModel(),
      'v2-reputation-weighted': new V2ReputationWeightedDistributionModel(),
    };
    const pubs = createDefaultPublishers();
    this.merklePublisher = pubs.merkle;
    this.proofDataPublisher = pubs.proofData;
  }

  /** Cron: every 5 minutes in test mode, otherwise hourly check. */
  @Cron(CronExpression.EVERY_5_MINUTES)
  async runSettlementCron() {
    if (!isTestEpochMode()) {
      const latest = await this.prisma.epoch.findFirst({
        orderBy: { epochNumber: 'desc' },
      });
      if (!latest) return;
      const boundaryMs = latest.endTime.getTime();
      if (Date.now() < boundaryMs) return;
    }
    try {
      await this.settleCurrentEpoch();
    } catch (err) {
      this.logger.error(`Epoch settlement failed: ${(err as Error).message}`);
    }
  }

  /** Public trigger — used by the controller's manual settle endpoint. */
  async settleCurrentEpoch(): Promise<{ epochNumber: number; merkleRoot: string; founderCount: number }> {
    const epoch = await this.ensureCurrentEpoch();
    if (epoch.status === 'PUBLISHED' || epoch.status === 'CLOSED') {
      return {
        epochNumber: epoch.epochNumber,
        merkleRoot: epoch.merkleRoot ?? '',
        founderCount: 0,
      };
    }

    await this.prisma.epoch.update({
      where: { id: epoch.id },
      data: { status: 'SETTLING' },
    });

    const tokensReleased = tokensPerEpoch();
    const snapshot = await this.ddollarEngine.exportSnapshot(epoch.epochNumber);

    const snapshotWithMultiplier = {
      ...snapshot,
      founders: snapshot.founders.map((f) => ({
        ...f,
        reputationMultiplier: computeReputationMultiplier(f.reputationMultiplierInputs),
      })),
    };

    const modelKey = process.env.FOUNDER_ECONOMICS_MODEL ?? 'v1-pro-rata';
    const model = this.models[modelKey] ?? this.models['v1-pro-rata'];
    if (!model.governanceApproved) {
      this.logger.warn(
        `Distribution model ${modelKey} is not governance-approved; refusing to publish root.`,
      );
      await this.prisma.epoch.update({
        where: { id: epoch.id },
        data: { status: 'OPEN' },
      });
      throw new Error(
        `Distribution model ${modelKey} is not governance-approved. Set FOUNDER_ECONOMICS_MODEL to an approved model.`,
      );
    }
    const epochMeta: EpochMeta = {
      epochNumber: epoch.epochNumber,
      startTime: epoch.startTime.toISOString(),
      endTime: epoch.endTime.toISOString(),
      tokensReleased,
    };
    const tree = model.computeShares(epochMeta, snapshotWithMultiplier);

    const claimRows = tree.leaves.map((leaf) => {
      const proof = findProof(tree, leaf.walletAddress, leaf.amount);
      const founder = snapshotWithMultiplier.founders.find(
        (f) => f.walletAddress.toLowerCase() === leaf.walletAddress,
      );
      return {
        epochId: epoch.id,
        userId: founder?.userId ?? '',
        walletAddress: leaf.walletAddress,
        amount: leaf.amount,
        merkleProof: proof?.proof ?? [],
      };
    }).filter((row) => row.userId && row.amount > 0);

    for (const row of claimRows) {
      await this.prisma.epochClaim.upsert({
        where: { epochId_userId: { epochId: row.epochId, userId: row.userId } },
        create: row,
        update: { amount: row.amount, merkleProof: row.merkleProof },
      });
    }

    const merkleResult = await this.merklePublisher.publish(
      epoch.epochNumber,
      tree.root,
      tokensReleased,
    );
    const proofResult = await this.proofDataPublisher.publish(epoch.epochNumber, {
      merkleRoot: tree.root,
      modelVersion: model.version,
      founderCount: claimRows.length,
      tokensReleased,
      leaves: tree.leaves.map((l) => ({ wallet: l.walletAddress, amount: l.amount })),
    });
    this.logger.log(
      `Epoch ${epoch.epochNumber} publish: merkle=${merkleResult.mode} (${merkleResult.detail.slice(0, 80)}) proof=${proofResult.mode}`,
    );

    const updated = await this.prisma.epoch.update({
      where: { id: epoch.id },
      data: {
        status: 'PUBLISHED',
        merkleRoot: tree.root,
        proofDataUri: proofResult.uri,
        publishTxHash: merkleResult.txHash,
        publishedAt: new Date(),
        distributionModelVersion: model.version,
        tokensReleased,
      },
    });

    await this.openNextEpoch(updated.epochNumber);

    this.logger.log(
      `Epoch ${updated.epochNumber} published: root=${tree.root} founders=${claimRows.length} model=${model.version}`,
    );

    return {
      epochNumber: updated.epochNumber,
      merkleRoot: tree.root,
      founderCount: claimRows.length,
    };
  }

  private async ensureCurrentEpoch() {
    const latest = await this.prisma.epoch.findFirst({
      orderBy: { epochNumber: 'desc' },
    });
    if (!latest) {
      return this.openNextEpoch(-1);
    }
    if (Date.now() >= latest.endTime.getTime()) {
      return latest;
    }
    return latest;
  }

  private async openNextEpoch(prevEpochNumber: number) {
    const nextNumber = prevEpochNumber + 1;
    const startTime = new Date();
    const endTime = new Date(startTime.getTime() + epochSeconds() * 1000);
    return this.prisma.epoch.upsert({
      where: { epochNumber: nextNumber },
      create: {
        epochNumber: nextNumber,
        startTime,
        endTime,
        tokensReleased: tokensPerEpoch(),
      },
      update: {},
    });
  }

  async epochHistory(limit = 25) {
    return this.prisma.epoch.findMany({
      orderBy: { epochNumber: 'desc' },
      take: limit,
      include: { _count: { select: { claims: true } } },
    });
  }

  async claimableForFounder(userId: string) {
    const claims = await this.prisma.epochClaim.findMany({
      where: { userId, claimed: false, amount: { gt: 0 } },
      include: { epoch: true },
    });
    return claims.map((c) => ({
      epochId: c.epochId,
      epochNumber: c.epoch.epochNumber,
      amount: c.amount,
      walletAddress: c.walletAddress,
      merkleRoot: c.epoch.merkleRoot,
      merkleProof: c.merkleProof,
      claimWindowOpen: c.epoch.status === 'PUBLISHED',
    }));
  }
}
