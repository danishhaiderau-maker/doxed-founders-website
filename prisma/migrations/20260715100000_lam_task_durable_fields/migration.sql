-- Phase 9 follow-up — durable Computer-Use agent-loop fields on LamTask.
--
-- The ComputerUseAdapter now drives a real Anthropic Computer Use agent loop
-- (send tool definitions → Claude responds with tool_use → execute against an
-- ExecutionTarget → send tool_result → repeat). To survive crashes mid-loop,
-- we persist the current iteration, the last Anthropic tool_use id we executed,
-- the per-tool retry counter, and the destructive-action confirmation state.
--
-- This is a pure ADD COLUMN migration; no existing rows are touched. All new
-- columns are nullable (or have defaults) so existing LamTask reads work
-- unchanged.
--
-- Idempotent (ADD COLUMN IF NOT EXISTS) so it is safe to apply on databases
-- that already had the columns added via `prisma db push`.

ALTER TABLE "LamTask"
    ADD COLUMN IF NOT EXISTS "currentStep" TEXT;

ALTER TABLE "LamTask"
    ADD COLUMN IF NOT EXISTS "lastToolCallId" TEXT;

ALTER TABLE "LamTask"
    ADD COLUMN IF NOT EXISTS "retryCount" INTEGER NOT NULL DEFAULT 0;

ALTER TABLE "LamTask"
    ADD COLUMN IF NOT EXISTS "confirmationState" JSONB;
