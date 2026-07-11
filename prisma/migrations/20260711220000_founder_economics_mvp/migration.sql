-- Phase 8 — Founder Economics MVP
-- See services/founder-economics/contracts/README.md and
--     packages/utils/src/founder-economics/
--
-- Five new tables back the automated epoch-based vesting and distribution
-- system (on-chain simple, off-chain swappable):
--   Epoch            — one row per vesting epoch (root + proofDataUri)
--   EpochClaim       — per-founder claim of an epoch allocation (Merkle proof)
--   DDollarGrant     — economic activity grants (positive) + penalties (negative)
--   KnowledgeNode    — knowledge contributions + lineage (parent/child)
--   ProofOfSuccess   — verified real-world milestones (Stripe, GitHub, Vercel, …)
--
-- Idempotent (CREATE TYPE IF NOT EXISTS / CREATE TABLE IF NOT EXISTS) so it
-- is safe to apply on databases that already had the tables created via
-- `prisma db push` during development.

-- Enum: OPEN → SETTLING → PUBLISHED → CLOSED
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'EpochStatus') THEN
        CREATE TYPE "EpochStatus" AS ENUM ('OPEN', 'SETTLING', 'PUBLISHED', 'CLOSED');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS "Epoch" (
    "id"                        TEXT             NOT NULL,
    "epochNumber"               INTEGER          NOT NULL,
    "startTime"                 TIMESTAMP(3)     NOT NULL,
    "endTime"                   TIMESTAMP(3)     NOT NULL,
    "tokensReleased"            INTEGER          NOT NULL DEFAULT 0,
    "merkleRoot"                TEXT,
    "proofDataUri"              TEXT,
    "status"                    "EpochStatus"    NOT NULL DEFAULT 'OPEN',
    "distributionModelVersion"  TEXT,
    "publishTxHash"             TEXT,
    "publishedAt"               TIMESTAMP(3),
    "createdAt"                 TIMESTAMP(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"                 TIMESTAMP(3)     NOT NULL,

    CONSTRAINT "Epoch_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "Epoch_epochNumber_key" ON "Epoch"("epochNumber");
CREATE INDEX IF NOT EXISTS "Epoch_status_idx" ON "Epoch"("status");
CREATE INDEX IF NOT EXISTS "Epoch_epochNumber_idx" ON "Epoch"("epochNumber");

CREATE TABLE IF NOT EXISTS "EpochClaim" (
    "id"            TEXT             NOT NULL,
    "epochId"       TEXT             NOT NULL,
    "userId"        TEXT             NOT NULL,
    "walletAddress" TEXT             NOT NULL,
    "amount"        INTEGER          NOT NULL DEFAULT 0,
    "claimed"       BOOLEAN          NOT NULL DEFAULT false,
    "claimedAt"     TIMESTAMP(3),
    "claimTxHash"   TEXT,
    "merkleProof"   JSONB,
    "createdAt"     TIMESTAMP(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"     TIMESTAMP(3)     NOT NULL,

    CONSTRAINT "EpochClaim_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "EpochClaim_epochId_userId_key" ON "EpochClaim"("epochId", "userId");
CREATE INDEX IF NOT EXISTS "EpochClaim_epochId_idx" ON "EpochClaim"("epochId");
CREATE INDEX IF NOT EXISTS "EpochClaim_userId_idx" ON "EpochClaim"("userId");
CREATE INDEX IF NOT EXISTS "EpochClaim_walletAddress_idx" ON "EpochClaim"("walletAddress");

ALTER TABLE "EpochClaim" DROP CONSTRAINT IF EXISTS "EpochClaim_epochId_fkey";
ALTER TABLE "EpochClaim"
    ADD CONSTRAINT "EpochClaim_epochId_fkey"
    FOREIGN KEY ("epochId") REFERENCES "Epoch"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "EpochClaim" DROP CONSTRAINT IF EXISTS "EpochClaim_userId_fkey";
ALTER TABLE "EpochClaim"
    ADD CONSTRAINT "EpochClaim_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

CREATE TABLE IF NOT EXISTS "DDollarGrant" (
    "id"           TEXT             NOT NULL,
    "userId"       TEXT             NOT NULL,
    "activityType" TEXT             NOT NULL,
    "activityId"   TEXT             NOT NULL,
    "amount"       INTEGER          NOT NULL,
    "proofType"    TEXT,
    "proofData"    JSONB,
    "reverted"     BOOLEAN          NOT NULL DEFAULT false,
    "createdAt"    TIMESTAMP(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "DDollarGrant_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "DDollarGrant_activityType_activityId_userId_key"
    ON "DDollarGrant"("activityType", "activityId", "userId");
CREATE INDEX IF NOT EXISTS "DDollarGrant_userId_createdAt_idx" ON "DDollarGrant"("userId", "createdAt");
CREATE INDEX IF NOT EXISTS "DDollarGrant_activityType_idx" ON "DDollarGrant"("activityType");

ALTER TABLE "DDollarGrant" DROP CONSTRAINT IF EXISTS "DDollarGrant_userId_fkey";
ALTER TABLE "DDollarGrant"
    ADD CONSTRAINT "DDollarGrant_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

CREATE TABLE IF NOT EXISTS "KnowledgeNode" (
    "id"            TEXT             NOT NULL,
    "founderId"     TEXT             NOT NULL,
    "knowledgeType" TEXT             NOT NULL,
    "content"       TEXT             NOT NULL,
    "parentNodeId"  TEXT,
    "impactScore"   INTEGER          NOT NULL DEFAULT 0,
    "createdAt"     TIMESTAMP(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"     TIMESTAMP(3)     NOT NULL,

    CONSTRAINT "KnowledgeNode_pkey" PRIMARY KEY ("id")
);

CREATE INDEX IF NOT EXISTS "KnowledgeNode_founderId_idx" ON "KnowledgeNode"("founderId");
CREATE INDEX IF NOT EXISTS "KnowledgeNode_parentNodeId_idx" ON "KnowledgeNode"("parentNodeId");
CREATE INDEX IF NOT EXISTS "KnowledgeNode_knowledgeType_idx" ON "KnowledgeNode"("knowledgeType");

ALTER TABLE "KnowledgeNode" DROP CONSTRAINT IF EXISTS "KnowledgeNode_founderId_fkey";
ALTER TABLE "KnowledgeNode"
    ADD CONSTRAINT "KnowledgeNode_founderId_fkey"
    FOREIGN KEY ("founderId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "KnowledgeNode" DROP CONSTRAINT IF EXISTS "KnowledgeNode_parentNodeId_fkey";
ALTER TABLE "KnowledgeNode"
    ADD CONSTRAINT "KnowledgeNode_parentNodeId_fkey"
    FOREIGN KEY ("parentNodeId") REFERENCES "KnowledgeNode"("id") ON DELETE SET NULL ON UPDATE CASCADE;

CREATE TABLE IF NOT EXISTS "ProofOfSuccess" (
    "id"             TEXT             NOT NULL,
    "userId"         TEXT             NOT NULL,
    "proofType"      TEXT             NOT NULL,
    "externalId"     TEXT             NOT NULL,
    "verifiedMetric" INTEGER          NOT NULL DEFAULT 0,
    "metricLabel"    TEXT             NOT NULL DEFAULT '',
    "multiplier"     DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    "verifiedData"   JSONB,
    "reverified"     BOOLEAN          NOT NULL DEFAULT false,
    "verifiedAt"     TIMESTAMP(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "createdAt"      TIMESTAMP(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"      TIMESTAMP(3)     NOT NULL,

    CONSTRAINT "ProofOfSuccess_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "ProofOfSuccess_proofType_externalId_userId_key"
    ON "ProofOfSuccess"("proofType", "externalId", "userId");
CREATE INDEX IF NOT EXISTS "ProofOfSuccess_userId_idx" ON "ProofOfSuccess"("userId");
CREATE INDEX IF NOT EXISTS "ProofOfSuccess_proofType_idx" ON "ProofOfSuccess"("proofType");

ALTER TABLE "ProofOfSuccess" DROP CONSTRAINT IF EXISTS "ProofOfSuccess_userId_fkey";
ALTER TABLE "ProofOfSuccess"
    ADD CONSTRAINT "ProofOfSuccess_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
