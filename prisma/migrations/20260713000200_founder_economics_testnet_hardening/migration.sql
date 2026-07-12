-- Founder Economics testnet hardening.
-- Roots now retain their immutable settlement inputs and exact uint256 values;
-- the database lifecycle mirrors EpochDistributor's propose -> challenge ->
-- finalize flow. This migration is deliberately additive for safe rollout.

ALTER TYPE "ChainSlug" ADD VALUE IF NOT EXISTS 'ROBINHOOD_EVM_TESTNET';
ALTER TYPE "EpochStatus" ADD VALUE IF NOT EXISTS 'PROPOSED';
ALTER TYPE "EpochStatus" ADD VALUE IF NOT EXISTS 'FAILED';

ALTER TABLE "Epoch"
    ADD COLUMN IF NOT EXISTS "totalAllocatedRaw" TEXT,
    ADD COLUMN IF NOT EXISTS "modelCodeHash" TEXT,
    ADD COLUMN IF NOT EXISTS "settlementSnapshotHash" TEXT,
    ADD COLUMN IF NOT EXISTS "settlementConfig" JSONB,
    ADD COLUMN IF NOT EXISTS "challengeEndsAt" TIMESTAMP(3),
    ADD COLUMN IF NOT EXISTS "publicationBlockNumber" INTEGER,
    ADD COLUMN IF NOT EXISTS "settlementStartedAt" TIMESTAMP(3),
    ADD COLUMN IF NOT EXISTS "settlementAttemptCount" INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS "settlementNextAttemptAt" TIMESTAMP(3),
    ADD COLUMN IF NOT EXISTS "settlementError" TEXT,
    ADD COLUMN IF NOT EXISTS "finalizedAt" TIMESTAMP(3);

ALTER TABLE "EpochClaim"
    ADD COLUMN IF NOT EXISTS "amountRaw" TEXT NOT NULL DEFAULT '0';

CREATE UNIQUE INDEX IF NOT EXISTS "WalletConnection_chain_address_key"
    ON "WalletConnection"("chain", "address");

CREATE INDEX IF NOT EXISTS "Epoch_challengeEndsAt_idx" ON "Epoch"("challengeEndsAt");
CREATE INDEX IF NOT EXISTS "Epoch_settlementNextAttemptAt_idx" ON "Epoch"("settlementNextAttemptAt");

CREATE TABLE IF NOT EXISTS "DistributionModelApproval" (
    "id"               TEXT         NOT NULL,
    "version"          TEXT         NOT NULL,
    "codeHash"         TEXT         NOT NULL,
    "activationEpoch"  INTEGER      NOT NULL,
    "governanceTxHash" TEXT,
    "approved"         BOOLEAN      NOT NULL DEFAULT true,
    "approvedAt"       TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "revokedAt"        TIMESTAMP(3),
    "createdAt"        TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"        TIMESTAMP(3) NOT NULL,
    CONSTRAINT "DistributionModelApproval_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "DistributionModelApproval_codeHash_key"
    ON "DistributionModelApproval"("codeHash");
CREATE UNIQUE INDEX IF NOT EXISTS "DistributionModelApproval_version_activationEpoch_key"
    ON "DistributionModelApproval"("version", "activationEpoch");
CREATE UNIQUE INDEX IF NOT EXISTS "DistributionModelApproval_governanceTxHash_key"
    ON "DistributionModelApproval"("governanceTxHash")
    WHERE "governanceTxHash" IS NOT NULL;
CREATE INDEX IF NOT EXISTS "DistributionModelApproval_approved_activationEpoch_idx"
    ON "DistributionModelApproval"("approved", "activationEpoch");
