-- DDollar two-ledger foundation (Slice 1)
ALTER TABLE "User" ADD COLUMN "lifetimeContributionEarned" INTEGER NOT NULL DEFAULT 0;

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

CREATE TABLE "DdollarDailyEmission" (
    "id" TEXT NOT NULL,
    "emissionDate" DATE NOT NULL,
    "amountIssued" INTEGER NOT NULL DEFAULT 0,
    "usersAwarded" INTEGER NOT NULL DEFAULT 0,
    "note" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "DdollarDailyEmission_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "MarketplaceLedgerEntry_userId_createdAt_idx" ON "MarketplaceLedgerEntry"("userId", "createdAt");
CREATE INDEX "MarketplaceLedgerEntry_listingKey_idx" ON "MarketplaceLedgerEntry"("listingKey");

CREATE INDEX "FounderTreasuryLedgerEntry_actionKey_idx" ON "FounderTreasuryLedgerEntry"("actionKey");
CREATE INDEX "FounderTreasuryLedgerEntry_createdAt_idx" ON "FounderTreasuryLedgerEntry"("createdAt");

CREATE UNIQUE INDEX "DdollarDailyEmission_emissionDate_key" ON "DdollarDailyEmission"("emissionDate");

ALTER TABLE "MarketplaceLedgerEntry" ADD CONSTRAINT "MarketplaceLedgerEntry_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "FounderTreasuryLedgerEntry" ADD CONSTRAINT "FounderTreasuryLedgerEntry_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE SET NULL ON UPDATE CASCADE;
