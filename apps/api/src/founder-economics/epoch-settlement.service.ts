/**
 * Durable, testnet-only epoch settlement worker.
 *
 * The vault is the source of truth for funding. This worker may compute and
 * propose a governed root for a funded epoch, but it cannot invent funding,
 * choose a model through an environment variable, or turn a proposal into a
 * claimable root. Contract state and receipts remain authoritative.
 */

import { Injectable, Logger } from '@nestjs/common';
import { Cron, CronExpression } from '@nestjs/schedule';
import { Contract, JsonRpcProvider, getAddress, isHexString } from 'ethers';
import { PrismaService } from '../prisma/prisma.service';
import { DdollarEngineService } from './ddollar-engine.service';
import {
  createDefaultPublishers,
  hashProofPayload,
  ROBINHOOD_EVM_TESTNET_CHAIN_ID,
  type MerkleRootPublisher,
  type ProofDataPublisher,
} from './publishers';
import {
  TOKEN_UNIT,
  V1ProRataDistributionModel,
  V2ReputationWeightedDistributionModel,
  computeReputationMultiplier,
  findProof,
  type DistributionModel,
  type Epoch as EpochMeta,
} from '@dcf/utils';

const DISTRIBUTOR_READ_ABI = [
  'function epochs(uint256 epoch) view returns (uint256 funded, uint256 totalClaimed, bytes32 root, bytes32 modelCodeHash, bytes32 proofDataHash, uint64 proposedAt, uint64 finalizedAt, uint8 status)',
  'function modelRegistry() view returns (address)',
  'event Claimed(uint256 indexed epoch, address indexed account, uint256 amount)',
];
const VAULT_READ_ABI = [
  'function releasedEpochs() view returns (uint256)',
  'function championsReleased() view returns (bool)',
  'function startTimestamp() view returns (uint256)',
  'function terminalTimestamp() view returns (uint256)',
];
const MODEL_REGISTRY_READ_ABI = [
  'function isActive(bytes32 codeHash, uint256 epoch) view returns (bool)',
];
const EPOCH_SECONDS = 90 * 24 * 60 * 60;
const CHALLENGE_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;
const STALE_SETTLEMENT_MS = 15 * 60 * 1000;
const MAX_RETRY_DELAY_MS = 60 * 60 * 1000;

type ReadContracts = {
  distributor: Contract;
  vault: Contract;
};

