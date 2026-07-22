CREATE TYPE "FounderCoordinationMode" AS ENUM ('FOCUS', 'TEAM');

ALTER TYPE "FounderCoordinationTaskStatus" ADD VALUE IF NOT EXISTS 'RUNNING';
ALTER TYPE "FounderCoordinationTaskStatus" ADD VALUE IF NOT EXISTS 'BLOCKED';
ALTER TYPE "FounderCoordinationTaskStatus" ADD VALUE IF NOT EXISTS 'VERIFYING';

ALTER TABLE "FounderCoordinationTask"
  ADD COLUMN "goal" TEXT,
  ADD COLUMN "mode" "FounderCoordinationMode" NOT NULL DEFAULT 'FOCUS',
  ADD COLUMN "expectedOutput" JSONB,
  ADD COLUMN "dependencies" JSONB,
  ADD COLUMN "parentTaskId" TEXT,
  ADD COLUMN "resultCommit" TEXT,
  ADD COLUMN "verification" JSONB;

UPDATE "FounderCoordinationTask" SET "goal" = "title" WHERE "goal" IS NULL;
ALTER TABLE "FounderCoordinationTask" ALTER COLUMN "goal" SET NOT NULL;

CREATE INDEX "FounderCoordinationTask_parentTaskId_status_idx"
  ON "FounderCoordinationTask"("parentTaskId", "status");

ALTER TABLE "FounderCoordinationTask"
  ADD CONSTRAINT "FounderCoordinationTask_parentTaskId_fkey"
  FOREIGN KEY ("parentTaskId") REFERENCES "FounderCoordinationTask"("id")
  ON DELETE CASCADE ON UPDATE CASCADE;
