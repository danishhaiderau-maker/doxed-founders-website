-- Founder OS Phase 7 — Deployment Modes (Private / Public / Hybrid).
-- See docs/DEPLOYMENT-MODES-UX.md §4 (config shapes) and §5 (publish flow).
--
-- Adds:
--   • DeploymentMode enum on Project (default HYBRID — the recommended mode).
--   • ProjectDeploymentConfig — 1:1 per-project runtime config (git/db/hosting/
--     phone/ai + the Hybrid publish plan + Founder Node runtime status flags).
--   • DeploymentPublishJob — one row per Publish click; the frontend polls the
--     latest job for live 4-step progress (git mirror → DB migrate → Vercel
--     deploy → health verify).
--
-- Idempotent (CREATE TYPE IF NOT EXISTS / DO $$ ... ADD COLUMN IF NOT EXISTS)
-- so it is safe to apply on databases where the table was already created via
-- `prisma db push` during development.

-- ─── Enums ───────────────────────────────────────────────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'DeploymentMode') THEN
        CREATE TYPE "DeploymentMode" AS ENUM ('PRIVATE', 'PUBLIC', 'HYBRID');
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'PublishJobStatus') THEN
        CREATE TYPE "PublishJobStatus" AS ENUM ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED');
    END IF;
END$$;

-- ─── Project.deploymentMode ──────────────────────────────────────────────────

ALTER TABLE "Project"
    ADD COLUMN IF NOT EXISTS "deploymentMode" "DeploymentMode" NOT NULL DEFAULT 'HYBRID';

CREATE INDEX IF NOT EXISTS "Project_deploymentMode_idx" ON "Project"("deploymentMode");

-- ─── ProjectDeploymentConfig (1:1 with Project) ──────────────────────────────

CREATE TABLE IF NOT EXISTS "ProjectDeploymentConfig" (
    "id"               TEXT NOT NULL,
    "projectId"        TEXT NOT NULL,
    "gitBackend"       TEXT NOT NULL DEFAULT 'forgejo',
    "gitUrl"           TEXT,
    "dbProvider"       TEXT NOT NULL DEFAULT 'sqlite',
    "dbUrl"            TEXT,
    "hostingType"      TEXT NOT NULL DEFAULT 'tunnel-on-demand',
    "hostingUrl"       TEXT,
    "phoneRoute"       TEXT NOT NULL DEFAULT 'tailscale',
    "aiGateway"        TEXT NOT NULL DEFAULT 'founder-os-cloud',
    "publishPlan"      JSONB,
    "forgejoOnline"    BOOLEAN DEFAULT false,
    "sqlitePresent"    BOOLEAN DEFAULT false,
    "tunnelActive"     BOOLEAN DEFAULT false,
    "tailscaleReady"   BOOLEAN DEFAULT false,
    "runtimeStatusAt"  TIMESTAMP(3),
    "createdAt"        TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"        TIMESTAMP(3) NOT NULL,

    CONSTRAINT "ProjectDeploymentConfig_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "ProjectDeploymentConfig_projectId_key"
    ON "ProjectDeploymentConfig"("projectId");

CREATE INDEX IF NOT EXISTS "ProjectDeploymentConfig_projectId_idx"
    ON "ProjectDeploymentConfig"("projectId");

ALTER TABLE "ProjectDeploymentConfig"
    DROP CONSTRAINT IF EXISTS "ProjectDeploymentConfig_projectId_fkey";

ALTER TABLE "ProjectDeploymentConfig"
    ADD CONSTRAINT "ProjectDeploymentConfig_projectId_fkey"
    FOREIGN KEY ("projectId") REFERENCES "Project"("id")
    ON DELETE CASCADE ON UPDATE CASCADE;

-- ─── DeploymentPublishJob (1:N with Project) ─────────────────────────────────

CREATE TABLE IF NOT EXISTS "DeploymentPublishJob" (
    "id"           TEXT NOT NULL,
    "projectId"    TEXT NOT NULL,
    "planSnapshot" JSONB NOT NULL,
    "status"       "PublishJobStatus" NOT NULL DEFAULT 'PENDING',
    "currentStep"  INTEGER NOT NULL DEFAULT 0,
    "steps"        JSONB NOT NULL DEFAULT '[]',
    "liveUrl"      TEXT,
    "errorMessage" TEXT,
    "startedAt"    TIMESTAMP(3),
    "completedAt"  TIMESTAMP(3),
    "createdAt"    TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"    TIMESTAMP(3) NOT NULL,

    CONSTRAINT "DeploymentPublishJob_pkey" PRIMARY KEY ("id")
);

CREATE INDEX IF NOT EXISTS "DeploymentPublishJob_projectId_createdAt_idx"
    ON "DeploymentPublishJob"("projectId", "createdAt" DESC);

CREATE INDEX IF NOT EXISTS "DeploymentPublishJob_status_idx"
    ON "DeploymentPublishJob"("status");

ALTER TABLE "DeploymentPublishJob"
    DROP CONSTRAINT IF EXISTS "DeploymentPublishJob_projectId_fkey";

ALTER TABLE "DeploymentPublishJob"
    ADD CONSTRAINT "DeploymentPublishJob_projectId_fkey"
    FOREIGN KEY ("projectId") REFERENCES "Project"("id")
    ON DELETE CASCADE ON UPDATE CASCADE;
