-- Founder OS Phase 9 — LAM persistence.
--
-- Replaces the in-memory Map<taskId, LamTask> in LamOrchestratorService so
-- task state survives restarts and the controller can read history from the
-- DB. Status is stored as a plain String (PLANNING/RUNNING/SYNTHESIZING/
-- COMPLETED/FAILED) to match the DebugSquasherRun pattern and avoid
-- enum-migration churn.
--
-- Idempotent (CREATE TABLE IF NOT EXISTS / DO $$ ... ADD CONSTRAINT) so it is
-- safe to apply on databases where the tables were already created via
-- `prisma db push` during development.

-- ─── LAM — LamTask ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "LamTask" (
    "id"           TEXT NOT NULL,
    "userId"       TEXT NOT NULL,
    "goal"         TEXT NOT NULL,
    "status"       TEXT NOT NULL DEFAULT 'PLANNING',
    "planJson"     JSONB,
    "resultJson"   JSONB,
    "result"       TEXT,
    "elapsedMs"    INTEGER,
    "costDdollar"  INTEGER,
    "errorMessage" TEXT,
    "createdAt"    TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"    TIMESTAMP(3) NOT NULL,
    "completedAt"  TIMESTAMP(3),

    CONSTRAINT "LamTask_pkey" PRIMARY KEY ("id")
);

CREATE INDEX IF NOT EXISTS "LamTask_userId_createdAt_idx"
    ON "LamTask"("userId", "createdAt" DESC);

CREATE INDEX IF NOT EXISTS "LamTask_status_idx"
    ON "LamTask"("status");

-- ─── LAM — LamStep ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "LamStep" (
    "id"         TEXT NOT NULL,
    "taskId"     TEXT NOT NULL,
    "stepIndex"  INTEGER NOT NULL,
    "action"     TEXT NOT NULL,
    "adapter"    TEXT NOT NULL,
    "inputJson"  JSONB,
    "outputJson" JSONB,
    "status"     TEXT NOT NULL DEFAULT 'pending',
    "error"      TEXT,
    "durationMs" INTEGER,
    "createdAt"  TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "LamStep_pkey" PRIMARY KEY ("id")
);

CREATE INDEX IF NOT EXISTS "LamStep_taskId_stepIndex_idx"
    ON "LamStep"("taskId", "stepIndex");

ALTER TABLE "LamStep"
    DROP CONSTRAINT IF EXISTS "LamStep_taskId_fkey";

ALTER TABLE "LamStep"
    ADD CONSTRAINT "LamStep_taskId_fkey"
    FOREIGN KEY ("taskId") REFERENCES "LamTask"("id")
    ON DELETE CASCADE ON UPDATE CASCADE;
