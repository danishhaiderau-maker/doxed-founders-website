-- Durable LAM execution queue and per-step confirmation gate.
ALTER TABLE "LamTask"
    ADD COLUMN IF NOT EXISTS "attemptCount" INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS "nextAttemptAt" TIMESTAMP(3),
    ADD COLUMN IF NOT EXISTS "executionClaimedAt" TIMESTAMP(3),
    ADD COLUMN IF NOT EXISTS "confirmationStepIndex" INTEGER,
    ADD COLUMN IF NOT EXISTS "confirmedStepIndex" INTEGER,
    ADD COLUMN IF NOT EXISTS "confirmedAt" TIMESTAMP(3);

CREATE INDEX IF NOT EXISTS "LamTask_status_nextAttemptAt_idx"
    ON "LamTask"("status", "nextAttemptAt");
