-- Founder OS Phase 6 — Founder Idea Validator.
-- See docs/FOUNDER-IDEA-VALIDATOR.md §3.
--
-- The IdeaCheck model stores a competitive-landscape check for a founder's
-- idea. Created on-demand, on doxxing-application submit, or on project
-- creation. Append-only — re-runs make new rows so the founder can see how
-- the landscape shifts over time.
--
-- Idempotent (CREATE TYPE IF NOT EXISTS / CREATE TABLE IF NOT EXISTS) so it
-- is safe to apply on databases where the table was already created via
-- `prisma db push` during development.

-- Enum: PENDING → RUNNING → COMPLETED | FAILED
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'IdeaCheckStatus') THEN
        CREATE TYPE "IdeaCheckStatus" AS ENUM ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS "IdeaCheck" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "projectId" TEXT,
    "applicationId" TEXT,
    "ideaText" TEXT NOT NULL,
    "status" "IdeaCheckStatus" NOT NULL DEFAULT 'PENDING',
    "searchQueries" JSONB,
    "resultJson" JSONB,
    "differentiationScore" INTEGER,
    "similarProjectsJson" JSONB,
    "suggestedOssJson" JSONB,
    "errorMessage" TEXT,
    "dismissed" BOOLEAN NOT NULL DEFAULT false,
    "viewed" BOOLEAN NOT NULL DEFAULT false,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completedAt" TIMESTAMP(3),
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "IdeaCheck_pkey" PRIMARY KEY ("id")
);

CREATE INDEX IF NOT EXISTS "IdeaCheck_userId_idx" ON "IdeaCheck"("userId");
CREATE INDEX IF NOT EXISTS "IdeaCheck_projectId_idx" ON "IdeaCheck"("projectId");
CREATE INDEX IF NOT EXISTS "IdeaCheck_applicationId_idx" ON "IdeaCheck"("applicationId");
CREATE INDEX IF NOT EXISTS "IdeaCheck_status_idx" ON "IdeaCheck"("status");
CREATE INDEX IF NOT EXISTS "IdeaCheck_createdAt_idx" ON "IdeaCheck"("createdAt");

ALTER TABLE "IdeaCheck"
    ADD CONSTRAINT "IdeaCheck_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id")
    ON DELETE CASCADE ON UPDATE CASCADE;
