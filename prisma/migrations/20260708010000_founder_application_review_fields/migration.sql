-- Doxxing admin review surface (Phase 1 wiring, Step 3).
-- Adds the review-audit columns to FounderApplication so the new
-- GET /founder-applications/pending + PATCH /founder-applications/:id
-- admin endpoints can persist the decision and reviewer.
ALTER TABLE "FounderApplication" ADD COLUMN IF NOT EXISTS "reviewNotes" TEXT;
ALTER TABLE "FounderApplication" ADD COLUMN IF NOT EXISTS "reviewerId" TEXT;
ALTER TABLE "FounderApplication" ADD COLUMN IF NOT EXISTS "reviewedAt" TIMESTAMP(3);
