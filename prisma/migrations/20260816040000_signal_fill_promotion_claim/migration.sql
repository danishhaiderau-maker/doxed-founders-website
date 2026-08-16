ALTER TABLE "SignalCycleParticipant"
  ADD COLUMN "fillPromotionKey" TEXT,
  ADD COLUMN "fillPromotionClaimToken" TEXT,
  ADD COLUMN "fillPromotionClaimedAt" TIMESTAMP(3),
  ADD COLUMN "fillPromotionGeneration" TEXT,
  ADD COLUMN "fillPromotionEntryOrderId" BIGINT,
  ADD COLUMN "fillPromotionStopOrderId" BIGINT,
  ADD COLUMN "fillPromotionStopClientId" INTEGER,
  ADD COLUMN "fillPromotionPhase" TEXT;

ALTER TABLE "SignalCycleParticipant"
  ADD COLUMN "partialEmergencyRequestId" TEXT,
  ADD COLUMN "partialEmergencyPhase" TEXT,
  ADD COLUMN "partialEmergencyClaimedAt" TIMESTAMP(3),
  ADD COLUMN "partialEmergencyOrderId" BIGINT,
  ADD COLUMN "partialEmergencyBeforeAmount" DECIMAL(20,8),
  ADD COLUMN "partialEmergencyTargetAmount" DECIMAL(20,8);

CREATE UNIQUE INDEX "SignalCycleParticipant_fillPromotionKey_key"
  ON "SignalCycleParticipant"("fillPromotionKey");

CREATE UNIQUE INDEX "SignalCycleParticipant_fillPromotionClaimToken_key"
  ON "SignalCycleParticipant"("fillPromotionClaimToken");

CREATE UNIQUE INDEX "SignalCycleParticipant_partialEmergencyRequestId_key"
  ON "SignalCycleParticipant"("partialEmergencyRequestId");
