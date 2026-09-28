CREATE TABLE "RelayPositionReductionAudit" (
  "id" TEXT NOT NULL,
  "eventId" TEXT NOT NULL,
  "reductionId" TEXT NOT NULL,
  "cycleId" TEXT NOT NULL,
  "tradeId" TEXT NOT NULL,
  "eventSeq" INTEGER NOT NULL,
  "priorQty" DECIMAL(20,8) NOT NULL,
  "reducedQty" DECIMAL(20,8) NOT NULL,
  "remainingQty" DECIMAL(20,8) NOT NULL,
  "fillPrice" DECIMAL(20,8) NOT NULL,
  "sourceEventAt" TIMESTAMP(3) NOT NULL,
  "platformReceivedAt" TIMESTAMP(3) NOT NULL,
  "payload" JSONB NOT NULL,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "RelayPositionReductionAudit_pkey" PRIMARY KEY ("id")
);
CREATE UNIQUE INDEX "RelayPositionReductionAudit_eventId_key" ON "RelayPositionReductionAudit"("eventId");
CREATE UNIQUE INDEX "RelayPositionReductionAudit_reductionId_key" ON "RelayPositionReductionAudit"("reductionId");
CREATE UNIQUE INDEX "RelayPositionReductionAudit_tradeId_eventSeq_key" ON "RelayPositionReductionAudit"("tradeId", "eventSeq");
CREATE INDEX "RelayPositionReductionAudit_cycleId_createdAt_idx" ON "RelayPositionReductionAudit"("cycleId", "createdAt" DESC);
CREATE INDEX "RelayPositionReductionAudit_tradeId_createdAt_idx" ON "RelayPositionReductionAudit"("tradeId", "createdAt" DESC);

CREATE TYPE "ParticipantReductionPhase" AS ENUM ('CLAIMED', 'SUBMITTING', 'ACKNOWLEDGED', 'CONFIRMED');
CREATE TABLE "SignalCycleParticipantReduction" (
  "id" TEXT NOT NULL, "participantId" TEXT NOT NULL, "reductionId" TEXT NOT NULL,
  "sourceEventId" TEXT NOT NULL, "sourceEventSeq" INTEGER NOT NULL,
  "phase" "ParticipantReductionPhase" NOT NULL DEFAULT 'CLAIMED',
  "ownerToken" TEXT NOT NULL, "requestToken" TEXT NOT NULL,
  "beforeQty" DECIMAL(20,8) NOT NULL, "targetQty" DECIMAL(20,8) NOT NULL,
  "reduceQty" DECIMAL(20,8) NOT NULL, "exchangeOrderId" BIGINT,
  "claimedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "submittingAt" TIMESTAMP(3), "acknowledgedAt" TIMESTAMP(3), "confirmedAt" TIMESTAMP(3),
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP, "updatedAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "SignalCycleParticipantReduction_pkey" PRIMARY KEY ("id"),
  CONSTRAINT "SignalCycleParticipantReduction_participantId_fkey" FOREIGN KEY ("participantId") REFERENCES "SignalCycleParticipant"("id") ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE UNIQUE INDEX "SignalCycleParticipantReduction_ownerToken_key" ON "SignalCycleParticipantReduction"("ownerToken");
CREATE UNIQUE INDEX "SignalCycleParticipantReduction_requestToken_key" ON "SignalCycleParticipantReduction"("requestToken");
CREATE UNIQUE INDEX "SignalCycleParticipantReduction_participantId_reductionId_key" ON "SignalCycleParticipantReduction"("participantId", "reductionId");
CREATE UNIQUE INDEX "SignalCycleParticipantReduction_participantId_sourceEventId_key" ON "SignalCycleParticipantReduction"("participantId", "sourceEventId");
CREATE UNIQUE INDEX "SignalCycleParticipantReduction_participantId_sourceEventSeq_key" ON "SignalCycleParticipantReduction"("participantId", "sourceEventSeq");
CREATE INDEX "SignalCycleParticipantReduction_participantId_phase_createdAt_idx" ON "SignalCycleParticipantReduction"("participantId", "phase", "createdAt" DESC);
CREATE INDEX "SignalCycleParticipantReduction_exchangeOrderId_idx" ON "SignalCycleParticipantReduction"("exchangeOrderId");
