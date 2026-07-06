-- AiTokenUsageLog runtime telemetry (Founder Brain Phase 1 quick wins)
ALTER TABLE "AiTokenUsageLog" ADD COLUMN IF NOT EXISTS "cacheLevel" TEXT;
ALTER TABLE "AiTokenUsageLog" ADD COLUMN IF NOT EXISTS "localToolUsed" BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE "AiTokenUsageLog" ADD COLUMN IF NOT EXISTS "confidenceScore" DOUBLE PRECISION;
