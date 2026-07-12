ALTER TABLE "IdeaCheck"
  ADD COLUMN IF NOT EXISTS "attemptCount" INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS "nextAttemptAt" TIMESTAMP(3),
  ADD COLUMN IF NOT EXISTS "processingStartedAt" TIMESTAMP(3);

CREATE INDEX IF NOT EXISTS "IdeaCheck_status_nextAttemptAt_idx"
  ON "IdeaCheck"("status", "nextAttemptAt");