function requiredSetting(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required for Founder Economics testnet operations`);
  return value;
}

function wholeTokensFromRaw(raw: bigint): number {
  if (raw % TOKEN_UNIT !== 0n) {
    throw new Error('VestingVault funding must be whole-token aligned');
  }
  const whole = raw / TOKEN_UNIT;
  if (whole > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('Funded amount exceeds API integer range');
  return Number(whole);
}

function displayWholeTokens(raw: bigint): number {
  const whole = raw / TOKEN_UNIT;
  if (whole > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('Claim amount exceeds API integer range');
  return Number(whole);
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
    const publishers = createDefaultPublishers();
    this.merklePublisher = publishers.merkle;
    this.proofDataPublisher = publishers.proofData;
  }

  /** Polls funding, finalized roots and claims. Safe to run from multiple pods. */
  @Cron(CronExpression.EVERY_5_MINUTES)
  async runSettlementCron() {
    try {
      await this.syncFundedEpochs();
      await this.syncOnChainStates();
      await this.reconcileClaims();
      await this.settleCurrentEpoch();
    } catch (error) {
      this.logger.error(`Epoch worker failed: ${(error as Error).message}`);
    }
  }

  /** Admin-triggerable catch-up; it still uses only on-chain funded epochs. */
  async settleCurrentEpoch(): Promise<{ epochNumber: number; merkleRoot: string; founderCount: number }> {
    await this.requeueStaleSettlements();
    const epoch = await this.claimNextEpoch();
    if (!epoch) return { epochNumber: 0, merkleRoot: '', founderCount: 0 };

    try {
      return await this.settleClaimedEpoch(epoch);
    } catch (error) {
      await this.recoverOrScheduleRetry(epoch.id, error as Error);
      throw error;
    }
  }

  /**
   * Mirrors only epochs actually funded by the deployed VestingVault. The
   * contract validates the transfer and distributor reserve before this code
   * ever sees an epoch.
   */
  async syncFundedEpochs(): Promise<number> {
    const { distributor, vault } = await this.readContracts();
    const releasedEpochs = Number(await vault.releasedEpochs());
    const championsReleased = Boolean(await vault.championsReleased());
    const startTimestamp = Number(await vault.startTimestamp());
    const terminalTimestamp = Number(await vault.terminalTimestamp());
    const epochNumbers = Array.from(
      { length: releasedEpochs + (championsReleased ? 1 : 0) },
      (_, index) => index + 1,
    );

    for (const epochNumber of epochNumbers) {
      const onChain = await distributor.epochs(BigInt(epochNumber));
      const funded = BigInt(onChain.funded);
      if (funded === 0n) continue;
      const start = epochNumber === 41
        ? new Date(terminalTimestamp * 1000)
        : new Date((startTimestamp + (epochNumber - 1) * EPOCH_SECONDS) * 1000);
      const end = epochNumber === 41
        ? new Date(start.getTime() + CHALLENGE_WINDOW_MS)
        : new Date(start.getTime() + EPOCH_SECONDS * 1000);
      await this.prisma.epoch.upsert({
        where: { epochNumber },
        create: {
          epochNumber,
          startTime: start,
          endTime: end,
          tokensReleased: wholeTokensFromRaw(funded),
          totalAllocatedRaw: funded.toString(),
        } as any,
        update: {
          tokensReleased: wholeTokensFromRaw(funded),
          totalAllocatedRaw: funded.toString(),
        } as any,
      });
    }
    return epochNumbers.length;
  }

  /** Finalized means claimable; proposed roots remain visible but unavailable. */
  async syncOnChainStates(): Promise<void> {
    const { distributor } = await this.readContracts();
    const epochs = await this.prisma.epoch.findMany({
      where: { status: { in: ['SETTLING', 'PROPOSED', 'PUBLISHED', 'CLOSED'] as any } },
    });
    for (const epoch of epochs) {
      const onChain = await distributor.epochs(BigInt(epoch.epochNumber));
      const status = Number(onChain.status);
      const proposedAt = Number(onChain.proposedAt);
      const finalizedAt = Number(onChain.finalizedAt);
      const rootMatches = epoch.merkleRoot
        && epoch.merkleRoot.toLowerCase() === String(onChain.root).toLowerCase();

      if ((status === 2 || status === 3 || status === 4) && !rootMatches) {
        await this.prisma.epoch.update({
          where: { id: epoch.id },
          data: {
            status: 'FAILED' as any,
            settlementError: 'On-chain root does not match the persisted settlement snapshot',
          } as any,
        });
        continue;
      }
      if (status === 2) {
        await this.prisma.epoch.update({
          where: { id: epoch.id },
          data: {
            status: 'PROPOSED' as any,
            challengeEndsAt: new Date(proposedAt * 1000 + CHALLENGE_WINDOW_MS),
            settlementError: null,
          } as any,
        });
      } else if (status === 3) {
        await this.prisma.epoch.update({
          where: { id: epoch.id },
          data: {
            status: 'PUBLISHED' as any,
            finalizedAt: new Date(finalizedAt * 1000),
            publishedAt: new Date(finalizedAt * 1000),
            settlementError: null,
          } as any,
        });
      } else if (status === 4) {
        await this.prisma.epoch.update({
          where: { id: epoch.id },
          data: { status: 'CLOSED' as any },
        });
      }
    }
  }

  /** Reconciles only verified `Claimed` logs back into platform records. */
  async reconcileClaims(): Promise<number> {
    const { distributor } = await this.readContracts();
    const published = await this.prisma.epoch.findMany({
      where: { status: 'PUBLISHED' as any, publicationBlockNumber: { not: null } },
    });
    let reconciled = 0;
    for (const epoch of published) {
      const filter = distributor.filters.Claimed(BigInt(epoch.epochNumber));
      const events = await distributor.queryFilter(filter, epoch.publicationBlockNumber!, 'latest');
      for (const event of events) {
        const claimedEvent = event as any;
        const account = String(claimedEvent.args?.account ?? '').toLowerCase();
        const amountRaw = BigInt(claimedEvent.args?.amount ?? 0n).toString();
        const result = await this.prisma.epochClaim.updateMany({
          where: {
            epochId: epoch.id,
            walletAddress: { equals: account, mode: 'insensitive' },
            amountRaw,
            claimed: false,
          } as any,
          data: {
            claimed: true,
            claimedAt: new Date(),
            claimTxHash: claimedEvent.transactionHash,
          },
        });
        reconciled += result.count;
      }
    }
    return reconciled;
  }

  /** Records an on-chain governance approval; this cannot itself approve a model. */
  async syncGovernanceModelApproval(input: {
    version: string;
    codeHash: string;
    activationEpoch: number;
    governanceTxHash?: string;
  }) {
    const model = this.models[input.version];
    if (!model) throw new Error(`No installed distribution model named ${input.version}`);
    if (!isHexString(input.codeHash, 32)) throw new Error('Model codeHash must be a bytes32 hex value');
    if (!Number.isSafeInteger(input.activationEpoch) || input.activationEpoch <= 0) {
      throw new Error('Model activation epoch must be a positive integer');
    }
    const { distributor } = await this.readContracts();
    const registry = new Contract(await distributor.modelRegistry(), MODEL_REGISTRY_READ_ABI, distributor.runner);
    if (!await registry.isActive(input.codeHash, BigInt(input.activationEpoch))) {
      throw new Error('Model is not active in the on-chain governance registry at its activation epoch');
    }
    return this.prisma.distributionModelApproval.upsert({
      where: { codeHash: input.codeHash.toLowerCase() },
      create: {
        version: input.version,
        codeHash: input.codeHash.toLowerCase(),
        activationEpoch: input.activationEpoch,
        governanceTxHash: input.governanceTxHash,
      },
      update: {
        version: input.version,
        activationEpoch: input.activationEpoch,
        governanceTxHash: input.governanceTxHash,
        approved: true,
        revokedAt: null,
      },
    });
  }

  async epochHistory(limit = 25) {
    return this.prisma.epoch.findMany({
      orderBy: { epochNumber: 'desc' },
      take: Math.min(Math.max(Math.floor(limit), 1), 100),
      include: { _count: { select: { claims: true } } },
    });
  }

  async claimableForFounder(userId: string) {
    const claims = await this.prisma.epochClaim.findMany({
      where: {
        userId,
        claimed: false,
        amountRaw: { not: '0' },
        epoch: { status: 'PUBLISHED' as any },
      } as any,
      include: { epoch: true },
    });
    return claims.map((claim) => ({
      epochId: claim.epochId,
      epochNumber: claim.epoch.epochNumber,
      amount: claim.amount,
      amountRaw: claim.amountRaw,
      walletAddress: claim.walletAddress,
      merkleRoot: claim.epoch.merkleRoot,
      merkleProof: claim.merkleProof,
      claimWindowOpen: true,
      claimEndsAt: claim.epoch.finalizedAt
        ? new Date(claim.epoch.finalizedAt.getTime() + 365 * 24 * 60 * 60 * 1000).toISOString()
        : null,
    }));
  }

  private async settleClaimedEpoch(epoch: any) {
    const governed = await this.governedModelForEpoch(epoch.epochNumber);
    const snapshot = await this.ddollarEngine.exportSnapshot(epoch.epochNumber);
    const snapshotWithMultiplier = {
      ...snapshot,
      founders: snapshot.founders.map((founder) => ({
        ...founder,
        reputationMultiplier: computeReputationMultiplier(founder.reputationMultiplierInputs),
      })),
    };
    const epochMeta: EpochMeta = {
      epochNumber: epoch.epochNumber,
      startTime: epoch.startTime.toISOString(),
      endTime: epoch.endTime.toISOString(),
      tokensReleased: epoch.tokensReleased,
      distributionModelVersion: governed.version,
    };
    const tree = governed.model.computeShares(epochMeta, snapshotWithMultiplier);
    const totalAllocatedRaw = tree.leaves.reduce((sum, leaf) => sum + leaf.amount, 0n);
    if (totalAllocatedRaw.toString() !== epoch.totalAllocatedRaw) {
      throw new Error(
        `Merkle leaves (${totalAllocatedRaw}) do not equal on-chain funding (${epoch.totalAllocatedRaw})`,
      );
    }

    const foundersByWallet = new Map(
      snapshotWithMultiplier.founders
        .filter((founder) => founder.walletAddress)
        .map((founder) => [founder.walletAddress.toLowerCase(), founder]),
    );
    const leaves = tree.leaves.map((leaf) => {
      const proof = findProof(tree, leaf.walletAddress, leaf.amount);
      const founder = foundersByWallet.get(leaf.walletAddress.toLowerCase());
      if (!founder || !proof) throw new Error('Merkle leaf lacks a verified claimant or proof');
      return {
        userId: founder.userId,
        walletAddress: leaf.walletAddress,
        amount: displayWholeTokens(leaf.amount),
        amountRaw: leaf.amount.toString(),
        merkleProof: proof.proof,
      };
    });
    const snapshotHash = hashProofPayload(snapshotWithMultiplier);
    const settlementConfig = {
      chainId: ROBINHOOD_EVM_TESTNET_CHAIN_ID,
      modelVersion: governed.version,
      modelCodeHash: governed.codeHash,
      totalAllocatedRaw: totalAllocatedRaw.toString(),
      snapshotHash,
    };

    await this.prisma.epoch.update({
      where: { id: epoch.id },
      data: {
        merkleRoot: tree.root,
        distributionModelVersion: governed.version,
        modelCodeHash: governed.codeHash,
        settlementSnapshotHash: snapshotHash,
        settlementConfig,
        settlementError: null,
      } as any,
    });
    for (const leaf of leaves) {
      await this.prisma.epochClaim.upsert({
        where: { epochId_userId: { epochId: epoch.id, userId: leaf.userId } },
        create: { epochId: epoch.id, ...leaf } as any,
        update: {
          walletAddress: leaf.walletAddress,
          amount: leaf.amount,
          amountRaw: leaf.amountRaw,
          merkleProof: leaf.merkleProof,
          claimed: false,
          claimedAt: null,
          claimTxHash: null,
        } as any,
      });
    }

    const proofPayload = {
      schemaVersion: 1,
      chainId: ROBINHOOD_EVM_TESTNET_CHAIN_ID,
      epochNumber: epoch.epochNumber,
      merkleRoot: tree.root,
      totalAllocatedRaw: totalAllocatedRaw.toString(),
      model: { version: governed.version, codeHash: governed.codeHash },
      snapshotHash,
      snapshot: snapshotWithMultiplier,
      leaves: leaves.map((leaf) => ({
        walletAddress: leaf.walletAddress,
        amountRaw: leaf.amountRaw,
        proof: leaf.merkleProof,
      })),
    };
    const proofResult = await this.proofDataPublisher.publish(epoch.epochNumber, proofPayload);
    await this.prisma.epoch.update({
      where: { id: epoch.id },
      data: {
        proofDataUri: proofResult.uri,
        settlementConfig: { ...settlementConfig, proofDataHash: proofResult.contentHash },
      } as any,
    });
    const publication = await this.merklePublisher.propose({
      epochNumber: epoch.epochNumber,
      root: tree.root,
      totalAllocatedRaw,
      modelCodeHash: governed.codeHash,
      proofDataHash: proofResult.contentHash,
    });
    await this.prisma.epoch.update({
      where: { id: epoch.id },
      data: {
        status: 'PROPOSED' as any,
        publishTxHash: publication.txHash,
        publicationBlockNumber: publication.blockNumber,
        challengeEndsAt: publication.challengeEndsAt,
        settlementStartedAt: null,
        settlementNextAttemptAt: null,
        settlementError: null,
      } as any,
    });
    this.logger.log(`Epoch ${epoch.epochNumber} proposed with ${leaves.length} claim leaves`);
    return { epochNumber: epoch.epochNumber, merkleRoot: tree.root, founderCount: leaves.length };
  }

  private async governedModelForEpoch(epochNumber: number): Promise<{
    version: string;
    codeHash: string;
    model: DistributionModel;
  }> {
    const candidates = await this.prisma.distributionModelApproval.findMany({
      where: { approved: true, revokedAt: null, activationEpoch: { lte: epochNumber } },
      orderBy: [{ activationEpoch: 'desc' }, { approvedAt: 'desc' }],
    });
    const { distributor } = await this.readContracts();
    const registry = new Contract(await distributor.modelRegistry(), MODEL_REGISTRY_READ_ABI, distributor.runner);
    for (const candidate of candidates) {
      const model = this.models[candidate.version];
      if (model && await registry.isActive(candidate.codeHash, BigInt(epochNumber))) {
        return { version: candidate.version, codeHash: candidate.codeHash, model };
      }
    }
    throw new Error(`No on-chain governance-approved distribution model is active for epoch ${epochNumber}`);
  }

  private async claimNextEpoch() {
    const now = new Date();
    const candidate = await this.prisma.epoch.findFirst({
      where: {
        status: { in: ['OPEN', 'FAILED'] as any },
        OR: [
          { settlementNextAttemptAt: null },
          { settlementNextAttemptAt: { lte: now } },
        ],
      } as any,
      orderBy: { epochNumber: 'asc' },
    });
    if (!candidate) return null;
    const claim = await this.prisma.epoch.updateMany({
      where: { id: candidate.id, status: { in: ['OPEN', 'FAILED'] as any } } as any,
      data: {
        status: 'SETTLING' as any,
        settlementStartedAt: now,
        settlementAttemptCount: { increment: 1 },
        settlementNextAttemptAt: null,
        settlementError: null,
      } as any,
    });
    return claim.count === 1 ? this.prisma.epoch.findUnique({ where: { id: candidate.id } }) : null;
  }

  private async requeueStaleSettlements() {
    const staleBefore = new Date(Date.now() - STALE_SETTLEMENT_MS);
    await this.prisma.epoch.updateMany({
      where: { status: 'SETTLING' as any, settlementStartedAt: { lt: staleBefore } } as any,
      data: {
        status: 'FAILED' as any,
        settlementNextAttemptAt: new Date(),
        settlementError: 'Recovered a settlement left in progress by a stopped worker',
      } as any,
    });
  }

  private async recoverOrScheduleRetry(epochId: string, error: Error) {
    await this.syncOnChainStates();
    const current = await this.prisma.epoch.findUnique({ where: { id: epochId } });
    if (!current || current.status === ('PROPOSED' as any) || current.status === ('PUBLISHED' as any)) return;
    const attempt = Math.max(current.settlementAttemptCount, 1);
    const delay = Math.min(2 ** Math.min(attempt, 10) * 60_000, MAX_RETRY_DELAY_MS);
    await this.prisma.epoch.update({
      where: { id: epochId },
      data: {
        status: 'FAILED' as any,
        settlementStartedAt: null,
        settlementNextAttemptAt: new Date(Date.now() + delay),
        settlementError: error.message.slice(0, 2_000),
      } as any,
    });
  }

  private async readContracts(): Promise<ReadContracts> {
    const provider = new JsonRpcProvider(requiredSetting('FOUNDER_ECONOMICS_RPC_URL'));
    const network = await provider.getNetwork();
    if (Number(network.chainId) !== ROBINHOOD_EVM_TESTNET_CHAIN_ID) {
      throw new Error(
        `Refusing Founder Economics operation on chain ${network.chainId}; expected ${ROBINHOOD_EVM_TESTNET_CHAIN_ID}`,
      );
    }
    return {
      distributor: new Contract(
        getAddress(requiredSetting('FOUNDER_ECONOMICS_DISTRIBUTOR_ADDRESS')),
        DISTRIBUTOR_READ_ABI,
        provider,
      ),
      vault: new Contract(
        getAddress(requiredSetting('FOUNDER_ECONOMICS_VESTING_VAULT_ADDRESS')),
        VAULT_READ_ABI,
        provider,
      ),
    };
  }
}
