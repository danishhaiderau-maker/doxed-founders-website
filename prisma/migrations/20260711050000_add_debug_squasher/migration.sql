-- Add Debug Squasher tables (Phase 6.5).
-- Applies the same changes that were pushed via `prisma db push` so the
-- migration history records the schema transition cleanly.

-- User consent fields for the daily debug-squasher opt-in pop-up.
ALTER TABLE "User" ADD COLUMN "debugSquasherConsent" TEXT NOT NULL DEFAULT 'unset';
ALTER TABLE "User" ADD COLUMN "debugSquasherConsentAt" TIMESTAMP(3);

-- One row per debug-squasher harness run (overall + per-pillar).
CREATE TABLE "DebugSquasherRun" (
    "id"               TEXT            NOT NULL,
    "pillar"           TEXT            NOT NULL,
    "status"           TEXT            NOT NULL,
    "summary"          TEXT            NOT NULL,
    "diagnosis"        TEXT,
    "suggestedFixJson" JSONB,
    "runDurationMs"    INTEGER         NOT NULL DEFAULT 0,
    "triggeredBy"      TEXT            NOT NULL DEFAULT 'manual',
    "createdAt"        TIMESTAMP(3)    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "DebugSquasherRun_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "DebugSquasherRun_createdAt_idx" ON "DebugSquasherRun"("createdAt" DESC);
CREATE INDEX "DebugSquasherRun_pillar_createdAt_idx" ON "DebugSquasherRun"("pillar", "createdAt" DESC);
CREATE INDEX "DebugSquasherRun_status_idx" ON "DebugSquasherRun"("status");
