ALTER TABLE "SignalCycleParticipant"
  ADD COLUMN "protectiveStopRequestId" TEXT,
  ADD COLUMN "protectiveStopPhase" TEXT,
  ADD COLUMN "protectiveStopClaimedAt" TIMESTAMP(3),
  ADD COLUMN "protectiveStopPurpose" TEXT,
  ADD COLUMN "protectiveStopClientId" INTEGER,
  ADD COLUMN "protectiveStopOrderId" BIGINT,
  ADD COLUMN "protectiveStopQty" DECIMAL(20,8),
  ADD COLUMN "protectiveStopPrice" DECIMAL(20,8),
  ADD COLUMN "protectiveStopPredecessorId" BIGINT;

CREATE UNIQUE INDEX "SignalCycleParticipant_protectiveStopRequestId_key"
  ON "SignalCycleParticipant"("protectiveStopRequestId");
