-- Founder OS Kernel §3 — Memory Engine stores.
--
-- Adds the four Memory Engine tables (Conversation/Project/Founder/Workspace)
-- described in docs/KERNEL.md §3. The MemoryEngineService routes get/set/
-- query/forget to the right table based on the `store` argument.
--
-- Idempotent (CREATE TABLE IF NOT EXISTS) so it is safe to apply on databases
-- where the tables were already created via `prisma db push` during development.

-- ─── Memory Engine — Conversation store ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS "ConversationMemory" (
    "id"        TEXT NOT NULL,
    "sessionId" TEXT NOT NULL,
    "userId"    TEXT,
    "role"      TEXT NOT NULL,
    "content"   TEXT NOT NULL,
    "tokens"    INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ConversationMemory_pkey" PRIMARY KEY ("id")
);

CREATE INDEX IF NOT EXISTS "ConversationMemory_sessionId_createdAt_idx"
    ON "ConversationMemory"("sessionId", "createdAt");

CREATE INDEX IF NOT EXISTS "ConversationMemory_userId_idx"
    ON "ConversationMemory"("userId");

-- ─── Memory Engine — Project store ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "ProjectMemory" (
    "id"         TEXT NOT NULL,
    "projectId"  TEXT NOT NULL,
    "key"        TEXT NOT NULL,
    "value"      JSONB NOT NULL,
    "source"     TEXT NOT NULL DEFAULT 'system',
    "confidence" DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    "createdAt"  TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"  TIMESTAMP(3) NOT NULL,

    CONSTRAINT "ProjectMemory_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "ProjectMemory_projectId_key_key"
    ON "ProjectMemory"("projectId", "key");

CREATE INDEX IF NOT EXISTS "ProjectMemory_projectId_idx"
    ON "ProjectMemory"("projectId");

CREATE INDEX IF NOT EXISTS "ProjectMemory_projectId_updatedAt_idx"
    ON "ProjectMemory"("projectId", "updatedAt" DESC);

-- ─── Memory Engine — Founder store ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "FounderMemory" (
    "id"        TEXT NOT NULL,
    "userId"    TEXT NOT NULL,
    "key"       TEXT NOT NULL,
    "value"     JSONB NOT NULL,
    "source"    TEXT NOT NULL DEFAULT 'system',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "FounderMemory_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "FounderMemory_userId_key_key"
    ON "FounderMemory"("userId", "key");

CREATE INDEX IF NOT EXISTS "FounderMemory_userId_idx"
    ON "FounderMemory"("userId");

CREATE INDEX IF NOT EXISTS "FounderMemory_userId_updatedAt_idx"
    ON "FounderMemory"("userId", "updatedAt" DESC);

-- ─── Memory Engine — Workspace store (ephemeral) ────────────────────────────

CREATE TABLE IF NOT EXISTS "WorkspaceMemory" (
    "id"          TEXT NOT NULL,
    "workspaceId" TEXT NOT NULL,
    "key"         TEXT NOT NULL,
    "value"       JSONB NOT NULL,
    "expiresAt"   TIMESTAMP(3),
    "createdAt"   TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"   TIMESTAMP(3) NOT NULL,

    CONSTRAINT "WorkspaceMemory_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "WorkspaceMemory_workspaceId_key_key"
    ON "WorkspaceMemory"("workspaceId", "key");

CREATE INDEX IF NOT EXISTS "WorkspaceMemory_workspaceId_idx"
    ON "WorkspaceMemory"("workspaceId");

CREATE INDEX IF NOT EXISTS "WorkspaceMemory_expiresAt_idx"
    ON "WorkspaceMemory"("expiresAt");
