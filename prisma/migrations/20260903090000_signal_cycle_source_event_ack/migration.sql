ALTER TABLE "SignalCycleEvent"
  ADD COLUMN "sourceEventId" TEXT,
  ADD COLUMN "sourcePayloadSha256" TEXT,
  ADD COLUMN "sourceEventSeq" INTEGER,
  ADD COLUMN "platformReceivedAt" TIMESTAMP(3);

CREATE UNIQUE INDEX "SignalCycleEvent_sourceEventId_key"
  ON "SignalCycleEvent"("sourceEventId");
