-- Phase 1.5 Trust Layer (Slice 4): regulatory, launch qualification, progressive unlock

CREATE TYPE "LaunchStage" AS ENUM ('BUILDER', 'WORKSPACE', 'PROJECT', 'RAISE_ROOM', 'GRADUATION', 'FOUNDER_EXCHANGE');
CREATE TYPE "RegulatoryClass" AS ENUM ('PENDING', 'COMMUNITY', 'UTILITY', 'GOVERNANCE', 'CAPITAL_RAISE', 'RESTRICTED');

ALTER TABLE "Project" ADD COLUMN "launchStage" "LaunchStage" NOT NULL DEFAULT 'BUILDER';
ALTER TABLE "Project" ADD COLUMN "launchQualificationScore" INTEGER NOT NULL DEFAULT 0;
ALTER TABLE "Project" ADD COLUMN "launchQualificationTier" TEXT;
ALTER TABLE "Project" ADD COLUMN "launchQualificationMeta" JSONB;
ALTER TABLE "Project" ADD COLUMN "launchQualificationAt" TIMESTAMP(3);
ALTER TABLE "Project" ADD COLUMN "regulatoryClass" "RegulatoryClass" NOT NULL DEFAULT 'PENDING';
ALTER TABLE "Project" ADD COLUMN "regulatoryClassifiedAt" TIMESTAMP(3);
ALTER TABLE "Project" ADD COLUMN "regulatoryQuestionnaireCompletedAt" TIMESTAMP(3);

ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "lifetimeContributionEarned" INTEGER NOT NULL DEFAULT 0;

ALTER TABLE "RaiseAllocation" ADD COLUMN "trustWeight" INTEGER NOT NULL DEFAULT 1;
ALTER TABLE "RaiseAllocation" ADD COLUMN "effectivePaperUsd" DECIMAL(16,2) NOT NULL DEFAULT 0;

CREATE TABLE "RegulatoryQuestionnaire" (
    "id" TEXT NOT NULL,
    "projectId" TEXT NOT NULL,
    "answers" JSONB NOT NULL,
    "completedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "RegulatoryQuestionnaire_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "RegulatoryQuestionnaire_projectId_key" ON "RegulatoryQuestionnaire"("projectId");
CREATE INDEX "RegulatoryQuestionnaire_projectId_idx" ON "RegulatoryQuestionnaire"("projectId");
CREATE INDEX "Project_launchStage_idx" ON "Project"("launchStage");
CREATE INDEX "Project_launchQualificationScore_idx" ON "Project"("launchQualificationScore");
CREATE INDEX "Project_regulatoryClass_idx" ON "Project"("regulatoryClass");

ALTER TABLE "RegulatoryQuestionnaire" ADD CONSTRAINT "RegulatoryQuestionnaire_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "Project"("id") ON DELETE CASCADE ON UPDATE CASCADE;

CREATE TABLE "MarketplaceLedgerEntry" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "listingKey" TEXT NOT NULL,
    "amountDdollar" INTEGER NOT NULL,
    "label" TEXT NOT NULL,
    "metadata" JSONB,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "MarketplaceLedgerEntry_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "MarketplaceLedgerEntry_userId_createdAt_idx" ON "MarketplaceLedgerEntry"("userId", "createdAt");
CREATE INDEX "MarketplaceLedgerEntry_listingKey_idx" ON "MarketplaceLedgerEntry"("listingKey");
ALTER TABLE "MarketplaceLedgerEntry" ADD CONSTRAINT "MarketplaceLedgerEntry_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

CREATE TABLE "FounderTreasuryLedgerEntry" (
    "id" TEXT NOT NULL,
    "userId" TEXT,
    "amountDdollar" INTEGER NOT NULL,
    "actionKey" TEXT NOT NULL,
    "label" TEXT NOT NULL,
    "metadata" JSONB,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "FounderTreasuryLedgerEntry_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "FounderTreasuryLedgerEntry_userId_createdAt_idx" ON "FounderTreasuryLedgerEntry"("userId", "createdAt");
CREATE INDEX "FounderTreasuryLedgerEntry_actionKey_idx" ON "FounderTreasuryLedgerEntry"("actionKey");
ALTER TABLE "FounderTreasuryLedgerEntry" ADD CONSTRAINT "FounderTreasuryLedgerEntry_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE SET NULL ON UPDATE CASCADE;

CREATE TABLE "DdollarDailyEmission" (
    "id" TEXT NOT NULL,
    "emissionDate" DATE NOT NULL,
    "amountIssued" INTEGER NOT NULL,
    "usersAwarded" INTEGER NOT NULL DEFAULT 0,
    "note" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "DdollarDailyEmission_pkey" PRIMARY KEY ("id")
);
CREATE UNIQUE INDEX "DdollarDailyEmission_emissionDate_key" ON "DdollarDailyEmission"("emissionDate");
