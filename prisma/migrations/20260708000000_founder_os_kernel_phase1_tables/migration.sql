-- Founder OS Kernel — Phase 1 tables.
-- See docs/KERNEL.md §8 (Capability), §9 (RoutingDecision / Flight Recorder),
-- and §7 (WorkspaceExecutionProfile / Execution Profiles).
--
-- These three tables back the Routing Engine v2 + Flight Recorder + Execution
-- Profiles. The Learning Engine (Phase 4) will refine Capability.successRate /
-- retryRate / sampleCount from real traffic observed via the Flight Recorder.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS so the
-- migration is safe to apply on databases where the tables were already
-- created via `prisma db push` during Phase 1 development.

-- Capability — data-driven description of every AI capability the platform
-- can route to. Replaces hardcoded model-name lookups in the kernel
-- (KERNEL.md rule §2). Seeds live in apps/api/src/capability-registry/
-- capability-registry.seeds.ts and are applied via `npm run db:seed:capabilities`.
CREATE TABLE IF NOT EXISTS "Capability" (
    "id" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "model" TEXT NOT NULL,
    "displayName" TEXT NOT NULL,
    "isActive" BOOLEAN NOT NULL DEFAULT true,
    "toolUse" BOOLEAN NOT NULL DEFAULT false,
    "jsonMode" BOOLEAN NOT NULL DEFAULT false,
    "largeContext" BOOLEAN NOT NULL DEFAULT false,
    "largeContextWindow" INTEGER,
    "vision" BOOLEAN NOT NULL DEFAULT false,
    "streaming" BOOLEAN NOT NULL DEFAULT true,
    "inputCostPer1M" DOUBLE PRECISION NOT NULL,
    "outputCostPer1M" DOUBLE PRECISION NOT NULL,
    "latencyP50Ms" INTEGER NOT NULL,
    "codeScore" DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    "reasoningScore" DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    "simpleQaScore" DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    "agentScore" DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    "visionScore" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "successRate" DOUBLE PRECISION NOT NULL DEFAULT 1,
    "retryRate" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "sampleCount" INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Capability_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "Capability_provider_model_key" ON "Capability" ("provider", "model");
CREATE INDEX IF NOT EXISTS "Capability_provider_idx" ON "Capability" ("provider");
CREATE INDEX IF NOT EXISTS "Capability_isActive_idx" ON "Capability" ("isActive");

-- RoutingDecision — one row per routing decision made by the Routing Engine v2,
-- written by the Flight Recorder. The outcome-signal columns (accepted /
-- retried / edited / rating) are populated async and are the training inputs
-- the Learning Engine consumes in Phase 4.
CREATE TABLE IF NOT EXISTS "RoutingDecision" (
    "id" TEXT NOT NULL,
    "requestId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "workspaceId" TEXT,
    "intent" TEXT NOT NULL,
    "profile" TEXT NOT NULL,
    "candidates" JSONB NOT NULL,
    "chosenProvider" TEXT NOT NULL,
    "chosenModel" TEXT NOT NULL,
    "cacheLevel" TEXT NOT NULL,
    "cacheKey" TEXT,
    "promptHash" TEXT NOT NULL,
    "tokenCountPrompt" INTEGER,
    "tokenCountCompletion" INTEGER,
    "latencyMs" INTEGER,
    "costUsd" DOUBLE PRECISION,
    "accepted" BOOLEAN,
    "retried" BOOLEAN,
    "edited" BOOLEAN,
    "rating" INTEGER,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "RoutingDecision_pkey" PRIMARY KEY ("id")
);

CREATE INDEX IF NOT EXISTS "RoutingDecision_userId_idx" ON "RoutingDecision" ("userId");
CREATE INDEX IF NOT EXISTS "RoutingDecision_chosenProvider_chosenModel_idx" ON "RoutingDecision" ("chosenProvider", "chosenModel");
CREATE INDEX IF NOT EXISTS "RoutingDecision_createdAt_idx" ON "RoutingDecision" ("createdAt");

-- WorkspaceExecutionProfile — workspace-scoped router overrides. A founder
-- picks one Execution Profile per workspace; the Routing Engine reads it as a
-- soft override of the global "balanced" defaults. See KERNEL.md §7.
CREATE TABLE IF NOT EXISTS "WorkspaceExecutionProfile" (
    "id" TEXT NOT NULL,
    "workspaceId" TEXT NOT NULL,
    "profile" TEXT NOT NULL DEFAULT 'balanced',
    "overrides" JSONB,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "WorkspaceExecutionProfile_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "WorkspaceExecutionProfile_workspaceId_key" ON "WorkspaceExecutionProfile" ("workspaceId");
