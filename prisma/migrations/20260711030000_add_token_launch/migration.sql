-- Phase 8 - Raise Room - Token Launch flow.
-- See docs/RAISE_ROOM_LAUNCH_FLOW.md.
--
-- Three new tables back the flagship revenue flow:
--   TokenLaunch  - one row per project launch (pledging -> window -> live -> closed)
--   TokenPledge  - community DDollar pledges (escrowed, refunded if launch never goes live)
--   DexSwap      - DEX swap ledger (0.1% fee accrues to PlatformTreasury)
--
-- Idempotent (CREATE TYPE IF NOT EXISTS / CREATE TABLE IF NOT EXISTS / DO-block
-- constraint adds) so it is safe to apply on databases that already had the
-- tables created via `prisma db push` during development.

-- Enum: PLEDGING -> WINDOW_OPEN -> LIVE -> CLOSED
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'TokenLaunchStatus') THEN
        CREATE TYPE "TokenLaunchStatus" AS ENUM ('PLEDGING', 'WINDOW_OPEN', 'LIVE', 'CLOSED');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS "TokenLaunch" (
    "id"                  TEXT              NOT NULL,
    "projectId"           TEXT              NOT NULL,
    "launchDate"          TIMESTAMP(3),
    "solanaMint"          TEXT,
    "supply"              BIGINT            NOT NULL DEFAULT 1000000000,
    "initialPrice"        DECIMAL(24,12)    NOT NULL DEFAULT 0.0001,
    "pledgeThresholdMet"  BOOLEAN           NOT NULL DEFAULT false,
    "status"              "TokenLaunchStatus" NOT NULL DEFAULT 'PLEDGING',
    "pledgePoolPercent"   INTEGER           NOT NULL DEFAULT 5,
    "windowClosesAt"      TIMESTAMP(3),
    "finalizedAt"         TIMESTAMP(3),
    "closedAt"            TIMESTAMP(3),
    "closeReason"         TEXT,
    "createdAt"           TIMESTAMP(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"           TIMESTAMP(3)      NOT NULL,

    CONSTRAINT "TokenLaunch_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "TokenLaunch_projectId_key" ON "TokenLaunch"("projectId");
CREATE INDEX IF NOT EXISTS "TokenLaunch_status_idx" ON "TokenLaunch"("status");
CREATE INDEX IF NOT EXISTS "TokenLaunch_pledgeThresholdMet_idx" ON "TokenLaunch"("pledgeThresholdMet");
CREATE INDEX IF NOT EXISTS "TokenLaunch_windowClosesAt_idx" ON "TokenLaunch"("windowClosesAt");

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'TokenLaunch_projectId_fkey') THEN
        ALTER TABLE "TokenLaunch"
            ADD CONSTRAINT "TokenLaunch_projectId_fkey"
            FOREIGN KEY ("projectId") REFERENCES "Project"("id")
            ON DELETE CASCADE ON UPDATE CASCADE;
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS "TokenPledge" (
    "id"              TEXT             NOT NULL,
    "launchId"        TEXT             NOT NULL,
    "userId"          TEXT             NOT NULL,
    "amount"          INTEGER          NOT NULL DEFAULT 0,
    "allocatedTokens" DECIMAL(24,6),
    "refunded"        BOOLEAN          NOT NULL DEFAULT false,
    "createdAt"       TIMESTAMP(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"       TIMESTAMP(3)     NOT NULL,

    CONSTRAINT "TokenPledge_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "TokenPledge_launchId_userId_key" ON "TokenPledge"("launchId", "userId");
CREATE INDEX IF NOT EXISTS "TokenPledge_launchId_idx" ON "TokenPledge"("launchId");
CREATE INDEX IF NOT EXISTS "TokenPledge_userId_idx" ON "TokenPledge"("userId");
CREATE INDEX IF NOT EXISTS "TokenPledge_amount_idx" ON "TokenPledge"("amount" DESC);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'TokenPledge_launchId_fkey') THEN
        ALTER TABLE "TokenPledge"
            ADD CONSTRAINT "TokenPledge_launchId_fkey"
            FOREIGN KEY ("launchId") REFERENCES "TokenLaunch"("id")
            ON DELETE CASCADE ON UPDATE CASCADE;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'TokenPledge_userId_fkey') THEN
        ALTER TABLE "TokenPledge"
            ADD CONSTRAINT "TokenPledge_userId_fkey"
            FOREIGN KEY ("userId") REFERENCES "User"("id")
            ON DELETE CASCADE ON UPDATE CASCADE;
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS "DexSwap" (
    "id"           TEXT             NOT NULL,
    "launchId"     TEXT             NOT NULL,
    "userId"       TEXT,
    "inputAmount"  DECIMAL(24,6)    NOT NULL,
    "outputAmount" DECIMAL(24,6)    NOT NULL,
    "feeUsd"       DECIMAL(16,6)    NOT NULL DEFAULT 0,
    "createdAt"    TIMESTAMP(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "DexSwap_pkey" PRIMARY KEY ("id")
);

CREATE INDEX IF NOT EXISTS "DexSwap_launchId_createdAt_idx" ON "DexSwap"("launchId", "createdAt" DESC);
CREATE INDEX IF NOT EXISTS "DexSwap_userId_idx" ON "DexSwap"("userId");

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'DexSwap_launchId_fkey') THEN
        ALTER TABLE "DexSwap"
            ADD CONSTRAINT "DexSwap_launchId_fkey"
            FOREIGN KEY ("launchId") REFERENCES "TokenLaunch"("id")
            ON DELETE CASCADE ON UPDATE CASCADE;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'DexSwap_userId_fkey') THEN
        ALTER TABLE "DexSwap"
            ADD CONSTRAINT "DexSwap_userId_fkey"
            FOREIGN KEY ("userId") REFERENCES "User"("id")
            ON DELETE SET NULL ON UPDATE CASCADE;
    END IF;
END$$;