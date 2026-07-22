CREATE TYPE "AiManagedReservationStatus" AS ENUM (
  'RESERVED',
  'RECONCILED',
  'RELEASED',
  'UNCERTAIN'
);

CREATE TABLE "AiManagedReservation" (
  "id" TEXT NOT NULL,
  "requestId" TEXT NOT NULL,
  "userId" TEXT NOT NULL,
  "provider" TEXT NOT NULL,
  "model" TEXT NOT NULL,
  "tier" TEXT NOT NULL,
  "status" "AiManagedReservationStatus" NOT NULL DEFAULT 'RESERVED',
  "weightsVersion" TEXT NOT NULL DEFAULT 'founder-wtu-v1',
  "reservedWeightedUnits" INTEGER NOT NULL,
  "actualWeightedUnits" INTEGER,
  "estimatedInputTokens" INTEGER NOT NULL,
  "estimatedOutputTokens" INTEGER NOT NULL,
  "actualInputTokens" INTEGER,
  "actualCachedTokens" INTEGER,
  "actualOutputTokens" INTEGER,
  "actualReasoningTokens" INTEGER,
  "expiresAt" TIMESTAMP(3) NOT NULL,
  "upstreamStartedAt" TIMESTAMP(3),
  "reconciledAt" TIMESTAMP(3),
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMP(3) NOT NULL,

  CONSTRAINT "AiManagedReservation_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "AiManagedReservation_requestId_key"
ON "AiManagedReservation"("requestId");
CREATE INDEX "AiManagedReservation_userId_status_createdAt_idx"
ON "AiManagedReservation"("userId", "status", "createdAt");
CREATE INDEX "AiManagedReservation_status_expiresAt_idx"
ON "AiManagedReservation"("status", "expiresAt");

ALTER TABLE "AiManagedReservation"
ADD CONSTRAINT "AiManagedReservation_userId_fkey"
FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "PlatformSettings"
ALTER COLUMN "founderPromoTokenCap" SET DEFAULT 200000,
ALTER COLUMN "founderPromoWindowDays" SET DEFAULT 7;

UPDATE "PlatformSettings"
SET
  "founderPromoTokenCap" = 200000,
  "founderPromoWindowDays" = 7
WHERE "id" = 'default'
  AND "founderPromoTokenCap" = 5000000
  AND "founderPromoWindowDays" = 30;
