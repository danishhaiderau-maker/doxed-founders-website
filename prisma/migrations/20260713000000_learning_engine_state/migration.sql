CREATE TABLE IF NOT EXISTS "LearningEngineState" (
    "id" TEXT NOT NULL,
    "lastRollupAt" TIMESTAMP(3),
    "lastProcessedCount" INTEGER NOT NULL DEFAULT 0,
    "lastUpdatedCount" INTEGER NOT NULL DEFAULT 0,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "LearningEngineState_pkey" PRIMARY KEY ("id")
);
