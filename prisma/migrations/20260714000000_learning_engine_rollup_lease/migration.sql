-- Serialise Learning Engine rollups across API replicas. The short lease is
-- released after a successful watermark commit or reclaimed after a crash.
ALTER TABLE "LearningEngineState"
  ADD COLUMN IF NOT EXISTS "leaseOwner" TEXT,
  ADD COLUMN IF NOT EXISTS "leaseExpiresAt" TIMESTAMP(3);

CREATE INDEX IF NOT EXISTS "LearningEngineState_leaseExpiresAt_idx"
  ON "LearningEngineState"("leaseExpiresAt");
