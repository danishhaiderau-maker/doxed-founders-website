CREATE TYPE "FounderCoordinationTaskStatus" AS ENUM ('ACTIVE', 'WAITING', 'COMPLETE', 'CANCELED');

CREATE TABLE "FounderCoordinationTask" (
  "id" TEXT NOT NULL,
  "clientTaskId" TEXT NOT NULL,
  "ownerUserId" TEXT NOT NULL,
  "teamId" TEXT,
  "workspaceKey" TEXT NOT NULL,
  "title" TEXT NOT NULL,
  "scope" JSONB,
  "branch" TEXT,
  "provider" TEXT,
  "permissions" JSONB,
  "budgetWeightedUnits" INTEGER,
  "status" "FounderCoordinationTaskStatus" NOT NULL DEFAULT 'ACTIVE',
  "heartbeatAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "expiresAt" TIMESTAMP(3) NOT NULL,
  "completedAt" TIMESTAMP(3),
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "FounderCoordinationTask_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "FounderCoordinationPathClaim" (
  "id" TEXT NOT NULL,
  "taskId" TEXT NOT NULL,
  "workspaceKey" TEXT NOT NULL,
  "path" TEXT NOT NULL,
  "fencingToken" TEXT NOT NULL,
  "generation" INTEGER NOT NULL DEFAULT 1,
  "heartbeatAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "expiresAt" TIMESTAMP(3) NOT NULL,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "FounderCoordinationPathClaim_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "FounderCoordinationAudit" (
  "id" TEXT NOT NULL,
  "actorUserId" TEXT NOT NULL,
  "teamId" TEXT,
  "taskId" TEXT,
  "action" TEXT NOT NULL,
  "details" JSONB,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "FounderCoordinationAudit_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "FounderCoordinationTask_ownerUserId_clientTaskId_key" ON "FounderCoordinationTask"("ownerUserId", "clientTaskId");
CREATE INDEX "FounderCoordinationTask_workspaceKey_status_expiresAt_idx" ON "FounderCoordinationTask"("workspaceKey", "status", "expiresAt");
CREATE INDEX "FounderCoordinationTask_teamId_workspaceKey_status_idx" ON "FounderCoordinationTask"("teamId", "workspaceKey", "status");
CREATE UNIQUE INDEX "FounderCoordinationPathClaim_fencingToken_key" ON "FounderCoordinationPathClaim"("fencingToken");
CREATE UNIQUE INDEX "FounderCoordinationPathClaim_workspaceKey_path_key" ON "FounderCoordinationPathClaim"("workspaceKey", "path");
CREATE INDEX "FounderCoordinationPathClaim_taskId_expiresAt_idx" ON "FounderCoordinationPathClaim"("taskId", "expiresAt");
CREATE INDEX "FounderCoordinationAudit_teamId_createdAt_idx" ON "FounderCoordinationAudit"("teamId", "createdAt");
CREATE INDEX "FounderCoordinationAudit_actorUserId_createdAt_idx" ON "FounderCoordinationAudit"("actorUserId", "createdAt");
CREATE INDEX "FounderCoordinationAudit_taskId_createdAt_idx" ON "FounderCoordinationAudit"("taskId", "createdAt");

ALTER TABLE "FounderCoordinationTask" ADD CONSTRAINT "FounderCoordinationTask_ownerUserId_fkey" FOREIGN KEY ("ownerUserId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "FounderCoordinationTask" ADD CONSTRAINT "FounderCoordinationTask_teamId_fkey" FOREIGN KEY ("teamId") REFERENCES "FounderPlanTeam"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "FounderCoordinationPathClaim" ADD CONSTRAINT "FounderCoordinationPathClaim_taskId_fkey" FOREIGN KEY ("taskId") REFERENCES "FounderCoordinationTask"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "FounderCoordinationAudit" ADD CONSTRAINT "FounderCoordinationAudit_actorUserId_fkey" FOREIGN KEY ("actorUserId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "FounderCoordinationAudit" ADD CONSTRAINT "FounderCoordinationAudit_teamId_fkey" FOREIGN KEY ("teamId") REFERENCES "FounderPlanTeam"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "FounderCoordinationAudit" ADD CONSTRAINT "FounderCoordinationAudit_taskId_fkey" FOREIGN KEY ("taskId") REFERENCES "FounderCoordinationTask"("id") ON DELETE SET NULL ON UPDATE CASCADE;
